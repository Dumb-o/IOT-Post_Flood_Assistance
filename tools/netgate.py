"""netgate: a TCP relay that turns a real network outage on and off.

The simulator's MQTT clients dial the gate (127.0.0.1:<listen>) instead of the
broker; the gate splices bytes to the real broker (ThingsBoard on 8883). TLS is
end-to-end: the gate only sees ciphertext, and the client still verifies the
broker certificate against the broker's real hostname (edge/link.py dials the
gate but wraps TLS with server_hostname=<broker>).

Outage modes
  blackhole : existing connections stay open but every byte is silently
              dropped and new connections are accepted but never forwarded;
              this is what a cellular dead zone looks like to the modem.
              The client only notices through keep-alive or PUBACK timeouts,
              so in-flight batches are genuinely lost and must be replayed.
  refuse    : all spliced connections are reset and new ones are closed at
              once (a modem that has lost its PDP context).

Control
  * in-process: Relay.down(mode) / Relay.up()  (used by the scenario runner)
  * HTTP:       GET http://127.0.0.1:<ctl>/down?mode=blackhole | /up | /status
  * CLI:        python -m tools.netgate --listen 18883 --target host:8883 --ctl 18999
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("netgate")


class Relay:
    def __init__(self, listen_port: int, target_host: str, target_port: int, listen_host: str = "127.0.0.1",
                 delay_ms: float = 0.0):
        self.listen = (listen_host, listen_port)
        self.delay_s = delay_ms / 1000.0   # one-way added latency, e.g. 50 ms -> ~100 ms RTT (LTE Cat-1-like)
        self.target = (target_host, target_port)
        self.mode = "up"            # "up" | "blackhole" | "refuse"
        self._pairs = set()
        self._lock = threading.Lock()
        self._srv = None
        self._stop = threading.Event()
        self.history = []           # [(t_ms, mode)] for the results log

    # -- control -----------------------------------------------------------
    def down(self, mode: str = "blackhole"):
        assert mode in ("blackhole", "refuse")
        self._set(mode)
        if mode == "refuse":
            with self._lock:
                pairs = list(self._pairs)
            for a, b in pairs:
                for s in (a, b):
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                        s.close()
                    except OSError:
                        pass

    def up(self):
        # Connections that lived through a blackhole are stale (their TLS/MQTT
        # state is desynchronised by dropped bytes), so drop them; clients reconnect.
        if self.mode == "blackhole":
            with self._lock:
                pairs = list(self._pairs)
            for a, b in pairs:
                for s in (a, b):
                    try:
                        s.close()
                    except OSError:
                        pass
        self._set("up")

    def _set(self, mode):
        self.mode = mode
        t = time.time_ns() // 1_000_000
        self.history.append((t, mode))
        log.warning("NETGATE %s", mode.upper())

    # -- relay -------------------------------------------------------------
    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(self.listen)
        self._srv.listen(64)
        threading.Thread(target=self._accept_loop, name="netgate-accept", daemon=True).start()
        log.info("netgate listening on %s:%d -> %s:%d", *self.listen, *self.target)
        return self

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                cli, _ = self._srv.accept()
            except OSError:
                return
            if self.mode == "refuse":
                cli.close()
                continue
            threading.Thread(target=self._serve, args=(cli,), daemon=True).start()

    def _serve(self, cli: socket.socket):
        # In blackhole mode we accept but do not connect upstream until the link is back.
        while self.mode == "blackhole" and not self._stop.is_set():
            time.sleep(0.1)
            try:
                r, _, _ = select.select([cli], [], [], 0)
                if r and not cli.recv(65536, socket.MSG_PEEK):
                    cli.close()
                    return
            except OSError:
                return
        if self.mode != "up":
            cli.close()
            return
        try:
            up = socket.create_connection(self.target, timeout=10)
        except OSError as e:
            log.warning("netgate upstream connect failed: %s", e)
            cli.close()
            return
        pair = (cli, up)
        with self._lock:
            self._pairs.add(pair)
        q = collections.deque()           # (due_time, dst, data) for the delay line
        cv = threading.Condition()
        alive = [True]

        def sender():
            while alive[0]:
                with cv:
                    while alive[0] and not q:
                        cv.wait(0.5)
                    if not alive[0]:
                        return
                    due, dst, data = q[0]
                wait = due - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                with cv:
                    q.popleft()
                try:
                    dst.sendall(data)
                except OSError:
                    return

        if self.delay_s > 0:
            threading.Thread(target=sender, daemon=True).start()
        try:
            socks = [cli, up]
            while not self._stop.is_set():
                r, _, _ = select.select(socks, [], [], 0.5)
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    if self.mode == "blackhole":
                        continue               # drop silently
                    dst = up if s is cli else cli
                    if self.delay_s > 0:
                        with cv:
                            q.append((time.monotonic() + self.delay_s, dst, data))
                            cv.notify()
                    else:
                        dst.sendall(data)
        except OSError:
            return
        finally:
            alive[0] = False
            with self._lock:
                self._pairs.discard(pair)
            for s in pair:
                try:
                    s.close()
                except OSError:
                    pass


def serve_control(relay: Relay, port: int):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/down":
                relay.down(q.get("mode", ["blackhole"])[0])
            elif u.path == "/up":
                relay.up()
            body = json.dumps({"mode": relay.mode, "connections": len(relay._pairs)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("netgate control on http://127.0.0.1:%d/{down,up,status}", port)
    return srv


def main():
    ap = argparse.ArgumentParser(description="TCP relay that can simulate a network outage")
    ap.add_argument("--listen", type=int, default=18883)
    ap.add_argument("--target", required=True, help="host:port of the real broker")
    ap.add_argument("--ctl", type=int, default=18999)
    ap.add_argument("--delay-ms", type=float, default=0.0, help="one-way added latency")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    h, p = a.target.rsplit(":", 1)
    r = Relay(a.listen, h, int(p), delay_ms=a.delay_ms).start()
    serve_control(r, a.ctl)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        r.stop()


if __name__ == "__main__":
    main()
