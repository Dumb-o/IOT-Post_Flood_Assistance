"""Install the JalDrishti rule chain, alarms and clean dashboard (UI-only).

This tool never creates devices, tokens or tenants. It expects the user-created
profile ``JalDrishti Water Node`` and a token map such as
``results/tokens/default.json`` to choose the dashboard's default node.

    python -m tools.tb_ui --device BID-01
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tools import tb_config as C  # noqa: E402
from tools.tb_api import TB  # noqa: E402
from tools.tb_provision import (  # noqa: E402
    install_alarm_rules,
    test_tbel,
    upsert_profile,
    upsert_rule_chain,
)

DASHBOARD_TITLE = "JalDrishti – Bidur Water Safety"
DRAFT_TITLE = "JalDrishti-Bidur"
DUPLICATE_CHART = "Selected node: turbidity, TDS and pH (event and backfill)"


def log(*a):
    print("[ui]", *a, flush=True)


def resolve_default_device_id(tb: TB, device_name: str) -> str:
    """Look up a device UUID; the access token is NOT a valid dashboard entity id."""
    device = tb.get("/api/tenant/devices", params={"deviceName": device_name})
    if not device or not device.get("id", {}).get("id"):
        raise SystemExit(f"device '{device_name}' not found in ThingsBoard")
    return device["id"]["id"]


def clean_dashboard_template(raw: dict) -> dict:
    """Return a portable, de-duplicated copy of the generated dashboard."""
    clean = copy.deepcopy(raw)
    cfg = clean["configuration"]

    clean["title"] = DASHBOARD_TITLE
    clean["name"] = DASHBOARD_TITLE
    for alias in cfg.get("entityAliases", {}).values():
        if alias.get("alias") == "Pilot nodes":
            alias["alias"] = "Water node"

    main = cfg.get("states", {}).get("default", {}).get("layouts", {}).get("main", {}).get("widgets", {})
    detail = cfg.get("states", {}).get("node", {}).get("layouts", {}).get("main", {}).get("widgets", {})
    dupes = [wid for wid in main
             if cfg["widgets"].get(wid, {}).get("config", {}).get("title") == DUPLICATE_CHART]
    has_detail_copy = any(cfg["widgets"].get(wid, {}).get("config", {}).get("title") == DUPLICATE_CHART
                          for wid in detail)
    if has_detail_copy:
        for wid in dupes[:1]:
            main.pop(wid, None)
            cfg["widgets"].pop(wid, None)
    return clean


def _comparison_chart(alias_id: str, key: str, label: str, units: str, color: str,
                      title: str, icon: str, decimals: int):
    cfg = C._defaults("time_series_chart")
    th = json.load(open(os.path.join(ROOT, "thingsboard", "_widget_defaults", "thermostats_chart.json"),
                        encoding="utf-8"))
    base = th["config"]["datasources"][0]["dataKeys"][0]["settings"]
    axis = "cmp"
    cfg["settings"]["yAxes"] = {axis: C._yaxis(cfg["settings"]["yAxes"]["default"],
                                               axis, units, "left", 0, decimals)}
    cfg["datasources"] = [C._ds(alias_id, [C._series(key, label, color, axis, units, decimals, base)], latest=[])]
    cfg["settings"]["dataZoom"] = True
    cfg["useDashboardTimewindow"] = True
    return C._widget("system.time_series_chart", "timeseries", C._title(cfg, title, icon), 12, 5)


def _sensor_card(alias_id: str, key: str, label: str, units: str, decimals: int, icon: str, title: str):
    cfg = C._defaults("value_card")
    cfg["datasources"] = [C._ds(alias_id, [C._dk(key, "timeseries", label, units=units, decimals=decimals)])]
    cfg["units"] = units
    cfg["decimals"] = decimals
    cfg["settings"]["icon"] = icon
    cfg["settings"]["label"] = label
    cfg["settings"]["showLabel"] = True
    return C._widget("system.cards.value_card", "latest", C._title(cfg, title), 3, 2)


def extend_dashboard(template: dict) -> dict:
    """Add all-node comparison charts and individual sensor cards (idempotent)."""
    extended = copy.deepcopy(template)
    cfg = extended["configuration"]
    aliases = {a["alias"]: aid for aid, a in cfg["entityAliases"].items()}
    all_id, sel_id = aliases["Water node"], aliases["Selected node"]
    main = cfg["states"]["default"]["layouts"]["main"]["widgets"]
    detail = cfg["states"]["node"]["layouts"]["main"]["widgets"]
    titles = {w["config"].get("title") for w in cfg["widgets"].values()}

    def place(item, layout, row, col):
        wid, widget = item
        widget["row"], widget["col"] = row, col
        cfg["widgets"][wid] = widget
        layout[wid] = {"sizeX": widget["sizeX"], "sizeY": widget["sizeY"], "row": row, "col": col,
                       "mobileOrder": len(layout) + 1, "mobileHeight": max(3, widget["sizeY"])}

    charts = [
        ("Turbidity — all 4 nodes", _comparison_chart(all_id, "turbidity_ntu", "Turbidity", "NTU",
                                                       "#8D6E63", "Turbidity — all 4 nodes", "opacity", 1)),
        ("Flow rate — all 4 nodes", _comparison_chart(all_id, "flow_lpm", "Flow", "L/min",
                                                      "#00897B", "Flow rate — all 4 nodes", "water_pump", 1)),
    ]
    for index, (title, item) in enumerate(charts):
        if title not in titles:
            place(item, main, 20, index * 12)

    cards = [
        ("ph", "pH", "", 2, "water_drop"),
        ("turbidity_ntu", "Turbidity", "NTU", 1, "opacity"),
        ("tds_ppm", "TDS", "mg/L", 0, "water"),
        ("water_temp_c", "Water temp", "°C", 1, "thermostat"),
        ("flow_lpm", "Flow", "L/min", 1, "water_pump"),
        ("latitude", "Latitude", "°", 5, "place"),
        ("longitude", "Longitude", "°", 5, "place"),
        ("rssi_dbm", "RSSI", "dBm", 0, "signal_cellular_alt"),
        ("buffer_depth", "Buffer", "rows", 0, "storage"),
        ("wqi", "WQI", "", 0, "show_chart"),
    ]
    for index, (key, label, units, decimals, icon) in enumerate(cards):
        title = f"{label} ({key})"
        if title not in titles:
            place(_sensor_card(sel_id, key, label, units, decimals, icon, title),
                  detail, 11 + (index // 5) * 2, (index % 5) * 3)
    return extended


def render_dashboard(raw: dict, default_device_id: str) -> dict:
    """Fill the single portable device placeholder with a real device UUID."""
    rendered = extend_dashboard(clean_dashboard_template(raw))
    text = json.dumps(rendered, ensure_ascii=False).replace("${DEFAULT_DEVICE_ID}", default_device_id)
    return json.loads(text)


def install_ui(tb: TB, default_device_id: str, raw_dashboard: dict, live_path: str | os.PathLike) -> dict:
    """Install rule chain, alarm rules, profile link and the clean dashboard."""
    tests = test_tbel(tb)
    stamp = tests.get("stamp_wqi_check")
    if stamp is not None and not stamp.get("ok"):
        raise RuntimeError(f"TBEL WQI test failed: {stamp}")

    chain = upsert_rule_chain(tb, None)
    chain_id = chain["id"]["id"]
    profile = upsert_profile(
        tb,
        C.device_profile(C.PROFILE, "JalDrishti water-quality node; alarms: alarm_rules_cf.json"),
        chain_id,
    )
    alarm_mode = install_alarm_rules(tb, profile)

    draft = tb.find_by_name("/api/tenant/dashboards", DRAFT_TITLE, field="title")
    if draft:
        tb.delete(f"/api/dashboard/{draft['id']['id']}")
        log(f"deleted draft dashboard '{DRAFT_TITLE}'")

    rendered = render_dashboard(raw_dashboard, default_device_id)
    existing = tb.find_by_name("/api/tenant/dashboards", DASHBOARD_TITLE, field="title")
    if existing:
        rendered["id"] = existing["id"]
        rendered["version"] = tb.get(f"/api/dashboard/{existing['id']['id']}").get("version")

    saved = tb.post("/api/dashboard", rendered)
    live = os.fspath(live_path)
    os.makedirs(os.path.dirname(live) or ".", exist_ok=True)
    with open(live, "w", encoding="utf-8") as fh:
        json.dump(saved, fh, indent=2, ensure_ascii=False)

    log(f"dashboard '{saved['title']}': {saved['id']['id']}")
    log(f"rule chain: {chain_id}; alarms: {alarm_mode}; profile: {profile['id']['id']}")
    return {
        "rule_chain_id": chain_id,
        "profile_id": profile["id"]["id"],
        "alarm_rule_mode": alarm_mode,
        "dashboard_id": saved["id"]["id"],
        "tbel_tests": tests,
        "live_copy": live,
    }


def main():
    ap = argparse.ArgumentParser(description="Install JalDrishti UI backend (rules, alarms, dashboard)")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--user", default="tenant@thingsboard.org")
    ap.add_argument("--password", default="tenant")
    ap.add_argument("--device", default="BID-01", help="device name used as the dashboard's default node")
    ap.add_argument("--tokens", default=os.path.join(ROOT, "results", "tokens", "default.json"))
    ap.add_argument("--template", default=os.path.join(ROOT, "thingsboard", "dashboard.json"))
    ap.add_argument("--live-copy", default=os.path.join(ROOT, "thingsboard", "dashboard-live.json"))
    a = ap.parse_args()

    with open(a.tokens, encoding="utf-8") as fh:
        tokens = json.load(fh)
    if a.device not in tokens:
        raise SystemExit(f"{a.device} not in {a.tokens}; known: {', '.join(sorted(tokens))}")

    with open(a.template, encoding="utf-8") as fh:
        raw = json.load(fh)

    tb = TB(a.url, a.user, a.password)
    device_id = resolve_default_device_id(tb, a.device)
    log(f"default node {a.device} -> {device_id}")
    info = install_ui(tb, device_id, raw, a.live_copy)
    log(f"done: {a.url}/dashboards/{info['dashboard_id']}")


if __name__ == "__main__":
    main()
