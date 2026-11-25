"""Provision the JalDrishti backend on one ThingsBoard CE tenant (idempotent).

BACKUP-ONLY BY DEFAULT. Running this module:
  ALWAYS  : generates the JSON templates under thingsboard/ (files only, no server calls)
  ALWAYS  : creates/updates device profiles, devices, access tokens and server attributes
  ONLY WITH FLAGS: rule chain (--install-rules), alarm rules (--install-alarms),
                   dashboard (--install-dashboard)

Use --install-all if you want the full generated backend. The dashboard is
deliberately NOT pushed by default: import thingsboard/dashboard.json through
the ThingsBoard UI once, or build your own dashboard from scratch. Re-running
this script will then never overwrite your layout.

Tokens are written to <out>/tokens/<tenant>.json so several tenants can run at
the same time. Pick the file in a container with TB_TENANT=<tenant>.

    python -m tools.tb_provision --url http://localhost:8080 \
      --user tenant@thingsboard.org --password tenant \
      --out results/ --tenant default

    python -m tools.tb_provision ... --install-all   # full backup backend
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from edge.nodeconfig import load_nodes  # noqa: E402
from tools import tb_config as C  # noqa: E402
from tools.tb_api import TB, TBError  # noqa: E402


def log(*a):
    print("[provision]", *a, flush=True)


def _subst(obj, mapping):
    s = json.dumps(obj)
    for k, v in mapping.items():
        s = s.replace("${%s}" % k, v)
    return json.loads(s)


def test_tbel(tb: TB) -> dict:
    """Run the rule chain's TBEL scripts on the server before wiring them in."""
    cases = {
        "stamp": dict(script=C.TBEL_STAMP, msgType="POST_TELEMETRY_REQUEST",
                      msg={"ph": 6.3, "turbidity_ntu": 18.0, "tds_ppm": 480},
                      metadata={"ts": "1700000000000"}),
        "alarm_mirror": dict(script=C.TBEL_ALARM_ATTR, msgType="ALARM_CREATED",
                             msg={"type": "Gross Contamination", "severity": "CRITICAL",
                                  "status": "ACTIVE_UNACK", "cleared": False,
                                  "createdTime": 1700000000000, "clearTs": 0},
                             metadata={"isNewAlarm": "true"}),
        "alarm_mirror_clear": dict(script=C.TBEL_ALARM_ATTR, msgType="ALARM_CLEAR",
                                   msg={"type": "pH Out of Range", "severity": "CRITICAL",
                                        "status": "CLEARED_UNACK", "cleared": True,
                                        "createdTime": 1700000000000, "clearTs": 1700000060000},
                                   metadata={"isClearedAlarm": "true"}),
    }
    out = {}
    for name, c in cases.items():
        body = {"script": c["script"], "scriptType": "update",
                "argNames": ["msg", "metadata", "msgType"],
                "msg": json.dumps(c["msg"]), "metadata": c["metadata"], "msgType": c["msgType"]}
        try:
            r = tb.post("/api/ruleChain/testScript?scriptLang=TBEL", body)
        except TBError as e:
            r = {"error": str(e)}
        out[name] = r
        ok = not r.get("error")
        log(f"TBEL test {name}: {'OK' if ok else 'FAILED'} {json.dumps(r)[:300]}")
    try:
        w = json.loads(out["stamp"]["output"])["msg"]["wqi"]
        out["stamp_wqi_check"] = {"wqi": w, "expected": 243.3, "ok": abs(w - 243.3) < 0.2}
        log(f"TBEL WQI for Task 2 case B = {w:.1f} (expected 243.3)")
    except Exception as e:  # noqa: BLE001
        out["stamp_wqi_check"] = {"error": str(e)}
    return out


def upsert_rule_chain(tb: TB, webhook_url: str | None) -> dict:
    rc = C.rule_chain(webhook=bool(webhook_url))
    if webhook_url:
        rc = _subst(rc, {"WEBHOOK_URL": webhook_url})
    existing = tb.find_by_name("/api/ruleChains", C.RULE_CHAIN)
    body = dict(rc["ruleChain"])
    if existing:
        body["id"] = existing["id"]
        body["version"] = existing.get("version")
    saved = tb.post("/api/ruleChain", body)
    md = dict(rc["metadata"], ruleChainId=saved["id"])
    tb.post("/api/ruleChain/metadata?updateRelated=true", md)
    log(f"rule chain '{C.RULE_CHAIN}' {'updated' if existing else 'created'}: {saved['id']['id']}")
    return saved


def upsert_profile(tb: TB, template: dict, rc_id: str = "") -> dict:
    body = _subst(template, {"RULE_CHAIN_ID": rc_id})
    existing = tb.find_by_name("/api/deviceProfiles", body["name"])
    if existing:
        full = tb.get(f"/api/deviceProfile/{existing['id']['id']}")
        full.update({k: v for k, v in body.items() if k != "profileData"})
        full["profileData"]["transportConfiguration"] = body["profileData"]["transportConfiguration"]
        body = full
    saved = tb.post("/api/deviceProfile", body)
    log(f"device profile '{saved['name']}' {'updated' if existing else 'created'}: {saved['id']['id']}")
    return saved


def install_alarm_rules(tb: TB, profile: dict) -> str:
    pid = profile["id"]["id"]
    names = {a["type"] for a in C.ALARMS}
    try:
        current = list(tb.pages(f"/api/DEVICE_PROFILE/{pid}/calculatedFields", {"type": "ALARM"}))
    except TBError as e:
        if e.status in (404, 405):
            current = None
        else:
            current = []
    if current is not None:
        try:
            for cf in current:
                if cf.get("name") in names:
                    tb.delete(f"/api/calculatedField/{cf['id']['id']}")
            for cf in C.alarm_rules_cf():
                body = dict(cf, entityId={"entityType": "DEVICE_PROFILE", "id": pid})
                saved = tb.post("/api/calculatedField", body)
                log(f"alarm rule (calculated field) '{saved['name']}' -> {saved['id']['id']}")
            return "calculated_fields"
        except TBError as e:
            log(f"calculated-field alarm rules rejected ({e}); falling back to device-profile alarm rules")
    full = tb.get(f"/api/deviceProfile/{pid}")
    full["profileData"]["alarms"] = C.alarm_rules_legacy()
    tb.post("/api/deviceProfile", full)
    log("alarm rules installed in device profile (legacy format)")
    return "device_profile_alarms"


def _token(n=20):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


def upsert_device(tb: TB, spec: dict, profile: dict, reset: bool, inactivity_ms: int) -> tuple[dict, str]:
    name = spec["name"]
    try:
        existing = tb.get("/api/tenant/devices", params={"deviceName": name})
    except TBError as e:
        if e.status != 404:
            raise
        existing = None
    if existing and reset:
        tb.delete(f"/api/device/{existing['id']['id']}")
        log(f"device {name} deleted (--reset)")
        existing = None
    if existing:
        dev = existing
        if dev["deviceProfileId"]["id"] != profile["id"]["id"] or dev.get("label") != spec.get("label"):
            dev["deviceProfileId"] = profile["id"]
            dev["label"] = spec.get("label")
            dev = tb.post("/api/device", dev)
        cred = tb.get(f"/api/device/{dev['id']['id']}/credentials")
        token = cred["credentialsId"]
        log(f"device {name} exists: {dev['id']['id']}")
    else:
        token = _token()
        body = {"device": {"name": name, "label": spec.get("label"), "deviceProfileId": profile["id"],
                           "additionalInfo": {"description": spec.get("label", "")}},
                "credentials": {"credentialsType": "ACCESS_TOKEN", "credentialsId": token}}
        dev = tb.post("/api/device-with-credentials", body)
        log(f"device {name} created: {dev['id']['id']}")
    attrs = {"latitude": spec["lat"], "longitude": spec["lon"], "inactivityTimeout": inactivity_ms,
             "site_label": spec.get("label", "")}
    if profile["name"] == C.PROFILE:
        attrs.update({k: "NONE" for k in C.ALM_KEYS})
    tb.post(f"/api/plugins/telemetry/DEVICE/{dev['id']['id']}/attributes/SERVER_SCOPE", attrs)
    return dev, token


def upsert_dashboard(tb: TB, default_device_id: str) -> dict:
    dash = _subst(C.dashboard(), {"DEFAULT_DEVICE_ID": default_device_id})
    existing = tb.find_by_name("/api/tenant/dashboards", C.DASHBOARD, field="title")
    if existing:
        dash["id"] = existing["id"]
        dash["version"] = tb.get(f"/api/dashboard/{existing['id']['id']}").get("version")
    saved = tb.post("/api/dashboard", dash)
    log(f"dashboard '{C.DASHBOARD}' {'updated' if existing else 'created'}: {saved['id']['id']}")
    return saved


def provision(url, user, password, out_dir, webhook_url=None, reset=False, verify=True,
              inactivity_ms=C.INACTIVITY_TIMEOUT_MS, tenant="default",
              install_rules=False, install_alarms=False, install_dashboard=False):
    os.makedirs(out_dir, exist_ok=True)
    written = C.write_all()                       # local files only; no ThingsBoard calls
    log("templates written (local files only): " + ", ".join(written))
    tb = TB(url, user, password, verify=verify)
    info = {
        "url": url, "tenant": tenant,
        "installed": {"rules": install_rules, "alarms": install_alarms, "dashboard": install_dashboard},
    }
    try:
        info["system_info"] = tb.get("/api/system/info")
    except TBError:
        info["system_info"] = None
    info["clock_offset_ms_http_date"] = tb.server_time_offset_ms()

    rc_id = ""
    if install_rules:
        info["tbel_tests"] = test_tbel(tb)
        rc_id = upsert_rule_chain(tb, webhook_url)["id"]["id"]
    else:
        log("SKIP rule chain (use --install-rules when you want the generated one)")

    def make_profile(name, description):
        template = C.device_profile(name, description)
        if not install_rules:
            # Without the generated rule chain, omit the override so ThingsBoard
            # routes device telemetry through its Root rule chain instead.
            template.pop("defaultRuleChainId", None)
        return upsert_profile(tb, template, rc_id)

    prof = make_profile(C.PROFILE, "JalDrishti post-flood water-quality node (ESP32-S3 + "
                                   "A7670E, simulated). Alarm rules: alarm_rules_cf.json")
    rig = make_profile(C.RIG_PROFILE, "Control and benchmark devices (no alarm rules)")

    info["alarm_rule_mode"] = "skipped"
    if install_alarms:
        info["alarm_rule_mode"] = install_alarm_rules(tb, prof)
    else:
        log("SKIP alarm rules (create them in the ThingsBoard UI, or use --install-alarms)")

    # Hybrid config: nodes.json holds control/bench nodes; nodes/*.json holds field nodes.
    spec = load_nodes(os.path.join(ROOT, "config"))
    tokens, devices = {}, {}
    for n in spec["nodes"]:
        d, t = upsert_device(tb, n, prof, reset, inactivity_ms)
        tokens[n["name"]], devices[n["name"]] = t, d["id"]["id"]
    for n in spec.get("control_nodes", []) + spec.get("bench_nodes", []):
        d, t = upsert_device(tb, n, rig, reset, inactivity_ms)
        tokens[n["name"]], devices[n["name"]] = t, d["id"]["id"]
    log(f"provisioned {len(tokens)} devices from config/nodes/ + config/nodes.json")

    if install_dashboard:
        dash = upsert_dashboard(tb, devices[spec["nodes"][0]["name"]])
        info["dashboard_id"] = dash["id"]["id"]
        log(f"dashboard available at {url}/dashboards/{dash['id']['id']}")
    else:
        log("SKIP dashboard: import thingsboard/dashboard.json in the UI, or build your own")

    info.update({
        "rule_chain_id": rc_id or None,
        "profile_id": prof["id"]["id"],
        "rig_profile_id": rig["id"]["id"],
        "devices": devices,
        "webhook_url": webhook_url,
        "inactivity_timeout_ms": inactivity_ms,
    })

    token_dir = os.path.join(out_dir, "tokens")
    os.makedirs(token_dir, exist_ok=True)
    token_path = os.path.join(token_dir, f"{tenant}.json")
    with open(token_path, "w", encoding="utf-8") as fh:
        json.dump(tokens, fh, indent=2)
    with open(os.path.join(out_dir, f"provision-{tenant}.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    log(f"done: tokens -> {token_path} ({len(tokens)} nodes)")
    log(f"done: ids    -> {os.path.join(out_dir, f'provision-{tenant}.json')}")
    return info, tokens


def main():
    ap = argparse.ArgumentParser(description="Provision the JalDrishti ThingsBoard backend")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--user", default="tenant@thingsboard.org")
    ap.add_argument("--password", default="tenant")
    ap.add_argument("--out", default=os.path.join(ROOT, "results"))
    ap.add_argument("--tenant", default="default",
                    help="token file slug: <out>/tokens/<tenant>.json (default: default)")
    ap.add_argument("--webhook-url")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the devices (clean history)")
    ap.add_argument("--inactivity-ms", type=int, default=C.INACTIVITY_TIMEOUT_MS)
    ap.add_argument("--install-rules", action="store_true", help="create the generated rule chain")
    ap.add_argument("--install-alarms", action="store_true", help="install generated alarm rules")
    ap.add_argument("--install-dashboard", action="store_true", help="push the generated dashboard")
    ap.add_argument("--install-all", action="store_true", help="all three of the above")
    a = ap.parse_args()
    provision(a.url, a.user, a.password, a.out, a.webhook_url, a.reset,
              inactivity_ms=a.inactivity_ms, tenant=a.tenant,
              install_rules=a.install_rules or a.install_all,
              install_alarms=a.install_alarms or a.install_all,
              install_dashboard=a.install_dashboard or a.install_all)


if __name__ == "__main__":
    main()
