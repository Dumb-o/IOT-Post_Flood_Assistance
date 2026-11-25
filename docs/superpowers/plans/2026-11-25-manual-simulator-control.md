# Manual Simulator Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional 2 × 2 browser control window at `http://localhost:8766` that lets the operator pause MQTT publishing per node, choose exact telemetry values with sliders, resume publishing with those values, and resume the existing random model, without changing current behavior when no control file exists.

**Architecture:** Runtime JSON files under `runs/control/<NODE>.json` are written by a host-side standard-library HTTP tool and polled by each simulator node immediately before it samples. A new `edge/control.py` applies only state changes; `Link` gets a per-node publisher pause flag; `NodeModel` gets an optional manual-value override. No Docker socket, ThingsBoard API, tenant, dashboard, token, webhook, or Compose changes are involved.

**Tech Stack:** Python 3.13, standard library (`http.server`, `sqlite3`, `json`, `tempfile`), existing `paho-mqtt` link, existing pytest suite. Current baseline: **19 passed**.

**Repository note:** This project is not a Git repository. Use the listed "Checkpoint" commands instead of `git commit` steps.

---

## File Structure

- Create `edge/control.py`: reads/normalizes control files and applies transitions to a node.
- Modify `edge/link.py`: add `_paused` flag and `set_paused()`; skip pending-row selection while paused.
- Modify `edge/sensors.py`: add `manual_values`, `set_manual()`, `clear_manual()`, and manual branch in `sample()`.
- Modify `edge/simulator.py`: add `--control-dir`, pass it into `Node`, and call control polling in `_sample_loop()`.
- Create `tools/control_ui.py`: host-side browser UI on port 8766.
- Create `tests/test_control.py`: control file, model, link pause, apply transitions, and simulator polling tests.
- Create `tests/test_control_ui.py`: form parsing, atomic writes, and page rendering tests.
- Modify `ROADMAP.md`: add prominent local ports table (8765 + 8766) and Milestone 10.
- Do **not** modify `Dockerfile`, `docker-compose.yaml`, `thingsboard/`, `results/`, or runtime `runs/`.

---

## Task 1: Add control-file reader and state applier

**Files:**
- Create: `edge/control.py`
- Test: `tests/test_control.py`

- [ ] **Step 1: Write failing tests for control file normalization**

Create `tests/test_control.py` with:

```python
"""Tests for additive per-node manual control."""
from __future__ import annotations

import json
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'edge.control'`.

- [ ] **Step 3: Write `edge/control.py`**

Create the file with exactly:

```python
"""Read-only control-file bridge for the additive simulator control UI."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

MANUAL_KEYS = (
    "ph",
    "turbidity_ntu",
    "tds_ppm",
    "water_temp_c",
    "flow_lpm",
    "rssi_dbm",
)

MANUAL_DEFAULTS = {
    "ph": 7.2,
    "turbidity_ntu": 4.0,
    "tds_ppm": 180,
    "water_temp_c": 20.0,
    "flow_lpm": 13.0,
    "rssi_dbm": -85,
}

_NODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_control(node: str) -> dict[str, Any]:
    """Return the exact state used when no valid control file exists."""
    return {
        "node": node,
        "paused": False,
        "override": False,
        "values": dict(MANUAL_DEFAULTS),
        "updated_ms": 0,
    }


def read_control(control_dir: str | Path, node: str) -> dict[str, Any]:
    """Read ``<control_dir>/<node>.json``; invalid input means no control."""
    default = default_control(node)
    if not _NODE_RE.fullmatch(str(node)):
        return default

    path = Path(control_dir) / f"{node}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default
    if not isinstance(raw, dict):
        return default

    raw_values = raw.get("values")
    if not isinstance(raw_values, dict):
        raw_values = {}
    values: dict[str, Any] = {}
    for key in MANUAL_KEYS:
        try:
            number = float(raw_values.get(key, MANUAL_DEFAULTS[key]))
        except (TypeError, ValueError):
            number = float(MANUAL_DEFAULTS[key])
        if key in {"tds_ppm", "rssi_dbm"}:
            values[key] = int(round(number))
        else:
            values[key] = number

    try:
        updated_ms = int(raw.get("updated_ms", 0) or 0)
    except (TypeError, ValueError):
        updated_ms = 0

    return {
        "node": node,
        "paused": raw.get("paused") is True,
        "override": raw.get("override") is True,
        "values": values,
        "updated_ms": updated_ms,
    }


def _same_values(left: dict[str, Any] | None, right: dict[str, Any]) -> bool:
    if not left:
        return False
    return all(left.get(key) == right.get(key) for key in MANUAL_KEYS)


def _record(log, events: Callable[[dict], None] | None, action: str,
            node: Any, **extra) -> None:
    name = getattr(node, "name", "?")
    record = {"ev": "control", "action": action, "node": name,
              "t_ms": int(time.time() * 1000), **extra}
    if log is not None:
        log.warning("CONTROL %s node=%s %s", action, name, extra)
    if events is not None:
        events(record)


def apply_control(node, control: dict[str, Any], log=None,
                  events: Callable[[dict], None] | None = None) -> None:
    """Apply only changed control state to ``node``; no-op for unchanged state."""
    previous = getattr(node, "_control_state", None) or {
        "paused": False,
        "override": False,
        "values": None,
    }
    target_paused = control.get("paused") is True
    target_override = control.get("override") is True
    target_values = dict(control.get("values") or {})
    old_paused = previous.get("paused") is True
    old_override = previous.get("override") is True

    if target_override and (
        not old_override or not _same_values(previous.get("values"), target_values)
    ):
        node.model.set_manual(target_values)
        _record(log, events, "override", node, values=target_values)
    elif not target_override and old_override:
        node.model.clear_manual()
        _record(log, events, "random", node)

    if target_paused != old_paused:
        node.link.set_paused(target_paused)
        _record(log, events, "pause" if target_paused else "resume", node,
                paused=target_paused)

    node._control_state = {
        "paused": target_paused,
        "override": target_override,
        "values": target_values if target_override else None,
    }
```

- [ ] **Step 4: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `22 passed`.

---

## Task 2: Add manual values to the sensor model

**Files:**
- Modify: `edge/sensors.py:46-113`
- Test: `tests/test_control.py`

- [ ] **Step 1: Append the failing model tests to `tests/test_control.py`**

```python
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
    second = model.sample(0.0)
    assert second != first


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: FAIL with `AttributeError: 'NodeModel' object has no attribute 'set_manual'`.

- [ ] **Step 3: Modify `edge/sensors.py`**

In the `NodeModel` dataclass, add this field after `rssi_penalty_db: float = 0.0`:

```python
    manual_values: dict | None = None
```

Add these methods after `add_interruption()`:

```python
    def set_manual(self, values: dict) -> None:
        """Force every future sample to these operator-chosen values."""
        self.manual_values = {
            "ph": round(float(values.get("ph", self.ph0)), 2),
            "turbidity_ntu": round(float(values.get("turbidity_ntu", self.turb0)), 1),
            "tds_ppm": int(round(float(values.get("tds_ppm", self.tds0)))),
            "water_temp_c": round(float(values.get("water_temp_c", self.temp_mean)), 1),
            "flow_lpm": round(float(values.get("flow_lpm", self.flow0)), 1),
            "rssi_dbm": int(round(float(values.get("rssi_dbm", self.rssi0)))),
        }

    def clear_manual(self) -> None:
        """Return to the existing random physics model."""
        self.manual_values = None
```

At the top of `sample(t_min: float)`, insert before `g = self.rng.gauss`:

```python
        if self.manual_values is not None:
            return {
                **self.manual_values,
                "latitude": self.lat,
                "longitude": self.lon,
            }
```

- [ ] **Step 4: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `24 passed`.

---

## Task 3: Add per-node publisher pause to `Link`

**Files:**
- Modify: `edge/link.py:63-85`, `edge/link.py:237-254`
- Test: `tests/test_control.py`

- [ ] **Step 1: Append the failing link-pause tests to `tests/test_control.py`**

```python
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


def test_paused_link_never_reads_or_publishes_pending_rows():
    from edge.link import Link, mqtt

    link = object.__new__(Link)
    link.node = "BID-03"
    link.log = None
    link.events = None
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
        def pending(self, *_args, **_kwargs):
            raise AssertionError("paused link must not select pending rows")

    class StoppingClient:
        def loop(self, timeout=0.0):
            link._stop.set()
            return mqtt.MQTT_ERR_SUCCESS

    link.buf = ForbiddenBuffer()
    link.client = StoppingClient()

    assert link._online_loop() == "stopped"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: FAIL with `AttributeError: 'Link' object has no attribute 'set_paused'`.

- [ ] **Step 3: Modify `edge/link.py`**

In `Link.__init__()`, add after `self._kick = threading.Event()`:

```python
        self._paused = False
```

Add this method after `wake()`:

```python
    def set_paused(self, paused: bool) -> None:
        """Pause only this node's publisher; sampling continues into SQLite."""
        new = bool(paused)
        if new == self._paused:
            return
        self._paused = new
        if self.log is not None:
            self.log.warning("LINK publisher %s by manual control",
                             "PAUSED" if new else "RESUMED")
        self._wake.set()
```

In `_online_loop()`, insert this block immediately after `while not self._stop.is_set():`:

```python
            if self._paused:
                if not self._pump(0.05):
                    return "connection lost while paused"
                self._wake.wait(0.1)
                continue
```

- [ ] **Step 4: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: `7 passed`.

- [ ] **Step 5: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `26 passed`.

---

## Task 4: Test and wire `apply_control()`

**Files:**
- Modify: `tests/test_control.py`
- Modify: `edge/control.py` only if a test exposes a mismatch

- [ ] **Step 1: Append transition tests to `tests/test_control.py`**

```python
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
```

- [ ] **Step 2: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: `9 passed`. If fewer pass, fix `edge/control.py` to match the exact behavior above, then rerun.

- [ ] **Step 3: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `28 passed`.

---

## Task 5: Poll control files from the simulator loop

**Files:**
- Modify: `edge/simulator.py:20-28`, `edge/simulator.py:116-151`, `edge/simulator.py:269-324`
- Test: `tests/test_control.py`

- [ ] **Step 1: Append the failing simulator integration test to `tests/test_control.py`**

```python
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

    monkeypatch.setattr(simulator, "read_control",
                        lambda _dir, _node: {"marker": True})
    monkeypatch.setattr(simulator, "apply_control",
                        lambda n, c, log, events: applied.append((n, c)))

    node._sample_loop()

    assert applied and applied[0][0] is node
    assert applied[0][1] == {"marker": True}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py::test_sample_loop_applies_control_before_generating_sample -q
```

Expected: FAIL because `edge.simulator` has no `read_control` attribute.

- [ ] **Step 3: Modify imports in `edge/simulator.py`**

Add after `from .buffer import Buffer, now_ms`:

```python
from .control import apply_control, read_control
```

- [ ] **Step 4: Modify `Node.__init__()` in `edge/simulator.py`**

Change the signature from:

```python
    def __init__(self, spec, sc, args, out_dir, events, samples, dial):
```

to:

```python
    def __init__(self, spec, sc, args, out_dir, events, samples, dial, control_dir):
```

Add after `self.spec = spec`:

```python
        self.control_dir = control_dir
```

- [ ] **Step 5: Modify `Node._sample_loop()` in `edge/simulator.py`**

In the loop, change this section:

```python
            t_min = self.sc.proc_min(t_real)
            v = self.model.sample(t_min)
```

to:

```python
            t_min = self.sc.proc_min(t_real)
            apply_control(
                self,
                read_control(self.control_dir, self.name),
                self.log,
                self.events,
            )
            v = self.model.sample(t_min)
```

- [ ] **Step 6: Add the CLI flag in `edge/simulator.py`**

Add after `ap.add_argument("--with-control", action="store_true")`:

```python
    ap.add_argument("--control-dir",
                    default=os.path.join(ROOT, "runs", "control"),
                    help="directory containing runtime-only <NODE>.json control files")
```

Change this line:

```python
    nodes = {n["name"]: Node(n, sc, a, out, events, samples, dial)
             for n in node_specs}
```

to:

```python
    nodes = {n["name"]: Node(n, sc, a, out, events, samples, dial, a.control_dir)
             for n in node_specs}
```

- [ ] **Step 7: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control.py -q
```

Expected: `10 passed`.

- [ ] **Step 8: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `29 passed`.

---

## Task 6: Build the host-side control UI on port 8766

**Files:**
- Create: `tools/control_ui.py`
- Test: `tests/test_control_ui.py`

- [ ] **Step 1: Write failing tests for form parsing and atomic writes**

Create `tests/test_control_ui.py` with:

```python
"""Tests for the additive browser control UI."""
from __future__ import annotations

import json
import stat


def test_parse_values_clamps_to_accepted_slider_ranges():
    from tools.control_ui import parse_values

    values = parse_values({
        "ph": ["99"],
        "turbidity_ntu": ["-5"],
        "tds_ppm": ["9999"],
        "water_temp_c": ["100"],
        "flow_lpm": ["-1"],
        "rssi_dbm": ["20"],
    })

    assert values == {
        "ph": 14.0,
        "turbidity_ntu": 0.0,
        "tds_ppm": 2000,
        "water_temp_c": 40.0,
        "flow_lpm": 0.0,
        "rssi_dbm": 0,
    }


def test_write_control_state_is_atomic_and_world_readable(tmp_path):
    from tools.control_ui import write_control_state

    values = {
        "ph": 6.1,
        "turbidity_ntu": 260.0,
        "tds_ppm": 780,
        "water_temp_c": 24.5,
        "flow_lpm": 2.0,
        "rssi_dbm": -95,
    }

    state = write_control_state(tmp_path, "BID-03", "override", values)

    path = tmp_path / "BID-03.json"
    assert json.loads(path.read_text(encoding="utf-8")) == state
    assert stat.S_IMODE(path.stat().st_mode) & 0o044 == 0o044
    assert list(tmp_path.glob("*.tmp")) == []


def test_apply_form_pause_keeps_override_state(tmp_path):
    from tools.control_ui import apply_form

    values = {
        "ph": "6.1",
        "turbidity_ntu": "260",
        "tds_ppm": "780",
        "water_temp_c": "24.5",
        "flow_lpm": "2",
        "rssi_dbm": "-95",
    }
    form = {"node": ["BID-03"], "action": ["override"], **{k: [v] for k, v in values.items()}}
    apply_form(tmp_path, form)

    form["action"] = ["pause"]
    state = apply_form(tmp_path, form)

    assert state["paused"] is True
    assert state["override"] is True
    assert state["values"]["ph"] == 6.1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_control_ui.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tools.control_ui'`.

- [ ] **Step 3: Write `tools/control_ui.py`**

Create the file with exactly:

```python
"""Optional browser control window for the additive simulator controls.

Run from the project root::

    .venv/bin/python -m tools.control_ui --db-dir runs --port 8766

Open http://localhost:8766. This tool writes only ``runs/control/<NODE>.json``.
It never touches Docker, ThingsBoard, certificates, tokens or SQLite.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from edge.control import MANUAL_DEFAULTS, MANUAL_KEYS, read_control
from tools.buffer_view import snapshot

NODES = ("BID-01", "BID-02", "BID-03", "BID-04")
_NODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ACTIONS = ("pause", "override", "random")

# key: (minimum, maximum, step, label)
SLIDERS = {
    "ph": (0.0, 14.0, 0.01, "pH"),
    "turbidity_ntu": (0.0, 1000.0, 1.0, "Turbidity (NTU)"),
    "tds_ppm": (0.0, 2000.0, 1.0, "TDS (ppm)"),
    "water_temp_c": (0.0, 40.0, 0.1, "Water temp (°C)"),
    "flow_lpm": (0.0, 40.0, 0.1, "Flow (L/min)"),
    "rssi_dbm": (-120.0, 0.0, 1.0, "RSSI (dBm)"),
}


def parse_values(form: dict[str, list[str]]) -> dict[str, float | int]:
    """Parse and clamp the six slider values; invalid input uses defaults."""
    result: dict[str, float | int] = {}
    for key, (low, high, _step, _label) in SLIDERS.items():
        supplied = form.get(key)
        raw = supplied[0] if supplied else MANUAL_DEFAULTS[key]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(MANUAL_DEFAULTS[key])
        value = max(low, min(high, value))
        if key in {"tds_ppm", "rssi_dbm"}:
            result[key] = int(round(value))
        else:
            result[key] = value
    return result


def _existing(control_dir: str | Path, node: str) -> dict:
    path = Path(control_dir) / f"{node}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_control_state(control_dir: str | Path, node: str, action: str,
                        values: dict[str, float | int],
                        updated_ms: int | None = None) -> dict:
    """Atomically write one node's control state with container-readable mode."""
    if action not in _ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if not _NODE_RE.fullmatch(str(node)):
        raise ValueError(f"unsafe node name: {node}")

    old = _existing(control_dir, node)
    if action == "pause":
        paused, override = True, old.get("override") is True
    elif action == "override":
        paused, override = False, True
    else:
        paused, override = False, False

    state = {
        "node": node,
        "paused": paused,
        "override": override,
        "values": {key: values[key] for key in MANUAL_KEYS},
        "updated_ms": int(updated_ms if updated_ms is not None
                          else time.time_ns() // 1_000_000),
    }

    directory = Path(control_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{node}.json"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{node}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return state


def apply_form(control_dir: str | Path, form: dict[str, list[str]]) -> dict:
    """Validate a submitted form and write the corresponding control file."""
    node = (form.get("node") or [""])[0]
    action = (form.get("action") or [""])[0]
    if node not in NODES:
        raise ValueError(f"unknown node: {node}")
    return write_control_state(control_dir, node, action, parse_values(form))


def _slider_html(key: str, value: float | int) -> str:
    low, high, step, label = SLIDERS[key]
    return (
        f'<label><span>{html.escape(label)}</span>'
        f'<input type="range" name="{key}" min="{low:g}" max="{high:g}" '
        f'step="{step:g}" value="{value:g}"></label>'
    )


def _panel_html(node: str, db_dir: str | Path, control_dir: str | Path) -> str:
    db_path = Path(db_dir) / f"{node}-compose" / "db" / f"{node}.db"
    snap = snapshot(db_path, rows=0) if db_path.is_file() else None
    state = read_control(control_dir, node)
    pending = snap["pending"] if snap else 0
    link = snap["link"].get("state", "-") if snap else "-"
    mode = "PAUSED" if state["paused"] else "OVERRIDE" if state["override"] else "RANDOM"
    sliders = "\n".join(_slider_html(k, state["values"][k]) for k in MANUAL_KEYS)

    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>{html.escape(node)}</h2>
        <span class="mode mode-{mode.lower()}">{mode}</span>
      </div>
      <div class="facts">
        <span>MQTT: <b>{html.escape(str(link))}</b></span>
        <span>Pending: <b>{pending}</b></span>
      </div>
      <form method="post" action="/control">
        <input type="hidden" name="node" value="{html.escape(node)}">
        {sliders}
        <div class="buttons">
          <button name="action" value="pause">Pause node</button>
          <button name="action" value="override">Override &amp; resume</button>
          <button name="action" value="random">Resume random</button>
        </div>
      </form>
    </section>"""


def render_page(db_dir: str | Path, control_dir: str | Path) -> str:
    """Render the 2 × 2 control grid."""
    panels = "\n".join(_panel_html(node, db_dir, control_dir) for node in NODES)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>JalDrishti simulator control</title>
  <style>
    body {{ font: 14px/1.4 system-ui, sans-serif; margin: 1.25rem; color: #17324d; background: #f4f7fa; }}
    h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
    .hint {{ color: #557; margin-top: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 1rem; }}
    .panel {{ background: #fff; border: 1px solid #d5e0ea; border-radius: 8px; padding: 1rem; }}
    .panel-head {{ display: flex; align-items: center; justify-content: space-between; }}
    .panel h2 {{ margin: 0; font-size: 1.1rem; }}
    .mode {{ font-size: .72rem; font-weight: 700; padding: .2rem .45rem; border-radius: 999px; }}
    .mode-paused {{ background: #ffebee; color: #b71c1c; }}
    .mode-override {{ background: #fff3e0; color: #e65100; }}
    .mode-random {{ background: #e8f5e9; color: #1b5e20; }}
    .facts {{ display: flex; gap: 1rem; color: #557; margin: .4rem 0 .7rem; }}
    label {{ display: grid; grid-template-columns: 130px 1fr 55px; align-items: center; gap: .4rem; margin: .3rem 0; }}
    input[type=range] {{ width: 100%; }}
    .buttons {{ display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .8rem; }}
    button {{ border: 1px solid #0d47a1; background: #0d47a1; color: white; border-radius: 5px; padding: .45rem .7rem; cursor: pointer; }}
    button[value=pause] {{ background: #b71c1c; border-color: #b71c1c; }}
    button[value=random] {{ background: #1b5e20; border-color: #1b5e20; }}
    @media (max-width: 760px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>JalDrishti simulator control</h1>
  <p class="hint">Port 8766. Move sliders, press <b>Override &amp; resume</b> to send your values,
     or <b>Pause node</b> to grow the SQLite backlog shown on port 8765.
     <a href="/">Refresh status</a>.</p>
  <div class="grid">
    {panels}
  </div>
</body>
</html>"""


def _make_handler(db_dir: str | Path, control_dir: str | Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str, content_type="text/html; charset=utf-8",
                  location: str | None = None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, render_page(db_dir, control_dir))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = parse_qs(raw, keep_blank_values=True)
            try:
                apply_form(control_dir, form)
            except ValueError as exc:
                self._send(400, f"<p>{html.escape(str(exc))}</p>")
                return
            self._send(303, "", location="/")

        def log_message(self, *_args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual simulator control window")
    parser.add_argument("--db-dir", default="runs")
    parser.add_argument("--control-dir", default="runs/control")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)

    Path(args.control_dir).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        (args.host, args.port), _make_handler(args.db_dir, args.control_dir))
    print(f"Simulator control: http://{args.host}:{args.port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control_ui.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Add the page-rendering test to `tests/test_control_ui.py`**

```python
def test_render_page_has_four_panels_buttons_and_sliders(tmp_path):
    from tools.control_ui import render_page

    page = render_page(tmp_path / "runs", tmp_path / "control")

    assert page.count('<section class="panel">') == 4
    for node in ("BID-01", "BID-02", "BID-03", "BID-04"):
        assert node in page
    for action in ("pause", "override", "random"):
        assert f'value="{action}"' in page
    for key in ("ph", "turbidity_ntu", "tds_ppm",
                "water_temp_c", "flow_lpm", "rssi_dbm"):
        assert f'name="{key}"' in page
```

- [ ] **Step 6: Run the tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_control_ui.py -q
```

Expected: `4 passed`.

- [ ] **Step 7: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `33 passed`.

---

## Task 7: Document both local ports and Milestone 10

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Add the prominent local-tool port table**

Insert this section near the top of `ROADMAP.md`, before the existing setup/checklist tables:

```markdown
## Local tool ports — memorise these

These are **host-side tools**, not Docker Compose services. Run them from the project root in
separate terminals while the edge containers are running.

| Port | Tool | Exact command | Purpose |
|---|---|---|---|
| **8765** | SQLite buffer viewer | `.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765` | Read-only live view of `runs/<node>-compose/db/<node>.db`; shows `PENDING`, oldest backlog age, MQTT link state and a sparkline. Open `http://localhost:8765`. |
| **8766** | Simulator control | `.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766` | Optional manual control window. Open `http://localhost:8766` to pause a node, send chosen sensor values, or resume random data. |

Port 8765 never writes to SQLite. Port 8766 writes only `runs/control/<NODE>.json`;
it does not touch Docker, ThingsBoard, certificates, tokens, tenants, dashboards or webhooks.
```

- [ ] **Step 2: Add Milestone 10 after Milestone 9**

Insert this after the Milestone 9 verification block and before `### Next milestone`:

```markdown
### Milestone 10 — manual simulator control (additive)

The user asked to control what is sent to ThingsBoard without disturbing the working stack.
This milestone adds an optional control window at **http://localhost:8766** while leaving
**http://localhost:8765** as the read-only SQLite viewer.

#### Buttons

Each node panel (BID-01…BID-04) has six sliders — pH 0–14, turbidity 0–1000 NTU,
TDS 0–2000 ppm, temperature 0–40 °C, flow 0–40 L/min, RSSI −120–0 dBm — and three actions:

| Button | Effect |
|---|---|
| **Pause node** | Stops only that node's MQTT publisher. Sampling continues, so `synced=0` rows accumulate in SQLite and appear as rising `PENDING` on port 8765. Other nodes are unaffected. |
| **Override & resume** | Restores that node's MQTT publisher, drains the backlog with original timestamps, then sends your slider values on every new sample. Existing breach/eviction/flush logic still applies. |
| **Resume random** | Restores MQTT if paused and releases the manual values; the original physics model continues unchanged. |

#### Safety and behavior

- No control file means normal behavior: `paused=false`, `override=false`.
- Control files live only under `runs/control/` and are excluded from the golden image.
- `edge/control.py` polls the file immediately before each sample; changes appear within one
  sample period (normally about two seconds).
- Pause is a per-node flag in `edge/link.py`, not the shared netgate relay, so one panel cannot
  blackhole another node.
- Manual values pass through the existing `is_breach()` check, so chosen values can trigger the
  existing calculated-field alarms. No new ThingsBoard device or webhook is created.

#### Demo workflow

1. Start the edge stack with `--profile edge` as before.
2. Start the SQLite viewer: `.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765`.
3. Start the control window: `.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766`.
4. Open both URLs. Press **Pause node** on BID-03; after a few seconds its `PENDING` count rises
   on 8765 while the panel shows `PAUSED`.
5. Set pH/TDS/turbidity/flow to your chosen values and press **Override & resume**. The backlog
   drains, ThingsBoard receives the chosen values, and the panel shows `OVERRIDE`.
6. Press **Resume random** to return to the physics model. The panel shows `RANDOM`.

#### Verification

- Added `tests/test_control.py` and `tests/test_control_ui.py` with 14 tests total.
- Full suite: **33 passed**.
- Golden image rebuilt because `Dockerfile` already copies `edge/` and `tools/`; no Dockerfile
  or Compose edit was needed.
```

- [ ] **Step 3: Verify documentation**

Run:

```bash
grep -n -E 'Local tool ports|8765|8766|Milestone 10' ROADMAP.md | head -20
```

Expected: output contains the port table and Milestone 10 headings.

- [ ] **Step 4: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: `33 passed`.

---

## Task 8: Rebuild the golden image and run the final audit

**Files:**
- Modified source from Tasks 1–6 is already copied by `Dockerfile`.
- No Dockerfile or Compose changes.

- [ ] **Step 1: Compile and test the host code**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q edge tools
```

Expected: `33 passed`; compileall prints nothing.

- [ ] **Step 2: Smoke-test both host servers**

Run:

```bash
.venv/bin/python - <<'PY'
import subprocess, time, urllib.request
procs = [
    subprocess.Popen(['.venv/bin/python', '-m', 'tools.buffer_view', '--db-dir', 'runs', '--port', '8765'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    subprocess.Popen(['.venv/bin/python', '-m', 'tools.control_ui', '--db-dir', 'runs', '--control-dir', 'runs/control', '--port', '8766'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
]
try:
    time.sleep(1)
    viewer = urllib.request.urlopen('http://127.0.0.1:8765', timeout=3).read().decode()
    control = urllib.request.urlopen('http://127.0.0.1:8766', timeout=3).read().decode()
    print('viewer_ok', 'BID-03' in viewer, 'polyline' in viewer)
    print('control_ok', 'Override &amp; resume' in control, control.count('class="panel"'))
finally:
    for p in procs:
        p.terminate()
        p.wait()
PY
```

Expected output includes `viewer_ok True True` and `control_ok True 4`.

- [ ] **Step 3: Rebuild the image**

Run:

```bash
docker build -t jaldridhti-edge:1.0.0 .
```

Expected: successful build.

- [ ] **Step 4: Audit the image**

Run:

```bash
docker run --rm --entrypoint sh jaldridhti-edge:1.0.0 -c '
echo "control=$(test -f /app/edge/control.py && echo yes || echo no)"
echo "ui=$(test -f /app/tools/control_ui.py && echo yes || echo no)"
python -m tools.control_ui --help >/dev/null && echo control_help=ok
python -m tools.buffer_view --help >/dev/null && echo viewer_help=ok
echo "pem=$(find /app -type f -name "*.pem" | wc -l)"
echo "tokenmaps=$(find /app -type f -name "tokens*.json" | wc -l)"
echo "runtime_dirs=$(find /app -maxdepth 1 -type d \( -name results -o -name runs \) | wc -l)"
echo "live_copy=$(test -e /app/thingsboard/dashboard-live.json && echo yes || echo no)"
echo "entrypoint_line1=$(head -1 /app/docker-entrypoint.sh)"
'
```

Expected:

```text
control=yes
ui=yes
control_help=ok
viewer_help=ok
pem=0
tokenmaps=0
runtime_dirs=0
live_copy=no
entrypoint_line1=#!/bin/sh
```

- [ ] **Step 5: Manual live check with the current four nodes**

Run in three terminals from the project root:

```bash
docker compose -f docker-compose.yaml --profile edge up -d
.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765
.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766
```

Open `http://localhost:8765` and `http://localhost:8766`. Press **Pause node** on BID-03, wait about 10 seconds and confirm BID-03 `PENDING` rises on 8765. Set pH to 6.1 and turbidity to 260, press **Override & resume**, confirm values appear in ThingsBoard, then press **Resume random**. Do not provisioning new devices, create tenants, add webhooks or run test-rig devices.

---

## Plan self-review

- Spec coverage: control UI, runtime JSON files, `edge/control.py`, per-node `Link` pause, manual `NodeModel`, simulator polling, tests, ROADMAP port table/Milestone 10, image rebuild and audit are all assigned tasks.
- Additive safety: no-control-file default, no Docker socket, no Compose/Dockerfile changes, no ThingsBoard/dashboard/token/tenant/webhook changes.
- Type consistency: `read_control()`, `apply_control()`, `set_manual()`, `clear_manual()`, `set_paused()`, `parse_values()`, `write_control_state()`, `apply_form()` and `render_page()` use the same names in tests and implementation.
- Placeholder scan: none.

## Amendment — pause now disconnects the node (2026-11-25)

After this plan was completed, the user asked for ThingsBoard to show a paused node as not
connected. Task 3's publisher-only pause was amended: `edge/link.py` now tears down only that node's
MQTT client on pause, holds in `PAUSED` without pumping or reconnecting, and reconnects on resume.
`tests/test_control.py` gained tests for disconnect, no pump/reconnect while paused, and
reconnect after resume; the suite is 38 passed. The image was rebuilt and the four edge containers
recreated. `ROADMAP.md` Milestone 10 and the design spec were updated with the built-in
~60 s inactive/active timing. No tenant, device, token, webhook, Compose or Dockerfile change.
