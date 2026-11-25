"""JalDrishti multi-node edge simulator.

Two threads per node share one SQLite file:
  sampler : generates a reading, commits it write-first, wakes the uplink;
  link    : MQTT/TLS state machine that drains the buffer in timestamp order.
A scenario thread fires the JSON timeline (rain, interruption, outage, bad token).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

from .buffer import Buffer, now_ms
from .control import apply_control, read_control
from .link import Link
from .nodeconfig import load_nodes
from .scenario import Event, Scenario
from .sensors import NodeModel, is_breach

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.netgate import Relay, serve_control  # noqa: E402


class EventLog:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")
        self.lock = threading.Lock()

    def __call__(self, d: dict):
        with self.lock:
            self.f.write(json.dumps(d, separators=(",", ":")) + "\n")
            self.f.flush()


class SampleLog:
    FIELDS = ["node", "seq_local", "ts_ms", "t_proc_min", "ph", "turbidity_ntu",
              "tds_ppm", "water_temp_c", "flow_lpm", "rssi_dbm", "breach",
              "depth_after_insert"]

    def __init__(self, path):
        new = not os.path.exists(path)
        self.f = open(path, "a", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.f, fieldnames=self.FIELDS, extrasaction="ignore")
        if new:
            self.w.writeheader()
        self.lock = threading.Lock()

    def write(self, row):
        with self.lock:
            self.w.writerow(row)
            self.f.flush()


def setup_logging(out_dir: str, verbose: bool):
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = logging.FileHandler(os.path.join(out_dir, "console.log"), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    root.handlers[:] = [fh, sh]
    logging.getLogger("paho").setLevel(logging.WARNING)


class Node:
    def __init__(self, spec, sc, args, out_dir, events, samples, dial, control_dir):
        self.name = spec["name"]
        self.spec = spec
        self.control_dir = control_dir
        self.sc = sc
        self.log = logging.getLogger(self.name)
        base = spec.get("baseline", {})
        self.model = NodeModel(
            node=self.name, lat=spec["lat"], lon=spec["lon"],
            seed=sc.seed * 1000 + int(spec.get("seed", 0)),
            start_hour=sc.start_hour, **base)
        self.buf = Buffer(
            os.path.join(out_dir, "db", f"{self.name}.db"),
            max_rows=int(spec.get("max_rows", args.max_rows)),
            node=self.name, log=logging.getLogger(f"{self.name}.buf"), events=events)
        self.link = Link(
            self.name, spec["token"], self.buf, args.host, args.port,
            tls=not args.no_tls, ca_file=args.ca, dial=dial,
            batch=int(spec.get("batch", args.batch)),
            backoff_min=args.backoff_min, backoff_max=args.backoff_max,
            auth_backoff_s=args.auth_backoff, keepalive=args.keepalive,
            ack_timeout=args.ack_timeout, seed=int(spec.get("seed", 0)),
            log=logging.getLogger(f"{self.name}.link"), events=events,
            replay=bool(spec.get("store_forward", True)),
            password=args.mqtt_password)
        self.events, self.samples = events, samples
        self.phase = float(spec.get("phase_s", 0.0))
        self.generated = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(
            target=self._sample_loop, name=f"sampler-{self.name}", daemon=True)

    def start(self, t0):
        self.t0 = t0
        self.link.start()
        self.thread.start()

    def stop_sampling(self):
        self._stop.set()

    def _sample_loop(self):
        period = self.sc.sample_period_s
        k = 0
        while not self._stop.is_set():
            target = self.t0 + self.phase + k * period
            delay = target - time.time()
            if delay > 0 and self._stop.wait(delay):
                break
            ts = now_ms()
            t_real = ts / 1000.0 - self.t0
            if t_real > self.sc.duration_s:
                break
            t_min = self.sc.proc_min(t_real)
            apply_control(
                self,
                read_control(self.control_dir, self.name),
                self.log,
                self.events,
            )
            v = self.model.sample(t_min)
            b = is_breach(v)
            depth = self.buf.insert(ts, v, b)   # write-first
            self.link.wake()
            self.generated += 1
            self.samples.write({
                "node": self.name, "seq_local": self.generated, "ts_ms": ts,
                "t_proc_min": round(t_min, 2), **v, "breach": int(b),
                "depth_after_insert": depth})
            self.events({"ev": "sample", "node": self.name, "t_ms": ts, "ts": ts,
                         "depth": depth, "breach": int(b),
                         "turb": v["turbidity_ntu"], "ph": v["ph"],
                         "tds": v["tds_ppm"], "flow": v["flow_lpm"]})
            self.log.info(
                "SAMPLE ts=%d pH=%.2f turb=%.1f TDS=%d T=%.1f Q=%.1f rssi=%d %s| link=%s depth=%d",
                ts, v["ph"], v["turbidity_ntu"], v["tds_ppm"], v["water_temp_c"],
                v["flow_lpm"], v["rssi_dbm"], "BREACH " if b else "",
                self.link.state, depth)
            if depth and depth % 25 == 0 and self.link.state != "ONLINE":
                self.log.warning(
                    "BUFFER depth=%d (link %s) - samples are safe on flash",
                    depth, self.link.state)
            k += 1


def run_scenario(sc, nodes, relay, t0, events, stop):
    log = logging.getLogger("scenario")
    for ev in sc.events:
        delay = t0 + ev.at - time.time()
        if delay > 0 and stop.wait(delay):
            return
        a = ev.args
        t_min = sc.proc_min(ev.at)
        events({"ev": "scenario", "type": ev.type, "at": ev.at,
                "t_ms": now_ms(), **a})
        target = a.get("node")
        if target is not None and target not in nodes:
            log.warning(
                "SKIP scenario %s at t=%ss: node %s is not owned by this process",
                ev.type, ev.at, target)
            continue
        if ev.type == "rain":
            nodes[a["node"]].model.add_rain(
                t_min, float(a["peak_ntu"]),
                float(a.get("rise_min", 10)), float(a.get("washout_min", 90)))
            log.warning("EVENT rain at %s: peak %.0f NTU", a["node"], a["peak_ntu"])
        elif ev.type == "interruption":
            nodes[a["node"]].model.add_interruption(t_min, float(a["duration_min"]))
            log.warning("EVENT supply interruption at %s", a["node"])
        elif ev.type == "outage":
            if relay is None:
                log.error("outage requested but netgate disabled (--no-gate)")
                continue
            mode = a.get("mode", "blackhole")
            log.warning("EVENT network outage (%s) for %ss", mode, a["duration_s"])
            relay.down(mode)
            threading.Timer(
                float(a["duration_s"]),
                lambda: (relay.up(),
                         log.warning("EVENT network restored"),
                         events({"ev": "scenario", "type": "restore",
                                 "t_ms": now_ms()}))).start()
        elif ev.type == "rssi":
            nodes[a["node"]].model.rssi_penalty_db = float(a["penalty_db"])
        elif ev.type == "bad_token":
            n = nodes[a["node"]]
            good = n.link.token
            n.link.token = "INVALID-" + good[:4]
            n.link.reconnect("operator: token rotated to an invalid value")
            log.warning("EVENT %s reconnects with an invalid token", a["node"])

            def restore(n=n, good=good):
                n.link.token = good
                n.link.backoff.reset()
                log.warning("EVENT %s token restored", n.name)
                n.link.reconnect("token restored by operator")
            threading.Timer(float(a["duration_s"]), restore).start()
        elif ev.type == "mark":
            log.warning("MARK %s", a.get("label", ""))


HELP = """commands (type while the simulator runs):
  rain <node> <peak_ntu> [rise_min] [washout_min]
  interrupt <node> <minutes>
  outage [seconds] [blackhole|refuse]
  restore
  badtoken <node> <seconds>
  status
  help"""


def console(sc, nodes, relay, t0, events, stop):
    print(HELP, flush=True)
    while not stop.is_set():
        line = sys.stdin.readline()
        if not line:
            return
        p = line.split()
        if not p:
            continue
        cmd, now_s = p[0].lower(), time.time() - t0
        try:
            if cmd == "rain":
                ev = Event(now_s, "rain", {
                    "node": p[1], "peak_ntu": float(p[2]),
                    "rise_min": float(p[3]) if len(p) > 3 else 10,
                    "washout_min": float(p[4]) if len(p) > 4 else 70})
            elif cmd == "interrupt":
                ev = Event(now_s, "interruption",
                           {"node": p[1], "duration_min": float(p[2])})
            elif cmd == "outage":
                ev = Event(now_s, "outage", {
                    "duration_s": float(p[1]) if len(p) > 1 else 60,
                    "mode": p[2] if len(p) > 2 else "blackhole"})
            elif cmd == "restore":
                if relay:
                    relay.up()
                events({"ev": "scenario", "type": "restore", "t_ms": now_ms()})
                continue
            elif cmd == "badtoken":
                ev = Event(now_s, "bad_token",
                           {"node": p[1], "duration_s": float(p[2])})
            elif cmd == "status":
                for n in nodes.values():
                    logging.getLogger("console").info(
                        "STATUS %s link=%s pending=%d on_disk=%d generated=%d",
                        n.name, n.link.state, n.buf.depth(), n.buf.total(),
                        n.generated)
                continue
            else:
                print(HELP, flush=True)
                continue
        except (IndexError, ValueError, KeyError):
            print(HELP, flush=True)
            continue
        logging.getLogger("console").warning("operator command: %s", line.strip())
        run_scenario(
            Scenario(sc.name, sc.duration_s, sc.sample_period_s, sc.compression,
                     sc.start_hour, sc.seed, [ev]),
            nodes, relay, t0, events, stop)


def main(argv=None):
    ap = argparse.ArgumentParser(description="JalDrishti edge simulator")
    ap.add_argument("--nodes", default=os.path.join(ROOT, "config"),
                    help="config directory (nodes.json + nodes/*.json)")
    ap.add_argument("--tokens", help="JSON {node: token} from tools/tb_provision.py")
    ap.add_argument("--scenario", default=os.path.join(ROOT, "config", "scenarios", "demo.json"))
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--ca", help="CA bundle for the broker certificate")
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--mqtt-password")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--max-rows", type=int, default=20000)
    ap.add_argument("--backoff-min", type=float, default=1.0)
    ap.add_argument("--backoff-max", type=float, default=30.0)
    ap.add_argument("--auth-backoff", type=float, default=None)
    ap.add_argument("--keepalive", type=int, default=10)
    ap.add_argument("--ack-timeout", type=float, default=8.0)
    ap.add_argument("--no-gate", action="store_true")
    ap.add_argument("--gate-port", type=int, default=18883)
    ap.add_argument("--gate-ctl", type=int, default=18999)
    ap.add_argument("--only", help="comma-separated node names to run")
    ap.add_argument("--with-control", action="store_true")
    ap.add_argument("--control-dir",
                    default=os.path.join(ROOT, "runs", "control"),
                    help="directory containing runtime-only <NODE>.json control files")
    ap.add_argument("--out")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--console", action="store_true")
    a = ap.parse_args(argv)

    sc = Scenario.load(a.scenario)
    out = a.out or os.path.join(ROOT, "runs", f"{sc.name}-{datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(os.path.join(out, "db"), exist_ok=True)
    setup_logging(out, a.verbose)
    log = logging.getLogger("sim")

    # Hybrid config: directory -> loader; legacy single file still accepted.
    if os.path.isdir(a.nodes):
        spec = load_nodes(a.nodes)
    else:
        with open(a.nodes, encoding="utf-8") as fh:
            spec = json.load(fh)

    tokens = json.load(open(a.tokens, encoding="utf-8")) if a.tokens else {}
    pool = spec["nodes"] + (spec.get("control_nodes", []) if a.with_control else [])
    node_specs = [n for n in pool if not a.only or n["name"] in a.only.split(",")]
    for n in node_specs:
        n["token"] = tokens.get(n["name"], n.get("token", ""))
        if not n["token"]:
            ap.error(f"no access token for {n['name']} (run tools/tb_provision.py)")

    relay = None
    dial = None
    if not a.no_gate:
        relay = Relay(a.gate_port, a.host, a.port).start()
        serve_control(relay, a.gate_ctl)
        dial = ("127.0.0.1", a.gate_port)

    events = EventLog(os.path.join(out, "events.jsonl"))
    samples = SampleLog(os.path.join(out, "samples.csv"))
    nodes = {n["name"]: Node(n, sc, a, out, events, samples, dial, a.control_dir)
             for n in node_specs}
    for n in nodes.values():
        log.info(
            "node %s at (%.5f, %.5f) buffer=%s journal=%s max_rows=%d batch=%d store_forward=%s",
            n.name, n.model.lat, n.model.lon, os.path.relpath(n.buf.path, out),
            n.buf.journal_mode(), n.buf.max_rows, n.link.batch, n.link.replay)

    t0 = time.time()
    events({"ev": "start", "t_ms": int(t0 * 1000), "scenario": sc.name,
            "compression": sc.compression, "sample_period_s": sc.sample_period_s,
            "duration_s": sc.duration_s, "batch": a.batch,
            "host": a.host, "port": a.port})
    stop = threading.Event()
    for n in nodes.values():
        n.start(t0)
    threading.Thread(
        target=run_scenario, args=(sc, nodes, relay, t0, events, stop),
        daemon=True).start()
    if a.console:
        threading.Thread(
            target=console, args=(sc, nodes, relay, t0, events, stop),
            daemon=True).start()
    try:
        while time.time() - t0 < sc.duration_s:
            time.sleep(0.5)
        for n in nodes.values():
            n.stop_sampling()
        log.info("sampling finished; draining buffers (up to %.0f s)", sc.settle_s)
        if relay and relay.mode != "up":
            relay.up()
        t_end = time.time() + sc.settle_s
        while (time.time() < t_end
               and any(n.buf.depth() > 0 for n in nodes.values() if n.link.replay)):
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.warning("interrupted - buffers are intact on disk")
    finally:
        stop.set()
        for n in nodes.values():
            n.stop_sampling()
            n.link.stop()
        for n in nodes.values():
            n.link.join(timeout=5)

    summary = {"scenario": sc.name, "t0_ms": int(t0 * 1000), "end_ms": now_ms(),
               "out": out, "gate_history": relay.history if relay else [],
               "nodes": {}}
    for n in nodes.values():
        summary["nodes"][n.name] = {
            "generated": n.generated, "pending": n.buf.depth(),
            "rows_on_disk": n.buf.total(), "evicted_unsynced": n.buf.evicted_total,
            "store_forward": n.link.replay, "batch": n.link.batch, **n.link.stats}
        log.info("summary %s: %s", n.name,
                 json.dumps(summary["nodes"][n.name]))
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    events({"ev": "end", "t_ms": now_ms()})
    if relay:
        relay.stop()
    print(out)
    return out


if __name__ == "__main__":
    main()