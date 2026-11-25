"""Compute the six mandatory Task 3 metrics for one simulator run.

Sources of truth
  * edge side   : <run>/samples.csv (every generated sample), <run>/events.jsonl
                  (link transitions, flushes, outages), <run>/db/<node>.db
                  (first_send_ms, synced_ms, attempts per row)
  * server side : ThingsBoard REST (timeseries incl. rx_ms stamped by the rule
                  chain, alarms), or a broker-side receipt log for pre-validation.

Metrics (brief, "Mandatory Quantitative Results")
   1 end-to-end latency  rx_ms - ts for samples sent live, mean/median/P95/max;
                         clock-free bounds [PUBLISH - ts, PUBACK - ts]
   2 delivery ratio      unique generated timestamps present on the server / generated
   3 alarm delay         alarm createdTime - ts of the first breaching sample
   4 flush throughput    rows / (last PUBACK - first PUBLISH) for each post-outage drain
   5 recovery time       link restored -> buffer empty, with depth at restore
   6 data loss           generated - delivered after full sync, count and percentage

Examples:
    python -m tools.measure --run runs/measure --tb-url http://localhost:8080
           --tokens results/tokens/default.json --out results/measure-metrics
    python -m tools.measure --run runs/measure --sink runs/measure/sink.jsonl
           --out results/prevalidation-metrics
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import statistics as st
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CONDITIONS = {
    "High Turbidity": lambda s: s["turbidity_ntu"] > 50,
    "Gross Contamination": lambda s: s["turbidity_ntu"] > 200,
    "pH Out of Range": lambda s: s["ph"] < 6.5 or s["ph"] > 8.5,
    "High TDS": lambda s: s["tds_ppm"] > 500,
    "Supply Interruption": lambda s: s["flow_lpm"] == 0.0,
}
DURATION_S = {"Supply Interruption": 30}


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def describe(xs):
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": st.mean(xs), "median": st.median(xs),
            "p95": pct(xs, 95), "max": max(xs), "min": min(xs), "stdev": st.pstdev(xs)}


class Run:
    """One simulator run directory."""

    def __init__(self, path):
        self.path = str(path)
        self.summary = json.load(open(os.path.join(self.path, "summary.json"), encoding="utf-8"))
        with open(os.path.join(self.path, "events.jsonl"), encoding="utf-8") as fh:
            self.events = [json.loads(line) for line in fh if line.strip()]
        start = next(e for e in self.events if e["ev"] == "start")
        self.t0 = start["t_ms"]
        self.period_ms = start["sample_period_s"] * 1000
        self.end = self.summary["end_ms"]

        self.samples = defaultdict(list)
        with open(os.path.join(self.path, "samples.csv"), encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                record = {key: (float(value) if key != "node" else value)
                          for key, value in row.items()}
                record["ts_ms"] = int(record["ts_ms"])
                self.samples[record["node"]].append(record)

        self.rows = {}
        for node in self.summary["nodes"]:
            db = sqlite3.connect(os.path.join(self.path, "db", f"{node}.db"))
            try:
                self.rows[node] = {
                    row[0]: {"first_send_ms": row[1], "synced_ms": row[2],
                             "attempts": row[3], "synced": row[4]}
                    for row in db.execute(
                        "SELECT ts_ms, first_send_ms, synced_ms, attempts, synced "
                        "FROM telemetry_buffer")
                }
            finally:
                db.close()

        self.restores = [e["t_ms"] for e in self.events
                         if e["ev"] == "scenario" and e.get("type") == "restore"]
        self.outages = [(e["t_ms"], e.get("duration_s")) for e in self.events
                        if e["ev"] == "scenario" and e.get("type") == "outage"]

    def node_events(self, node, ev):
        return [e for e in self.events if e.get("node") == node and e["ev"] == ev]


class TBSource:
    """ThingsBoard REST source for received telemetry and alarms."""

    def __init__(self, url, user, password, provision=None, tokens=None, nodes=None):
        from tools.tb_api import TB

        self.name = "thingsboard"
        self.tb = TB(url, user, password)
        if not provision and not tokens:
            raise ValueError("TBSource needs --provision or --tokens to resolve device ids")
        if provision:
            self.devices = json.load(open(provision, encoding="utf-8"))["devices"]
        else:
            token_map = json.load(open(tokens, encoding="utf-8"))
            names = list(nodes or token_map)
            self.devices = {}
            for name in names:
                device = self.tb.get("/api/tenant/devices", params={"deviceName": name})
                if not device:
                    raise KeyError(f"ThingsBoard device not found: {name}")
                self.devices[name] = device["id"]["id"]

    def received(self, node, t0, t1):
        did = self.devices[node]
        data = self.tb.get(
            f"/api/plugins/telemetry/DEVICE/{did}/values/timeseries",
            params={"keys": "rx_ms,ph", "startTs": t0, "endTs": t1,
                    "limit": 200000, "agg": "NONE", "orderBy": "ASC",
                    "useStrictDataTypes": "true"})
        rx = {int(point["ts"]): float(point["value"]) for point in data.get("rx_ms", [])}
        for point in data.get("ph", []):
            rx.setdefault(int(point["ts"]), None)
        return rx

    def alarms(self, node, t0, t1):
        did = self.devices[node]
        result = []
        page = 0
        while True:
            data = self.tb.get(
                f"/api/alarm/DEVICE/{did}",
                params={"pageSize": 200, "page": page, "startTime": t0,
                        "endTime": t1, "sortProperty": "createdTime",
                        "sortOrder": "ASC", "fetchOriginator": "false"})
            result.extend(data.get("data", []))
            if not data.get("hasNext"):
                return result
            page += 1


class SinkSource:
    """Broker-side receipt log used for Mosquitto pre-validation."""

    def __init__(self, path):
        self.name = "local_mosquitto"
        self.rx = defaultdict(dict)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                self.rx[record["node"]].setdefault(
                    int(record["ts"]), float(record["rx_ms"]))

    def received(self, node, t0, t1):
        return {ts: rx for ts, rx in self.rx[node].items() if t0 <= ts <= t1}

    def alarms(self, node, t0, t1):
        return None


def compute(run: Run, src, out_dir, offset_ms=0.0):
    os.makedirs(out_dir, exist_ok=True)
    pilot = [name for name, stats in run.summary["nodes"].items()
             if stats.get("store_forward", True)]
    control = [name for name, stats in run.summary["nodes"].items()
               if not stats.get("store_forward", True)]
    metrics = {"source": src.name, "run": os.path.basename(run.path),
               "t0_ms": run.t0, "end_ms": run.end,
               "sample_period_s": run.period_ms / 1000,
               "clock_offset_ms_applied": offset_ms}

    lat_rows = []
    live_latencies = []
    publish_bounds = []
    ack_bounds = []
    aged_latencies = []
    delivery = {}
    loss = {}
    received = {}
    for node in pilot + control:
        rx = src.received(node, run.t0 - 5000, run.end + 600000)
        received[node] = rx
        generated = [sample["ts_ms"] for sample in run.samples[node]]
        delivered = [ts for ts in generated if ts in rx]
        synced = sum(1 for ts in generated
                     if run.rows[node].get(ts, {}).get("synced") == 1)
        delivery[node] = {
            "generated": len(generated), "delivered": len(delivered),
            "ratio": len(delivered) / len(generated) if generated else None,
            "edge_confirmed_synced": synced,
            "edge_synced_ratio": synced / len(generated) if generated else None,
            "store_forward": node in pilot,
        }
        loss[node] = {
            "lost": len(generated) - len(delivered),
            "lost_pct": (100.0 * (len(generated) - len(delivered)) / len(generated)
                         if generated else None),
            "evicted_unsynced": run.summary["nodes"][node].get("evicted_unsynced", 0),
            "pending_at_end": run.summary["nodes"][node].get("pending", 0),
        }
        if node not in pilot:
            continue
        for ts in generated:
            row = run.rows[node].get(ts, {})
            first_send = row.get("first_send_ms")
            synced_ms = row.get("synced_ms")
            live = (first_send is not None
                    and (first_send - ts) < min(1000, run.period_ms / 2)
                    and row.get("attempts") == 1)
            latency = rx[ts] - offset_ms - ts if rx.get(ts) is not None else None
            lat_rows.append({
                "node": node, "ts_ms": ts, "live": int(live),
                "tb_latency_ms": latency,
                "send_minus_ts_ms": first_send - ts if first_send else None,
                "ack_minus_ts_ms": synced_ms - ts if synced_ms else None,
                "attempts": row.get("attempts"),
            })
            if live:
                if latency is not None:
                    live_latencies.append(latency)
                if first_send and synced_ms:
                    publish_bounds.append(first_send - ts)
                    ack_bounds.append(synced_ms - ts)
            elif latency is not None:
                aged_latencies.append(latency)

    with open(os.path.join(out_dir, "latency.csv"), "w", newline="", encoding="utf-8") as fh:
        fieldnames = list(lat_rows[0]) if lat_rows else ["node"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(lat_rows)

    metrics["latency_ms"] = {
        "server_stamped_live": describe(live_latencies),
        "edge_publish_bound_live": describe(publish_bounds),
        "edge_puback_bound_live": describe(ack_bounds),
        "backfilled_age_at_receipt": describe(aged_latencies),
    }
    generated_total = sum(delivery[node]["generated"] for node in pilot)
    delivered_total = sum(delivery[node]["delivered"] for node in pilot)
    synced_total = sum(delivery[node]["edge_confirmed_synced"] for node in pilot)
    metrics["delivery"] = {
        "per_node": delivery,
        "pilot_ratio": delivered_total / generated_total if generated_total else None,
        "pilot_edge_synced_ratio": synced_total / generated_total if generated_total else None,
        "pilot_generated": generated_total,
        "pilot_delivered": delivered_total,
        "outage_s": sum(duration or 0 for _, duration in run.outages),
    }
    if control:
        control_generated = sum(delivery[node]["generated"] for node in control)
        control_delivered = sum(delivery[node]["delivered"] for node in control)
        metrics["delivery"]["control_ratio_no_store_forward"] = (
            control_delivered / control_generated if control_generated else None)
    metrics["data_loss"] = {
        "per_node": loss,
        "pilot_lost": generated_total - delivered_total,
        "pilot_lost_pct": (100.0 * (generated_total - delivered_total) / generated_total
                           if generated_total else None),
        "republished_rows": sum(run.summary["nodes"][node].get("republished_rows", 0)
                                for node in pilot),
    }

    recovery_rows = []
    for restore_ms in run.restores:
        for node in pilot:
            flushes = [event for event in run.node_events(node, "flush")
                       if event["t_pub"] >= restore_ms - 1000]
            online = [event for event in run.node_events(node, "link")
                      if event.get("to") == "ONLINE" and event["t_ms"] >= restore_ms]
            if not flushes or not online:
                continue
            reconnect_ms = online[0]["t_ms"]
            depth = online[0].get("depth")
            rows_flushed = 0
            first_publish = None
            last_ack = None
            drained = False
            for event in flushes:
                if event["t_pub"] < reconnect_ms:
                    continue
                first_publish = first_publish or event["t_pub"]
                rows_flushed += event["rows"]
                last_ack = event["t_ack"]
                if event["depth"] == 0:
                    drained = True
                    break
            if not drained:
                continue
            drain_s = (last_ack - first_publish) / 1000.0
            recovery_rows.append({
                "node": node, "restore_ms": restore_ms,
                "depth_at_reconnect": depth, "rows_flushed": rows_flushed,
                "reconnect_after_restore_s": (reconnect_ms - restore_ms) / 1000.0,
                "drain_s": drain_s,
                "throughput_rows_per_s": rows_flushed / drain_s if drain_s > 0 else None,
                "recovery_s_from_restore": (last_ack - restore_ms) / 1000.0,
                "recovery_s_from_reconnect": (last_ack - reconnect_ms) / 1000.0,
                "batch": run.summary["nodes"][node].get("batch"),
            })
    metrics["recovery"] = recovery_rows
    metrics["flush_throughput_rows_per_s"] = describe(
        [row["throughput_rows_per_s"] for row in recovery_rows
         if row["throughput_rows_per_s"]])

    alarm_rows = []
    for node in pilot:
        alarms = src.alarms(node, run.t0 - 60000, run.end + 600000)
        if alarms is None:
            continue
        samples = run.samples[node]
        previous_clear = defaultdict(lambda: run.t0 - 1)
        for alarm in sorted(alarms, key=lambda item: item["createdTime"]):
            alarm_type = alarm["type"]
            created = alarm["createdTime"]
            row = {"node": node, "type": alarm_type, "severity": alarm.get("severity"),
                   "created_ms": created, "start_ts": alarm.get("startTs"),
                   "clear_ts": alarm.get("clearTs") or None,
                   "status": alarm.get("status")}
            condition = CONDITIONS.get(alarm_type)
            if condition:
                candidates = [sample for sample in samples
                              if previous_clear[alarm_type] < sample["ts_ms"] <= created + 2000
                              and condition(sample)]
                if candidates:
                    breach_ts = candidates[0]["ts_ms"]
                    sample_row = run.rows[node].get(breach_ts, {})
                    first_send = sample_row.get("first_send_ms")
                    live = first_send is not None and first_send - breach_ts < 1000
                    row.update({
                        "breach_ts": breach_ts,
                        "delay_ms": created - offset_ms - breach_ts,
                        "delay_minus_duration_ms": (
                            created - offset_ms - breach_ts
                            - 1000 * DURATION_S.get(alarm_type, 0)),
                        "breach_sent_live": int(live),
                    })
            elif alarm_type == "Node Inactive":
                data_times = sorted(ts for ts, value in received[node].items() if ts < created)
                last_data = data_times[-1] if data_times else None
                row.update({"last_data_ts": last_data,
                            "detection_ms": created - offset_ms - last_data if last_data else None})
            if alarm.get("clearTs"):
                previous_clear[alarm_type] = alarm["clearTs"]
            alarm_rows.append(row)
    metrics["alarms"] = alarm_rows
    live_delays = [row["delay_minus_duration_ms"] for row in alarm_rows
                   if row.get("delay_ms") is not None and row.get("breach_sent_live")]
    buffered_delays = [row["delay_ms"] for row in alarm_rows
                       if row.get("delay_ms") is not None and not row.get("breach_sent_live")]
    metrics["alarm_delay_ms"] = {
        "live_threshold_alarms": describe(live_delays),
        "buffered_breach_alarms": describe(buffered_delays),
        "node_inactive_detection": describe(
            [row["detection_ms"] for row in alarm_rows if row.get("detection_ms") is not None]),
    }
    if alarm_rows:
        with open(os.path.join(out_dir, "alarms.csv"), "w", newline="", encoding="utf-8") as fh:
            fieldnames = sorted({key for row in alarm_rows for key in row})
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(alarm_rows)

    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)
    write_markdown(metrics, os.path.join(out_dir, "metrics.md"))
    return metrics


def fmt(value, digits=1):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_markdown(metrics, path):
    lines = [f"# Metrics ({metrics['source']}, run {metrics['run']})", ""]
    latency = metrics["latency_ms"]
    lines += ["| Latency (live samples) | n | mean | median | P95 | max |",
              "|---|---|---|---|---|---|"]
    for key, label in (("server_stamped_live", "rx_ms - ts (server stamped)"),
                       ("edge_publish_bound_live", "lower bound: PUBLISH - ts"),
                       ("edge_puback_bound_live", "upper bound: PUBACK - ts")):
        values = latency[key]
        lines.append(
            f"| {label} | {values.get('n', 0)} | {fmt(values.get('mean'))} | "
            f"{fmt(values.get('median'))} | {fmt(values.get('p95'))} | "
            f"{fmt(values.get('max'))} |")
    delivery = metrics["delivery"]
    lines += [
        "",
        f"Delivery ratio (store-and-forward nodes): "
        f"{fmt(100 * delivery['pilot_ratio'] if delivery['pilot_ratio'] is not None else None, 2)} % "
        f"present on server ({delivery['pilot_delivered']}/{delivery['pilot_generated']}); "
        f"{fmt(100 * delivery['pilot_edge_synced_ratio'] if delivery['pilot_edge_synced_ratio'] is not None else None, 2)} % "
        f"confirmed synced by PUBACK; outage {delivery['outage_s']} s",
    ]
    if "control_ratio_no_store_forward" in delivery:
        control_ratio = delivery["control_ratio_no_store_forward"]
        lines.append(f"Control node without store-and-forward: "
                     f"{fmt(100 * control_ratio if control_ratio is not None else None, 2)} %")
    loss = metrics["data_loss"]
    lines.append(f"Data loss after full sync: {loss['pilot_lost']} rows "
                 f"({fmt(loss['pilot_lost_pct'], 3)} %), "
                 f"republished rows {loss['republished_rows']}")
    lines += ["", "| Node | depth at reconnect | rows | drain s | rows/s | recovery s (from restore) |",
              "|---|---|---|---|---|---|"]
    for row in metrics["recovery"]:
        lines.append(
            f"| {row['node']} | {row['depth_at_reconnect']} | {row['rows_flushed']} | "
            f"{fmt(row['drain_s'], 2)} | {fmt(row['throughput_rows_per_s'])} | "
            f"{fmt(row['recovery_s_from_restore'], 2)} |")
    alarms = metrics["alarm_delay_ms"]
    lines += [
        "",
        f"Alarm activation delay, live threshold alarms: "
        f"n={alarms['live_threshold_alarms'].get('n', 0)} "
        f"mean={fmt(alarms['live_threshold_alarms'].get('mean'))} ms "
        f"median={fmt(alarms['live_threshold_alarms'].get('median'))} ms "
        f"max={fmt(alarms['live_threshold_alarms'].get('max'))}",
        f"Buffered-breach alarms: n={alarms['buffered_breach_alarms'].get('n', 0)} "
        f"mean={fmt(alarms['buffered_breach_alarms'].get('mean'))} ms",
        f"Node Inactive detection: n={alarms['node_inactive_detection'].get('n', 0)} "
        f"mean={fmt(alarms['node_inactive_detection'].get('mean'))} ms",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="simulator run directory")
    parser.add_argument("--out", required=True, help="metrics output directory")
    parser.add_argument("--sink", help="broker receipt log instead of ThingsBoard")
    parser.add_argument("--tb-url", default="http://localhost:8080")
    parser.add_argument("--user", default="tenant@thingsboard.org")
    parser.add_argument("--password", default="tenant")
    parser.add_argument("--provision", help="old-style provision.json with device ids")
    parser.add_argument("--tokens", help="results/tokens/default.json (device token map)")
    parser.add_argument("--clock-offset-ms", type=float, default=0.0,
                        help="server-minus-edge clock offset to subtract")
    args = parser.parse_args(argv)

    run = Run(args.run)
    if args.sink:
        source = SinkSource(args.sink)
    else:
        source = TBSource(args.tb_url, args.user, args.password,
                          provision=args.provision, tokens=args.tokens,
                          nodes=list(run.summary["nodes"]))
    metrics = compute(run, source, args.out, args.clock_offset_ms)
    print(open(os.path.join(args.out, "metrics.md"), encoding="utf-8").read())
    return metrics


if __name__ == "__main__":
    main()
