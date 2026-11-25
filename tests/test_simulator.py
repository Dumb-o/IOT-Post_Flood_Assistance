"""Tests for scenario execution across single-node containers."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from edge.scenario import Event, Scenario
from edge.sensors import NodeModel


def test_run_scenario_skips_events_for_nodes_not_owned_by_this_process(caplog):
    from edge.simulator import run_scenario

    scenario = Scenario(
        name="demo",
        duration_s=10,
        sample_period_s=1,
        compression=60,
        start_hour=6.0,
        seed=1,
        events=[
            Event(0.0, "rain", {"node": "BID-02", "peak_ntu": 140}),
            Event(0.0, "rain", {"node": "BID-01", "peak_ntu": 650}),
        ],
    )
    nodes = {"BID-01": SimpleNamespace(model=NodeModel("BID-01", 27.9, 85.1, 1))}
    seen = []

    run_scenario(
        scenario,
        nodes,
        None,
        time.time() - 10,
        lambda payload: seen.append(payload),
        threading.Event(),
    )

    assert len(nodes["BID-01"].model.events) == 1
    assert nodes["BID-01"].model.events[0].peak_ntu == 650
    assert [event["node"] for event in seen] == ["BID-02", "BID-01"]
    assert "BID-02" in caplog.text
    assert "skip" in caplog.text.lower()
