# Manuscript source — Section III corrections and Section 8

Source of truth for every number below:

- Run: `runs/measure-20261126-000140/` (raw evidence; excluded from this public repo)
- Computed metrics: `results/measure-metrics/metrics.json`, `metrics.md`, `latency.csv`, `alarms.csv`
- Scenario and screenshot times: `config/scenarios/measure.json`
- ThingsBoard export: `thingsboard/*.json`

The run used **three pilot nodes** (`BID-01`, `BID-02`, `BID-03`), 720 samples per node,
one 180 s `blackhole` outage, and a 60× process clock. All timestamps are Unix epoch ms
from the simulator wall clock; at 60×, one wall-clock second represents one process minute.
Therefore the reported 180 s outage represents 180 process minutes.

---

## A. Corrections to Section III — System Architecture

Use the pasted Section III as a Task 2 recap, but apply these corrections:

| Pasted statement | Correction |
|---|---|
| 10-minute sampling / 30-minute cloud uploads | Task 2 design only. Task 3 samples every **2 s** in `demo.json` and `measure.json` and publishes per batch after PUBACK, not on a 30-minute timer. |
| Seven days of SQLite backup | WAL is implemented, but retention is **20,000 rows by default** with tiered eviction (`edge/simulator.py --max-rows`, `edge/buffer.py`), not a literal 7-day timer. |
| Seven implemented alarm rules | Five telemetry alarms plus **Node Inactive** = six implemented rules. R6/WQI is a derived TBEL key, not an alarm rule. The Task 2 case-B WQI check (`243.3`) is implemented in `tools/tb_provision.py`. |
| 50 pilot nodes | Task 2 scale. Task 3 results use three pilot nodes; ten `BID-*` nodes are configured and four are in the live Compose stack. |
| Netgate can accept/reject/slow | Implemented modes are `up`, `blackhole`, `refuse`; added latency is configurable with `--delay-ms`. There is no named “slow” scenario mode. |
| TLS/authentication | Accurate: TLS 1.2 on 8883 is retained, server CA/hostname are verified, and ThingsBoard uses per-device access tokens instead of client X.509 certificates. |
| Client timestamps | Accurate as simulator wall-clock epoch ms; add the 60× caveat above so reviewers do not read 180 s as 3 hours of wall-clock outage. |
| `buffer_depth` in Table II | Correct: sampled value is `rssi_dbm`; `buffer_depth` is injected at send time in `edge/link.py`. |

### Revised condensed III-B wording

> Each virtual node runs a sampler thread and an uplink thread over one SQLite WAL file.
> Every 2 s the sampler evaluates the coupled process model, commits the reading first, and
> wakes the uplink. The uplink selects the oldest unsynchronised rows, publishes them as a
> timestamped array to `v1/devices/me/telemetry` at QoS 1, and marks them synchronised only
> after PUBACK. A TCP netgate relay provides `up`, `blackhole` and `refuse` states while
> carrying only TLS ciphertext. ThingsBoard stamps `rx_ms`, derives `wqi`, mirrors alarm
> state into `alm_*` attributes, and the dashboard reads those attributes for the map and
> status table. A 60× process clock compresses physical-time behaviour into a short demo;
> client timestamps remain Unix epoch milliseconds of the simulator wall clock.

---

## B. Section 8 — Results and Discussion

## 8.1 Data visualisation

Use annotated dashboard captures from the `measure.json` shot list. Capture the PNGs into
`report/figures/`; the manuscript owns these screenshots.

| Shot | Time (s) | Dashboard state | What it proves | Suggested caption |
|---|---:|---|---|---|
| `01_normal.png` | 45 | fleet `default` | Baseline five-parameter telemetry on all three nodes | “Fleet dashboard during normal operation at t≈45 s.” |
| `02_event_warning_and_critical.png` | 140 | fleet `default` | Coupled contamination event: turbidity rises, TDS rises, pH falls; Warning then Critical | “Contamination event: coupled turbidity/TDS/pH response and alarm escalation.” |
| `03_supply_interruption.png` | 445 | fleet `default` | `flow_lpm = 0` sustained on BID-02 | “Supply interruption at BID-02 with zero flow.” |
| `04_event_BID-02.png` | 505 | `node`, BID-02 | Per-node detail with event history | “BID-02 node detail during the second contamination event.” |
| `05_outage_nodes_inactive.png` | 845 | fleet `default` | Netgate blackhole; nodes shown inactive; no fresh telemetry | “180 s netgate blackhole with devices marked inactive.” |
| `06_after_backfill.png` | 905 | fleet `default` | Gap refilled with original client timestamps | “Fleet view after reconnection and historical backfill.” |
| `07_after_backfill_BID-03.png` | 905 | `node`, BID-03 | Deepest backlog (105 rows) replayed | “BID-03 detail after replay of the deepest backlog.” |
| `08_second_event.png` | 1090 | fleet `default` | Repeatable second rain event | “Second scripted rain event showing repeatability.” |
| `09_interruption_BID-01.png` | 1245 | fleet `default` | BID-01 interruption before final bad-token event | “BID-01 supply interruption near the end of the run.” |

Annotation guidance: circle the five parameter widgets, draw one arrow from the rain event to
the turbidity rise, and a second arrow from the outage band to the backfilled history. Keep
captions below figures, IEEE style.

## 8.2 Alerting evidence

Live threshold alarms from `results/measure-metrics/alarms.csv`:

| Quantity | Value |
|---|---:|
| Live threshold alarm events | n=21 |
| Mean activation delay | 550.3 ms |
| Median activation delay | 437.0 ms |
| Maximum activation delay | 1082.0 ms |
| Buffered-breach alarm delay | n=2, mean 185054.5 ms |
| Node Inactive detection | n=6, mean 33561.0 ms |

Representative active/cleared examples (epoch ms converted to UTC where helpful):

| Node | Alarm | Started | Created / delay | Cleared | Status |
|---|---|---|---|---|---|
| BID-01 | High Turbidity (WARNING) | 1795630667860 (18:17:47.860) | 1795630668657, +797 ms | 1795630911860 (18:21:51.860) | CLEARED_UNACK |
| BID-01 | Gross Contamination (CRITICAL) | 1795630671860 (18:17:51.860) | 1795630672717, +857 ms | 1795630813860 (18:20:13.860) | CLEARED_UNACK |
| BID-01 | Node Inactive (CRITICAL) | 1795631375500 (18:29:35.500) | 1795631375526, +1666 ms detection | 1795631487210 (18:31:27.210) | CLEARED_UNACK |
| BID-03 | High Turbidity, backfilled breach | 1795631329060 (18:28:49.060) | 1795631516096, +187036 ms | 1795631516357 | CLEARED_UNACK |

**Interpretation:** the ~0.3–1.1 s values are server activation delays for live threshold
breaches. The ~185 s values are *queue ages* for breaches generated during the blackhole and
only evaluated after backfill; they must not be presented as network latency. Likewise, the
Node Inactive detection time is a device-state timeout, not a sensor alarm delay.

Suggested caption: “ThingsBoard alarm table showing active and cleared alarms with creation,
start and clear timestamps; `alm_*` attributes mirror the same state to the map.”

## 8.3 Resilience evidence

| Event / quantity | Value |
|---|---|
| Outage start | t=700 s, mode `blackhole`, duration 180 s |
| Restore | t=880 s |
| Depth at reconnect | BID-01 91, BID-02 99, BID-03 105 rows |
| Rows replayed | 91 / 99 / 105 (all rows retained) |
| Drain time | 0.08 / 0.10 / 0.10 s |
| Flush throughput | 1151.9 / 1042.1 / 1029.4 rows/s; mean 1074.5 rows/s |
| Recovery from restore | BID-01 1.37 s, BID-02 16.84 s, BID-03 29.91 s |
| Flush records in run | 1859 |
| Final state | 0 pending, 0 evicted unsynced, 2160/2160 delivered, 3 republished rows |

Buffer-depth figure specification (generate from `runs/measure-20261126-000140/events.jsonl`):

- x-axis: `t_ms - t0` in seconds (0–1440 s)
- y-axis: `depth` from `sample`, `link` and `flush` events
- shade 700–880 s as the outage band; draw a vertical line at 880 s for restore
- annotate the three reconnect depths and the drain to zero

Suggested caption: “Buffer depth over the 180 s blackhole. Depth accumulates during the
outage and drains within 0.10 s of reconnect; original timestamps are preserved on replay.”

## 8.4 Performance measurement

Use only `results/measure-metrics/metrics.md` for Table IX.

| Metric | Result | Brief minimum |
|---|---|---|
| Live latency (`rx_ms - ts`) | n=1847; mean 30.1 ms; median 30.0 ms; P95 45.0 ms; max 736.0 ms | ≥200 messages |
| PUBLISH / PUBACK bounds | mean 10.7 / 17.7 ms; P95 15 / 25 ms | supporting evidence |
| Delivery with outage | 2160/2160 = 100 %; PUBACK-confirmed 100 % | all pilot rows present |
| Outage length | 180 s, one restore | 180 s |
| Alarm activation delay | n=21; mean 550.3 ms; median 437.0 ms | ≥10 alarms |
| Flush throughput | mean 1074.5 rows/s | measured, not thresholded |
| Recovery | 3 events: 1.37 / 16.84 / 29.91 s | ≥1 with rows flushed |
| Pilot data loss | 0 rows (0.000 %) | 0 |

Acceptance checks all PASS. Mosquitto/`--sink` figures are **pre-validation** and must be
kept in a separate labelled table; they are not ThingsBoard evidence for Table IX. The
unbuffered control node was not part of this measured run, so the 8766 page shows `- %` for
`control_ratio_no_store_forward` rather than a fabricated number.

## 8.5 Limitations

- **Surrogate indicators:** pH, turbidity, TDS, temperature and flow are process-model
  surrogates; they do not establish laboratory-grade water safety or regulatory compliance.
- **Simulation vs hardware:** no ESP32-S3, ADS1115, A7670E modem, real sensors, battery or
  solar panel were used. The 60× clock and software netgate compress and simplify real WAN
  behaviour; only three of the fifty pilot sites were measured.
- **Unvalidated sensor accuracy:** no calibration against reference instruments, no sensor
  drift/fouling model, and one host clock for both client and server latency stamps.
- **Scope of evidence:** one 24-minute run, three nodes, one 180 s outage; results show a
  working prototype, not a statistical production study.
- **Alarm semantics:** buffered-breach delays include queue residence time; Node Inactive
  delay is a device-state timeout. Both must be reported separately from live activation delay.

---

## C. Evidence-to-claim map

| Claim | Evidence file |
|---|---|
| Architecture / data flow | `edge/simulator.py`, `edge/link.py`, `edge/buffer.py`, `tools/netgate.py` |
| Sensor model / coupling | `edge/sensors.py`, `config/scenarios/measure.json` |
| Alarm rules and WQI | `thingsboard/alarm_rules_cf.json`, `thingsboard/rule_chain.json`, `tools/tb_config.py` |
| Dashboard screenshots | user-captured PNGs from the `screenshots` array above |
| Alarm delays | `results/measure-metrics/alarms.csv` |
| Outage, depth and replay | `runs/measure-20261126-000140/events.jsonl` |
| Six mandatory metrics | `results/measure-metrics/metrics.json` and `metrics.md` |
| Full demonstration console log | `runs/measure-20261126-000140/console.log` (submission evidence, not public repo) |

## D. Submission split

**Implementation & Demonstration ZIP:** editable source, `SQLite_schema_DDL.sql`, ThingsBoard
JSON export, full console log plus raw/metric evidence, completed Appendix A. Dashboard
screenshots are optional in the ZIP because the JSON export is provided.

**Technical Manuscript PDF:** Section 8 screenshots, Table IX from ThingsBoard metrics, the
corrected Section III, and the limitations above. The PDF is a separate submission and is not
placed inside the ZIP.
