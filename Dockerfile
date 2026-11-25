# syntax=docker/dockerfile:1
#
# Golden edge image for JalDrishti.
# This image contains ONLY the simulator runtime. It contains no certificates,
# no access tokens, no results and no secrets. Build from the PROJECT ROOT:
#
#   docker build -t jaldridhti-edge:1.0.0 .

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
# Non-secret ThingsBoard templates (dashboard, rule chain, alarm rules, profiles).
# They are a portable backup only: nothing installs them automatically.
COPY thingsboard/ ./thingsboard/
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN chmod +x ./docker-entrypoint.sh

USER edge

ENTRYPOINT ["/app/docker-entrypoint.sh"]
