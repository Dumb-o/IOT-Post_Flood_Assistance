# Dashboard comparison charts and node detail cards

**Goal:** Main state compares all four BID nodes; `node` state shows one selected node's raw sensors.

**Files:** `tools/tb_ui.py`, `tests/test_tb_ui.py`, `thingsboard/dashboard.json`, `ROADMAP.md`.

- [x] Test helper that adds turbidity/flow comparison charts using the `Water node` alias.
- [x] Test helper that adds `ph`, turbidity, TDS, water temp, flow, lat/lon, RSSI, buffer and WQI cards to `node` state.
- [x] Verify existing row-click/map-tooltip actions still target the `node` state.
- [x] Re-run `python -m tools.tb_ui`; verify live dashboard via REST.
- [x] Rebuild golden image; verify no secrets and 30 widgets in template.
- [x] Update `ROADMAP.md` Milestone 8.
