"""Generate every ThingsBoard artefact from ONE alarm/threshold table.

Running this module writes, under thingsboard/:
  device_profile.json        device profile "JalDrishti Water Node" (MQTT, JSON payload)
  device_profile_rig.json    profile for the control and benchmark devices (no alarm rules)
  alarm_rules_cf.json        ThingsBoard 4.3 alarm rules (calculated fields of type ALARM)
  alarm_rules_legacy.json    the same rules in the pre-4.3 device-profile "alarms" format
  rule_chain.json            rule chain "JalDrishti Ingest" (importable from the UI)
  dashboard.json             dashboard "JalDrishti - Bidur water safety" (importable from the UI)

Formats were taken from the ThingsBoard CE v4.3.0.1 sources (AlarmCalculatedFieldConfiguration,
alarm.rule.condition.*, TbMsgTypeSwitchNode, TbTransformMsgNode, TbMsgAttributesNode, the
thermostats demo dashboard, and the widget type descriptors) because they are imported blind.
tools/tb_provision.py substitutes the entity ids (${...}) at provisioning time.
"""
from __future__ import annotations

import copy
import json
import math
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TB = os.path.join(ROOT, "thingsboard")
PROFILE = "JalDrishti Water Node"
RIG_PROFILE = "JalDrishti Test Rig"
RULE_CHAIN = "JalDrishti Ingest"
DASHBOARD = "JalDrishti - Bidur water safety"

# Demo-time compression: 1 real second = 1 process minute (config/scenarios/*.json).
CLEAR_DWELL_S = 30          # Task 2: "< threshold for 30 min" -> 30 s at 60x
SUPPLY_ZERO_S = 30          # Task 2 R5: "flow = 0 for > 30 min" -> 30 s at 60x
INACTIVITY_TIMEOUT_MS = 60_000   # Task 2 R7: 3 upload intervals; demo value (see manuscript)

# ---------------------------------------------------------------------------
# The single alarm table (brief Table, Task 2 Table 8.3).  op tuples are
# (operation, value); a list means several predicates combined with 'join'.
# ---------------------------------------------------------------------------
ALARMS = [
    dict(type="High Turbidity", severity="WARNING", arg="turbidity", key="turbidity_ntu",
         create=dict(preds=[("GREATER", 50.0)]), clear=dict(preds=[("LESS", 40.0)], dwell=CLEAR_DWELL_S),
         attr="alm_turbidity_warn", details="Turbidity above 50 NTU: chlorination efficacy compromised"),
    dict(type="Gross Contamination", severity="CRITICAL", arg="turbidity", key="turbidity_ntu",
         create=dict(preds=[("GREATER", 200.0)]), clear=dict(preds=[("LESS", 150.0)], dwell=CLEAR_DWELL_S),
         attr="alm_turbidity_crit", details="Turbidity above 200 NTU: isolate the source"),
    dict(type="pH Out of Range", severity="CRITICAL", arg="ph", key="ph",
         create=dict(preds=[("LESS", 6.5), ("GREATER", 8.5)], join="OR"),
         clear=dict(preds=[("GREATER_OR_EQUAL", 6.7), ("LESS_OR_EQUAL", 8.3)], join="AND", dwell=CLEAR_DWELL_S),
         attr="alm_ph", details="pH outside NDWQS 6.5-8.5"),
    dict(type="High TDS", severity="CRITICAL", arg="tds", key="tds_ppm",
         create=dict(preds=[("GREATER", 500.0)]), clear=dict(preds=[("LESS", 450.0)], dwell=CLEAR_DWELL_S),
         attr="alm_tds", details="TDS above 500 mg/L: ingress of run-off"),
    dict(type="Supply Interruption", severity="MAJOR", arg="flow", key="flow_lpm",
         create=dict(preds=[("EQUAL", 0.0)], duration=SUPPLY_ZERO_S), clear=dict(preds=[("GREATER", 0.0)]),
         attr="alm_flow", details="No flow for the sustained period"),
]
NODE_INACTIVE = dict(type="Node Inactive", severity="CRITICAL", attr="alm_inactive")
ALARM_ATTR = {a["type"]: a["attr"] for a in ALARMS + [NODE_INACTIVE]}


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Alarm rules, 4.3 format (calculated field type ALARM on the device profile)
# ---------------------------------------------------------------------------
def _cf_condition(spec: dict, arg: str) -> dict:
    preds = [{"type": "NUMERIC", "operation": op, "value": {"staticValue": v}} for op, v in spec["preds"]]
    flt = {"argument": arg, "valueType": "NUMERIC", "predicates": preds}
    if len(preds) > 1:
        flt["operation"] = spec.get("join", "AND")
    expr = {"type": "SIMPLE", "filters": [flt], "operation": "AND"}
    secs = spec.get("duration") or spec.get("dwell")
    if secs:
        return {"type": "DURATION", "unit": "SECONDS", "value": {"staticValue": int(secs)}, "expression": expr}
    return {"type": "SIMPLE", "expression": expr}


def alarm_rules_cf() -> list:
    out = []
    for a in ALARMS:
        out.append({
            "type": "ALARM",
            "name": a["type"],                      # the CF name becomes the alarm type
            "debugSettings": {"failuresEnabled": True, "allEnabled": False, "allEnabledUntil": 0},
            "configuration": {
                "type": "ALARM",
                "arguments": {a["arg"]: {"refEntityKey": {"key": a["key"], "type": "TS_LATEST"}}},
                "createRules": {a["severity"]: {"condition": _cf_condition(a["create"], a["arg"]),
                                                "alarmDetails": a["details"]}},
                "clearRule": {"condition": _cf_condition(a["clear"], a["arg"])},
                "propagate": False, "propagateToOwner": False, "propagateToTenant": False,
                "propagateRelationTypes": [],
            },
        })
    return out


# ---------------------------------------------------------------------------
# Alarm rules, legacy (<= 4.2) device-profile format, used only as a fallback
# ---------------------------------------------------------------------------
def _legacy_condition(spec: dict, key: str) -> dict:
    preds = [{"type": "NUMERIC", "operation": op, "value": {"defaultValue": v, "userValue": None, "dynamicValue": None}}
             for op, v in spec["preds"]]
    pred = preds[0] if len(preds) == 1 else {"type": "COMPLEX", "operation": spec.get("join", "AND"), "predicates": preds}
    cond = {"condition": [{"key": {"type": "TIME_SERIES", "key": key}, "valueType": "NUMERIC",
                           "value": None, "predicate": pred}]}
    secs = spec.get("duration") or spec.get("dwell")
    cond["spec"] = ({"type": "DURATION", "unit": "SECONDS", "predicate": {"defaultValue": int(secs)}}
                    if secs else {"type": "SIMPLE"})
    return cond


def alarm_rules_legacy() -> list:
    return [{
        "id": a["attr"], "alarmType": a["type"],
        "createRules": {a["severity"]: {"condition": _legacy_condition(a["create"], a["key"]),
                                        "schedule": None, "alarmDetails": a["details"], "dashboardId": None}},
        "clearRule": {"condition": _legacy_condition(a["clear"], a["key"]), "schedule": None,
                      "alarmDetails": None, "dashboardId": None},
        "propagate": False, "propagateToOwner": False, "propagateToTenant": False, "propagateRelationTypes": None,
    } for a in ALARMS]


# ---------------------------------------------------------------------------
# Device profiles
# ---------------------------------------------------------------------------
def device_profile(name: str, description: str) -> dict:
    return {
        "name": name, "type": "DEFAULT", "transportType": "MQTT", "provisionType": "DISABLED",
        "description": description,
        "defaultRuleChainId": {"entityType": "RULE_CHAIN", "id": "${RULE_CHAIN_ID}"},
        "profileData": {
            "configuration": {"type": "DEFAULT"},
            "transportConfiguration": {
                "type": "MQTT",
                "deviceTelemetryTopic": "v1/devices/me/telemetry",
                "deviceAttributesTopic": "v1/devices/me/attributes",
                "deviceAttributesSubscribeTopic": "v1/devices/me/attributes",
                "sparkplug": False, "sendAckOnValidationException": False,
                "transportPayloadTypeConfiguration": {"transportPayloadType": "JSON"},
            },
            "provisionConfiguration": {"type": "DISABLED", "provisionDeviceSecret": None},
            "alarms": [],
        },
    }


# ---------------------------------------------------------------------------
# Rule chain "JalDrishti Ingest"
# ---------------------------------------------------------------------------
TBEL_STAMP = r"""// JalDrishti: stamp server receipt time and derive the Brown WQI (Task 2, Sec. 8.2).
// One message per client timestamp: metadata.ts is the node's acquisition time.
var out = msg;
out.rx_ms = Date.now();
if (msg.ph != null && msg.turbidity_ntu != null && msg.tds_ppm != null) {
    var dph = msg.ph - 7.0;
    if (dph < 0) { dph = -dph; }
    var tds = msg.tds_ppm;
    if (tds > 1000) { tds = 1000; }
    // q_i = 100 |V_i - V_0| / (S_i - V_0);  w = 0.36921, 0.62765, 0.00314 (sum 1)
    out.wqi = 0.36921 * (100.0 * dph / 1.5) + 0.62765 * (20.0 * msg.turbidity_ntu) + 0.00314 * (0.1 * tds);
}
return {msg: out, metadata: metadata, msgType: msgType};"""

TBEL_ALARM_ATTR = r"""// JalDrishti: mirror each alarm into a server attribute alm_<kind> = severity | NONE.
// The map marker colour, the status table and the node detail view read these keys.
var keys = %s;
var k = keys[msg.type];
if (k == null) { k = 'alm_other'; }
var st = msg.status;
var cleared = msgType == 'ALARM_CLEAR' || metadata.isClearedAlarm == 'true' || msg.cleared == true
              || (st != null && st.startsWith('CLEARED'));
var out = {};
out[k] = cleared ? 'NONE' : msg.severity;
out[k + '_ts'] = cleared ? msg.clearTs : msg.createdTime;
return {msg: out, metadata: metadata, msgType: 'POST_ATTRIBUTES_REQUEST'};""" % json.dumps(ALARM_ATTR)

TBEL_IS_NEW_CRITICAL = r"""return msg.severity == 'CRITICAL' && (metadata.isNewAlarm == 'true' || msgType == 'ALARM_CREATED');"""

TBEL_DETAILS = r"""var details = {};
details.reason = 'no telemetry within the inactivity timeout';
if (metadata.lastActivityTime != null) { details.lastActivityTime = metadata.lastActivityTime; }
return details;"""


def rule_chain(webhook: bool = True) -> dict:
    N = []

    def node(typ, name, cfg, x, y, ver=0):
        N.append({"type": typ, "name": name, "debugSettings": {"failuresEnabled": True, "allEnabled": False,
                                                              "allEnabledUntil": 0},
                  "singletonMode": False, "queueName": None, "configurationVersion": ver,
                  "configuration": cfg, "additionalInfo": {"description": "", "layoutX": x, "layoutY": y}})
        return len(N) - 1

    RE = "org.thingsboard.rule.engine."
    sw = node(RE + "filter.TbMsgTypeSwitchNode", "Message Type Switch", {"version": 0}, 300, 300)
    stamp = node(RE + "transform.TbTransformMsgNode", "Stamp rx_ms + WQI",
                 {"scriptLang": "TBEL", "jsScript": "return {msg: msg, metadata: metadata, msgType: msgType};",
                  "tbelScript": TBEL_STAMP}, 620, 120)
    ts = node(RE + "telemetry.TbMsgTimeseriesNode", "Save Timeseries (client ts)",
              {"defaultTTL": 0, "useServerTs": False, "processingSettings": {"type": "ON_EVERY_MESSAGE"}}, 920, 120, 1)
    cattr = node(RE + "telemetry.TbMsgAttributesNode", "Save Client Attributes",
                 {"processingSettings": {"type": "ON_EVERY_MESSAGE"}, "scope": "CLIENT_SCOPE", "notifyDevice": False,
                  "sendAttributesUpdatedNotification": False, "updateAttributesOnlyOnValueChange": True}, 620, 20, 3)
    inact = node(RE + "action.TbCreateAlarmNode", "Node Inactive: create",
                 {"alarmType": "Node Inactive", "severity": "CRITICAL", "scriptLang": "TBEL",
                  "alarmDetailsBuildJs": "return {};", "alarmDetailsBuildTbel": TBEL_DETAILS,
                  "propagate": False, "propagateToOwner": False, "propagateToTenant": False,
                  "useMessageAlarmData": False, "overwriteAlarmDetails": False, "dynamicSeverity": False,
                  "relationTypes": []}, 620, 420)
    act = node(RE + "action.TbClearAlarmNode", "Node Inactive: clear",
               {"alarmType": "Node Inactive", "scriptLang": "TBEL", "alarmDetailsBuildJs": "return {};",
                "alarmDetailsBuildTbel": "return {};"}, 620, 520)
    mirror = node(RE + "transform.TbTransformMsgNode", "Alarm -> alm_* attribute",
                  {"scriptLang": "TBEL", "jsScript": "return {msg: msg, metadata: metadata, msgType: msgType};",
                   "tbelScript": TBEL_ALARM_ATTR}, 920, 320)
    sattr = node(RE + "telemetry.TbMsgAttributesNode", "Save Server Attributes",
                 {"processingSettings": {"type": "ON_EVERY_MESSAGE"}, "scope": "SERVER_SCOPE", "notifyDevice": False,
                  "sendAttributesUpdatedNotification": False, "updateAttributesOnlyOnValueChange": False}, 1220, 320, 3)
    log_other = node(RE + "action.TbLogNode", "Log Other",
                     {"scriptLang": "TBEL", "jsScript": "return JSON.stringify(msg);",
                      "tbelScript": "return '\\nIncoming message:\\n' + JSON.stringify(msg) + '\\nIncoming metadata:\\n' + JSON.stringify(metadata);"},
                     620, 620)
    C = [(sw, stamp, "Post telemetry"), (stamp, ts, "Success"), (sw, cattr, "Post attributes"),
         (sw, inact, "Inactivity Event"), (sw, act, "Activity Event"),
         (sw, mirror, "Alarm Created"), (sw, mirror, "Alarm Updated"), (sw, mirror, "Alarm Severity Updated"),
         (sw, mirror, "Alarm Cleared"), (inact, mirror, "Created"), (act, mirror, "Cleared"),
         (mirror, sattr, "Success"), (sw, log_other, "Other")]
    if webhook:
        filt = node(RE + "filter.TbJsFilterNode", "New Critical?",
                    {"scriptLang": "TBEL", "jsScript": "return false;", "tbelScript": TBEL_IS_NEW_CRITICAL}, 920, 470)
        rest = node(RE + "rest.TbRestApiCallNode", "Notify (webhook)",
                    {"restEndpointUrlPattern": "${WEBHOOK_URL}", "requestMethod": "POST",
                     "useSimpleClientHttpFactory": False, "parseToPlainText": False, "ignoreRequestBody": False,
                     "enableProxy": False, "useSystemProxyProperties": False, "proxyScheme": None,
                     "proxyHost": None, "proxyPort": 0, "proxyUser": None, "proxyPassword": None,
                     "readTimeoutMs": 5000, "maxParallelRequestsCount": 0,
                     "headers": {"Content-Type": "application/json"}, "credentials": {"type": "anonymous"},
                     "maxInMemoryBufferSizeInKb": 256}, 1220, 470, 3)
        C += [(sw, filt, "Alarm Created"), (inact, filt, "Created"), (filt, rest, "True")]
    return {
        "ruleChain": {"additionalInfo": {"description": "JalDrishti: rx stamp, WQI, alarm mirror, inactivity, webhook"},
                      "name": RULE_CHAIN, "type": "CORE", "firstRuleNodeId": None, "root": False,
                      "debugMode": False, "configuration": None},
        "metadata": {"firstNodeIndex": sw, "nodes": N, "ruleChainConnections": None,
                     "connections": [{"fromIndex": f, "toIndex": t, "type": r} for f, t, r in C]},
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
REF = os.path.join(ROOT, "thingsboard", "_widget_defaults")
SEV_COLOR_JS = """var s = [data.alm_turbidity_crit, data.alm_ph, data.alm_tds, data.alm_inactive,
         data.alm_flow, data.alm_turbidity_warn];
var worst = 'NONE';
for (var i = 0; i < s.length; i++) {
  if (s[i] === 'CRITICAL') { worst = 'CRITICAL'; break; }
  if (s[i] === 'MAJOR' && worst !== 'CRITICAL') { worst = 'MAJOR'; }
  if (s[i] === 'WARNING' && worst === 'NONE') { worst = 'WARNING'; }
}"""
COLORS = {"CRITICAL": "#D32F2F", "MAJOR": "#F57C00", "WARNING": "#FBC02D", "NONE": "#2E7D32"}
COLOR_JS = SEV_COLOR_JS + "\nreturn ({CRITICAL:'%s', MAJOR:'%s', WARNING:'%s', NONE:'%s'})[worst];" % (
    COLORS["CRITICAL"], COLORS["MAJOR"], COLORS["WARNING"], COLORS["NONE"])
ALM_KEYS = ["alm_turbidity_crit", "alm_ph", "alm_tds", "alm_inactive", "alm_flow", "alm_turbidity_warn"]


def _defaults(name):
    d = json.load(open(os.path.join(REF, name + ".json"), encoding="utf-8"))
    return json.loads(d["descriptor"]["defaultConfig"])


def _dk(name, typ, label, color="#2196f3", units=None, decimals=None, settings=None, post=None):
    k = {"name": name, "type": typ, "label": label, "color": color, "settings": settings or {},
         "_hash": round(abs(hash(name + label)) % 10**6 / 10**6, 6)}
    if units is not None:
        k["units"] = units
    if decimals is not None:
        k["decimals"] = decimals
    if post:
        k["usePostProcessing"] = True
        k["postFuncBody"] = post
    return k


def _ds(alias, keys, latest=None):
    d = {"type": "entity", "name": "", "entityAliasId": alias, "filterId": None, "dataKeys": keys}
    if latest is not None:
        d["latestDataKeys"] = latest
    return d


def _widget(fqn, wtype, cfg, sx, sy):
    wid = _uid()
    return wid, {"typeFullFqn": fqn, "type": wtype, "sizeX": sx, "sizeY": sy, "config": cfg, "row": 0, "col": 0,
                 "id": wid}


def _title(cfg, title, icon=None):
    cfg["title"] = title
    cfg["showTitle"] = True
    if icon:
        cfg["showTitleIcon"] = True
        cfg["titleIcon"] = icon
    return cfg


def w_status_table(alias_all, title="Which source is unsafe now?"):
    cfg = _defaults("entities_table")
    status_cell = SEV_COLOR_JS.replace("data.", "entity.") + """
var label = ({CRITICAL:'UNSAFE - Critical', MAJOR:'Supply problem', WARNING:'Caution', NONE:'Safe'})[worst];
var bg = ({CRITICAL:'%s', MAJOR:'%s', WARNING:'%s', NONE:'%s'})[worst];
return '<span style="background:' + bg + ';color:#fff;border-radius:12px;padding:3px 10px;font-weight:600">' + label + '</span>';""" % (
        COLORS["CRITICAL"], COLORS["MAJOR"], COLORS["WARNING"], COLORS["NONE"])
    keys = [
        _dk("label", "entityField", "Source"),
        _dk("alm_turbidity_crit", "attribute", "Status",
            settings={"useCellContentFunction": True, "cellContentFunction": status_cell, "columnWidth": "150px"}),
        _dk("turbidity_ntu", "timeseries", "Turbidity", units="NTU", decimals=1),
        _dk("ph", "timeseries", "pH", decimals=2),
        _dk("tds_ppm", "timeseries", "TDS", units="mg/L", decimals=0),
        _dk("flow_lpm", "timeseries", "Flow", units="L/min", decimals=1),
    ] + [_dk(k, "attribute", k, settings={"columnWidth": "0px", "defaultColumnVisibility": "hidden",
                                          "columnSelectionToDisplay": "disabled"}) for k in ALM_KEYS[1:]]
    cfg["datasources"] = [_ds(alias_all, keys)]
    cfg["settings"].update({"enableSearch": False, "displayPagination": False, "defaultPageSize": 10,
                            "defaultSortOrder": "entityName", "displayEntityName": True,
                            "entityNameColumnTitle": "Node", "displayEntityLabel": False,
                            "displayEntityType": False, "entitiesTitle": title,
                            "enableSelectColumnDisplay": False})
    cfg["actions"] = {
        "rowClick": [{"id": _uid(), "name": "Select node", "icon": "more_horiz", "type": "updateDashboardState",
                      "targetDashboardStateId": "default", "setEntityId": True, "stateEntityParamName": None,
                      "openRightLayout": False, "openInSeparateDialog": False, "openInPopover": False}],
        "actionCellButton": [{"id": _uid(), "name": "Node detail", "icon": "insights", "type": "openDashboardState",
                              "targetDashboardStateId": "node", "setEntityId": True, "stateEntityParamName": None,
                              "openRightLayout": False, "openInSeparateDialog": False, "openInPopover": False}],
    }
    return _widget("system.cards.entities_table", "latest", _title(cfg, title, "water_drop"), 10, 5)


def w_map(alias_all):
    th = json.load(open(os.path.join(ROOT, "thingsboard", "_widget_defaults", "thermostats_map.json")))
    cfg = copy.deepcopy(th["config"])
    m = cfg["settings"]["markers"][0]
    m["dsEntityAliasId"] = alias_all
    m["additionalDataKeys"] = ([_dk(k, "attribute", k) for k in ALM_KEYS] +
                               [_dk("turbidity_ntu", "timeseries", "turbidity_ntu"), _dk("ph", "timeseries", "ph"),
                                _dk("tds_ppm", "timeseries", "tds_ppm"), _dk("active", "attribute", "active")])
    m["label"] = {"show": True, "type": "function", "pattern": "${entityLabel}",
                  "patternFunction": SEV_COLOR_JS + """
var c = ({CRITICAL:'%s', MAJOR:'%s', WARNING:'%s', NONE:'%s'})[worst];
return '<span style="border:2px solid ' + c + ';border-radius:10px;color:' + c + ';background:#fff;padding:2px 6px;font-size:13px;font-weight:600">${entityName}: ' + (worst === 'NONE' ? 'safe' : worst) + '</span>';""" % (
                      COLORS["CRITICAL"], COLORS["MAJOR"], COLORS["WARNING"], COLORS["NONE"])}
    m["tooltip"]["pattern"] = ("<b>${entityLabel}</b><br/>Turbidity: ${turbidity_ntu:1} NTU<br/>pH: ${ph:2}<br/>"
                               "TDS: ${tds_ppm:0} mg/L<br/><link-act name=\"node_detail\">Open node detail</link-act>")
    m["tooltip"]["tagActions"] = [{"name": "node_detail", "type": "openDashboardState",
                                   "targetDashboardStateId": "node", "setEntityId": True, "stateEntityParamName": None,
                                   "openRightLayout": False, "openInSeparateDialog": False, "openInPopover": False,
                                   "id": _uid()}]
    m["markerIcon"] = {"size": 44, "color": {"type": "function", "color": COLORS["NONE"], "range": None,
                                             "colorFunction": COLOR_JS},
                       "iconContainer": "iconContainer1", "icon": "water_drop"}
    m["xKey"] = {"name": "latitude", "label": "latitude", "type": "attribute", "settings": {}, "color": "#2196f3"}
    m["yKey"] = {"name": "longitude", "label": "longitude", "type": "attribute", "settings": {}, "color": "#2196f3"}
    cfg["settings"]["defaultZoomLevel"] = 14
    cfg["settings"]["fitMapBounds"] = True
    cfg = _title(cfg, "Sources - marker colour = worst active alarm", "map")
    cfg["useDashboardTimewindow"] = True
    return _widget("system.map", "latest", cfg, 14, 5)


def w_radial(alias_sel, key, label, units, vmin, vmax, highlights, decimals, ticks, post=None, title=None,
             valuebox=True):
    cfg = _defaults("radial_gauge")
    cfg["datasources"] = [_ds(alias_sel, [_dk(key, "timeseries", label, units=units, decimals=decimals, post=post)])]
    s = cfg["settings"]
    s.update({"minValue": vmin, "maxValue": vmax, "highlights": highlights, "highlightsWidth": 12,
              "majorTicksCount": ticks, "minorTicks": 5, "unitTitle": units, "showUnitTitle": True,
              "valueBox": valuebox, "valueInt": 1, "defaultColor": "#e65100",
              "valueFont": {**s["valueFont"], "family": "Roboto", "size": 30, "weight": "500"}})
    cfg["decimals"] = decimals
    cfg["units"] = units
    return _widget("system.analogue_gauges.radial_gauge_canvas_gauges", "latest",
                   _title(cfg, title or label, None), 5, 4)


def w_tds(alias_sel):
    cfg = _defaults("gauge")
    cfg["datasources"] = [_ds(alias_sel, [_dk("tds_ppm", "timeseries", "TDS", units="mg/L", decimals=0)])]
    s = cfg["settings"]
    pv = lambda v: {"valueSource": "predefinedValue", "value": v}  # noqa: E731
    s.update({"minValue": 0, "maxValue": 1500, "gaugeType": "arc", "showValue": True, "showMinMax": True,
              "useFixedLevelColor": True, "levelColors": ["#2E7D32", "#FBC02D", "#D32F2F"],
              "fixedLevelColors": [{"from": pv(0), "to": pv(500), "color": "#2E7D32"},
                                   {"from": pv(500), "to": pv(1000), "color": "#FBC02D"},
                                   {"from": pv(1000), "to": pv(1500), "color": "#D32F2F"}],
              "showTicks": True, "ticksValue": [{"type": "constant", "value": 500}, {"type": "constant", "value": 1000}],
              "units": "mg/L", "decimals": 0, "showUnitTitle": True, "unitTitle": "mg/L",
              "title": "TDS", "showTitle": False})
    return _widget("system.digital_gauges.gauge_justgage", "latest",
                   _title(cfg, "TDS (bands 500 / 1000 mg/L)"), 5, 4)


def w_value(alias_sel, key, label, units, decimals, icon, title):
    cfg = _defaults("value_card")
    cfg["datasources"] = [_ds(alias_sel, [_dk(key, "timeseries", label, units=units, decimals=decimals)])]
    cfg["units"] = units
    cfg["decimals"] = decimals
    cfg["settings"]["icon"] = icon
    cfg["settings"]["label"] = title
    cfg["settings"]["showLabel"] = True
    return _widget("system.cards.value_card", "latest", _title(cfg, title), 3, 2)


def _yaxis(base, aid, units, pos, order, decimals=0, vmin=None, vmax=None):
    a = copy.deepcopy(base)
    a.update({"id": aid, "units": units, "position": pos, "order": order, "decimals": decimals, "label": units,
              "show": True})
    if vmin is not None:
        a["min"] = vmin
    if vmax is not None:
        a["max"] = vmax
    return a


def _series(name, label, color, axis, units, decimals, base_settings):
    st = copy.deepcopy(base_settings)
    st.update({"yAxisId": axis, "showInLegend": True, "dataHiddenByDefault": False, "type": "line"})
    st.setdefault("lineSettings", {})
    st["lineSettings"].update({"showLine": True, "smooth": False, "lineWidth": 2, "showPoints": False})
    return _dk(name, "timeseries", label, color=color, units=units, decimals=decimals, settings=st)


def w_multi_chart(alias_sel):
    cfg = _defaults("time_series_chart")
    th = json.load(open(os.path.join(REF, "thermostats_chart.json")))
    base_series = th["config"]["datasources"][0]["dataKeys"][0]["settings"]
    yb = cfg["settings"]["yAxes"]["default"]
    cfg["settings"]["yAxes"] = {
        "ntu": _yaxis(yb, "ntu", "NTU", "left", 0, 0, 0),
        "mgl": _yaxis(yb, "mgl", "mg/L", "right", 1, 0, 0),
        "ph": _yaxis(yb, "ph", "pH", "right", 2, 1, 5, 9),
    }
    cfg["datasources"] = [_ds(alias_sel, [
        _series("turbidity_ntu", "Turbidity", "#8D6E63", "ntu", "NTU", 1, base_series),
        _series("tds_ppm", "TDS", "#1E88E5", "mgl", "mg/L", 0, base_series),
        _series("ph", "pH", "#D81B60", "ph", "", 2, base_series),
    ], latest=[])]
    thr = lambda v, axis, color, lbl: {"type": "constant", "value": v, "yAxisId": axis, "units": "",  # noqa: E731
                                      "decimals": 0, "lineColor": color, "lineType": "dashed", "lineWidth": 1,
                                      "startSymbol": "none", "startSymbolSize": 5, "endSymbol": "none",
                                      "endSymbolSize": 5, "showLabel": True, "labelPosition": "end",
                                      "labelFont": {"size": 11, "family": "Roboto", "weight": "500",
                                                    "style": "normal"}, "labelColor": color}
    cfg["settings"]["thresholds"] = [thr(50, "ntu", "#FBC02D", "50"), thr(200, "ntu", "#D32F2F", "200"),
                                     thr(500, "mgl", "#1E88E5", "500")]
    cfg["settings"]["dataZoom"] = True
    cfg["useDashboardTimewindow"] = True
    return _widget("system.time_series_chart", "timeseries",
                   _title(cfg, "Selected node: turbidity, TDS and pH (event and backfill)", "show_chart"), 16, 6)


def w_temp_flow(alias_sel):
    cfg = _defaults("time_series_chart")
    th = json.load(open(os.path.join(REF, "thermostats_chart.json")))
    base_series = th["config"]["datasources"][0]["dataKeys"][0]["settings"]
    yb = cfg["settings"]["yAxes"]["default"]
    cfg["settings"]["yAxes"] = {"degc": _yaxis(yb, "degc", "°C", "left", 0, 1),
                                "lpm": _yaxis(yb, "lpm", "L/min", "right", 1, 0, 0)}
    cfg["datasources"] = [_ds(alias_sel, [
        _series("water_temp_c", "Water temperature", "#FB8C00", "degc", "°C", 1, base_series),
        _series("flow_lpm", "Flow", "#00897B", "lpm", "L/min", 1, base_series),
    ], latest=[])]
    cfg["useDashboardTimewindow"] = True
    return _widget("system.time_series_chart", "timeseries",
                   _title(cfg, "Selected node: temperature and flow", "thermostat"), 12, 5)


def w_alarms(alias_all, title="Alarms - active and cleared"):
    cfg = _defaults("alarms_table")
    dur = """var end = (alarm.clearTs && alarm.clearTs > 0) ? alarm.clearTs : Date.now();
var s = Math.max(0, Math.round((end - alarm.createdTime) / 1000));
var m = Math.floor(s / 60);
return (m > 0 ? m + ' min ' : '') + (s % 60) + ' s' + ((alarm.clearTs && alarm.clearTs > 0) ? '' : ' (ongoing)');"""
    sev = """var c = ({CRITICAL:'%s', MAJOR:'%s', MINOR:'#FFA000', WARNING:'%s', INDETERMINATE:'#757575'})[value] || '#000';
return {color: c, fontWeight: 600};""" % (COLORS["CRITICAL"], COLORS["MAJOR"], COLORS["WARNING"])
    keys = [
        _dk("createdTime", "alarm", "Created"),
        _dk("originatorLabel", "alarm", "Source"),
        _dk("type", "alarm", "Type"),
        _dk("severity", "alarm", "Severity", settings={"useCellStyleFunction": True, "cellStyleFunction": sev}),
        _dk("status", "alarm", "Status"),
        _dk("clearTime", "alarm", "Cleared"),
        _dk("startTime", "alarm", "Duration", settings={"useCellContentFunction": True, "cellContentFunction": dur}),
    ]
    cfg["alarmSource"] = {"type": "entity", "name": "alarms", "entityAliasId": alias_all, "filterId": None,
                          "dataKeys": keys}
    cfg["datasources"] = []
    cfg["alarmFilterConfig"] = {"statusList": [], "severityList": [], "typeList": [], "searchPropagatedAlarms": False}
    cfg["settings"].update({"enableSearch": True, "displayDetails": True, "allowAcknowledgment": True,
                            "allowClear": True, "displayPagination": True, "defaultPageSize": 10,
                            "defaultSortOrder": "-createdTime", "alarmsTitle": title, "enableFilter": True})
    cfg["useDashboardTimewindow"] = False
    cfg["timewindow"] = {"realtime": {"realtimeType": 1, "interval": 1000, "timewindowMs": 86400000},
                         "aggregation": {"type": "NONE", "limit": 500}}
    cfg["alarmsPollingInterval"] = 2
    return _widget("system.alarm_widgets.alarms_table", "alarm", _title(cfg, title, "notifications"), 8, 6)


def w_node_status(alias_all):
    cfg = _defaults("entities_table")
    ago = """if (!value) return '-';
var s = Math.round((Date.now() - value) / 1000);
return new Date(value).toLocaleTimeString() + ' (' + s + ' s ago)';"""
    dot = """return value === 'true' || value === true ? '<span style="color:#2E7D32">&#11044; online</span>' : '<span style="color:#D32F2F">&#11044; inactive</span>';"""
    depth_style = "var v = Number(value); return v > 0 ? {color:'#F57C00', fontWeight:600} : {};"
    keys = [
        _dk("active", "attribute", "State", settings={"useCellContentFunction": True, "cellContentFunction": dot}),
        _dk("lastActivityTime", "attribute", "Last activity",
            settings={"useCellContentFunction": True, "cellContentFunction": ago}),
        _dk("rssi_dbm", "timeseries", "RSSI", units="dBm", decimals=0),
        _dk("buffer_depth", "timeseries", "Buffer depth (rows)", decimals=0,
            settings={"useCellStyleFunction": True, "cellStyleFunction": depth_style}),
        _dk("wqi", "timeseries", "WQI", decimals=0),
    ]
    cfg["datasources"] = [_ds(alias_all, keys)]
    cfg["settings"].update({"enableSearch": False, "displayPagination": False, "defaultSortOrder": "entityName",
                            "displayEntityName": True, "entityNameColumnTitle": "Node", "displayEntityLabel": False,
                            "displayEntityType": False, "entitiesTitle": "Node health"})
    return _widget("system.cards.entities_table", "latest", _title(cfg, "Node health: activity, RSSI, buffer", "router"),
                   12, 5)


def w_node_series(alias_sel, keys, title, h=5):
    cfg = _defaults("time_series_chart")
    th = json.load(open(os.path.join(REF, "thermostats_chart.json")))
    base_series = th["config"]["datasources"][0]["dataKeys"][0]["settings"]
    yb = cfg["settings"]["yAxes"]["default"]
    cfg["settings"]["yAxes"] = {}
    series = []
    for i, (k, label, units, color, dec) in enumerate(keys):
        aid = "a%d" % i
        cfg["settings"]["yAxes"][aid] = _yaxis(yb, aid, units, "left" if i == 0 else "right", i, dec)
        series.append(_series(k, label, color, aid, units, dec, base_series))
    cfg["datasources"] = [_ds(alias_sel, series, latest=[])]
    cfg["useDashboardTimewindow"] = True
    return _widget("system.time_series_chart", "timeseries", _title(cfg, title, "show_chart"), 12, h)


def dashboard() -> dict:
    alias_all, alias_sel = _uid(), _uid()
    widgets, layout = {}, {}

    def place(item, row, col, state_layout):
        wid, w = item
        w["row"], w["col"] = row, col
        widgets[wid] = w
        state_layout[wid] = {"sizeX": w["sizeX"], "sizeY": w["sizeY"], "row": row, "col": col,
                             "mobileOrder": len(state_layout) + 1, "mobileHeight": max(3, w["sizeY"])}

    main, detail = {}, {}
    log_post = "return value > 0 ? Math.log(Math.max(value, 1)) / Math.LN10 : 0;"
    place(w_status_table(alias_all), 0, 0, main)
    place(w_map(alias_all), 0, 10, main)
    place(w_radial(alias_sel, "ph", "pH", "pH", 4, 10,
                   [{"from": 4, "to": 6.5, "color": "#D32F2F"}, {"from": 6.5, "to": 8.5, "color": "#2E7D32"},
                    {"from": 8.5, "to": 10, "color": "#D32F2F"}], 2, 6, title="pH (safe band 6.5-8.5)"), 5, 0, main)
    place(w_radial(alias_sel, "turbidity_ntu", "Turbidity", "log10 NTU", 0, 3,
                   [{"from": 0, "to": math.log10(5), "color": "#2E7D32"},
                    {"from": math.log10(5), "to": math.log10(50), "color": "#FBC02D"},
                    {"from": math.log10(50), "to": math.log10(200), "color": "#F57C00"},
                    {"from": math.log10(200), "to": 3, "color": "#D32F2F"}], 2, 3, post=log_post,
                   title="Turbidity, log scale (1-10-100-1000 NTU; bands 5/50/200)", valuebox=False), 5, 5, main)
    place(w_value(alias_sel, "turbidity_ntu", "Turbidity", "NTU", 1, "opacity", "Turbidity now"), 5, 10, main)
    place(w_value(alias_sel, "wqi", "WQI", "", 0, "water", "WQI (Brown)"), 7, 10, main)
    place(w_tds(alias_sel), 5, 13, main)
    place(w_value(alias_sel, "buffer_depth", "Buffer", "rows", 0, "storage", "Edge buffer depth"), 5, 18, main)
    place(w_value(alias_sel, "rssi_dbm", "RSSI", "dBm", 0, "signal_cellular_alt", "RSSI"), 7, 18, main)
    place(w_value(alias_sel, "flow_lpm", "Flow", "L/min", 1, "water_pump", "Flow"), 5, 21, main)
    place(w_value(alias_sel, "water_temp_c", "Temp", "°C", 1, "thermostat", "Water temp"), 7, 21, main)
    place(w_multi_chart(alias_sel), 9, 0, main)
    place(w_alarms(alias_all), 9, 16, main)
    place(w_temp_flow(alias_sel), 15, 0, main)
    place(w_node_status(alias_all), 15, 12, main)
    # node detail state (opened from the map tooltip or the status table button)
    place(w_multi_chart(alias_sel), 0, 0, detail)
    place(w_alarms(alias_sel, "This node's alarms"), 0, 16, detail)
    place(w_node_series(alias_sel, [("buffer_depth", "Buffer depth", "rows", "#F57C00", 0),
                                    ("rssi_dbm", "RSSI", "dBm", "#5E35B1", 0)],
                        "Buffer depth and RSSI (store-and-forward)"), 6, 0, detail)
    place(w_node_series(alias_sel, [("water_temp_c", "Water temperature", "°C", "#FB8C00", 1),
                                    ("flow_lpm", "Flow", "L/min", "#00897B", 1),
                                    ("wqi", "WQI", "", "#455A64", 0)],
                        "Temperature, flow and WQI"), 6, 12, detail)
    grid = {"backgroundColor": "#eceff1", "columns": 24, "margin": 8, "outerMargin": True,
            "backgroundSizeMode": "100%", "autoFillHeight": False, "rowHeight": 62, "mobileAutoFillHeight": False,
            "mobileRowHeight": 70, "layoutType": "default"}
    return {
        "title": DASHBOARD, "image": None, "mobileHide": False, "mobileOrder": None,
        "configuration": {
            "description": "JalDrishti post-flood water-safety monitoring, Bidur pilot (ITS67404 Task 3, Group 07)",
            "widgets": widgets,
            "states": {
                "default": {"name": "JalDrishti - Bidur: which source is unsafe now?", "root": True,
                            "layouts": {"main": {"widgets": main, "gridSettings": grid}}},
                "node": {"name": "${entityLabel} (${entityName}) - detail", "root": False,
                         "layouts": {"main": {"widgets": detail, "gridSettings": grid}}},
            },
            "entityAliases": {
                alias_all: {"id": alias_all, "alias": "Pilot nodes",
                            "filter": {"type": "deviceType", "resolveMultiple": True, "deviceNameFilter": "",
                                       "deviceTypes": [PROFILE]}},
                alias_sel: {"id": alias_sel, "alias": "Selected node",
                            "filter": {"type": "stateEntity", "resolveMultiple": False, "stateEntityParamName": None,
                                       "defaultStateEntity": {"entityType": "DEVICE", "id": "${DEFAULT_DEVICE_ID}"}}},
            },
            "filters": {},
            "timewindow": {"displayValue": "", "selectedTab": 0, "hideAggregation": False, "hideAggInterval": False,
                           "realtime": {"realtimeType": 0, "interval": 1000, "timewindowMs": 900000,
                                        "quickInterval": "CURRENT_DAY"},
                           "history": {"historyType": 0, "interval": 1000, "timewindowMs": 900000},
                           "aggregation": {"type": "NONE", "limit": 50000}},
            "settings": {"stateControllerId": "entity", "showTitle": True, "showDashboardsSelect": False,
                         "showEntitiesSelect": False, "showDashboardTimewindow": True, "showDashboardExport": True,
                         "toolbarAlwaysOpen": True, "titleColor": "rgba(0,0,0,0.870588)", "showDashboardLogo": False,
                         "hideToolbar": False, "showFilters": False, "showUpdateDashboardImage": False,
                         "dashboardCss": ""},
        },
        "name": DASHBOARD, "resources": [],
    }


def write_all():
    os.makedirs(TB, exist_ok=True)
    out = {
        "device_profile.json": device_profile(PROFILE, "JalDrishti post-flood water-quality node (ESP32-S3 + "
                                                       "A7670E, simulated). Alarm rules: alarm_rules_cf.json"),
        "device_profile_rig.json": device_profile(RIG_PROFILE, "Control (no store-and-forward) and batch-size "
                                                               "benchmark devices; no alarm rules"),
        "alarm_rules_cf.json": alarm_rules_cf(),
        "alarm_rules_legacy.json": alarm_rules_legacy(),
        "rule_chain.json": rule_chain(webhook=True),
        "dashboard.json": dashboard(),
    }
    for name, obj in out.items():
        with open(os.path.join(TB, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
    return list(out)


if __name__ == "__main__":
    for n in write_all():
        print("wrote thingsboard/" + n)
