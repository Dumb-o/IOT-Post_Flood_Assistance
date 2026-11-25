"""Tests for the mandatory ThingsBoard measurement computation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


class FakeSource:
    name = "fake"

    def __init__(self, received=None, alarms=None):
        self.received_data = received or {}
        self.alarm_data = alarms or {}

    def received(self, node, t0, t1):
        return {ts: rx for ts, rx in self.received_data.get(node, {}).items()
                if t0 <= ts <= t1}

    def alarms(self, node, t0, t1):
        return self.alarm_data.get(node, [])


def _write_run(run_dir: Path, *, nodes: dict, samples: dict, rows: dict,
               events: list[dict] | None = None):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "db").mkdir(exist_ok=True)
    json.dump({"nodes": nodes, "end_ms": 5000}, (run_dir / "summary.json").open("w"))

    event_list = [{"ev": "start", "t_ms": 0, "sample_period_s": 1.0}]
    event_list.extend(events or [])
    with (run_dir / "events.jsonl").open("w") as fh:
        for event in event_list:
            fh.write(json.dumps(event) + "\n")

    fields = ["node", "ts_ms", "ph", "turbidity_ntu", "tds_ppm",
              "water_temp_c", "flow_lpm", "rssi_dbm", "breach"]
    with (run_dir / "samples.csv").open("w") as fh:
        fh.write(",".join(fields) + "\n")
        for node, samples_for_node in samples.items():
            for sample in samples_for_node:
                fh.write(",".join(str(sample.get(key, "")) for key in fields) + "\n")

    for node, rows_for_node in rows.items():
        db = sqlite3.connect(run_dir / "db" / f"{node}.db")
        db.execute("CREATE TABLE telemetry_buffer (ts_ms INTEGER PRIMARY KEY, "
                   "first_send_ms INTEGER, synced_ms INTEGER, attempts INTEGER, synced INTEGER)")
        db.executemany("INSERT INTO telemetry_buffer VALUES (?,?,?,?,?)", rows_for_node)
        db.commit()
        db.close()


def test_compute_reports_live_latency_delivery_and_loss(tmp_path):
    from tools.measure import Run, compute

    run_dir = tmp_path / "run"
    _write_run(
        run_dir,
        nodes={"BID-01": {"store_forward": True, "pending": 0,
                          "evicted_unsynced": 0, "republished_rows": 0, "batch": 25}},
        samples={"BID-01": [{"node": "BID-01", "ts_ms": 1000, "ph": 7.1,
                             "turbidity_ntu": 3.0, "tds_ppm": 180,
                             "water_temp_c": 20.0, "flow_lpm": 12.0,
                             "rssi_dbm": -85, "breach": 0}]},
        rows={"BID-01": [(1000, 1004, 1008, 1, 1)]},
    )
    src = FakeSource(received={"BID-01": {1000: 1010}})

    metrics = compute(Run(run_dir), src, tmp_path / "metrics")

    assert metrics["latency_ms"]["server_stamped_live"]["n"] == 1
    assert metrics["latency_ms"]["server_stamped_live"]["mean"] == 10
    assert metrics["delivery"]["pilot_ratio"] == 1.0
    assert metrics["data_loss"]["pilot_lost"] == 0


def test_compute_keeps_control_node_loss_out_of_pilot_delivery(tmp_path):
    from tools.measure import Run, compute

    run_dir = tmp_path / "run"
    nodes = {
        "BID-01": {"store_forward": True, "pending": 0, "evicted_unsynced": 0,
                   "republished_rows": 0, "batch": 25},
        "JD-CTRL-01": {"store_forward": False, "pending": 1, "evicted_unsynced": 0,
                       "republished_rows": 0, "batch": 25},
    }
    samples = {
        "BID-01": [{"node": "BID-01", "ts_ms": 1000, "ph": 7.1,
                    "turbidity_ntu": 3.0, "tds_ppm": 180, "water_temp_c": 20.0,
                    "flow_lpm": 12.0, "rssi_dbm": -85, "breach": 0}],
        "JD-CTRL-01": [{"node": "JD-CTRL-01", "ts_ms": 1000, "ph": 7.1,
                        "turbidity_ntu": 3.0, "tds_ppm": 180, "water_temp_c": 20.0,
                        "flow_lpm": 12.0, "rssi_dbm": -85, "breach": 0}],
    }
    rows = {
        "BID-01": [(1000, 1004, 1008, 1, 1)],
        "JD-CTRL-01": [(1000, None, None, 0, 0)],
    }
    _write_run(run_dir, nodes=nodes, samples=samples, rows=rows)
    src = FakeSource(received={"BID-01": {1000: 1010}})

    metrics = compute(Run(run_dir), src, tmp_path / "metrics")

    assert metrics["delivery"]["pilot_ratio"] == 1.0
    assert metrics["delivery"]["control_ratio_no_store_forward"] == 0.0
    assert metrics["data_loss"]["pilot_lost"] == 0
    assert metrics["data_loss"]["per_node"]["JD-CTRL-01"]["lost"] == 1


def test_compute_reports_alarm_delay_and_recovery_rows(tmp_path):
    from tools.measure import Run, compute

    run_dir = tmp_path / "run"
    samples = {"BID-01": [{"node": "BID-01", "ts_ms": 2000, "ph": 7.0,
                           "turbidity_ntu": 600.0, "tds_ppm": 700,
                           "water_temp_c": 20.0, "flow_lpm": 12.0,
                           "rssi_dbm": -85, "breach": 1}]}
    events = [
        {"ev": "scenario", "type": "restore", "at": 3000, "t_ms": 3000},
        {"ev": "link", "node": "BID-01", "from": "BACKOFF", "to": "ONLINE",
         "depth": 2, "t_ms": 3050},
        {"ev": "flush", "node": "BID-01", "rows": 2, "t_pub": 3060,
         "t_ack": 3070, "depth": 0},
    ]
    _write_run(
        run_dir,
        nodes={"BID-01": {"store_forward": True, "pending": 0,
                          "evicted_unsynced": 0, "republished_rows": 0, "batch": 25}},
        samples=samples,
        rows={"BID-01": [(2000, 2002, 2005, 1, 1)]},
        events=events,
    )
    src = FakeSource(
        received={"BID-01": {2000: 2010}},
        alarms={"BID-01": [{"type": "Gross Contamination", "severity": "CRITICAL",
                            "createdTime": 2150, "startTs": 2000, "clearTs": None,
                            "status": "ACTIVE_UNACK"}]},
    )

    metrics = compute(Run(run_dir), src, tmp_path / "metrics")

    assert metrics["alarm_delay_ms"]["live_threshold_alarms"]["mean"] == 150
    assert len(metrics["recovery"]) == 1
    assert metrics["recovery"][0]["rows_flushed"] == 2
    assert metrics["recovery"][0]["recovery_s_from_restore"] == pytest.approx(0.07)
    assert metrics["recovery"][0]["throughput_rows_per_s"] == 200
