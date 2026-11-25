# Manual simulator control — design spec

Date: 2026-11-25

## Goal

Add an optional browser control window that lets the operator pause MQTT publishing per node, choose exact telemetry values, then resume publishing with those values or return to the existing random physics model. It must be purely additive: with no control file present, the current simulator behavior is unchanged.

## Non-goals

- No ThingsBoard provisioning, dashboard, tenant, token or alarm changes.
- No Docker socket access from the browser tool.
- No changes to the golden image's secret policy or to the read-only SQLite viewer at port 8765.
- No separate “send one reading” button; “Override & resume” covers the requested workflow.
- No new pip dependency.

## Components

### 1. `tools/control_ui.py`

Host-side standard-library HTTP server on port 8766.

- `GET /` renders a 2 × 2 grid with one panel for BID-01…BID-04.
- Each panel has six range inputs:
  - pH 0–14, step 0.01
  - turbidity 0–1000 NTU, step 1
  - TDS 0–2000 ppm, step 1
  - water temperature 0–40 °C, step 0.1
  - flow 0–40 L/min, step 0.1
  - RSSI −120–0 dBm, step 1
- Each panel has three submit buttons:
  - `Pause node`
  - `Override & resume`
  - `Resume random`
- `POST /control` accepts `node`, `action`, and the six values, writes the node's control file atomically (temporary file plus `os.replace`), then redirects to `/`.
- The page reuses `tools.buffer_view.snapshot()` to show link state and pending backlog, so it does not duplicate SQLite parsing.
- Default bind is `127.0.0.1`; flags `--host`, `--port`, `--db-dir`, `--control-dir` override the defaults.

### 2. `runs/control/<NODE>.json`

Runtime-only state files, created only by browser actions. `runs/` is already excluded from the Docker image.

Schema:

```json
{
  "node": "BID-03",
  "paused": false,
  "override": false,
  "values": {
    "ph": 7.2,
    "turbidity_ntu": 4.0,
    "tds_ppm": 180,
    "water_temp_c": 20.0,
    "flow_lpm": 13.0,
    "rssi_dbm": -85
  },
  "updated_ms": 1795543465000
}
```

Missing or malformed files are treated as `paused=false`, `override=false`, with no manual values.

### 3. `edge/control.py`

Small helper used by the simulator.

- `read_control(control_dir, node) -> dict` returns normalized state.
- `apply_control(node, control, log, events) -> None` applies only transitions:
  - `paused` false → true: call `node.link.set_paused(True)`; sampling continues and SQLite rows accumulate as `synced=0`.
  - `paused` true → false: call `node.link.set_paused(False)`; existing backlog drains in timestamp order.
  - `override` false → true: call `node.model.set_manual(values)`; generated samples use exact slider values.
  - `override` true → false: call `node.model.clear_manual()`; existing random physics resumes.
- Transition events are written to `events.jsonl` for evidence.

Pause is implemented with a per-node flag in `Link`, not with the shared `netgate` relay. This prevents one panel from blackholing other nodes when the simulator is run in host multi-node mode.

### 4. `edge/link.py`

Additive per-node publisher pause:

- `self._paused` flag.
- `set_paused(bool)` sets the flag and kicks the uplink thread when resuming.
- In `_online_loop()`, while paused, the client keeps pumping/keepalive but does not select pending rows; no telemetry is published.
- With the flag unset, the existing publish path executes exactly as before.

### 5. `edge/sensors.py`

Additive manual-value path:

- `NodeModel.manual_values: dict | None = None`.
- `set_manual(values)` and `clear_manual()`.
- `sample(t_min)` returns the manual values plus latitude/longitude when `manual_values` is set; otherwise the existing model runs unchanged.
- `is_breach()` still evaluates the returned values, so operator-set turbidity above 200 NTU still triggers the existing alarms.

### 6. `edge/simulator.py`

One small call in `Node._sample_loop()`: read and apply the node's control file immediately before generating each sample. Add `--control-dir` with the existing default derived from the project root, so the Compose bind mount `./runs:/app/runs` is sufficient. No entrypoint or Compose changes.

## Button behavior

| Button | File state | Effect |
|---|---|---|
| Pause node | `paused=true`, `override` unchanged | MQTT publishing stops locally; sampling and SQLite writes continue; ThingsBoard sees inactivity after its timeout. |
| Override & resume | `paused=false`, `override=true`, slider values saved | Publishing resumes, backlog drains with original timestamps, then every new sample uses the chosen values. |
| Resume random | `paused=false`, `override=false` | Publishing resumes and the existing physics model takes over. |

Changes take effect at the next sample, typically within two seconds.

## Tests

- `tests/test_control.py`: missing/malformed file defaults; state normalization; transition application; manual model values; pause path does not publish.
- `tests/test_control_ui.py`: atomic write, form parsing, 2 × 2 panel rendering, accepted slider ranges and button actions.
- Full suite must pass; current baseline is 19 tests.

## Documentation and verification

- Add a local-tool port table to `ROADMAP.md` with 8765 (read-only SQLite viewer) and 8766 (manual simulator control), including exact run commands.
- Add Milestone 10 describing the additive design and button workflow.
- Rebuild `jaldridhti-edge:1.0.0`; audit that it contains no PEM/token/results/runs files and that the entrypoint still starts with `#!/bin/sh`.
- Manual smoke: run a node, press Pause, confirm `PENDING` grows in the 8765 viewer, press Override & resume, confirm backlog drains and chosen values reach ThingsBoard, press Resume random.

## Amendment — pause now disconnects the node (2026-11-25)

Per user request, **Pause node** was changed from suppressing only local publishing to disconnecting
that node's own MQTT client. `Link` enters `PAUSED`, stops pumping/reconnecting, and SQLite keeps
sampling. ThingsBoard shows its built-in inactive/network-not-connected state after the existing
inactivity timeout (60 s), and returns to active within about 60 s after resume. Other nodes,
the netgate relay, control-file format, UI and Compose setup are unchanged. Tests now cover
disconnect-on-pause, no pump/reconnect while paused, and reconnect after resume. See `ROADMAP.md`
Milestone 10 for the user-facing behavior and troubleshooting steps.
