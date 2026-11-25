"""MQTT/TLS uplink with exponential back-off and batched replay.

States: OFFLINE -> CONNECTING -> ONLINE; failures go to BACKOFF or AUTH_FAILED.
Manual pause disconnects this node's MQTT client and waits in PAUSED, so the
broker sees the device go inactive while SQLite keeps accepting samples.
A batch is marked synced only after QoS 1 PUBACK. Rows keep their original
timestamps when replayed after an outage.
"""
from __future__ import annotations

import json
import logging
import random
import socket
import ssl
import threading
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from .buffer import Buffer, now_ms

TELEMETRY_TOPIC = "v1/devices/me/telemetry"
AUTH_CODES = {4, 5, 134, 135, 140}


class GateClient(mqtt.Client):
    """paho client that can dial a relay while TLS still verifies the broker."""

    def __init__(self, *a, dial: Optional[tuple] = None, **kw):
        super().__init__(*a, **kw)
        self._dial = dial

    def _create_socket_connection(self):
        if not self._dial:
            return super()._create_socket_connection()
        return socket.create_connection(self._dial, timeout=self._connect_timeout)


class Backoff:
    """Exponential back-off with equal jitter."""

    def __init__(self, d_min: float, d_max: float, rng: random.Random):
        self.d_min, self.d_max, self.rng = d_min, d_max, rng
        self.n = 0

    def next(self) -> float:
        d = min(self.d_max, self.d_min * (2 ** self.n))
        self.n += 1
        return d * self.rng.uniform(0.5, 1.0)

    def reset(self):
        self.n = 0


class Link(threading.Thread):
    def __init__(self, node: str, token: str, buffer: Buffer, host: str, port: int,
                 tls: bool = True, ca_file: Optional[str] = None, dial: Optional[tuple] = None,
                 batch: int = 25, backoff_min: float = 1.0, backoff_max: float = 30.0,
                 auth_backoff_s: Optional[float] = None, keepalive: int = 10,
                 ack_timeout: float = 8.0, seed: int = 0, log: Optional[logging.Logger] = None,
                 events: Optional[Callable[[dict], None]] = None, replay: bool = True,
                 password: Optional[str] = None):
        super().__init__(name=f"link-{node}", daemon=True)
        self.node, self.token, self.buf = node, token, buffer
        self.host, self.port, self.tls, self.ca_file, self.dial = host, port, tls, ca_file, dial
        self.batch = max(1, int(batch))
        self.backoff = Backoff(backoff_min, backoff_max, random.Random(seed))
        self.auth_backoff_s = auth_backoff_s if auth_backoff_s is not None else backoff_max
        self.keepalive, self.ack_timeout = keepalive, ack_timeout
        self.log = log or logging.getLogger(f"link.{node}")
        self.events = events
        self.state = "OFFLINE"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._kick = threading.Event()
        self._paused = False
        self.password = password
        self.replay = replay
        self.online_since_ms = 0
        self._conn_rc = None
        self._connected = threading.Event()
        self._disconnected = threading.Event()
        self.batch_id = buffer.last_batch_id()
        self.stats = {"connects": 0, "auth_failures": 0, "net_failures": 0,
                      "batches": 0, "rows_flushed": 0, "republished_rows": 0}
        self.client: Optional[GateClient] = None

    def _emit(self, **kw):
        if self.events:
            kw.setdefault("node", self.node)
            kw.setdefault("t_ms", now_ms())
            self.events(kw)

    def _goto(self, new: str, reason: str = ""):
        if new != self.state:
            self.log.info("LINK %s -> %s%s", self.state, new,
                          f" ({reason})" if reason else "")
            self._emit(ev="link", frm=self.state, to=new, reason=reason,
                       depth=self.buf.depth())
            self.state = new

    def wake(self):
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        """Pause only this node's MQTT session; sampling continues into SQLite."""
        new = bool(paused)
        if new == self._paused:
            return
        self._paused = new
        if self.log is not None:
            self.log.warning("LINK MQTT session %s by manual control",
                             "PAUSED" if new else "RESUMED")
        self._wake.set()

    def _wait_while_paused(self):
        """Stay disconnected until resume; the link thread owns the client."""
        self._kick.clear()
        self._wake.clear()
        self._goto("PAUSED", "manual control")
        while not self._stop.is_set() and self._paused:
            self._wake.wait(0.2)

    def stop(self):
        self._stop.set()
        self._wake.set()

    def reconnect(self, reason: str):
        self.log.info("LINK reconnect requested (%s)", reason)
        self._kick.set()
        self._wake.set()

    # ---- paho callbacks ---------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        self._conn_rc = int(rc.value if hasattr(rc, "value") else rc)
        self._connected.set()

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self._disconnected.set()

    def _new_client(self) -> GateClient:
        c = GateClient(mqtt.CallbackAPIVersion.VERSION2,
                       client_id=f"jd-{self.node}",
                       protocol=mqtt.MQTTv311,
                       clean_session=True,
                       dial=self.dial)
        c.username_pw_set(self.token, self.password)
        if self.tls:
            ctx = (ssl.create_default_context(cafile=self.ca_file)
                   if self.ca_file else ssl.create_default_context())
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            c.tls_set_context(ctx)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.max_inflight_messages_set(1)
        return c

    # ---- main loop --------------------------------------------------------
    def run(self):
        while not self._stop.is_set():
            if self._paused:
                self._wait_while_paused()
                continue
            self._kick.clear()
            self._goto("CONNECTING")
            ok = self._connect_once()
            if ok is True:
                self.backoff.reset()
                self.stats["connects"] += 1
                self._goto("ONLINE", f"{self._tls_desc()} depth={self.buf.depth()}")
                reason = self._online_loop()
                self._teardown()
                if self._stop.is_set():
                    break
                if self._paused or reason == "paused by manual control":
                    continue
                if reason == "reconnect requested":
                    d = 0.5
                    self._goto("BACKOFF", f"operator: {reason}; retry in {d:.1f}s")
                else:
                    self.stats["net_failures"] += 1
                    d = self.backoff.next()
                    self._goto("BACKOFF", f"network: {reason}; retry in {d:.1f}s")
                self._sleep(d)
            elif ok == "auth":
                self.stats["auth_failures"] += 1
                self._goto("AUTH_FAILED",
                           f"CONNACK rc={self._conn_rc}: check access token; "
                           f"retry in {self.auth_backoff_s:.0f}s")
                self._sleep(self.auth_backoff_s)
            else:
                self.stats["net_failures"] += 1
                d = self.backoff.next()
                self._goto("BACKOFF", f"network: {ok}; retry in {d:.1f}s")
                self._sleep(d)
        self._goto("OFFLINE", "stopped")

    def _sleep(self, s: float):
        end = time.monotonic() + s
        while (not self._stop.is_set() and not self._kick.is_set()
               and not self._paused and time.monotonic() < end):
            time.sleep(max(0.0, min(0.2, end - time.monotonic())))

    def _connect_once(self):
        self._connected.clear()
        self._disconnected.clear()
        self._conn_rc = None
        self.client = self._new_client()
        try:
            self.client.connect(self.host, self.port, keepalive=self.keepalive)
        except ssl.SSLCertVerificationError as e:
            self._teardown()
            return f"TLS certificate rejected ({e.verify_message})"
        except (OSError, ssl.SSLError) as e:
            self._teardown()
            return f"{type(e).__name__}: {e}"
        t_end = time.monotonic() + 10
        while time.monotonic() < t_end and not self._connected.is_set():
            try:
                self.client.loop(timeout=0.1)
            except Exception as e:
                self._teardown()
                return f"{type(e).__name__}: {e}"
            if self._disconnected.is_set() and not self._connected.is_set():
                break
        if not self._connected.is_set():
            self._teardown()
            return "CONNACK timeout"
        if self._conn_rc == 0:
            return True
        self._teardown()
        if self._conn_rc in AUTH_CODES:
            return "auth"
        return f"CONNACK rc={self._conn_rc}"

    def _tls_desc(self) -> str:
        try:
            sk = self.client.socket()
            return f"{sk.version()} {sk.cipher()[0]}" if self.tls else "plain TCP"
        except Exception:
            return "tls ?"

    def _teardown(self):
        c = self.client
        self.client = None
        if c is None:
            return
        try:
            c.disconnect()
        except Exception:
            pass
        try:
            if c.socket():
                c.socket().close()
        except Exception:
            pass

    def _pump(self, timeout: float = 0.05) -> bool:
        rc = self.client.loop(timeout=timeout)
        return (rc == mqtt.MQTT_ERR_SUCCESS and not self._disconnected.is_set())

    def _online_loop(self) -> str:
        self.online_since_ms = now_ms()
        while not self._stop.is_set():
            if self._paused:
                self._kick.clear()
                self._teardown()
                self._goto("PAUSED", "manual control")
                return "paused by manual control"
            if self._kick.is_set():
                self._kick.clear()
                return "reconnect requested"
            min_ts = 0 if self.replay else self.online_since_ms
            self._wake.clear()
            if self._paused:
                continue
            rows = self.buf.pending(self.batch, min_ts=min_ts)
            if not rows:
                if not self._pump(0.0):
                    return "connection lost while idle"
                self._wake.wait(0.25)
                continue
            reason = self._send_batch(rows)
            if reason:
                return reason
        return "stopped"

    def _send_batch(self, rows) -> Optional[str]:
        depth_before = self.buf.depth()
        arr = [{"ts": r.ts_ms, "values": json.loads(r.payload)} for r in rows]
        for a in arr:
            a["values"]["buffer_depth"] = depth_before
        body = json.dumps(arr if len(arr) > 1 else arr[0], separators=(",", ":"))
        seqs = [r.seq for r in rows]
        t0 = now_ms()
        self.buf.mark_attempt(seqs, t0)
        try:
            info = self.client.publish(TELEMETRY_TOPIC, body, qos=1)
        except Exception as e:
            return f"publish raised {type(e).__name__}"
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            return f"publish rc={info.rc}"
        t_end = time.monotonic() + self.ack_timeout
        while not info.is_published():
            if time.monotonic() > t_end:
                self.stats["republished_rows"] += len(rows)
                self.log.warning(
                    "PUBACK timeout batch rows=%d after %.1fs; rows stay pending",
                    len(rows), self.ack_timeout)
                self._emit(ev="ack_timeout", rows=len(rows), depth=depth_before)
                return "PUBACK timeout"
            if not self._pump(0.02):
                if info.is_published():
                    break
                return "connection lost awaiting PUBACK"
        t1 = now_ms()
        self.batch_id += 1
        depth = self.buf.mark_synced(seqs, t1, self.batch_id)
        self.stats["batches"] += 1
        self.stats["rows_flushed"] += len(rows)
        backlog = len(rows) > 1 or depth_before > len(rows)
        lvl = logging.INFO if backlog else logging.DEBUG
        self.log.log(lvl, "FLUSH batch=%d rows=%d bytes=%d ack_ms=%d depth %d->%d",
                     self.batch_id, len(rows), len(body), t1 - t0, depth_before, depth)
        self._emit(ev="flush", batch=self.batch_id, rows=len(rows), bytes=len(body),
                   t_pub=t0, t_ack=t1, depth_before=depth_before, depth=depth,
                   oldest_ts=rows[0].ts_ms, newest_ts=rows[-1].ts_ms)
        return None