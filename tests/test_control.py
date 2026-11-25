"""Tests for additive per-node manual control."""
from __future__ import annotations

import json
import logging
import threading
import time
from types import SimpleNamespace

from edge.sensors import NodeModel


def test_read_control_defaults_when_file_is_missing(tmp_path):
    from edge.control import read_control

    state = read_control(tmp_path, "BID-03")

    assert state["node"] == "BID-03"
    assert state["paused"] is False
    assert state["override"] is False
    assert state["values"]["ph"] == 7.2
    assert state["values"]["turbidity_ntu"] == 4.0


def test_read_control_ignores_malformed_file(tmp_path):
    from edge.control import read_control

    (tmp_path / "BID-03.json").write_text("{not json", encoding="utf-8")

    state = read_control(tmp_path, "BID-03")

    assert state["paused"] is False
    assert state["override"] is False


def test_read_control_reads_valid_state(tmp_path):
    from edge.control import read_control

    (tmp_path / "BID-03.json").write_text(json.dumps({
        "node": "BID-03",
        "paused": True,
        "override": True,
        "values": {
            "ph": 6.1,
            "turbidity_ntu": 260,
            "tds_ppm": 780,
            "water_temp_c": 24.5,
            "flow_lpm": 2.0,
            "rssi_dbm": -95,
        },
        "updated_ms": 1234,
    }), encoding="utf-8")

    state = read_control(tmp_path, "BID-03")

    assert state["paused"] is True
    assert state["override"] is True
    assert state["values"]["ph"] == 6.1
    assert state["values"]["rssi_dbm"] == -95
    assert state["updated_ms"] == 1234


def test_read_control_rejects_non_finite_numbers(tmp_path):
    from edge.control import MANUAL_DEFAULTS, read_control

    (tmp_path / "BID-03.json").write_text(json.dumps({
        "values": {
            "ph": float("nan"),
            "turbidity_ntu": float("inf"),
            "tds_ppm": float("nan"),
            "water_temp_c": float("-inf"),
            "flow_lpm": float("inf"),
            "rssi_dbm": float("-inf"),
        },
        "updated_ms": float("inf"),
    }), encoding="utf-8")

    state = read_control(tmp_path, "BID-03")

    assert state["values"] == MANUAL_DEFAULTS
    assert state["updated_ms"] == 0


def test_node_model_manual_values_replace_physics_sample():
    model = NodeModel(node="BID-03", lat=27.1, lon=85.1, seed=7)

    model.set_manual({
        "ph": 6.1,
        "turbidity_ntu": 260,
        "tds_ppm": 780,
        "water_temp_c": 24.5,
        "flow_lpm": 2.0,
        "rssi_dbm": -95,
    })
    first = model.sample(0.0)

    assert first["ph"] == 6.1
    assert first["turbidity_ntu"] == 260.0
    assert first["tds_ppm"] == 780
    assert first["water_temp_c"] == 24.5
    assert first["flow_lpm"] == 2.0
    assert first["rssi_dbm"] == -95
    assert first["latitude"] == 27.1
    assert first["longitude"] == 85.1

    model.clear_manual()
    assert model.manual_values is None
    fresh = NodeModel(node="BID-03", lat=27.1, lon=85.1, seed=7)
    assert model.sample(0.0) == fresh.sample(0.0)


def test_manual_values_are_still_evaluated_for_breach():
    from edge.sensors import is_breach

    model = NodeModel(node="BID-03", lat=27.1, lon=85.1, seed=7)
    model.set_manual({
        "ph": 7.2,
        "turbidity_ntu": 260,
        "tds_ppm": 180,
        "water_temp_c": 20.0,
        "flow_lpm": 13.0,
        "rssi_dbm": -85,
    })

    assert is_breach(model.sample(0.0)) is True


def test_set_manual_non_finite_values_fall_back_to_node_baselines():
    model = NodeModel(
        node="BID-03", lat=27.1, lon=85.1, seed=7,
        ph0=6.8, turb0=9.0, tds0=210.0, temp_mean=22.0,
        flow0=11.0, rssi0=-92.0,
    )

    model.set_manual({
        "ph": float("nan"),
        "turbidity_ntu": float("inf"),
        "tds_ppm": float("-inf"),
        "water_temp_c": "not a number",
        "flow_lpm": float("nan"),
        "rssi_dbm": float("inf"),
    })
    sample = model.sample(0.0)

    assert sample["ph"] == 6.8
    assert sample["turbidity_ntu"] == 9.0
    assert sample["tds_ppm"] == 210
    assert sample["water_temp_c"] == 22.0
    assert sample["flow_lpm"] == 11.0
    assert sample["rssi_dbm"] == -92
    json.dumps(sample, allow_nan=False)


def test_link_set_paused_toggles_flag():
    from edge.link import Link

    link = object.__new__(Link)
    link.node = "BID-03"
    link.log = None
    link.events = None
    link._paused = False
    link._wake = threading.Event()
    link._emitted = []

    link.set_paused(True)
    assert link._paused is True

    link.set_paused(False)
    assert link._paused is False


def test_paused_link_disconnects_without_pumping_or_reading_pending_rows():
    from edge.link import Link

    link = object.__new__(Link)
    link.node = "BID-03"
    link.log = logging.getLogger("test.pause")
    link.events = None
    link.state = "ONLINE"
    link._paused = True
    link._stop = threading.Event()
    link._kick = threading.Event()
    link._wake = threading.Event()
    link._disconnected = threading.Event()
    link._connected = threading.Event()
    link.replay = True
    link.batch = 25
    link.stats = {"batches": 0, "rows_flushed": 0}

    class ForbiddenBuffer:
        def depth(self):
            return 0

        def pending(self, *_args, **_kwargs):
            raise AssertionError("paused link must not select pending rows")

    class RecordingClient:
        def __init__(self):
            self.disconnects = 0

        def disconnect(self):
            self.disconnects += 1

        def loop(self, timeout=0.0):
            raise AssertionError("paused link must not pump MQTT")

    client = RecordingClient()
    link.buf = ForbiddenBuffer()
    link.client = client

    assert link._online_loop() == "paused by manual control"
    assert client.disconnects == 1
    assert link.state == "PAUSED"


def test_paused_link_ignores_kick_without_pumping():
    from edge.link import Link

    link = object.__new__(Link)
    link.node = "BID-03"
    link.log = logging.getLogger("test.pause")
    link.events = None
    link.state = "ONLINE"
    link._paused = True
    link._stop = threading.Event()
    link._kick = threading.Event()
    link._kick.set()
    link._wake = threading.Event()
    link._disconnected = threading.Event()
    link._connected = threading.Event()

    class ForbiddenClient:
        def disconnect(self):
            pass

        def loop(self, timeout=0.0):
            raise AssertionError("kick while paused must not trigger pumping")

    class FakeBuffer:
        def depth(self):
            return 0

    link.buf = FakeBuffer()
    link.client = ForbiddenClient()

    assert link._online_loop() == "paused by manual control"
    assert not link._kick.is_set()


def test_paused_wait_clears_stale_wake_without_connecting(monkeypatch):
    from edge.link import Link

    link = object.__new__(Link)
    link.node = "BID-03"
    link.log = logging.getLogger("test.pause")
    link.events = None
    link.state = "OFFLINE"
    link._paused = True
    link._stop = threading.Event()
    link._stop.set()
    link._kick = threading.Event()
    link._wake = threading.Event()
    link._wake.set()

    class ForbiddenBuffer:
        def depth(self):
            return 0

    link.buf = ForbiddenBuffer()
    monkeypatch.setattr(link, "_connect_once",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("must not connect while paused")))

    link._wait_while_paused()

    assert link.state == "PAUSED"
    assert not link._wake.is_set()


def test_run_reconnects_only_after_pause_is_cleared(monkeypatch):
    from edge.link import Link

    link = object.__new__(Link)
    link.node = "BID-03"
    link.log = logging.getLogger("test.pause")
    link.events = None
    link.state = "OFFLINE"
    link._paused = True
    link._stop = threading.Event()
    link._kick = threading.Event()
    link._wake = threading.Event()
    link._connected = threading.Event()
    link._disconnected = threading.Event()
    link._conn_rc = None
    link.client = None
    link.online_since_ms = 0
    link.stats = {"connects": 0, "net_failures": 0}
    link.backoff = SimpleNamespace(reset=lambda: None, next=lambda: 0.01)

    class FakeBuffer:
        def depth(self):
            return 0

    link.buf = FakeBuffer()
    calls = []

    def fake_connect_once():
        if link._paused:
            raise AssertionError("run() connected while paused")
        calls.append("connect")
        link._stop.set()
        return "stopped for test"

    monkeypatch.setattr(link, "_connect_once", fake_connect_once)

    def resume():
        time.sleep(0.2)
        link.set_paused(False)

    thread = threading.Thread(target=resume)
    thread.start()
    link.run()
    thread.join()

    assert calls == ["connect"]


def test_apply_control_pauses_resumes_and_clears_override():
    from edge.control import apply_control

    class FakeLink:
        def __init__(self):
            self.paused = False

        def set_paused(self, value):
            self.paused = value

    node = SimpleNamespace(
        name="BID-03",
        model=NodeModel(node="BID-03", lat=27.1, lon=85.1, seed=7),
        link=FakeLink(),
    )
    events = []

    apply_control(node, {
        "paused": True,
        "override": False,
        "values": {},
    }, events=events.append)
    assert node.link.paused is True
    assert any(r["action"] == "pause" for r in events)

    apply_control(node, {
        "paused": False,
        "override": True,
        "values": {"ph": 6.0, "turbidity_ntu": 300, "tds_ppm": 800,
                   "water_temp_c": 25.0, "flow_lpm": 0.0, "rssi_dbm": -100},
    }, events=events.append)
    assert node.link.paused is False
    assert node.model.manual_values["ph"] == 6.0

    apply_control(node, {
        "paused": False,
        "override": False,
        "values": {},
    }, events=events.append)
    assert node.model.manual_values is None
    assert any(r["action"] == "random" for r in events)


def test_apply_control_reapplies_changed_values_while_already_overriding():
    from edge.control import apply_control

    class FakeLink:
        def set_paused(self, _value):
            pass

    node = SimpleNamespace(
        name="BID-03",
        model=NodeModel(node="BID-03", lat=27.1, lon=85.1, seed=7),
        link=FakeLink(),
    )
    first = {"ph": 6.0, "turbidity_ntu": 300, "tds_ppm": 800,
             "water_temp_c": 25.0, "flow_lpm": 0.0, "rssi_dbm": -100}
    second = dict(first, ph=5.5, turbidity_ntu=500)

    apply_control(node, {"paused": False, "override": True, "values": first})
    apply_control(node, {"paused": False, "override": True, "values": second})

    assert node.model.manual_values["ph"] == 5.5
    assert node.model.manual_values["turbidity_ntu"] == 500.0


def test_sample_loop_applies_control_before_generating_sample(monkeypatch):
    import edge.simulator as simulator

    applied = []

    class FakeModel:
        def sample(self, _t_min):
            return {
                "ph": 7.0,
                "turbidity_ntu": 4.0,
                "tds_ppm": 180,
                "water_temp_c": 20.0,
                "flow_lpm": 13.0,
                "latitude": 27.1,
                "longitude": 85.1,
                "rssi_dbm": -85,
            }

    class FakeBuffer:
        def insert(self, ts, values, breach):
            return 1

    class FakeLink:
        state = "ONLINE"

        def wake(self):
            pass

    class FakeSamples:
        def write(self, _row):
            pass

    node = object.__new__(simulator.Node)
    node.name = "BID-01"
    node.sc = SimpleNamespace(sample_period_s=0.01, duration_s=1.0,
                              proc_min=lambda _x: 0.0)
    node.t0 = time.time()
    node.phase = 0.0
    node._stop = threading.Event()
    node.model = FakeModel()
    node.buf = FakeBuffer()
    node.link = FakeLink()
    node.samples = FakeSamples()
    node.events = lambda _record: None
    node.generated = 0
    node.control_dir = None
    node.log = SimpleNamespace(info=lambda *_a, **_k: None,
                               warning=lambda *_a, **_k: None)

    monkeypatch.setattr(simulator, "read_control",
                        lambda _dir, _node: {"marker": True})
    monkeypatch.setattr(simulator, "apply_control",
                        lambda n, c, log, events: applied.append((n, c)))

    node._sample_loop()

    assert applied and applied[0][0] is node
    assert applied[0][1] == {"marker": True}
