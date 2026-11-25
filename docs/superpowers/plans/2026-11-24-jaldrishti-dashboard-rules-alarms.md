# JalDrishti Dashboard, Rule Engine and Alarms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Install rule chain, alarms and a clean 4-node dashboard into `tenant@thingsboard.org`, then bake the portable template into the golden image.

**Architecture:** Reuse `tools/tb_config.py` JSON templates and `tools/tb_provision.py` upsert helpers. Add one focused tool `tools/tb_ui.py` that never creates devices/tokens. Render `thingsboard/dashboard.json` with a `Water node` alias and `${DEFAULT_DEVICE_ID}` placeholder; import a rendered copy into ThingsBoard.

**Tech Stack:** Python 3.13, ThingsBoard REST API 4.3.1.8, pytest, Docker.

---

### Task 1: Install UI skills
- [x] `npx --yes skills add anthropics/skills@frontend-design -g -y`
- [x] `npx --yes skills add vercel-labs/agent-skills@web-design-guidelines -g -y`

### Task 2: Clean dashboard template helper
**Files:** Modify `tools/tb_ui.py` (new), Test `tests/test_tb_ui.py` (new).
- [x] `clean_dashboard_template(raw: dict) -> dict`: rename alias `Pilot nodes` → `Water node`, keep `${DEFAULT_DEVICE_ID}`, drop one duplicate `Selected node: turbidity, TDS and ...` timeseries widget, set title.
- [x] Test asserts alias name, placeholder count, widget count.
- [x] Run `.venv/bin/python -m pytest tests/test_tb_ui.py -q` — must pass.

### Task 3: Targeted installer
**Files:** `tools/tb_ui.py`.
- [x] Reuse `tb_provision.upsert_rule_chain(tb, None)` and `tb_provision.install_alarm_rules(tb, profile)`.
- [x] Set profile `defaultRuleChainId` to installed chain.
- [x] Delete dashboard `JalDrishti-Bidur` if present (user authorized).
- [x] Import rendered dashboard named `JalDrishti – Bidur Water Safety`.
- [x] Write rendered copy to `thingsboard/dashboard-live.json` (runtime artifact, not imported by image).

### Task 4: Live verification
- [x] Run `python -m tools.tb_ui`.
- [x] Verify rule chain, 5 alarms, profile link via REST.
- [x] Publish one high-turbidity MQTT test message using BID-01 token.
- [x] Verify `wqi`, `alm_*` and an active alarm via REST.
- [x] Verify dashboard exists and widgets use live keys.

### Task 5: Golden image + docs
- [x] Keep `thingsboard/dashboard.json` portable with placeholder.
- [x] Rebuild `jaldridhti-edge:1.0.0`; verify no secrets.
- [x] Update `ROADMAP.md` with Milestone 7 and widget rationale.
- [x] No devices, tokens or tenants are created/changed.
