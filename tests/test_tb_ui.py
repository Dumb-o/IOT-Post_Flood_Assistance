"""Tests for the targeted rule/alarm/dashboard installer."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def raw_dashboard():
    return json.loads((ROOT / "thingsboard" / "dashboard.json").read_text(encoding="utf-8"))


def test_clean_dashboard_uses_water_node_alias(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template

    dirty = copy.deepcopy(raw_dashboard)
    next(iter(dirty["configuration"]["entityAliases"].values()))["alias"] = "Pilot nodes"
    clean = clean_dashboard_template(dirty)
    aliases = {a["alias"] for a in clean["configuration"]["entityAliases"].values()}
    assert "Water node" in aliases
    assert "Pilot nodes" not in aliases


def test_clean_dashboard_keeps_portable_device_placeholder(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template

    clean = clean_dashboard_template(raw_dashboard)
    assert json.dumps(clean).count("${DEFAULT_DEVICE_ID}") == 1


def test_clean_dashboard_drops_one_duplicate_overview_chart(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template

    dupe_title = "Selected node: turbidity, TDS and pH (event and backfill)"
    dirty = copy.deepcopy(raw_dashboard)
    cfg = dirty["configuration"]
    detail_ids = cfg["states"]["node"]["layouts"]["main"]["widgets"]
    source_id = next(wid for wid in detail_ids
                     if cfg["widgets"][wid]["config"].get("title") == dupe_title)
    dupe_id = str(uuid.uuid4())
    cfg["widgets"][dupe_id] = copy.deepcopy(cfg["widgets"][source_id])
    cfg["widgets"][dupe_id]["id"] = dupe_id
    cfg["states"]["default"]["layouts"]["main"]["widgets"][dupe_id] = {
        "sizeX": 16, "sizeY": 6, "row": 9, "col": 0, "mobileOrder": 99, "mobileHeight": 6,
    }

    clean = clean_dashboard_template(dirty)
    clean_cfg = clean["configuration"]
    assert len(clean_cfg["widgets"]) == len(cfg["widgets"]) - 1

    main_ids = set(clean_cfg["states"]["default"]["layouts"]["main"]["widgets"])
    detail_ids = set(clean_cfg["states"]["node"]["layouts"]["main"]["widgets"])
    assert not any(clean_cfg["widgets"][wid]["config"].get("title") == dupe_title for wid in main_ids)
    assert sum(clean_cfg["widgets"][wid]["config"].get("title") == dupe_title for wid in detail_ids) == 1


def test_clean_dashboard_is_idempotent(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template

    once = clean_dashboard_template(raw_dashboard)
    assert clean_dashboard_template(once) == once


def test_render_dashboard_replaces_placeholder_and_title(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template, render_dashboard

    rendered = render_dashboard(clean_dashboard_template(raw_dashboard), "device-123")
    text = json.dumps(rendered)
    assert "${DEFAULT_DEVICE_ID}" not in text
    assert "device-123" in text
    assert rendered["title"] == "JalDrishti – Bidur Water Safety"
    assert rendered["name"] == rendered["title"]


def test_extend_dashboard_adds_all_node_comparison_charts(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template, extend_dashboard

    clean = clean_dashboard_template(raw_dashboard)
    extended = extend_dashboard(clean)
    cfg = extended["configuration"]
    titles = [w["config"].get("title") for w in cfg["widgets"].values()]
    assert "Turbidity — all 4 nodes" in titles
    assert "Flow rate — all 4 nodes" in titles

    alias_ids = {a["alias"]: aid for aid, a in cfg["entityAliases"].items()}
    for title, key in [("Turbidity — all 4 nodes", "turbidity_ntu"),
                       ("Flow rate — all 4 nodes", "flow_lpm")]:
        wid = next(wid for wid, w in cfg["widgets"].items() if w["config"].get("title") == title)
        ds = cfg["widgets"][wid]["config"]["datasources"][0]
        assert ds["entityAliasId"] == alias_ids["Water node"]
        assert [k["name"] for k in ds["dataKeys"]] == [key]
        assert wid in cfg["states"]["default"]["layouts"]["main"]["widgets"]


def test_extend_dashboard_adds_individual_sensor_cards_to_node_state(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template, extend_dashboard

    clean = clean_dashboard_template(raw_dashboard)
    extended = extend_dashboard(clean)
    cfg = extended["configuration"]
    expected = {"ph", "turbidity_ntu", "tds_ppm", "water_temp_c", "flow_lpm",
                "latitude", "longitude", "rssi_dbm", "buffer_depth", "wqi"}
    alias_ids = {a["alias"]: aid for aid, a in cfg["entityAliases"].items()}
    node_ids = set(cfg["states"]["node"]["layouts"]["main"]["widgets"])
    keys = set()
    for wid in node_ids:
        for ds in cfg["widgets"][wid]["config"].get("datasources", []):
            if ds.get("entityAliasId") == alias_ids["Selected node"]:
                for k in ds.get("dataKeys", []):
                    keys.add(k["name"])
    assert expected <= keys


def test_extend_dashboard_preserves_node_drilldown_actions(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template, extend_dashboard

    extended = extend_dashboard(clean_dashboard_template(raw_dashboard))
    cfg = extended["configuration"]
    table = next(w for w in cfg["widgets"].values()
                 if w["config"].get("title", "").startswith("Which source"))
    actions = table["config"]["actions"]
    assert actions["rowClick"][0]["targetDashboardStateId"] == "node" or \
        actions["actionCellButton"][0]["targetDashboardStateId"] == "node"
    map_widget = next(w for w in cfg["widgets"].values()
                      if w["config"].get("title", "").startswith("Sources -"))
    tooltip = map_widget["config"]["settings"]["markers"][0]["tooltip"]["tagActions"]
    assert tooltip[0]["targetDashboardStateId"] == "node"


def test_extend_dashboard_is_idempotent(raw_dashboard):
    from tools.tb_ui import clean_dashboard_template, extend_dashboard

    once = extend_dashboard(clean_dashboard_template(raw_dashboard))
    assert extend_dashboard(once) == once


def test_resolve_default_device_id_uses_device_uuid_not_access_token():
    import tools.tb_ui as ui

    class FakeTB:
        def __init__(self):
            self.queries = []

        def get(self, path, **kw):
            self.queries.append((path, kw))
            return {"id": {"entityType": "DEVICE", "id": "device-uuid-1"}}

    tb = FakeTB()
    assert ui.resolve_default_device_id(tb, "BID-01") == "device-uuid-1"
    assert tb.queries == [("/api/tenant/devices", {"params": {"deviceName": "BID-01"}})]


def test_install_ui_replaces_draft_and_links_rule_chain(monkeypatch, tmp_path, raw_dashboard):
    import tools.tb_ui as ui

    profile = {"id": {"entityType": "DEVICE_PROFILE", "id": "profile-1"},
               "name": "JalDrishti Water Node", "profileData": {"alarms": []}}
    seen = {}

    monkeypatch.setattr(ui, "test_tbel", lambda tb: {"stamp_wqi_check": {"ok": True}})
    monkeypatch.setattr(ui, "upsert_rule_chain",
                        lambda tb, url: {"id": {"entityType": "RULE_CHAIN", "id": "chain-1"}})
    monkeypatch.setattr(ui, "install_alarm_rules", lambda tb, prof: "calculated_fields")

    def fake_upsert_profile(tb, template, rc_id):
        seen["rc_id"] = rc_id
        seen["template"] = template
        return profile

    monkeypatch.setattr(ui, "upsert_profile", fake_upsert_profile)

    class FakeTB:
        def __init__(self):
            self.deleted = []
            self.posted = []

        def find_by_name(self, path, name, field="name"):
            assert path == "/api/tenant/dashboards"
            return {"id": {"entityType": "DASHBOARD", "id": "draft-1"}}

        def delete(self, path):
            self.deleted.append(path)

        def get(self, path, **kw):
            return {"version": 3}

        def post(self, path, body=None, **kw):
            self.posted.append((path, body))
            saved = dict(body)
            saved["id"] = {"entityType": "DASHBOARD", "id": "dashboard-1"}
            return saved

    tb = FakeTB()
    result = ui.install_ui(tb, "device-123", raw_dashboard, tmp_path / "dashboard-live.json")

    assert seen["rc_id"] == "chain-1"
    assert seen["template"]["defaultRuleChainId"]["id"] == "${RULE_CHAIN_ID}"
    assert result["dashboard_id"] == "dashboard-1"
    assert tb.deleted == ["/api/dashboard/draft-1"]
    dashboards = [body for path, body in tb.posted if path == "/api/dashboard"]
    assert len(dashboards) == 1
    assert "device-123" in json.dumps(dashboards[0])
    written = json.loads((tmp_path / "dashboard-live.json").read_text(encoding="utf-8"))
    assert written["title"] == "JalDrishti – Bidur Water Safety"
