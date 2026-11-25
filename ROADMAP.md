# JalDrishti — Golden Image & Full Rebuild Roadmap

**ITS67404 Assessment Task 3, Group 07 — prepared 2026-11-24**

This is the complete, from-zero walkthrough for rebuilding the project in
`/home/sarun/Desktop/sem_9/IOT_Final_Assignment_Technical_Implementation/`,
understanding every technical choice, and producing a Docker image you can publish to Docker Hub.

> **How to use this file:** the unnumbered `Local tool ports` section and `Current status snapshot`
> come before the numbered sections — read those first. Then follow the sections in order.
> Sections 0–3 are the theory you must be able to explain; Sections 4 and 4A document the
> **actual** project layout and the `config/` folder; Sections 5–8A are the hands-on build;
> Sections 9–15 are verification, publishing and submission; Section 16 is the live build log.

> **Redistribution plan:** until the rebuild is finished, this file is the **single working document**.
> After everything is complete, its content will be redistributed into specific project files
> (README, docs, config notes, etc.). Do not create those separate files yet — keep everything here.

---

## Local tool ports — memorise these

These are **host-side tools**, not Docker Compose services. Run them from the project root in
separate terminals while the edge containers are running.

| Port | Tool | Exact command | Purpose |
|---|---|---|---|
| **8765** | SQLite buffer viewer | `.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765` | Read-only live view of `runs/<node>-compose/db/<node>.db`; shows `PENDING`, oldest backlog age, MQTT link state and a sparkline. Open `http://localhost:8765`. |
| **8766** | Simulator control | `.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766` | Manual control window. Open `http://localhost:8766` to pause a node, send chosen sensor values, or resume random data. The read-only **Metrics** tab at `http://localhost:8766/metrics` shows the last generated measurement. |

Port 8765 never writes to SQLite. Port 8766 only writes `runs/control/<NODE>.json`;
it does not touch Docker, ThingsBoard, certificates, tokens, tenants, dashboards or webhooks. Its
`/metrics` tab only **reads** `results/measure-metrics/metrics.json` on each page load.

> **One-time control directory setup:** `runs/` is owned by UID 10001 so the containers can write
> SQLite files. Before starting port 8766 for the first time, create a host-writable control
> directory from the project root:
>
> ```bash
> docker run --rm --user root --entrypoint sh \
>   -v "$PWD/runs:/app/runs" jaldridhti-edge:1.0.0 \
>   -c "mkdir -p /app/runs/control && chown $(id -u):$(id -g) /app/runs/control && chmod 755 /app/runs/control"
> ```
>
> The edge containers only need to read files in this directory. Port 8766 writes files as `0644`.
>
> **Keep port 8766 alive:** the control window is a foreground terminal process, not a Compose
> service. If its terminal is closed or the process is stopped, the page may still be in the
> browser but its buttons will do nothing and nodes will continue sending random data. Check with
> `ss -ltnp '( sport = :8766 )'`; a healthy server shows a Python process listening on 127.0.0.1:8766.
> Restart it in its own terminal and leave that terminal open while using the buttons.

---

## Current status snapshot

| Item | State | Notes |
|---|---|---|
| Root `Dockerfile`, `docker-entrypoint.sh`, `docker-compose.yaml` | Done | Compose filename is `docker-compose.yaml`, so every command needs `-f docker-compose.yaml` |
| `docker-compose.yaml` edge services | Done | 10 edge containers: `BID-01`…`BID-09` + `JD-CTRL-01`; all mount `config` |
| `thingsboard/docker-compose.yml` teaching copy | Done | Keeps the `/etc/thingsboard/certs` fix |
| `certs/` (CA + server PEMs + `gen_certs.sh`) | Done | SAN contains `localhost`, `thingsboard-ce`, `127.0.0.1` |
| `config/nodes.json` global file | Done | Holds control node + bench node only |
| `config/nodes/BID-01.json` … `BID-10.json` | Done | Ten field sensors, one file per node, all valid JSON |
| `config/scenarios/smoke.json`, `demo.json`, `measure.json` | Done | All three valid JSON; `measure.json` is the 24-minute metric-generation timeline |
| Host `.venv` (`python3 -m venv .venv`) | **Done** | Contains paho 2.2.0, requests 2.34.2, matplotlib 3.11.2, playwright 1.65.2, pytest 9.2.0 |
| `edge/` source code (8 files) | **Done** | All package files written; compile and unit checks pass |
| `tools/` runtime files | **Done** | `__init__.py` + `netgate.py`; simulator `--help` now passes |
| `tools/` generator + provisioning | **Done** | `tb_api.py`, `tb_config.py`, `tb_provision.py` (backup-only defaults), `tb_ui.py` (UI-only installer) |
| `tools/buffer_view.py` | **Done** | Read-only live SQLite viewer: terminal table plus browser page; baked into the golden image by `COPY tools/` |
| `tools/control_ui.py` | **Done** | Host-side simulator control window on port 8766; writes only `runs/control/<NODE>.json`. Read-only `/metrics` tab shows the last `tools/measure.py` output. |
| `edge/control.py` | **Done** | Per-node control-file reader/applier; no file means unchanged random behavior. |
| `tools/measure.py` | **Done** | Computes the six mandatory metrics from one combined run dir; 3 unit tests pass. See section 9A. |
| Scenario cross-node bug | **Fixed** | `run_scenario()` skips events for nodes not owned by a `--only` container; regression test added. Full suite 45 passed. |
| Real ThingsBoard measurement | **Done** | `runs/measure-20261126-000140` — 3 nodes × 720 samples, one 180 s blackhole, 0 pending, 0 evicted. See section 9A. |
| `results/measure-metrics/` | **Done** | `metrics.json`, `metrics.md`, `latency.csv`, `alarms.csv`; all six acceptance checks PASS. Live at `http://localhost:8766/metrics`. |
| Optional host tools | Not needed | `sweep.py`, `webhook_receiver.py` only if the manuscript needs extra figures |
| Dashboard/rule/alarm templates | **Installed** | `tools/tb_ui.py` pushed rule chain + 5 alarms + 30-widget dashboard; portable template baked into the image |
| `results/tokens/default.json` | **Done** | Written manually with 10 token keys; only BID-01…BID-04 devices exist so far |
| Golden image `jaldridhti-edge:1.0.0` | **Rebuilt** | No PEM / token / results / runs / `dashboard-live.json`; clean 30-widget template; includes `tools/buffer_view.py`, `tools/control_ui.py` and `edge/control.py`; entrypoint has `#!/bin/sh` |
| New ThingsBoard stack | **Running** | postgres healthy, HTTP 200, TLS verify `0 (ok)` |
| Old ThingsBoard stack | Not running | No container or port conflict found |
| `runs/` host ownership | **Done** | `chown -R 10001:10001 runs` — image runs as UID 10001 and must write the bind mount |
| ThingsBoard devices (default tenant) | **4 done** | BID-01…BID-04, all on `JalDrishti Water Node`, tokens match `default.json` |
| Rules / alarms / dashboard | **Installed** | `JalDrishti Ingest`, 5 calculated-field alarms, `JalDrishti – Bidur Water Safety` (30 widgets) |
| Main comparison charts | **Done** | Turbidity and flow line charts use alias `Water node`, so all 4 devices appear together |
| Node drill-down | **Done** | Status-table action button and map tooltip open the `node` state for the selected device; 10 sensor cards there |
| Dashboard default node | **Fixed** | `Selected node` default entity is BID-01's device UUID, not its access token |
| Edge containers | **Stopped after measurement** | `edge-bid01`…`edge-bid04` were stopped for the clean run; `docker compose -f docker-compose.yaml start edge-bid01 edge-bid02 edge-bid03 edge-bid04` restores the live demo stack. Remaining 6 intentionally not started. |

---

## 0. Read this first — what you are actually building

There are three separate layers. Confusing them is the main reason the earlier setup went wrong.

| Layer | What it is | Lives where | Is it a Docker image you publish? |
|---|---|---|---|
| **Platform** | Docker Engine + Docker Compose | Your PC | No |
| **Data plane** | PostgreSQL 16 + ThingsBoard CE 4.3.1.8 with MQTT/TLS | Two containers from Compose | No — use upstream images |
| **Edge plane** | Python simulator: sensors, SQLite buffer, MQTT uploader | **One container per sensor** | **Yes — this is your golden image** |

The golden image should be the **edge plane**: a small, clean Python image that contains the
simulator code but **no certificates, no access tokens, no results, no secrets**. You then run one
container from it per sensor. The ThingsBoard stack stays as a Compose file, because it is two
third-party images plus configuration.

### The single most important rule

> **Never bake PEM private keys or MQTT tokens into an image you publish.**
> `ca_key.pem` and `server_key.pem` are secrets. `tokens.json` contains the passwords for all
> devices. All of these must be mounted at runtime or generated at runtime.

### The image/container distinction

- The **image** is a static blueprint. It does not send data.
- A **container** started from that image runs `python -m edge.simulator` and sends data.
- One image, many containers: `NODE=BID-01`, `NODE=BID-02`, … each with its own token.

---

## 1. Architecture — how data actually flows

```
   [ sensor process ]  --sample-->  [ SQLite file ]  --QoS1 MQTT/TLS-->  [ThingsBoard ]
    edge/sensors.py                  edge/buffer.py     edge/link.py        4.3.1.8
          |                                |                                  |
          |                         synced=0 -> 1 on PUBACK              rule chain:
          |                                |                          rx_ms + WQI + alarms
          |                                |                                  |
          +------ netgate (tools/netgate.py) --------> PostgreSQL in Docker
                  real TCP outage / added latency
```

Step by step:

1. `edge/sensors.py` generates a physics-based reading (pH, turbidity, TDS, temperature, flow, RSSI).
2. `edge/buffer.py` writes that reading to SQLite **before** anything is published. `synced=0`.
3. `edge/link.py` publishes a batch of up to 25 rows with QoS 1 over TLS on port 8883.
4. Only when the **PUBACK** arrives are those rows marked `synced=1`.
5. ThingsBoard's rule chain stamps `rx_ms`, computes WQI, raises alarms, mirrors alarm severity into
   server attributes `alm_*`, and (optionally) POSTs a webhook.
6. `tools/measure.py` queries ThingsBoard's REST API to compute the six assignment metrics.

There are **two databases** and you never connect to either manually:

- **SQLite** — created automatically by `Buffer.__init__` from `edge/schema.sql`, one file per node.
- **PostgreSQL** — created by the Docker Compose stack; only ThingsBoard talks to it.

---

## 2. Why our Compose stack is "special"

The file in `thingsboard/docker-compose.yml` is based on the official ThingsBoard Docker guide but
differs in six deliberate ways. Understanding each one is part of the demonstration.

### 2.1 Image and queue

```yaml
image: "thingsboard/tb-node:4.3.1.8"
environment:
  TB_SERVICE_ID: tb-ce-node
  TB_QUEUE_TYPE: in-memory
```

The official single-node guide often uses an all-in-one image and Kafka/Zookeeper. We use the
**node image with an in-memory queue**, which is correct for a one-node teaching deployment:
no Kafka containers, no extra memory, no extra failure modes. The trade-off is that queued messages
are lost if ThingsBoard itself crashes — acceptable here because the *edge* SQLite buffer is the
durability mechanism being studied, not the server queue.

### 2.2 MQTT over TLS is explicitly enabled

```yaml
MQTT_SSL_ENABLED: "true"
MQTT_SSL_BIND_PORT: "8883"
MQTT_SSL_PROTOCOL: "TLSv1.2"
MQTT_SSL_CREDENTIALS_TYPE: "PEM"
MQTT_SSL_PEM_CERT: "/etc/thingsboard/certs/server.pem"
MQTT_SSL_PEM_KEY: "/etc/thingsboard/certs/server_key.pem"
MQTT_SSL_PEM_KEY_PASSWORD: ""
```

The default image listens on plain MQTT 1883 only. The brief requires TLS 1.2 on 8883, so these
five variables switch on the TLS listener and point it at our PEM files. `PEM_KEY_PASSWORD` is empty
because the generated key is unencrypted (`-nodes` in the generation script).

### 2.3 THE BUG WE FIXED — where the certificates are mounted

**Original (broken):**

```yaml
- ./certs:/config/certs:ro
```

**Fixed:**

```yaml
- ./certs:/etc/thingsboard/certs:ro
```

This is the single most important thing to understand about the setup.

The image's startup script, `/usr/bin/start-tb-node.sh`, contains this logic:

```bash
CONF_FOLDER="/config"
if [ -d "${CONF_FOLDER}" ]; then
  LOGGING_CONFIG="${CONF_FOLDER}/logback.xml"
  source "${CONF_FOLDER}/thingsboard.conf"
  export LOADER_PATH=${CONF_FOLDER},${LOADER_PATH}
else
  CONF_FOLDER="/usr/share/thingsboard/conf"
  LOGGING_CONFIG="/usr/share/thingsboard/conf/logback.xml"
  source "${CONF_FOLDER}/thingsboard.conf"
fi
```

Mounting anything at `/config/certs` **creates** the `/config` directory. The script then assumes
you supplied a *complete* configuration folder and tries to source `/config/thingsboard.conf` and
load `/config/logback.xml`. Those files do not exist in our setup, so:

- logging fails to initialise (`FileNotFoundException: /config/logback.xml`);
- `JAVA_OPTS` never receives `-Dinstall.data_dir=/usr/share/thingsboard/data` (that setting lives in
  the image's own `thingsboard.conf`);
- the one-time installer fails with `Not valid working directory: /usr/share/thingsboard/bin`;
- the normal service then crashes with `relation "queue" does not exist` because no schema was created.

Mounting at `/etc/thingsboard/certs` leaves `/config` absent, so the script correctly falls back to
the image's built-in configuration. **Keep this fix in the golden setup.**

### 2.4 Device-state timing

```yaml
DEFAULT_INACTIVITY_TIMEOUT: "600"
DEFAULT_STATE_CHECK_INTERVAL: "5"
```

ThingsBoard normally checks device state infrequently. The brief requires "Node Inactive" alarms
to be visible in a short demo, so we check every 5 seconds. The per-device attribute
`inactivityTimeout` (60 s) is set by provisioning; this environment variable is the fallback.

### 2.5 host.docker.internal

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Lets the rule chain's webhook node reach `tools/webhook_receiver.py` running on the host. Linux
Docker does not provide `host.docker.internal` by default, hence the explicit mapping.

### 2.6 Persistent named volume

```yaml
volumes:
  postgres-data:
    name: jaldrishti-tb-postgres-data
```

A fixed volume name means `docker compose down` removes containers but **keeps the database**. This
is why you must not run the one-time installer again on an existing volume. There is also an old,
unused volume called `tb-postgres-data` from another project — Compose warns about it and leaves it
alone. Do not delete it without checking.

### 2.7 `run` versus `up`

```bash
docker compose up -d thingsboard-ce      # starts the permanent service
docker compose run --rm thingsboard-ce   # temporary container, deleted afterwards
```

The installer command `docker compose run --rm -e INSTALL_TB=true ...` is a **one-time job**. It
installs the schema into the shared volume and then deletes itself. It is not the service. Running
it when the database is already installed is unnecessary and can fail.

---

## 3. PEM files — what they are and why we used four

### 3.1 Plain-English explanation

PEM ("Privacy Enhanced Mail") is just a **text encoding** for certificates and keys. A PEM file is
plain ASCII that starts and ends with marker lines:

```
-----BEGIN CERTIFICATE-----
MIIDYzCCAkugAwIBAgIUZyfk9Lscne+EfnEL23MDTDaHsSswDQYJKoZIhvcNAQEL
...
-----END CERTIFICATE-----
```

Internally it is base64-encoded DER (a binary X.509 structure). It is called a "certificate" or
"key" depending on what is inside the markers.

TLS needs three ideas:

- **Certificate** — a digital ID card saying "this server is `localhost`", signed by a CA.
- **Private key** — the secret the server uses to prove it owns that ID card.
- **CA (Certificate Authority)** — the trusted signer. If your client already trusts the signer,
  the certificate is accepted.

When you connect with Python and the server uses a **public** CA (Let's Encrypt, or
`demo.thingsboard.io`), Python's built-in trust store already knows the signer, so you pass no
extra file. When you use your **own private CA**, Python cannot know it, so you must hand it the
CA's public certificate explicitly — that is the `--ca` argument.

### 3.2 The four files generated by `gen_certs.sh`

| File | What it is | Public or secret? | Where it goes |
|---|---|---|---|
| `ca.pem` | Public certificate of our private CA. Signed `server.pem`. | **Public** | Given to Python via `--ca`; used by `openssl -CAfile` |
| `ca_key.pem` | Private key of the CA; used once to sign the server certificate | **SECRET** — never publish | Stays on your machine; needed only to regenerate or add server certs |
| `server.pem` | The broker's own certificate | **Public** — sent to every client | Mounted into ThingsBoard at `/etc/thingsboard/certs/server.pem` |
| `server_key.pem` | The broker's private key | **SECRET** — never publish | Mounted read-only into ThingsBoard; `chmod 644` so the `thingsboard` user can read it |

The script also creates transient `server.csr`, `san.ext` and `ca.srl`, then deletes them.

### 3.3 Why the certificate has extra fields

On the CA:

```
basicConstraints=critical,CA:TRUE
keyUsage=critical,keyCertSign,cRLSign
```

On the server certificate:

```
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,DNS:thingsboard-ce,IP:127.0.0.1
```

- Python 3.13 enables strict X.509 verification; a CA certificate without `keyUsage=keyCertSign`
  is rejected.
- Modern mbedTLS on ESP32 also rejects such a CA.
- `subjectAltName` (SAN) is what modern clients actually check. The `CN` alone is ignored by Python.
  The SAN contains **both** names because host-side tests use `localhost` while edge containers use
  `thingsboard-ce`. If you add another broker hostname, regenerate the certificate with that name.

### 3.4 How to verify TLS — the proof commands

```bash
# See the certificate the broker presents
openssl s_client -connect 127.0.0.1:8883 -servername localhost -showcerts </dev/null

# Verify it against our CA — expect "Verify return code: 0 (ok)"
openssl s_client -connect 127.0.0.1:8883 -servername localhost \
  -CAfile certs/ca.pem </dev/null 2>&1 | grep 'Verify return code'
```

Note: `openssl s_client -connect localhost:8883` may try IPv6 `::1` first. Use `127.0.0.1` if you see
`unexpected eof while reading` from an IPv6 attempt.

### 3.5 Golden-image rule for certificates

Generate the PEMs **at setup time**, not at image build time. The Dockerfile must never `COPY` the
`certs/` directory. Mount `ca.pem` into each edge container and `server.pem`/`server_key.pem` into
ThingsBoard. If you publish the image, only the public `ca.pem` may appear in a documentation
example — and even that is unnecessary because it is generated locally.

---

## 4. Actual project layout and build state

This is the real tree in
`/home/sarun/Desktop/sem_9/IOT_Final_Assignment_Technical_Implementation/`
as of this milestone:

```
IOT_Final_Assignment_Technical_Implementation/
├── ROADMAP.md                     # this document: the single working file
├── Dockerfile                     # GOLDEN EDGE IMAGE (present)
├── .dockerignore                  # keeps secrets/artifacts out of the image
├── docker-entrypoint.sh           # turns env vars into simulator arguments
├── docker-compose.yaml            # postgres + thingsboard + edge profiles
├── requirements-runtime.txt       # paho-mqtt only — baked into the image
├── requirements-dev.txt           # requests, matplotlib, playwright, pytest — host only
├── certs/
│   ├── README.md
│   ├── gen_certs.sh
│   ├── ca.pem                     # public
│   ├── ca_key.pem                 # SECRET
│   ├── server.pem                 # public
│   └── server_key.pem             # SECRET
├── thingsboard/
│   └── docker-compose.yml         # teaching copy with the /etc/thingsboard/certs fix
├── config/
│   ├── nodes.json                 # global settings + control + bench nodes
│   ├── nodes/
│   │   ├── BID-01.json
│   │   ├── BID-02.json
│   │   ├── BID-03.json
│   │   ├── BID-04.json
│   │   ├── BID-05.json
│   │   ├── BID-06.json
│   │   ├── BID-07.json
│   │   ├── BID-08.json
│   │   ├── BID-09.json
│   │   └── BID-10.json
│   └── scenarios/
│       ├── smoke.json
│       └── demo.json
├── edge/                          # COMPLETE — 8 files, validated
│   ├── __init__.py
│   ├── nodeconfig.py              # hybrid loader: nodes.json + nodes/*.json
│   ├── scenario.py                # JSON timeline -> Event/Scenario
│   ├── schema.sql                 # SQLite schema for per-node buffers
│   ├── sensors.py                 # physics model + breach tags
│   ├── buffer.py                  # write-first SQLite store-and-forward
│   ├── link.py                    # MQTT/TLS QoS1 uplink + PUBACK marking
│   └── simulator.py               # CLI + sampler/link/scenario/console threads
├── tools/                         # BACKUP TOOLS COMPLETE
│   ├── __init__.py
│   ├── netgate.py                 # TCP relay: blackhole/refuse outage simulation
│   ├── tb_api.py                  # REST client (JWT login)
│   ├── tb_config.py               # generator -> thingsboard/*.json (files only)
│   ├── tb_provision.py            # devices/tokens by default; --install-* for backup backend
│   ├── buffer_view.py             # read-only SQLite buffer viewer (terminal + browser)
│   └── (optional) measure.py, sweep.py, webhook_receiver.py
├── .venv/                         # host Python env (ignored by Docker)
├── thingsboard/                   # generated templates (also baked into the image)
│   ├── dashboard.json             # 172 KB; contains ${DEFAULT_DEVICE_ID} placeholder
│   ├── rule_chain.json
│   ├── alarm_rules_cf.json, alarm_rules_legacy.json
│   ├── device_profile.json, device_profile_rig.json
│   ├── _widget_defaults/          # 9 small JSONs required by tb_config.py
│   └── docker-compose.yml         # teaching copy
├── results/tokens/                # mounted at /app/tokens; <tenant>.json per tenant
└── runs/                          # generated edge output (SQLite files)
```

### File-name decisions (fixed)

| Actual name | Why |
|---|---|
| `docker-compose.yaml` | Valid Compose filename; every command must use `-f docker-compose.yaml` |
| `requirements-runtime.txt` | The only requirements file copied into the image |
| `requirements-dev.txt` | Host-only tools; `-r requirements-runtime.txt` plus test/reporting packages |
| `certs/gen_certs.sh` | Certificate script lives beside the PEMs, not under `thingsboard/certs/` |
| `Dockerfile` (capital D) | Compose references `dockerfile: Dockerfile`; Linux is case-sensitive |

### VS Code YAML warning

VS Code may validate `docker-compose.yaml` against a **Kubernetes** schema and complain that
`services`, `volumes` and `x-tb-env` are "not allowed". That is the wrong schema. Add this first line
to the file:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/compose-spec/compose-spec/master/schema/compose-spec.json
```

The real source of truth is:

```bash
docker compose -f docker-compose.yaml config
```

---

## 4A. The `config/` folder — hybrid node registry and scenarios

### 4A.1 Design decision: options A + B combined

We chose a hybrid of the two add-node designs:

- **Option A — mount configuration at runtime.** Adding a node never requires rebuilding the image.
- **Option B — one config file per node.** Adding a node means dropping in one new file, not editing
  a shared list that BID-01/02/03 also depend on.

The governing rule is:

> **Bake code. Mount configuration. Mount secrets. Provision the platform over API.**

### 4A.2 File map

```
config/
├── nodes.json              # global: profile, control_nodes, bench_nodes
├── nodes/                  # one file per FIELD sensor
│   └── BID-XX.json
└── scenarios/
    ├── smoke.json          # 2-minute pipeline check
    └── demo.json           # 10-minute live demonstration
```

### 4A.3 `config/nodes.json` — the global file

This file deliberately **does not** contain the ten field sensors. They live in `config/nodes/`.
It contains only settings shared by everything:

| Key | Purpose |
|---|---|
| `_comment` | Human note explaining the split |
| `profile` | ThingsBoard device profile name used by provisioning |
| `control_nodes` | `JD-CTRL-01`, which has `store_forward: false` for the comparison study |
| `bench_nodes` | `JD-BENCH-01`, used for batch-size sweeps |

Control node:

```json
{
  "name": "JD-CTRL-01",
  "label": "Control node without store-and-forward",
  "lat": 27.90100,
  "lon": 85.15600,
  "seed": 101,
  "phase_s": 0.3,
  "store_forward": false,
  "baseline": {
    "ph0": 7.20, "turb0": 3.5, "tds0": 180,
    "temp_mean": 20.0, "flow0": 12.0, "rssi0": -86
  }
}
```

Bench node:

```json
{
  "name": "JD-BENCH-01",
  "label": "Batch-size sweep bench device",
  "lat": 27.90000,
  "lon": 85.15000,
  "seed": 102
}
```

> `store_forward: false` belongs **only** on the control node. Field nodes must not have it.

### 4A.4 The ten field sensors

Each file in `config/nodes/` is a complete node definition. Together they form this registry:

| File | name | label | lat | lon | seed | phase_s | ph0 | turb0 | tds0 | temp_mean | flow0 | rssi0 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `BID-01.json` | BID-01 | Bidur W-01 river-side intake | 27.89860 | 85.15940 | 1 | 0.0 | 7.25 | 4.2 | 185 | 20.5 | 13.5 | -84 |
| `BID-02.json` | BID-02 | Bidur W-02 river-side intake | 27.90430 | 85.15170 | 2 | 0.6 | 7.35 | 3.1 | 160 | 19.5 | 11.0 | -88 |
| `BID-03.json` | BID-03 | Bidur W-07 hill spring tank | 27.91500 | 85.14500 | 3 | 1.2 | 7.05 | 2.6 | 210 | 18.0 | 9.5 | -93 |
| `BID-04.json` | BID-04 | Bidur W-09 school tap | 27.92000 | 85.14000 | 4 | 1.8 | 7.20 | 3.8 | 175 | 20.0 | 12.0 | -87 |
| `BID-05.json` | BID-05 | Bidur W-11 lower irrigation channel | 27.89320 | 85.16410 | 5 | 2.4 | 7.15 | 5.0 | 195 | 21.0 | 14.0 | -82 |
| `BID-06.json` | BID-06 | Bidur W-13 clinic storage | 27.90910 | 85.14820 | 6 | 3.0 | 7.30 | 2.9 | 168 | 19.0 | 10.5 | -90 |
| `BID-07.json` | BID-07 | Bidur W-15 upper ridge tank | 27.92250 | 85.15330 | 7 | 3.6 | 6.95 | 2.2 | 220 | 17.5 | 8.8 | -96 |
| `BID-08.json` | BID-08 | Bidur W-17 riverside pump | 27.88740 | 85.15080 | 8 | 4.2 | 7.10 | 4.6 | 180 | 20.8 | 12.8 | -85 |
| `BID-09.json` | BID-09 | Bidur W-19 community tap | 27.91680 | 85.16190 | 9 | 4.8 | 7.40 | 3.4 | 155 | 19.8 | 11.5 | -89 |
| `BID-10.json` | BID-10 | Bidur W-21 temporary tank | 27.90170 | 85.13760 | 10 | 5.4 | 7.18 | 3.0 | 205 | 18.6 | 10.0 | -92 |

Field meanings:

| Field | Meaning |
|---|---|
| `name` | Must match the ThingsBoard device name and the key in `results/tokens.json` |
| `label` | Human-readable text for the dashboard |
| `lat`, `lon` | Map position in the Bidur pilot area |
| `seed` | Makes the synthetic sensor stream reproducible; must be unique |
| `phase_s` | Offsets this node's waveform from the others |
| `baseline` | Nominal pH, turbidity, TDS, temperature, flow and RSSI |

### 4A.5 Template and rules for adding a node

Base template:

```json
{
  "name": "BID-XX",
  "label": "Short human-readable location",
  "lat": 27.00000,
  "lon": 85.00000,
  "seed": 0,
  "phase_s": 0.0,
  "baseline": {
    "ph0": 7.20,
    "turb0": 4.0,
    "tds0": 180,
    "temp_mean": 20.0,
    "flow0": 12.0,
    "rssi0": -88
  }
}
```

Rules:

1. **Filename must equal `name`.** `BID-11.json` must contain `"name": "BID-11"`.
2. **Every `seed` must be unique**, otherwise two nodes produce identical synthetic streams.
3. **Every `phase_s` must differ**, so nodes do not rise and fall at the same instant.
4. **Coordinates must be close but not identical** — the pilot site is geographically clustered.
5. **Keep baselines physically plausible:** pH 6.9–7.5, turbidity 2–5 NTU, TDS 150–220 mg/L.
6. **Never put `store_forward: false` on a field node.**

### 4A.6 Scenario files

#### `config/scenarios/smoke.json` — 2-minute pipeline check

Global settings: `duration_s: 120`, `sample_period_s: 1.0`, `compression: 60`, `start_hour: 6.0`,
`seed: 3`, `settle_s: 40`.

| At (s) | Type | Target | Parameters | Purpose |
|---:|---|---|---|---|
| 10 | `rain` | BID-01 | `peak_ntu: 650`, `rise_min: 8`, `washout_min: 30` | Trigger a turbidity alarm |
| 40 | `outage` | all | `duration_s: 30`, `mode: blackhole` | Buffer unsynced rows |
| 85 | `bad_token` | BID-02 | `duration_s: 12` | Simulate auth failure |
| 100 | `outage` | all | `duration_s: 8`, `mode: refuse` | Simulate refused connection |

Screenshots: `01_normal` (8 s), `02_event` (28 s), `03_outage` (66 s), `04_after_backfill` (80 s).

#### `config/scenarios/demo.json` — full live demonstration

Global settings: `duration_s: 600`, `sample_period_s: 2.0`, `compression: 60`, `start_hour: 6.0`,
`seed: 7`, `settle_s: 90`.

| At (s) | Type | Target | Parameters | Purpose |
|---:|---|---|---|---|
| 40 | `mark` | — | `normal state: all nodes green` | Baseline screenshot |
| 60 | `rain` | BID-01 | `peak_ntu: 650`, `rise_min: 12`, `washout_min: 70` | Critical contamination |
| 75 | `rain` | BID-02 | `peak_ntu: 140`, `rise_min: 15`, `washout_min: 50` | Warning contamination |
| 200 | `interruption` | BID-03 | `duration_min: 60` | Flow forced to zero |
| 280 | `rssi` | BID-01 | `penalty_db: 18` | Degrade link margin |
| 290 | `outage` | all | `duration_s: 150`, `mode: blackhole` | Desync / buffer growth |
| 445 | `rssi` | BID-01 | `penalty_db: 0` | Restore link margin |
| 470 | `mark` | — | `backfill complete: charts gap-free` | Final screenshot |

`compression: 60` means 1 real second = 1 simulated minute. `duration_s: 600` is a 10-minute run
inside the 15-minute demo slot; the remaining time is for explanation and questions.

#### `config/scenarios/measure.json` — 24-minute metric run

Global settings: `duration_s: 1440`, `sample_period_s: 2.0`, `compression: 60`, `start_hour: 6.0`,
`seed: 11`, `settle_s: 120`. At 2 s per sample each node generates ~720 rows, comfortably above the
required 200. This is the only timeline that satisfies all mandatory measurement conditions.

| At (s) | Type | Target | Parameters | Purpose |
|---:|---|---|---|---|
| 60 | `rain` | BID-01 | `peak_ntu: 650`, `rise_min: 12`, `washout_min: 70` | First warning/critical alarm batch |
| 90 | `rain` | BID-02 | `peak_ntu: 140`, `rise_min: 15`, `washout_min: 50` | Warning-level contamination |
| 120 | `rain` | BID-03 | `peak_ntu: 320`, `rise_min: 10`, `washout_min: 60` | Third-source event |
| 400 | `interruption` | BID-02 | `duration_min: 60` | Supply-interruption alarm |
| 480 | `rain` | BID-02 | `peak_ntu: 720`, `rise_min: 12`, `washout_min: 60` | Second BID-02 event |
| 700 | `outage` | all | `duration_s: 180`, `mode: blackhole` | Required 180 s store-and-forward outage |
| 720 | `rain` | BID-03 | `peak_ntu: 250`, `rise_min: 10`, `washout_min: 40` | Breach *inside* the outage (buffered alarm) |
| 1000 | `rain` | BID-01 | `peak_ntu: 420`, `rise_min: 10`, `washout_min: 60` | Second BID-01 event |
| 1060 | `rain` | BID-03 | `peak_ntu: 660`, `rise_min: 12`, `washout_min: 60` | Third BID-03 event |
| 1200 | `interruption` | BID-01 | `duration_min: 60` | Second supply interruption |
| 1260 | `bad_token` | BID-02 | `duration_s: 40` | Auth-failure branch, then restore |

The `screenshots` array in this file records the useful capture moments (normal, event, interruption,
outage, backfill). No tool consumes it automatically; use those times as a manual shot list.

### 4A.7 How the three Docker files use `config/`

| File | Does it read config? | Detail |
|---|---|---|
| `Dockerfile` | Yes, by copying | `COPY config/ ./config/` places files under `/app/config/`. This bakes config into the image — see the gap below. |
| `docker-compose.yaml` | Indirectly | It sets `NODE: BID-01` etc. It does **not** currently mount `./config:/app/config:ro`. |
| `docker-entrypoint.sh` | Yes, by path | Defaults to `--scenario /app/config/scenarios/demo.json`; override with `SCENARIO=/app/config/scenarios/smoke.json`. |

Entrypoint environment variables:

| Variable | Effect |
|---|---|
| `NODE` | Final `--only` argument; picks one node from the config |
| `SCENARIO` | Scenario path inside the container |
| `MQTT_HOST`, `MQTT_PORT` | Broker address and TLS port |
| `WITH_CONTROL=1` | Adds `--with-control` so `JD-CTRL-01` from `nodes.json` is included |
| `CONSOLE=1` | Adds `--console` for live commands |
| `BATCH` | Adds `--batch` to override publish batch size |
| `ONLY_EXTRA` | Adds an extra `--only` before the final `NODE` |
| `RUN_ID` | Names the output directory under `/app/runs/` |

**This mount set is now present on every edge service** so config and tokens are mounted, not baked:

```yaml
environment:
  TB_TENANT: default
volumes:
  - ./config:/app/config:ro
  - ./certs/ca.pem:/app/ca.pem:ro
  - ./results/tokens:/app/tokens:ro
  - ./runs:/app/runs
```

A mounted `./config` overrides the baked `/app/config`, so adding a node file needs no rebuild.
This mount is now present on all ten edge services and was verified with
`docker compose -f docker-compose.yaml --profile edge config` (10 `/app/config` bind mounts).

Compose now has ten edge services: `edge-bid01` … `edge-bid09` plus `edge-ctrl`. Each mounts
`config`, `ca.pem`, `tokens.json` and `runs`. `config/nodes/BID-10.json` exists but has no service
yet — add `edge-bid10` only if you want all ten field nodes running (11 edge containers total).

### 4A.8 Implemented: the node-config loader

`edge/nodeconfig.py` is written. It reads `config/nodes.json`, scans `config/nodes/*.json`,
checks that `name` equals the filename, rejects duplicate names, and returns the global keys plus
a `nodes` list.

| Consumer | Status |
|---|---|
| `edge/simulator.py` | **Uses it** via `load_nodes(a.nodes)` when `--nodes` is a directory |
| `tools/tb_provision.py` | **Not written yet.** When adapted from the reference it must call `load_nodes("config")`, not read `config/nodes.json` directly, or only the old three field nodes are provisioned |

Verified:
`python3 -c "from edge.nodeconfig import load_nodes; print([n['name'] for n in load_nodes('config')['nodes']])"`
→ `['BID-01', …, 'BID-10']`.

### 4A.9 Validate the config folder

```bash
python -m json.tool config/nodes.json > /dev/null

for f in config/nodes/*.json config/scenarios/*.json; do
  python -m json.tool "$f" > /dev/null || exit 1
done
```

No output means every file is valid JSON. This was run at this milestone: all 13 files passed.

---

## 4B. What each folder does

| Folder | Type | Purpose | Goes into golden image? |
|---|---|---|---|
| `edge/` | Source code — **COMPLETE** | 8 files: package marker, hybrid loader, scenario parser, SQLite schema, physics, buffer, MQTT/TLS link, simulator loop | Yes |
| `tools/` | Source + host helpers — **COMPLETE** | `netgate.py`, `tb_api.py`, `tb_config.py`, `tb_provision.py`, `tb_ui.py`, `buffer_view.py`, `control_ui.py`, `measure.py` | Yes — `COPY tools/` bakes all of them in |
| `config/` | Configuration | Node registry and scenario timelines | Both: `COPY config/` in the image **and** a runtime bind mount (the mount wins) |
| `results/` | Generated host output | Tokens, provisioning output, harness ZIPs, metrics | No |
| `runs/` | Generated edge output | Per-node SQLite DBs, logs, CSVs | No |

Dependency flow:

```
config/  ------>  edge/  ------>  runs/
                    |
                    v
                 MQTT/TLS
                    |
                    v
               ThingsBoard

tools/  ------>  results/tokens.json  ------>  edge containers
```

A node is **addable** only if it exists in both places:

1. a file in `config/nodes/` (edge side), and
2. a ThingsBoard device with a token (cloud side).

### 4B.1 What creates and controls the JalDrishti dashboard

The dashboard is **not** created by `edge/`. It is a ThingsBoard artefact. Our code generates a
**backup template only** — nothing is installed unless you ask for it.

| Code | Effect |
|---|---|
| `tools/tb_config.py` | Pure file generator. Writes `dashboard.json` (status table, map, gauges, charts, alarm panel, two states), `rule_chain.json`, alarm rules and profiles into `thingsboard/`. No network calls. |
| `tools/tb_provision.py` | Default: profiles, devices, tokens, server attributes. Skips rule chain, alarm rules and dashboard unless `--install-rules`, `--install-alarms`, `--install-dashboard` (or `--install-all`). Writes `results/tokens/<tenant>.json`. |
| `tools/tb_api.py` | REST client used by provisioning. |

The edge side only supplies the telemetry keys those widgets read:

```text
ph, turbidity_ntu, tds_ppm, water_temp_c, flow_lpm,
latitude, longitude, rssi_dbm, buffer_depth
```

`sensors.py` produces the first eight; `link.py` adds `buffer_depth` at publish time. `wqi` and
`alm_*` are written by the ThingsBoard rule chain **if you install or create one**. If a telemetry
key changes, the corresponding dashboard widget goes blank.

> Two adaptations are already in the new script: it calls `edge.nodeconfig.load_nodes("config")`
> so `BID-04`…`BID-10` are provisioned, and when rules are skipped it removes `defaultRuleChainId`
> from the profiles so ThingsBoard routes telemetry through its Root rule chain.

---

## 5. The golden edge image

### 5.1 `.dockerignore`

```
.git
.venv
__pycache__/
*.pyc
*.pem
*_key.pem
tokens*.json
results/
runs/
evidence/
.pytest_cache/
*.md
.obsidian/
```

Yes, ignoring `*.md` is fine for the runtime image; put user documentation on Docker Hub instead.

### 5.2 `Dockerfile` (actual file at root)

```dockerfile
# syntax=docker/dockerfile:1

FROM python:3.13-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/build
COPY requirements-runtime.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements-runtime.txt

FROM python:3.13-slim
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 edge
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY edge/ ./edge/
COPY tools/ ./tools/
COPY config/ ./config/
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh
USER edge
ENTRYPOINT ["/app/docker-entrypoint.sh"]
```

Why these choices:

- **Two stages** — build tools and pip cache stay in the builder layer; the final image only carries
  the finished virtual environment.
- **`python:3.13-slim`** — matches the version the project was tested with, without a full OS.
- **Non-root `edge` user** — a container that only reads code and writes to a mounted `runs/` volume
  does not need root.
- **`PYTHONDONTWRITEBYTECODE`** — avoids writing `__pycache__` into mounted volumes.
- **No `COPY certs/` and no `COPY tokens*`** — the whole point of a publishable image.
- `requirements-dev.txt` is **not** copied; Playwright, pytest and screenshot tooling stay on the host.

> `COPY config/ ./config/` is still present. Keep the runtime mount from Section 4A.7 so node files
> remain editable without a rebuild. Removing that `COPY` line is optional polish, not a blocker.

### 5.3 `docker-entrypoint.sh` (actual file at root)

```sh
#!/bin/sh
# Translate environment variables into edge.simulator arguments.
set -eu

: "${NODE:?set NODE=BID-01 | BID-02 | BID-03 | JD-CTRL-01}"

HOST="${MQTT_HOST:-thingsboard-ce}"
PORT="${MQTT_PORT:-8883}"
OUT="/app/runs/${NODE}-${RUN_ID:-latest}"

set -- -m edge.simulator \
       --scenario "${SCENARIO:-/app/config/scenarios/demo.json}" \
       --tokens /app/tokens.json \
       --host "$HOST" --port "$PORT" \
       --out "$OUT"

[ -f /app/ca.pem ] && set -- "$@" --ca /app/ca.pem
[ "${WITH_CONTROL:-0}" = "1" ] && set -- "$@" --with-control
[ "${CONSOLE:-0}" = "1" ] && set -- "$@" --console
[ -n "${ONLY_EXTRA:-}" ] && set -- "$@" --only "$ONLY_EXTRA"
[ -n "${BATCH:-}" ] && set -- "$@" --batch "$BATCH"

# NODE is always the final --only so a single container owns one node.
set -- "$@" --only "$NODE"

exec python "$@"
```

It does **not** hardcode nodes into ThingsBoard. It only converts `NODE` into `--only NODE`.
ThingsBoard devices are created separately by `tools/tb_provision.py`.

> `JD-CTRL-01` lives in `config/nodes.json` under `control_nodes`, which the simulator includes only
> when `--with-control` is passed. To run it, set `NODE=JD-CTRL-01 WITH_CONTROL=1`.

### 5.4 Build and smoke-test locally — DONE

Built successfully on 2026-11-24 from the project root:

```bash
docker build -t jaldridhti-edge:1.0.0 .
.venv/bin/python -m edge.simulator --help
docker history --no-trunc jaldridhti-edge:1.0.0
docker run --rm --entrypoint find jaldridhti-edge:1.0.0 /app -maxdepth 2 -type f
```

Verified image contents: `edge/`, `tools/`, `config/`, `thingsboard/` templates and the entrypoint.
**No** `.pem`, no `tokens*.json`, no `results/`, no `runs/` and no `.venv`. The new
`COPY thingsboard/ ./thingsboard/` line ships the non-secret backup JSONs only.

A later live smoke test caught a real bug that a file listing could not: the entrypoint had **no
shebang**. `docker run --rm jaldridhti-edge:1.0.0` failed with
`exec /app/docker-entrypoint.sh: exec format error`. Added `#!/bin/sh` as line 1 of
`docker-entrypoint.sh`, rebuilt the image, and re-ran the same command; it now reaches the script and
prints the expected `NODE: set NODE=BID-01 .. BID-09 | JD-CTRL-01` message.

Build context is the **project root**. If the Dockerfile were ever moved under `docker/`, use
`docker build -f docker/Dockerfile -t jaldridhti-edge:1.0.0 .`.

---

## 6. Compose stack for the golden setup

The golden file is `docker-compose.yaml` at the project root. Keep
`thingsboard/docker-compose.yml` as the teaching artefact.

Tenant selection: every edge service now has `TB_TENANT: default` and mounts
`./results/tokens:/app/tokens:ro`. The entrypoint resolves
`/app/tokens/${TB_TENANT}.json`. Change `TB_TENANT` on any service to point that
container at another tenant; no image rebuild is needed. The mounted directory
is the only place tokens live — they are never baked into the image.

Key structure of the root file:

```yaml
name: jaldridhti

x-tb-env: &tb-env
  TB_SERVICE_ID: tb-ce-node
  TB_QUEUE_TYPE: in-memory
  SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/thingsboard
  SPRING_DATASOURCE_USERNAME: postgres
  SPRING_DATASOURCE_PASSWORD: postgres
  MQTT_SSL_ENABLED: "true"
  MQTT_SSL_BIND_PORT: "8883"
  MQTT_SSL_PROTOCOL: "TLSv1.2"
  MQTT_SSL_CREDENTIALS_TYPE: "PEM"
  MQTT_SSL_PEM_CERT: "/etc/thingsboard/certs/server.pem"
  MQTT_SSL_PEM_KEY: "/etc/thingsboard/certs/server_key.pem"
  MQTT_SSL_PEM_KEY_PASSWORD: ""
  DEFAULT_INACTIVITY_TIMEOUT: "600"
  DEFAULT_STATE_CHECK_INTERVAL: "5"

services:
  postgres:            # postgres:16 + named volume + healthcheck
  thingsboard-ce:      # tb-node:4.3.1.8, env: *tb-env, certs at /etc/thingsboard/certs
  cert-init:           # profile: init
  tb-init:             # profile: init, INSTALL_TB=true, LOAD_DEMO=true
  edge-bid01:          # profile: edge, NODE=BID-01
  edge-bid02:          # profile: edge, NODE=BID-02
  edge-bid03:          # profile: edge, NODE=BID-03
  edge-ctrl:           # profile: edge, NODE=JD-CTRL-01, WITH_CONTROL=1

volumes:
  tb-postgres:
    name: jaldrishti-tb-postgres-data
```

Notes:

- The edge containers reach the broker as **`thingsboard-ce`**, so the server certificate SAN must
  contain that DNS name. Our `certs/gen_certs.sh` includes both `localhost` and `thingsboard-ce`.
- `cert-init` runs `OUT_DIR=/work sh /gen_certs.sh localhost 127.0.0.1` from `certs/gen_certs.sh`.
- `cert-init` and `tb-init` are behind the `init` profile, so they never start accidentally.
- Ten edge services now exist: `edge-bid01`…`edge-bid09` plus `edge-ctrl`. `BID-10.json` has a config file but no service yet; add it only if you want 11 containers.
- Validate with `docker compose -f docker-compose.yaml config`.

---

## 7. From-zero boot sequence

### Step 0 — stop the old stack first

The older test stack is still running and owns ports 8080/1883/8883:

```bash
docker compose -f /home/sarun/Desktop/sem_9/sem_9/IOT/group_assignment/Assignment_Start_Three/thingsboard/docker-compose.yml down
```

This removes containers but **keeps** the PostgreSQL volume. Add `-v` only if you intentionally
want to destroy the old tenant, devices and telemetry.

### Step 1 — certificates

PEMs already exist. Skip generation unless you want fresh ones:

```bash
cd /home/sarun/Desktop/sem_9/IOT_Final_Assignment_Technical_Implementation
bash certs/gen_certs.sh localhost 127.0.0.1
ls -l certs
```

Expect `ca.pem`, `ca_key.pem`, `server.pem`, `server_key.pem`.

### Step 2 — start the platform

```bash
docker compose -f docker-compose.yaml config
docker compose -f docker-compose.yaml up -d postgres
docker compose -f docker-compose.yaml up -d thingsboard-ce
curl -sS -m 10 -o /dev/null -w 'UI HTTP %{http_code}\n' http://localhost:8080/
```

Expect `UI HTTP 200`. ThingsBoard needs about 1–2 minutes to become ready.

A **fresh** Postgres volume needs the one-time schema install:

```bash
docker compose -f docker-compose.yaml --profile init run --rm tb-init
```

If you reused `jaldrishti-tb-postgres-data`, skip it.

### Step 3 — verify TLS

```bash
openssl s_client -connect 127.0.0.1:8883 -servername localhost \
  -CAfile certs/ca.pem </dev/null 2>&1 | grep 'Verify return code'
```

Expect `Verify return code: 0 (ok)`.

### Step 4 — Python environment on the host — DONE

The host venv is already created and populated. Verified installed packages:
`paho-mqtt 2.2.0`, `requests 2.34.2`, `matplotlib 3.11.2`, `playwright 1.65.2`, `pytest 9.2.0`.

If you ever need to recreate it (Arch protects system Python, PEP 668 — never use bare `pip`):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

### Step 5 — provision devices/tokens (rules, alarms and UI left to you)

The tools are written now. The default run intentionally installs only the backend identities:

```bash
python -m tools.tb_provision --url http://localhost:8080 \
  --user tenant@thingsboard.org --password tenant \
  --out results/ --tenant default
```

Creates/updates: two device profiles, **all ten** devices with access tokens, and server attributes.
Writes `results/tokens/default.json` and `results/provision-default.json`. Idempotent.

Skips: rule chain, alarm rules and dashboard. To install the generated backend later, add
`--install-rules --install-alarms --install-dashboard` (or simply `--install-all`).

To import the dashboard manually: Dashboards → import `thingsboard/dashboard.json`, then set the
**Selected node** alias default entity to `BID-01` (the raw file keeps `${DEFAULT_DEVICE_ID}`).
You can also build your own dashboard from scratch — see §4B.1 for the telemetry keys.

> Provisioning calls `edge.nodeconfig.load_nodes("config")`, so `BID-04`…`BID-10` are included.

### Step 6 — prove the pipeline

```bash
python run_harness.py --tb-url http://localhost:8080 --mqtt-host localhost \
  --ca certs/ca.pem --quick --no-screens
```

This is the 6-minute check. It is not the product; it is the proof that the product works.

---

## 8. Adding the sensors, one at a time

### One sensor

```bash
python -m edge.simulator --scenario config/scenarios/smoke.json \
  --tokens results/tokens.json --host localhost --ca certs/ca.pem \
  --only BID-01 --out runs/one-node
```

Inspect its buffer:

```bash
sqlite3 runs/one-node/db/BID-01.db \
  "select count(*) total, sum(synced) synced, sum(synced=0) pending from telemetry_buffer;"
```

### Three field sensors

Same command, remove `--only`.

### Add the control node

Add `--with-control`. Compare delivery: buffered nodes reach 100%, the control node about 61%.

### Full demo

```bash
python -m edge.simulator --scenario config/scenarios/demo.json \
  --tokens results/tokens.json --host localhost --ca certs/ca.pem --console
python -m tools.buffer_view --db-dir runs --port 8765   # second terminal: browser viewer
# or: python -m tools.buffer_view --db-dir runs --once   # one-shot terminal table
```

Using the golden image instead of host Python:

```bash
docker compose -f docker-compose.yaml --profile edge up -d edge-bid01
docker compose -f docker-compose.yaml --profile edge logs -f edge-bid01
```

---

## 8A. Adding new nodes after the platform is running — **MUST BE POSSIBLE**

This is a core requirement of the golden setup. The image is a **baseline**, not a fixed three-sensor
system. You must be able to add a fourth, fifth or tenth node without rebuilding ThingsBoard,
restarting ThingsBoard, or editing code inside a running container.

### 8A.1 The two-layer rule

There are **two different things** that people casually call "adding a node":

| Layer | What it means | Where it happens |
|---|---|---|
| **Cloud registration** | Create a ThingsBoard device entity and access token | ThingsBoard dashboard or REST API |
| **Edge instance** | Add the node config file and start a container for it | `config/nodes/BID-XX.json`, `results/tokens.json`, Docker |

Adding a device in the ThingsBoard dashboard creates only the **cloud registration**. It does not
generate telemetry by itself. Both sides must match:

```text
ThingsBoard device BID-11
        +
token in results/tokens.json
        +
config/nodes/BID-11.json
        +
running edge container with NODE=BID-11
```

If the cloud side is missing, MQTT authentication fails. If the edge side is missing, the
ThingsBoard device stays empty.

### 8A.2 Golden-image rule that makes this possible

> **Bake code. Mount configuration. Mount secrets. Provision the platform over API.**

| Item | Where it belongs | Rebuild image to change it? |
|---|---|---|
| `edge/` simulator code (8 files) | Baked into the golden image | Yes |
| `tools/netgate.py` | Baked into the golden image (still pending) | Yes |
| `config/nodes.json` | Mounted at runtime | **No** |
| `config/nodes/*.json` | Mounted at runtime | **No** |
| `config/scenarios/` | Mounted at runtime | **No** |
| `certs/ca.pem` | Mounted at runtime | **No** |
| `results/tokens.json` | Mounted at runtime | **No** |
| PostgreSQL data | Docker named volume | No |

See Section 4A.7 for the required edge-service volume block. If `config/` is only baked into the
image, adding a node requires a rebuild — avoid that.

### 8A.3 Method A — recommended, repeatable provisioning

**Step 1 — create `config/nodes/BID-11.json`** from the template in Section 4A.5. Use a unique name,
seed, phase and coordinates.

**Step 2 — re-run provisioning**

```bash
python -m tools.tb_provision \
  --url http://localhost:8080 \
  --user tenant@thingsboard.org \
  --password tenant \
  --out results/
```

This is idempotent: it creates the new device plus token and leaves existing devices intact.
`results/tokens.json` then contains a `BID-11` key.

**Step 3 — start one container for the new node**

```bash
docker run --rm \
  -e NODE=BID-11 \
  -e TB_TENANT=default \
  -e MQTT_HOST=thingsboard-ce \
  -v "$PWD/config:/app/config:ro" \
  -v "$PWD/certs/ca.pem:/app/ca.pem:ro" \
  -v "$PWD/results/tokens:/app/tokens:ro" \
  -v "$PWD/runs:/app/runs" \
  jaldridhti-edge:1.0.0
```

No image rebuild. No ThingsBoard restart. No change to existing containers.

If you want it permanent under Compose, add a service block:

```yaml
  edge-bid11:
    image: jaldridhti-edge:1.0.0
    profiles: ["edge"]
    restart: unless-stopped
    depends_on:
      - thingsboard-ce
    environment:
      NODE: BID-11
      TB_TENANT: default
      MQTT_HOST: thingsboard-ce
      MQTT_PORT: "8883"
      RUN_ID: compose
    volumes:
      - ./config:/app/config:ro
      - ./certs/ca.pem:/app/ca.pem:ro
      - ./results/tokens:/app/tokens:ro
      - ./runs:/app/runs
```

Then:

```bash
docker compose -f docker-compose.yaml --profile edge up -d edge-bid11
```

### 8A.4 Method B — manual ThingsBoard dashboard

Yes, you can add the device from the ThingsBoard dashboard while the stack is running.

1. Open **http://localhost:8080**.
2. Log in as `tenant@thingsboard.org` / `tenant`.
3. Go to **Devices** → **+ Add device**.
4. Name it exactly, e.g. `BID-11`.
5. Assign it to the device profile **JalDrishti Water Node**. This is critical — a device in the
   wrong profile will not inherit the correct rule chain, alarm rules or dashboard binding.
6. Open the new device → **Manage credentials**.
7. Choose **Access token** and set/copy the token.
8. Add server attributes:

   ```text
   latitude = 27.92000
   longitude = 85.14000
   inactivityTimeout = 60000
   alm_turbidity_warn = NONE
   alm_turbidity_crit = NONE
   alm_ph = NONE
   alm_tds = NONE
   alm_flow = NONE
   alm_inactive = NONE
   ```

9. Put the token into `results/tokens.json` under the key `BID-11`.
10. Create `config/nodes/BID-11.json`.
11. Start the edge container as in Method A, Step 3.

The dashboard does **not** do steps 9–11. It only creates the cloud identity.

### 8A.5 Do not use Compose `--scale`

Do **not** add nodes like this:

```bash
docker compose up --scale edge-bid01=3
```

That creates three containers all using `NODE=BID-01`, the same token and the same ThingsBoard
device. That is three copies of one sensor, not three sensors.

### 8A.6 Token and config update behaviour

| Change | Existing containers | Action needed |
|---|---|---|
| Add a new device/token | Unaffected | Start a new container for the new `NODE` |
| Add a new file in `config/nodes/` | Unaffected | New container reads it at startup |
| Change an existing token | Existing container keeps old token in memory | Restart that node's container |
| Change a scenario file | Existing container keeps old scenario | Restart that node's container |
| Change simulator Python code | Running containers keep old code | Rebuild image and recreate containers |

### 8A.7 Verification checklist for a newly added node

```bash
# 1. Cloud device exists
curl -sS -H "X-Authorization: Bearer $TB_TOKEN" \
  "http://localhost:8080/api/tenant/devices?deviceName=BID-11&pageSize=1&page=0"
```

```bash
# 2. Edge container is running with the right NODE
docker ps --filter name=edge-bid11
```

```bash
# 3. Its SQLite buffer was created
sqlite3 runs/BID-11-compose/db/BID-11.db \
  "select count(*) total, sum(synced) synced, sum(synced=0) pending from telemetry_buffer;"
```

```bash
# 4. Dashboard map shows the new marker
# Open http://localhost:8080/dashboards/<dashboard-id> and check BID-11
```

Success means:

```text
ThingsBoard device visible
MQTT connects with that token
SQLite file grows
Dashboard marker/live chart updates
No existing node restarts
No image rebuild
```

### 8A.8 What to say to the teacher

> "The golden image is a baseline edge-node application, not a fixed three-node system. ThingsBoard
> can accept new devices while it is running. To add a node, we drop a file into the mounted node
> registry, create its ThingsBoard device and token through provisioning or the dashboard, then start
> one more container from the same image with `NODE=BID-11`. No image rebuild and no platform restart
> are required. We do not use Compose scaling because that would duplicate one device token instead of
> creating distinct sensors."

---

## 8B. Tenants — where the devices live

### Can devices stay in `tenant@thingsboard.org`?

Yes. That is the default tenant and it already owns devices, rules and a dashboard after
provisioning. Nothing about the simulator forces a particular tenant.

### Can we create a new tenant and send data to it?

Yes. Devices belong to a tenant. The MQTT token identifies the device, and ThingsBoard decides
which tenant owns that device. The simulator sends only:

```text
MQTT username = device access token
MQTT password = empty
```

It never sends a tenant name. Therefore **the token file decides the destination tenant**.

### How to move to a new tenant

1. Log in as system administrator: `sysadmin@thingsboard.org` / `sysadmin`.
2. Go to **Tenants** and create a new tenant.
3. Record that tenant's administrator email and password.
4. Log in as that tenant administrator.
5. Provision with the new credentials:

   ```bash
   python -m tools.tb_provision \
     --url http://localhost:8080 \
     --user YOUR_NEW_TENANT_EMAIL \
     --password YOUR_NEW_TENANT_PASSWORD \
     --out results/
   ```

6. This replaces `results/tokens.json` with tokens belonging to the new tenant.
7. Recreate the edge containers so they load the new tokens:

   ```bash
   docker compose -f docker-compose.yaml --profile edge down
   docker compose -f docker-compose.yaml --profile edge up -d edge-bid01 edge-bid02 edge-bid03
   ```

> If you create a new tenant but keep the old `results/tokens.json`, data keeps going to the **old**
> tenant.

### Two tenants at the same time

Yes — that is now a first-class setting. Provision each tenant with its own slug:

```bash
python -m tools.tb_provision --user TENANT_A_EMAIL --password ... --out results/ --tenant tenant-a
python -m tools.tb_provision --user TENANT_B_EMAIL --password ... --out results/ --tenant tenant-b
```

This writes `results/tokens/tenant-a.json` and `results/tokens/tenant-b.json`. Every edge service
mounts the whole `results/tokens` directory, so you only change its `TB_TENANT` value:

```yaml
environment:
  TB_TENANT: tenant-b
```

No rebuild, and no token files inside the image. One Compose service set can drive two tenants at
once by giving each service block the appropriate `TB_TENANT`.

### What each tenant gets

Each tenant gets its own devices, rule chain, alarm rules, dashboard, tokens and telemetry. Creating
a new tenant is clean and does not disturb the old one.

### Recommended path for this rebuild

1. Stop the old stack **without** `-v` first.
2. Start the new stack with the existing volume.
3. Log in as `sysadmin@thingsboard.org` and create the new tenant.
4. Provision with that tenant's credentials.
5. Start edge containers.
6. Verify the new tenant dashboard receives data.

---

## 9. The offline / resync check (the "desync and sync" test)

This is the heart of the assignment. Use `--console` and type:

```text
status
outage 60
status
restore
```

During the outage you should see:

- `LINK ONLINE -> BACKOFF (network: ...)`;
- `BUFFER depth=...` warnings;
- `buffer_view` **PENDING** counts rising while `synced=0` rows accumulate.

After `restore`:

- `LINK BACKOFF -> CONNECTING -> ONLINE`;
- `FLUSH batch=25 rows=25 ... depth 75->50`;
- ThingsBoard charts backfill the gap with the **original timestamps**.

Verify in SQLite:

```bash
sqlite3 runs/demo-<stamp>/db/BID-01.db \
  "select synced, count(*) from telemetry_buffer group by synced;"
sqlite3 runs/demo-<stamp>/db/BID-01.db \
  "select tier, rows, depth_after from eviction_log order by id desc limit 10;"
```

Why this works: `edge/schema.sql` sets WAL mode so the sampler and uplink threads do not block each
other, and `synchronous=FULL` so a committed row survives power loss. Eviction at the 20,000-row cap
deletes already-synced rows first, then old unsynced normal rows, and only last of all old unsynced
breach rows.

### 9.1 Live SQLite inspection and disabling one node (BID-03)

`runs/` is bind-mounted into every edge container, so the SQLite files are readable on the host
while the simulator runs. The image runs as UID/GID `10001`, so the host directory must be writable
by that UID **before the first start**:

```bash
docker run --rm -v "$PWD/runs:/app/runs" --entrypoint sh python:3.13-slim \
  -c "chown -R 10001:10001 /app/runs"
```

Without this the simulator dies at `os.makedirs('/app/runs/<NODE>-compose/db')` with
`PermissionError: [Errno 13] Permission denied` and the container restart-loops. Files created this
way are still readable by the host user for inspection.

With `RUN_ID: compose`, node 3's database is:

```text
runs/BID-03-compose/db/BID-03.db
runs/BID-03-compose/samples.csv          # one row per generated second, easiest to tail
runs/BID-03-compose/events.jsonl
```

```bash
DB=runs/BID-03-compose/db/BID-03.db
sqlite3 "$DB" ".tables"
sqlite3 "$DB" "select seq, datetime(ts_ms/1000,'unixepoch') as t, breach, synced, attempts, payload from telemetry_buffer order by seq desc limit 10;"
sqlite3 "$DB" "select synced, count(*) from telemetry_buffer group by synced;"
sqlite3 "$DB" "select tier, rows, depth_after from eviction_log order by id desc limit 5;"
tail -f runs/BID-03-compose/samples.csv
```

`write-first` means every row appears with `synced=0` immediately; it flips to `synced=1` only when
the MQTT QoS 1 PUBACK for its batch arrives. During an outage you watch `synced=0` pile up.

To cut **only BID-03's network** while it keeps sampling (blackhole through its own netgate):

```bash
docker compose -f docker-compose.yaml exec edge-bid03 \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18999/down?mode=blackhole').read().decode())"
# watch the buffer grow in SQLite:
sqlite3 "$DB" "select count(*) from telemetry_buffer where synced=0;"

# or watch it live in a browser (second terminal):
python -m tools.buffer_view --db-dir runs --port 8765
# open http://localhost:8765 and keep the BID-03 card visible

docker compose -f docker-compose.yaml exec edge-bid03 \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18999/up').read().decode())"
```

To stop the whole node instead: `docker compose -f docker-compose.yaml stop edge-bid03`. The buffer
file stays intact; `docker compose ... start edge-bid03` replays it with the original timestamps.

ThingsBoard cannot pause an edge sampler — it only sees missing telemetry and marks the device
inactive after `inactivityTimeout` (60 s in the demo profile). Use the netgate or Compose stop for
outage tests.

---

## 9A. Getting the real ThingsBoard measurements

This is the procedure that fixes the evidence gap. The manuscript's Table IX must come from a
**real `measure.json` run against this ThingsBoard instance**, not from the 2-minute smoke run in the
old assignment folder (that run had only 4 alarms and a 38 s outage).

### Why a single process, not the Compose containers

Each Compose edge container runs with `--only <NODE>` and writes to `runs/<NODE>-compose/`.
`tools/measure.py` needs **one** run directory containing `summary.json`, `events.jsonl`,
`samples.csv` and `db/<NODE>.db` for every pilot node. So the measurement run is launched once on the
host with `--only BID-01,BID-02,BID-03`, which produces exactly that combined directory.

### Step 1 — stop the Compose edge containers

They publish to the same ThingsBoard devices and would corrupt the measurement with interleaved
timestamps. The platform stays up.

```bash
cd /home/sarun/Desktop/sem_9/IOT_Final_Assignment_Technical_Implementation
docker compose -f docker-compose.yaml stop edge-bid01 edge-bid02 edge-bid03 edge-bid04
```

### Step 2 — run the 24-minute measurement

`runs/` is owned by UID 10001 so the containers can write their bind mounts. The host user cannot
plainly `mkdir` inside it. Create the run directory as root in a throwaway container and hand it to
the host user first:

```bash
RUN="runs/measure-$(date +%Y%m%d-%H%M%S)"
SUB="${RUN#runs/}"
docker run --rm -u root --entrypoint sh -v "$PWD/runs:/runs" jaldridhti-edge:1.0.0 \
  -c "mkdir -p /runs/$SUB && chown 1000:1000 /runs/$SUB && \
      printf '%s\n' '$RUN' > /runs/.last-measure && chown 1000:1000 /runs/.last-measure"
```

Then run the simulator on the host (24 minutes, so use a terminal that stays open, or `nohup`):

```bash
RUN="$(cat runs/.last-measure)"
.venv/bin/python -m edge.simulator \
  --scenario config/scenarios/measure.json \
  --tokens results/tokens/default.json \
  --only BID-01,BID-02,BID-03 \
  --host localhost --port 8883 --ca certs/ca.pem \
  --out "$RUN" 2>&1 | tee "$RUN/console.log"
```

`--host localhost --port 8883 --ca certs/ca.pem` is the host-side TLS publish path exposed by
`thingsboard-ce`. The one process owns the netgate, so the 180 s blackhole applies to all three nodes
at once. Let it finish (~24 minutes, plus up to 120 s of buffer drain). Do **not** stop it early:
the final summary is written only after the drain loop ends.

Useful manual screenshot moments are listed in the `screenshots` array of `measure.json`
(45 s normal, 140 s event, 445 s interruption, 845 s outage, 905 s backfill).

### Step 3 — compute the six metrics from ThingsBoard

```bash
RUN="$(cat runs/.last-measure)"
.venv/bin/python -m tools.measure \
  --run "$RUN" \
  --tb-url http://localhost:8080 \
  --user tenant@thingsboard.org --password tenant \
  --tokens results/tokens/default.json \
  --out results/measure-metrics
```

`tools/measure.py` reads:

| Source | What it supplies |
|---|---|
| `samples.csv` | Every generated sample and its original `ts_ms` |
| `events.jsonl` | `start`, `scenario` outage/restore, `link` transitions and `flush` rows |
| `db/<NODE>.db` | `first_send_ms`, `synced_ms`, `attempts` (PUBLISH/PUBACK bounds) |
| ThingsBoard timeseries | `rx_ms` written by the rule chain → server-stamped latency, delivery |
| ThingsBoard alarms | `createdTime`, `startTs`, `clearTs` → alarm activation delay |

Outputs in `results/measure-metrics/`:

| File | Contents |
|---|---|
| `metrics.json` | All six metrics, machine-readable |
| `metrics.md` | Human-readable summary — paste the numbers into the manuscript |
| `latency.csv` | One row per generated sample with latency and PUBLISH/PUBACK bounds |
| `alarms.csv` | One row per alarm with breach time, delay and live/buffered flag |

The metrics are: live latency (`mean`/`median`/`P95`/`max`), delivery ratio with outage, alarm
activation delay, flush throughput, recovery time, and data loss. Backfilled samples are reported
separately as `backfilled_age_at_receipt` — that is queue age, **not** network latency, and must be
labelled as such in the manuscript. A control node without store-and-forward is excluded from the
pilot ratio and shown separately as `control_ratio_no_store_forward`.

`tools/measure.py` is repeatable: run it again after any run with a different `--run` / `--out` pair.
It only reads the run directory and ThingsBoard; it never changes telemetry or alarms.

**View in the simulator control window.** The read-only **Metrics** tab at
http://localhost:8766/metrics displays `results/measure-metrics/metrics.json` on every reload. Navigate
from the **Metrics** link in the header of http://localhost:8766. If `control_ui.py` was already
running before this tab was added, restart it:

```bash
kill <old-pid>
.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766
```

The tab only reads the JSON file; all simulator control still writes exclusively to
`runs/control/<NODE>.json`.

### Step 4 — accept the run only if all of these hold

```bash
cat results/measure-metrics/metrics.md
.venv/bin/python - <<'PY'
import json
m = json.load(open('results/measure-metrics/metrics.json'))
print('latency n        :', m['latency_ms']['server_stamped_live']['n'])
print('pilot delivered  :', m['delivery']['pilot_delivered'], '/', m['delivery']['pilot_generated'])
print('outage s         :', m['delivery']['outage_s'])
print('alarm n (live)   :', m['alarm_delay_ms']['live_threshold_alarms']['n'])
print('pilot lost rows  :', m['data_loss']['pilot_lost'])
print('recovery events  :', len(m['recovery']))
PY
```

| Check | Required |
|---|---|
| Live latency samples | ≥ 200 |
| Delivery with outage | all pilot rows present on ThingsBoard after full sync |
| Alarm delay events | ≥ 10 |
| Outage length | 180 s |
| Recovery events | ≥ 1, with non-zero `rows_flushed` |
| Pilot data loss | 0 |

If the alarm count is below 10, the event list in `measure.json` needs more rain events — do not
substitute the smoke-run numbers. If delivery is below 100 %, check `pending_at_end` and
`evicted_unsynced` before blaming the network.

### Step 5 — restore the demo stack (optional, but do it before the live demo)

```bash
docker compose -f docker-compose.yaml start edge-bid01 edge-bid02 edge-bid03 edge-bid04
```

This is **not** needed to produce or keep the measurements — the evidence is already in
`runs/measure-*` and `results/measure-metrics/`. It is needed only because the four containers are
the live demonstration setup: restarting them makes the dashboard live again, restores the
`runs/<NODE>-compose/` buffer viewers, and returns ThingsBoard devices to `active`. If you are done
with live demos, leaving them stopped is harmless. Do not run a measurement and the Compose nodes at
the same time, or the same devices receive two interleaved streams.

### Results obtained — 2026-11-26

Run `runs/measure-20261126-000140`, source `thingsboard`, computed into `results/measure-metrics/`.
These are the numbers to use in the manuscript's Table IX and results section.

| Metric | Value |
|---|---|
| Live latency (`rx_ms - ts`) | n=1847, mean 30.1 ms, median 30.0 ms, P95 45.0 ms, max 736.0 ms |
| PUBLISH / PUBACK bound (live) | mean 10.7 / 17.7 ms |
| Delivery with outage | 2160 / 2160 = 100.00 %, PUBACK-confirmed 100 % |
| Outage | 180 s blackhole, 1 restore |
| Alarm activation delay (live) | n=21, mean 550.3 ms, median 437.0 ms, max 1082.0 ms |
| Buffered-breach alarm delay | n=2, mean 185054.5 ms (queue age, not network latency) |
| Node Inactive detection | n=6, mean 33561.0 ms |
| Flush throughput | mean 1074.5 rows/s; per node 1151.9 / 1042.1 / 1029.4 rows/s |
| Recovery from restore | BID-01 1.37 s (91 rows), BID-02 16.84 s (99 rows), BID-03 29.91 s (105 rows) |
| Pilot data loss | 0 rows (0.000 %), 0 evicted unsynced, 0 pending at end |

Acceptance checks, all PASS:

```text
live latency n >= 200         : PASS (1847)
pilot delivery == generated   : PASS (2160/2160)
outage == 180 s               : PASS
alarm events >= 10            : PASS (21)
recovery events >= 1          : PASS (3, all with rows flushed)
pilot data loss == 0          : PASS
```

Run provenance: `scenario=measure`, `t0=1795630605859`, `end=1795632045903`, 7 rain events,
2 interruptions, 1 bad-token event, 1859 flush records, 3 pilot nodes (`BID-01`…`BID-03`).
The generated evidence is `metrics.json`, `metrics.md`, `latency.csv` and `alarms.csv` in
`results/measure-metrics/`; the raw run is `runs/measure-20261126-000140/`. The read-only
**Metrics** tab at `http://localhost:8766/metrics` displays the summary on reload.

The manuscript must state **three pilot nodes** for this run. The fourth live device (`BID-04`) was
deliberately excluded from the measurement process; it is part of the demo stack, not Table IX.

### Pre-validation is a different experiment

Any numbers from a local Mosquitto sink (`--sink ...`) are **pre-validation**, not ThingsBoard
evidence. Keep them in a separate figure/table and label them clearly. The mandatory Table IX uses
only ThingsBoard results from Steps 1–4. The old `20261123-075815` result is a smoke run and is not
a substitute for this one.

### Clock offset

`rx_ms` is stamped by the ThingsBoard rule chain and `ts_ms` by the edge, both on the same host, so
`--clock-offset-ms` defaults to 0. If you ever run edge and server on different machines, measure
the offset with `TB.server_time_offset_ms()` and pass it explicitly; otherwise latency is inflated.

---

## 10. Alarms — why they looked missing

They were never missing. A live query showed 18 alarm records: `High Turbidity`,
`Gross Contamination`, `pH Out of Range`, `High TDS`, `Supply Interruption`, `Node Inactive`.

Three reasons they seemed absent:

1. **Names differ.** The dashboard widget maps `CRITICAL → UNSAFE - Critical`,
   `WARNING → Caution`, `NONE → Safe`. ThingsBoard itself stores the engineering names above.
2. **30-second clear dwell.** Every alarm clears 30 s after recovery. After the rain washes out,
   all `alm_*` attributes return to `NONE`, so the dashboard looks calm.
3. **Timing.** Alarms must be watched *during* the event, not after it.

Force an alarm live:

```text
rain BID-02 650 5 180
```

then open the dashboard and the Alarms panel immediately. Verify by API:

```bash
.venv/bin/python - <<'PY'
import json,urllib.request
B='http://localhost:8080'
t=json.load(urllib.request.urlopen(urllib.request.Request(B+'/api/auth/login',
  data=b'{"username":"tenant@thingsboard.org","password":"tenant"}',
  headers={'Content-Type':'application/json'})))['token']
r=urllib.request.urlopen(urllib.request.Request(B+'/api/alarms?pageSize=20&page=0',
  headers={'X-Authorization':'Bearer '+t}))
for a in json.load(r)['data']: print(a['type'],a['severity'],a['status'])
PY
```

Live proof from Milestone 7: a single TLS MQTT sample with `turbidity_ntu=260` on BID-01 produced
`alm_turbidity_crit=CRITICAL`, `wqi≈512`, and ACTIVE_UNACK alarms `Gross Contamination` and
`High Turbidity`. This confirms the calculated-field rules, the `JalDrishti Ingest` profile binding
and the alarm-mirroring TBEL node all work end to end.

---

## 11. Publishing the golden image to Docker Hub

```bash
docker login
docker tag jaldridhti-edge:1.0.0 <your-dockerhub-user>/jaldridhti-edge:1.0.0
docker push <your-dockerhub-user>/jaldridhti-edge:1.0.0
```

Pre-publish hygiene checklist:

- [ ] `docker history` shows no cert or token files
- [ ] `.dockerignore` contains `*.pem`, `tokens*.json`, `results/`, `runs/`, `evidence/`, `.venv`
- [ ] Image runs as non-root `edge` user (UID 10001)
- [ ] `docker run --rm jaldridhti-edge:1.0.0` reaches the script (prints the `NODE` error), not `exec format error`
- [ ] Version tag is explicit (`1.0.0`), not `latest` alone
- [ ] Docker Hub description explains required mounts: `ca.pem`, `tokens/<tenant>.json`, `config/`, `runs/`
- [ ] Host `runs/` is writable by UID 10001 before the first edge start
- [ ] Docker Hub description states that ThingsBoard must be provisioned first

Anyone can then reproduce the setup with:

```bash
docker compose -f docker-compose.yaml --profile init run --rm cert-init
docker compose -f docker-compose.yaml up -d
python -m tools.tb_provision ...
docker compose -f docker-compose.yaml --profile edge up -d
```

---

## 12. Troubleshooting table — every problem we actually hit

| Symptom | Cause | Fix |
|---|---|---|
| `externally-managed-environment` on `pip install` | Arch protects system Python (PEP 668) | Create `.venv`, activate it, use `python` not `python3` |
| Harness: `No such file or directory` for `ca.pem` | Certificate generation step skipped | Run `bash certs/gen_certs.sh localhost 127.0.0.1` |
| Container restart loop, `/config/logback.xml` missing | certs mounted under `/config` created that directory | Mount at `/etc/thingsboard/certs` instead |
| Installer: `Not valid working directory` | Same `/config` bug meant `install.data_dir` was never set | Fixed by the mount change; then re-run installer once |
| `relation "queue" does not exist` | Empty database — install never completed | Run `tb-init` once on the fresh volume |
| UI HTTP 000 / `unexpected eof` on 8883 | Checked 3 seconds after `up -d`; ThingsBoard needs ~43 s | Wait 1–2 min, watch `docker compose logs -f` |
| `openssl` connects to `::1` then EOF | IPv6 attempted first | Use `127.0.0.1` explicitly |
| Sweep reports large "lost" counts | REST read immediately after PUBACK; TB writes asynchronously | `tools/sweep.py` polls until count stabilises; `endTs` includes last row |
| Dashboard shows all nodes "Safe" | Alarms cleared after 30 s dwell | Trigger `rain BID-02 650 5 180` and look immediately |
| Hostname verification fails in container | Certificate SAN lacks the compose service name | SAN now includes `thingsboard-ce`; regenerate if hostname changes |
| Edit container then wonder why nothing changed | TLS settings are fixed at container creation | `docker compose -f docker-compose.yaml up -d --force-recreate thingsboard-ce` |
| VS Code says `services`/`volumes` not allowed | Editor used the Kubernetes YAML schema | Add the Compose schema modeline from Section 4 |
| New node file ignored by image | `config/` baked in and no runtime mount | Add `./config:/app/config:ro` to edge services |
| `exec /app/docker-entrypoint.sh: exec format error` | Entry script had no `#!/bin/sh`; Docker execve got `ENOEXEC` | Add shebang as line 1, rebuild image, force-recreate containers |
| `PermissionError: '/app/runs/BID-0X-compose'` | Host `runs/` owned by UID 1000, container writes as UID 10001 | `chown -R 10001:10001 runs`, then force-recreate edge services |
| BID-03 absent from alias / widget | Device was named `BID03` and sat on profile `default` | Rename to `BID-03` and assign `JalDrishti Water Node` |
| Dashboard widget shows `null` values | REST returns null placeholders for keys with no telemetry | Query `/values/timeseries` with no `keys` to see real rows; check edge logs first |
| `Selected node` widgets stay blank after import | Default entity was set to the device **access token** instead of its UUID | Use `resolve_default_device_id()` and re-run `python -m tools.tb_ui` |

---

## 13. Evidence and verification checklist

For every claim you make to the teacher, have a command:

| Claim | Proof |
|---|---|
| TLS works | `openssl s_client ... -CAfile certs/ca.pem` → `Verify return code: 0 (ok)` |
| Pipeline works | quick harness ZIP with 0 data loss |
| Write-first buffer works | `python -m tools.buffer_view --db-dir runs --once` showing `PENDING > 0` during outage, or SQLite query showing `synced=0` |
| PUBACK-only marking works | SQLite `synced` flips after restore |
| Backfill works | ThingsBoard chart with original timestamps |
| Alarms work | `/api/alarms` output during event |
| Golden image is clean | `docker history` + successful `docker run --help` with no secrets |
| New nodes can be added | config file + token + container, with no existing node restarting |
| Six mandatory metrics | `results/measure-metrics/metrics.md` from the real `measure.json` run (section 9A) |
| ≥200 latency samples, ≥10 alarms | 1847 live samples, 21 live alarm delays (section 9A, “Results obtained”) |
| 180 s outage and recovery | `delivery.outage_s == 180`, 3 recovery events with 91/99/105 rows replayed |
| Zero pilot data loss | `data_loss.pilot_lost == 0` (2160/2160 delivered) |
| Metrics visible in 8766 | reload `http://localhost:8766/metrics` — shows run ID and all six metrics |

Submit these two artefacts separately:

- `GroupNo_Assessment-03_Implementation.zip`
- `GroupNo_Assessment-03_Manuscript.pdf`

---

## 14. What to say to the teacher, in order

1. "I generated the CA and server certificates and verified the TLS handshake myself."
2. "I built the edge image with a multi-stage Dockerfile; no keys or tokens are in it."
3. "I started the platform, provisioned ThingsBoard through its REST API and checked the TBEL scripts."
4. "I ran one sensor first, inspected its SQLite file, then added more sensors and the control node."
5. "I cut the network, watched unsynced rows accumulate, restored it and showed timestamped backfill."
6. "I triggered contamination events and showed Warning/Critical alarms and the Unsafe/Safe widget."
7. "I ran the harness and produced the metrics, figures and ThingsBoard export."
8. "I showed that the node registry is one file per sensor, mounted at runtime, so a new node needs
   no image rebuild."

---

## 15. Still outstanding from the original assignment folder

These belong to the old `Assignment_Start_Three` folder and must be carried over:

- `evidence/thingsboard_run/` still contains only its README; copy the real results from
  `runs/measure-20261126-000140/` and `results/measure-metrics/` into the submission package.
- `APPENDIX_A.md` ticks are blank — only your group knows who did what.
- No `Group07_Assessment-03_Manuscript.pdf` exists in the folder yet.
- **DONE:** the real 24-minute ThingsBoard measurement run (`runs/measure-20261126-000140`),
  with all six metric minimums satisfied (section 9A, “Results obtained”). The old
  `20261123-075815` smoke run must not be used as a substitute.

Additional outstanding items in the new folder:

- **DONE:** host `.venv` with all packages from `requirements-dev.txt`.
- **DONE:** `edge/` source code — `__init__.py`, `nodeconfig.py`, `scenario.py`, `schema.sql`,
  `sensors.py`, `buffer.py`, `link.py`, `simulator.py` (all validated).
- **DONE:** `tools/__init__.py`, `tools/netgate.py`, `tools/tb_api.py`, `tools/tb_config.py`,
  `tools/tb_provision.py` (hybrid loader + tenant token files + backup-only defaults).
- **DONE:** generated `thingsboard/*.json` templates and `_widget_defaults/`; baked into image.
- **DONE:** mount `./config:/app/config:ro` in every edge service (verified: 10 bind mounts).
- **DONE:** edge services for BID-04 through BID-09 (10 edge containers total).
- **DONE:** golden image built; new ThingsBoard stack initialised and running.
- **DONE:** `tools/measure.py` with `config/scenarios/measure.json` (section 9A). Optional
  `sweep.py` / `webhook_receiver.py` are not needed for the mandatory metrics; the live viewer is
  `tools/buffer_view.py` (Milestone 9).
- **DONE:** cross-node scenario bug fix with regression test; full suite 45 passed.
- **DONE:** read-only Metrics tab in `tools/control_ui.py` on port 8766.
- Still to do by the group: fill the manuscript placeholders with the section 9A results, insert
  the dashboard screenshots, complete Appendix A, and rebuild the final implementation ZIP.
- Optional: `edge-bid10` service if you want `BID-10.json` to run (11 containers total).
- Devices/tokens, dashboard and alarms are **already installed** in the running ThingsBoard stack;
  `results/tokens/default.json` exists. A new tenant is optional and not needed.

---

## 16. Live build log

### Milestone 1 — Docker files and certificates

- Wrote root `Dockerfile`, `docker-entrypoint.sh`, `docker-compose.yaml`, plus
  `thingsboard/docker-compose.yml`.
- Wrote `certs/gen_certs.sh` and generated the four PEM files.
- Decided: certs mount at `/etc/thingsboard/certs`, never `/config/certs`.
- Decided: compose commands always use `-f docker-compose.yaml`.

### Milestone 2 — config folder

- Chose the hybrid design: global `nodes.json` + one file per field node + mounted config.
- Wrote `config/nodes.json` with `JD-CTRL-01` and `JD-BENCH-01`.
- Wrote `config/nodes/BID-01.json` through `BID-10.json` (ten field sensors).
- Wrote `config/scenarios/smoke.json` and `config/scenarios/demo.json`.
- Validated all 13 JSON files: **all VALID**.

### Milestone 3 — edge package and Compose services

- Wrote all 8 `edge/` files: `__init__.py`, `nodeconfig.py`, `scenario.py`, `schema.sql`,
  `sensors.py`, `buffer.py`, `link.py`, `simulator.py`.
- `py_compile` passes for all 8; imports of `link`, `sensors`, `nodeconfig`, `scenario` pass.
- Functional buffer test (temporary DB under `/tmp/opencode`): WAL journal, write-first `synced=0`,
  PUBACK-style `mark_synced()` → pending depth 0.
- Added `./config:/app/config:ro` to all edge services; verified 10 bind mounts via Compose.
- Added `edge-bid04` … `edge-bid09`; Compose now has 10 edge containers
  (`BID-01`…`BID-09` + `JD-CTRL-01`). `BID-10.json` has no service yet.
- At that point `python -m edge.simulator --help` failed with `ModuleNotFoundError: tools.netgate`.

### Milestone 4 — Python environment + netgate

- Created host `.venv`; verified installed packages: paho 2.2.0, requests 2.34.2,
  matplotlib 3.11.2, playwright 1.65.2, pytest 9.2.0.
- Wrote `tools/__init__.py` and `tools/netgate.py` (8,714 bytes), so the simulator import is resolved.
- Read-only check found `__pycache__` folders in `edge/` and `tools/`; both are ignored by Docker.

### Milestone 5 — backup tools, golden image, live platform

- Copied `tb_config.py`, `tb_api.py` and `_widget_defaults/`; adapted `tb_provision.py` to use
  `load_nodes("config")`, write `results/tokens/<tenant>.json`, and skip rule chain / alarms /
  dashboard unless `--install-*` flags are given.
- Generated `thingsboard/*.json` with `python -m tools.tb_config` (local files only, no server calls).
- Added `COPY thingsboard/ ./thingsboard/` to the Dockerfile.
- `docker-entrypoint.sh` now resolves `/app/tokens/${TB_TENANT}.json`; all 10 edge services set
  `TB_TENANT: default` and mount `./results/tokens:/app/tokens:ro` (verified 10 of each).
- Built `jaldridhti-edge:1.0.0`; image file listing shows no PEM, token, results or runs.
- Fresh `jaldrishti-tb-postgres-data` volume created; `tb-init` finished successfully.
- `thingsboard-ce` running: HTTP 200, sysadmin and tenant logins return 200, MQTT/TLS verify
  `0 (ok)` on 8883.
- **No provisioning script was executed.** Templates were generated as files only. Devices and
  tokens were later created manually in the UI by the user.

### Milestone 6 — manual tenant setup, two live bugs fixed, 4 nodes live

- User created profile `JalDrishti Water Node`, 4 devices, access tokens and server attributes in
  `tenant@thingsboard.org`; `results/tokens/default.json` was written manually.
- Live run exposed `exec /app/docker-entrypoint.sh: exec format error`: the entrypoint had no
  shebang. Added `#!/bin/sh` as line 1, rebuilt `jaldridhti-edge:1.0.0`, force-recreated the edge
  containers. `docker run --rm jaldridhti-edge:1.0.0` now reaches the script and prints the `NODE`
  error instead.
- Second live run exposed `PermissionError: '/app/runs/BID-0X-compose'`: host `runs/` was owned by
  UID 1000 but the container user is UID 10001. Changed host ownership with a one-off root
  container and force-recreated the 4 services.
- ThingsBoard data corrections applied via REST: device `BID03` renamed to `BID-03`; `BID-03` and
  `BID-04` moved from profile `default` to `JalDrishti Water Node`. All 4 now match the alias filter.
- `edge-bid01`…`edge-bid04` are stable with TLS `LINK ... -> ONLINE`. Verified 9 telemetry keys per
  device in ThingsBoard: `ph`, `turbidity_ntu`, `tds_ppm`, `water_temp_c`, `flow_lpm`, `latitude`,
  `longitude`, `rssi_dbm`, `buffer_depth`. SQLite shows 10–11 rows per node, all `synced=1`,
  `unsynced=0`.
- At this point no rule chain, alarms or dashboard had been installed; the user later handed that
  work back, so Milestone 7 installs them by code.

### Milestone 7 — rule engine, alarms, clean dashboard and golden template (current)

- User explicitly changed the earlier “manual only” decision: build the dashboard, rule chain and
  alarms for the 4 live nodes in `tenant@thingsboard.org`.
- Installed the two requested UI skills (`frontend-design`, `web-design-guidelines`) with the
  `npx skills` CLI.
- Added `tools/tb_ui.py` + `tests/test_tb_ui.py` (7 tests pass). It is UI-only: it does **not** create
  devices, tokens or tenants. It reads `results/tokens/default.json` only to check the chosen node
  name, then resolves the real device UUID through `/api/tenant/devices`.
- Live bug found during verification: the first import used the BID-01 **access token** as
  `Selected node` default entity. Added `resolve_default_device_id()` with a failing test first,
  re-ran the installer, and confirmed the alias now points to device UUID
  `23244e00-e7fd-11f1-947e-57a10a1ad3af`.
- Server-side TBEL checks all passed; WQI for the reference case was `243.3` (expected `243.3`).
- Installed `JalDrishti Ingest` (9 nodes, 13 connections, no webhook) and linked the existing
  profile `JalDrishti Water Node` via `defaultRuleChainId`.
- Installed 5 calculated-field alarms: `High Turbidity`, `Gross Contamination`, `pH Out of Range`,
  `High TDS`, `Supply Interruption`.
- Deleted the old draft dashboard `JalDrishti-Bidur` (user authorised) and imported
  `JalDrishti – Bidur Water Safety` with 18 widgets, 2 states, `Water node` alias (device type,
  Resolve multiple **ON**) and `Selected node` alias (state entity, single device).
- Alarm proof: published one TLS MQTT telemetry sample to BID-01 with `turbidity_ntu=260`. REST then
  showed `alm_turbidity_crit=CRITICAL`, derived `wqi≈512`, and ACTIVE alarms `Gross Contamination`
  and `High Turbidity`.
- Overwrote `thingsboard/dashboard.json` with the cleaned portable 18-widget template (alias
  `Water node`, one `${DEFAULT_DEVICE_ID}` placeholder). Added `thingsboard/dashboard-live.json` to
  `.dockerignore` so a real device UUID never enters the image.
- Rebuilt `jaldridhti-edge:1.0.0` and verified: entrypoint line 1 is `#!/bin/sh`, `docker run --rm`
  reaches the script, image template has 18 widgets and 2 aliases, and there are **0** PEM/token/
  results/runs/live-copy files in the image.

#### Widget rationale — what each graph/card does and why

| Widget | Why it is there |
|---|---|
| Which source is unsafe now? | Table of all 4 nodes; worst active `alm_*` severity decides the colour badge, so the first screen answers “which tap do I close?” |
| Sources — marker colour = worst active alarm | Map uses server `latitude`/`longitude`; red/orange/yellow/green marker means critical/major/warning/safe, and the tooltip opens node detail. |
| pH (safe band 6.5–8.5) | Radial gauge makes NDWQS pH compliance readable at a glance; outside 6.5–8.5 is red. |
| Turbidity, log scale (1–10–100–1000 NTU) | Turbidity spans orders of magnitude; log scale plus 5/50/200 bands keeps low and flood values legible in one gauge. |
| Turbidity now / WQI (Brown) | Value cards give the raw NTU and the derived composite index without requiring chart reading. |
| TDS (bands 500 / 1000 mg/L) | Arc gauge marks the 500 mg/L alarm threshold and the 1000 mg/L severe band from the same single rule table. |
| Flow / Water temp | Cards show hydraulic and thermal context, so a turbidity spike can be read against flow and temperature. |
| Edge buffer depth | Shows store-and-forward backlog; non-zero means the node buffered rows during a network outage. |
| RSSI | Link quality explains why buffering happened; pairs with buffer depth in the detail chart. |
| Node health: activity, RSSI, buffer | One table for connectivity, last activity, WQI and backlog across all four devices. |
| Selected node: turbidity, TDS and pH | Detail-state time chart with three axes and threshold lines at 50/200 NTU and 500 mg/L; proves original client timestamps/backfill survive ingest. |
| Selected node: temperature and flow | Detail-state context chart for process conditions rather than contamination alone. |
| Buffer depth and RSSI (store-and-forward) | Detail-state pair chart used for the BID-03 outage/resync demonstration. |
| Temperature, flow and WQI | Detail-state correlation view: environmental conditions versus the derived water-quality score. |
| Alarms — active and cleared / This node's alarms | Alarm tables show the incident queue globally and per node, with duration, severity and ack/clear controls. |

### Milestone 8 — all-node comparison charts and per-node drill-down (current)

- User asked for a main view that compares all four nodes and a way to open a node-specific view with
  individual sensor data.
- Added `extend_dashboard()` to `tools/tb_ui.py` with 4 new tests; suite is now **11 passed**.
- Main state gained two line charts, both bound to alias `Water node` (device type, Resolve multiple
  **ON**) with one data key each, so ThingsBoard draws one series per device:
  - `Turbidity — all 4 nodes` → `turbidity_ntu` (NTU).
  - `Flow rate — all 4 nodes` → `flow_lpm` (L/min).
- Node state gained 10 value cards bound to alias `Selected node`: `ph`, `turbidity_ntu`, `tds_ppm`,
  `water_temp_c`, `flow_lpm`, `latitude`, `longitude`, `rssi_dbm`, `buffer_depth`, `wqi`.
- Drill-down already existed and was re-verified: the status table's `actionCellButton` uses
  `openDashboardState` → `node` with `setEntityId=true`; the map tooltip link does the same. The
  selected entity fills the `Selected node` alias, so each card/chart is that node alone.
- Live REST verification: dashboard has **30 widgets**, 2 states, comparison charts use the
  `Water node` alias, node cards use the `Selected node` alias, and the action is `openDashboardState`.
- Rebuilt `jaldridhti-edge:1.0.0`: 30-widget template baked in, `${DEFAULT_DEVICE_ID}` preserved,
  0 PEM/token/results/runs/live-copy files.

#### How the rule engine works

The device profile points to `JalDrishti Ingest` through `defaultRuleChainId`, so every BID-01…BID-04
telemetry message flows through the same 9-node chain (13 connections, no webhook):

1. **Message Type Switch** routes by ThingsBoard message type: post telemetry, post attributes,
   inactivity/activity events, alarm created/updated/cleared, or other.
2. **Stamp rx_ms + WQI** runs TBEL on each telemetry message, adds server receipt time `rx_ms` and
   derives the Brown WQI from `ph`, `turbidity_ntu` and `tds_ppm`.
3. **Save Timeseries (client ts)** stores each key using the node's original `ts`, so offline
   backfill keeps its true acquisition time instead of the replay time.
4. **Save Client Attributes** stores client-scope metadata.
5. **Node Inactive: create / clear** creates and clears a CRITICAL `Node Inactive` alarm from
   inactivity/activity events.
6. **Alarm -> alm_* attribute** mirrors any alarm into a server attribute such as
   `alm_turbidity_crit=CRITICAL` or `NONE`; **Save Server Attributes** persists it. This is what the
   status table and map marker colours read.
7. **Log Other** sends unmatched message types to the rule-engine log for debugging.

#### How the alarm settings work

Five calculated fields of type `ALARM` are attached to the device profile, so they apply to all four
devices without per-device configuration. Each reads `TS_LATEST` and uses a create condition, a clear
condition and a 30-second dwell/duration (demo time compression = 30 real seconds for 30 simulated
minutes):

| Alarm | Create | Clear | Severity |
|---|---|---|---|
| High Turbidity | `turbidity_ntu > 50` | `< 40` for 30 s | WARNING |
| Gross Contamination | `turbidity_ntu > 200` | `< 150` for 30 s | CRITICAL |
| pH Out of Range | `ph < 6.5 OR ph > 8.5` | `6.7 ≤ ph ≤ 8.3` for 30 s | CRITICAL |
| High TDS | `tds_ppm > 500` | `< 450` for 30 s | CRITICAL |
| Supply Interruption | `flow_lpm = 0` for 30 s | `> 0` | MAJOR |

`python -m tools.tb_ui` is idempotent: it re-tests TBEL, upserts the chain, re-links the profile,
recreates the 5 calculated fields, updates the same dashboard and writes `thingsboard/dashboard-live.json`
outside the image.

### Milestone 9 — live SQLite buffer viewer (current)

The user asked to see SQLite being used as a buffer, not just query it after the fact. Added
`tools/buffer_view.py`, a **host-side, read-only, standard-library** viewer.

#### How it works

1. `discover()` finds only `runs/<node>-compose/db/<node>.db`. Nothing under `results/` or `runs/`
   is copied into the image.
2. `snapshot()` opens each database with SQLite URI `mode=ro`. Because `edge/buffer.py` uses WAL,
   the viewer can read while the simulator is still committing rows; the viewer never writes.
3. It reads `SUM(synced=0)` as pending depth, `SUM(synced=1)` as synced rows, `SUM(synced=0 AND
   breach=1)` as pending evidence rows, `MIN(ts_ms)` of pending rows for oldest age, `MAX(ts_ms)`
   for last-sample age, and the non-synced tiers of `eviction_log`.
4. It parses the newest `{"ev":"link","node":"BID-03",...}` line from the sibling `events.jsonl`,
   so the card also shows `ONLINE`/`OFFLINE`, the reason and the transition time.
5. The browser page keeps the last 120 pending-depth samples in memory and draws an inline SVG
   sparkline. It auto-refreshes every second; payloads are HTML-escaped. No JavaScript, no new pip
   dependency, and **no shadow ThingsBoard device** is created.

#### Use

```bash
# Browser view (preferred while testing a blackhole)
.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765
# open http://localhost:8765

# One-shot terminal table for an evidence log
.venv/bin/python -m tools.buffer_view --db-dir runs --once
```

For the BID-03 outage, keep the browser open, blackhole that node's netgate, watch BID-03's
`PENDING` count and sparkline rise, then restore it and watch `PENDING` drain to zero while
`synced` increases. The netgate commands are in §9.1.

**Blackhole vs stop:** netgate `down?mode=blackhole` keeps the sampler running, so SQLite genuinely
accumulates unsynced rows. `docker compose -f docker-compose.yaml stop edge-bid03` freezes the file:
it proves persistence, but no new rows are created. In both cases the ThingsBoard device remains;
after about 60–65 s of missing activity the `Node Inactive` CRITICAL alarm and `alm_inactive=CRITICAL`
make the stopped node red in ThingsBoard. The local viewer is the honest place to watch backlog
growth because a node with no MQTT link cannot stream that backlog live.

#### Verification

- Added `tests/test_buffer_view.py` with 8 tests: real SQLite counts, empty buffer, link-event
  filtering, discovery, terminal table, sparkline scaling, HTML escaping and late-starting databases.
  Suite: **19 passed**.
- Live `--once` check showed all four compose nodes with their real row counts and `ONLINE` links.
- HTTP smoke check returned a page containing `BID-03` and an SVG `<polyline>`.
- Rebuilt `jaldridhti-edge:1.0.0`. Image audit: `viewer=yes`, `pem=0`, `tokenmaps=0`,
  `runtime_dirs=0`, `live_copy=no`, `entrypoint_line1=#!/bin/sh`.

### Milestone 10 — manual simulator control (additive)

The user asked to control what is sent to ThingsBoard without disturbing the working stack.
This milestone adds an optional control window at **http://localhost:8766** while leaving
**http://localhost:8765** as the read-only SQLite viewer.

#### Buttons

Each node panel (BID-01…BID-04) has six sliders — pH 0–14, turbidity 0–1000 NTU,
TDS 0–2000 ppm, temperature 0–40 °C, flow 0–40 L/min, RSSI −120–0 dBm — and three actions:

| Button | Effect |
|---|---|
| **Pause node** | Disconnects only that node's MQTT client, so ThingsBoard marks the device inactive (network not connected). Sampling continues, so `synced=0` rows accumulate in SQLite and appear as rising `PENDING` on port 8765. Other nodes are unaffected. |
| **Override & resume** | Reconnects that node's MQTT client, drains the backlog with original timestamps, then sends your slider values on every new sample. Existing breach/eviction/flush logic still applies. |
| **Resume random** | Reconnects MQTT if paused and releases the manual values; the original physics model continues unchanged. |

#### Safety and behavior

- No control file means normal behavior: `paused=false`, `override=false`.
- Control files live only under `runs/control/` and are excluded from the golden image.
- `edge/control.py` polls the file immediately before each sample; changes appear within one
  sample period (normally about two seconds).
- Pause disconnects only that node's MQTT client in `edge/link.py` and holds the link in `PAUSED`;
  it is not the shared netgate relay, so one panel cannot blackhole another node.
- ThingsBoard uses its built-in inactive state after the device's existing inactivity timeout
  (about 60 seconds); after resume it can take up to about 60 seconds to show active again.
  No ThingsBoard attribute, tenant, device or webhook is changed. The 8765 and 8766 panels show
  the pause immediately through rising `PENDING` and the `PAUSED` label.
- Manual values pass through the existing `is_breach()` check, so chosen values can trigger the
  existing calculated-field alarms. No new ThingsBoard device or webhook is created.
- The UI is a host-side standard-library HTTP server on port 8766. It writes control files
  atomically and makes them world-readable so the edge container (UID 10001) can read it. It must
  stay running in its own terminal; if it stops, button presses are not saved and the node stays random.

#### Demo workflow

1. Start the edge stack with `--profile edge` as before:
   `docker compose -f docker-compose.yaml --profile edge up -d`.
2. Prepare the host-writable control directory once (see the port table above):
   `docker run --rm --user root --entrypoint sh -v "$PWD/runs:/app/runs" jaldridhti-edge:1.0.0 -c "mkdir -p /app/runs/control && chown $(id -u):$(id -g) /app/runs/control && chmod 755 /app/runs/control"`.
3. Start the SQLite viewer: `.venv/bin/python -m tools.buffer_view --db-dir runs --port 8765`.
4. Start the control window in its own terminal and leave it open: `.venv/bin/python -m tools.control_ui --db-dir runs --control-dir runs/control --port 8766`.
5. Open both URLs. Press **Pause node** on BID-03; after a few seconds its `PENDING` count rises
   on 8765, the panel shows `PAUSED`, and the link log shows `LINK ONLINE -> PAUSED`. ThingsBoard
   marks the device inactive after about 60 seconds.
6. Set pH/TDS/turbidity/flow to your chosen values and press **Override & resume**. The MQTT client
   reconnects, the backlog drains with original timestamps, and ThingsBoard receives the chosen
   values; the panel shows `OVERRIDE`. ThingsBoard shows the device active again within about
   60 seconds.
7. Press **Resume random** to return to the physics model. The panel shows `RANDOM`.

#### Troubleshooting — buttons do nothing / random data continues

If a panel shows `RANDOM`, pending does not rise after **Pause node**, and ThingsBoard keeps
showing changing physics values, check these layers in order:

1. **Port 8766 listener:** run `ss -ltnp '( sport = :8766 )'`. If nothing is printed, the browser
   page is stale and no button press can be saved. Restart the command from step 4 and keep the
   terminal open. A temporary server started by an automated test is stopped when that test ends;
   it is not a replacement for your own terminal process.
2. **Control file:** after pressing a button, `runs/control/<NODE>.json` should exist. Inspect it
   with `cat runs/control/BID-03.json`; `paused=true` means pause, and `override=true` means manual
   values. If the file is absent or unchanged, the POST never reached the UI server.
3. **Container sees the file:** the edge services mount `./runs:/app/runs`, so the same file is
   visible at `/app/runs/control/<NODE>.json` inside the container. Confirm with
   `docker compose -f docker-compose.yaml exec edge-bid03 cat /app/runs/control/BID-03.json`.
4. **Simulator applied it:** look for `CONTROL` lines with
   `docker logs jaldridhti-edge-bid03-1 2>&1 | grep -E 'CONTROL|PAUSED'`. A real pause logs
   `LINK ONLINE -> PAUSED`, `LINK MQTT session PAUSED by manual control` and
   `CONTROL pause node=BID-03`, then no further `FLUSH` lines until resume.
5. **Timing:** control files are read on the next sample, normally within about two seconds.
   ThingsBoard's inactive state appears after its existing inactivity timeout (about 60 seconds),
   and can take up to about 60 seconds to return to active after resume. Refresh the 8766 page
   and watch the `PENDING` column on 8765 for the paused node.

**Resume random** writes `paused=false` and `override=false`. Deleting `runs/control/<NODE>.json`
has the same effect because no control file also means normal random behavior.

#### Verification

- Added `tests/test_control.py` and `tests/test_control_ui.py` with **19 tests total**
  (15 control-runtime tests plus 4 UI tests), including robustness checks for malformed and
  non-finite control values, per-node MQTT disconnect on pause, no pumping or reconnect while
  paused, reconnect after resume, state transitions, and simulator polling.
- Full suite after the disconnect change: **38 passed**.
- Golden image rebuilt because `Dockerfile` already copies `edge/` and `tools/`; no Dockerfile
  or Compose edit was needed. Recreate the four edge containers after rebuilding so they use the
  updated `edge/link.py`.

### Milestone 11 — real ThingsBoard measurement and 8766 metrics view (current)

- Fixed the cross-node scenario bug: `edge/simulator.py:run_scenario()` now skips events for nodes
  not owned by a `--only` container instead of raising `KeyError`. Added
  `tests/test_simulator.py`; it failed first, then passed.
- Added `config/scenarios/measure.json` (24-minute timeline, 3 pilot nodes, 180 s outage,
  7 rain events, 2 interruptions, 1 bad-token event) from the older assignment folder.
- Added `tools/measure.py` with `tests/test_measure.py` (3 tests): ThingsBoard REST source
  (`rx_ms` timeseries + alarms), broker-sink source for pre-validation, and all six mandatory
  metrics. Verified the live REST endpoints before use.
- Stopped `edge-bid01`…`edge-bid04`, created `runs/measure-20261126-000140` via a root container
  because `runs/` is UID 10001-owned, and ran the host-side simulator with
  `--only BID-01,BID-02,BID-03 --host localhost --port 8883 --ca certs/ca.pem`. Exit 0.
- Ran `tools/measure.py` against ThingsBoard into `results/measure-metrics/`; all six acceptance
  checks PASS (1847 latency samples, 2160/2160 delivery, 180 s outage, 21 alarm delays,
  3 recoveries, 0 data loss). See section 9A, “Results obtained”.
- Added a read-only `/metrics` tab to `tools/control_ui.py` with `tests/test_control_metrics.py`
  (3 tests), restarted the port-8766 server, and confirmed the page shows the generated run.
- Removed the unwanted screenshot-helper test file. Full suite now **45 passed**;
  `compileall` clean.

### Next milestone

Create the second tenant with the **same** profile name, add BID-05…BID-09 there, write
`results/tokens/<slug>.json`, change `TB_TENANT` on those 5 services, and export/import the dashboard
(reset the `Selected node` default entity to a device in that tenant). `JD-CTRL-01` stays in the
default tenant for now. Follow §9.1 for SQLite inspection and the BID-03 outage test.
