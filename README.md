# JalDrishti — post-flood water contamination IoT demonstrator

Public companion repository for Assessment Task 3. It contains the editable Python
implementation, ThingsBoard configuration, scenario timelines, tests, documentation and
the generated measurements from the real ThingsBoard run.

> **Security note:** private keys, device access tokens, runtime SQLite databases and
> console logs are deliberately excluded from this repository. Generate your own locally.
> Do not commit `certs/*.pem`, `results/tokens/` or `runs/`.

## What is here

| Path | Purpose |
|---|---|
| `edge/` | Virtual ESP32-S3 node: sensor process model, SQLite WAL buffer, MQTT/TLS uplink, simulator loop |
| `tools/` | ThingsBoard provisioning/config helpers, netgate relay, buffer viewer, control UI, metrics tool |
| `config/` | Node registry and repeatable scenario timelines (`demo.json`, `measure.json`, `smoke.json`) |
| `thingsboard/` | Exported device profiles, alarm rules, rule chain and dashboard JSON (non-secret templates) |
| `tests/` | Unit tests for buffer, simulator, measurements and control UI |
| `results/measure-metrics/` | Generated metrics from the real ThingsBoard run: `metrics.json`, `metrics.md`, `latency.csv`, `alarms.csv` |
| `report/MANUSCRIPT_SOURCE.md` | Paste-ready source for Section III corrections and Section 8 |
| `ROADMAP.md` | Full implementation log, commands, evidence checklist and measurement procedure |

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-runtime.txt -r requirements-dev.txt

# Build the golden edge image from the project root.
docker build -t jaldridhti-edge:1.0.0 .

# Provision your own ThingsBoard instance and device tokens, then start the demo stack.
.venv/bin/python -m tools.tb_provision --help
docker compose -f docker-compose.yaml up -d edge-bid01 edge-bid02 edge-bid03 edge-bid04
```

Certificates are not included. Generate a CA and server certificate for your own broker;
the simulator verifies the CA and the real broker hostname. Device authentication in this
prototype uses per-device access tokens rather than client X.509 certificates.

## Local viewer ports

| Port | Command | Purpose |
|---|---|---|
| 8765 | `.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765` | Read-only live SQLite buffer viewer |
| 8766 | `.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766` | Manual simulator control plus read-only `/metrics` tab |

## Real measured results

Run `measure-20261126-000140`, three pilot nodes, 720 samples each, one 180 s blackhole:

| Metric | Result |
|---|---|
| Live latency (`rx_ms - ts`) | n=1847, mean 30.1 ms, median 30.0 ms, P95 45.0 ms, max 736.0 ms |
| Delivery with outage | 2160/2160 = 100 %, PUBACK-confirmed 100 % |
| Alarm activation delay | n=21, mean 550.3 ms, median 437.0 ms, max 1082.0 ms |
| Flush throughput | mean 1074.5 rows/s |
| Recovery from restore | 1.37 s / 16.84 s / 29.91 s for BID-01/02/03 |
| Pilot data loss | 0 rows (0.000 %) |

Reproduce the calculation with:

```bash
.venv/bin/python -m tools.measure \
  --run runs/measure-20261126-000140 \
  --tb-url http://localhost:8080 \
  --user tenant@thingsboard.org --password tenant \
  --tokens results/tokens/default.json \
  --out results/measure-metrics
```

See `ROADMAP.md` section 9A for the full 24-minute procedure and acceptance checks.

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q edge tools
```

## Academic use

This repository is the Implementation & Demonstration source. The technical manuscript is a
separate PDF submission. Do not paste pre-validation/Mosquitto numbers into Table IX; use only
the ThingsBoard results in `results/measure-metrics/`.
