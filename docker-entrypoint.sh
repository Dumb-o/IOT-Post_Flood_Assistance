#!/bin/sh
# Translate environment variables into edge.simulator arguments.
# One container = one node. Secrets are mounted at runtime, never baked in.
# Executes automatically as the container starts up, reads those environment variables, builds the python -m edge.simulator ... command, and starts the simulator.
set -eu

: "${NODE:?set NODE=BID-01 .. BID-09 | JD-CTRL-01}"

# One mounted token directory, one tenant per container. Tokens are never baked
# into the image; tb_provision.py writes results/tokens/<tenant>.json on the host.
TENANT="${TB_TENANT:-default}"
TOKENS="/app/tokens/${TENANT}.json"
if [ ! -f "$TOKENS" ]; then
  echo "ERROR: no token file for tenant '$TENANT': $TOKENS" >&2
  echo "Provision it first: python -m tools.tb_provision --tenant $TENANT --out results/" >&2
  exit 1
fi

HOST="${MQTT_HOST:-thingsboard-ce}"
PORT="${MQTT_PORT:-8883}"
OUT="/app/runs/${NODE}-${RUN_ID:-latest}"

set -- -m edge.simulator \
       --scenario "${SCENARIO:-/app/config/scenarios/demo.json}" \
       --tokens "$TOKENS" \
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
