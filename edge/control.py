"""Control-file reader and state applier for the additive simulator control UI."""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

MANUAL_KEYS = (
    "ph",
    "turbidity_ntu",
    "tds_ppm",
    "water_temp_c",
    "flow_lpm",
    "rssi_dbm",
)

MANUAL_DEFAULTS = {
    "ph": 7.2,
    "turbidity_ntu": 4.0,
    "tds_ppm": 180,
    "water_temp_c": 20.0,
    "flow_lpm": 13.0,
    "rssi_dbm": -85,
}

_NODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_control(node: str) -> dict[str, Any]:
    """Return the exact state used when no valid control file exists."""
    return {
        "node": node,
        "paused": False,
        "override": False,
        "values": dict(MANUAL_DEFAULTS),
        "updated_ms": 0,
    }


def read_control(control_dir: str | Path, node: str) -> dict[str, Any]:
    """Read ``<control_dir>/<node>.json``; invalid input means no control."""
    default = default_control(node)
    if not _NODE_RE.fullmatch(str(node)):
        return default

    path = Path(control_dir) / f"{node}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default
    if not isinstance(raw, dict):
        return default

    raw_values = raw.get("values")
    if not isinstance(raw_values, dict):
        raw_values = {}
    values: dict[str, Any] = {}
    for key in MANUAL_KEYS:
        try:
            number = float(raw_values.get(key, MANUAL_DEFAULTS[key]))
        except (TypeError, ValueError, OverflowError):
            number = float(MANUAL_DEFAULTS[key])
        if not math.isfinite(number):
            number = float(MANUAL_DEFAULTS[key])
        if key in {"tds_ppm", "rssi_dbm"}:
            try:
                values[key] = int(round(number))
            except (ValueError, OverflowError):
                values[key] = int(MANUAL_DEFAULTS[key])
        else:
            values[key] = number

    try:
        updated_ms = int(raw.get("updated_ms", 0) or 0)
        if not math.isfinite(updated_ms):
            updated_ms = 0
    except (TypeError, ValueError, OverflowError):
        updated_ms = 0

    return {
        "node": node,
        "paused": raw.get("paused") is True,
        "override": raw.get("override") is True,
        "values": values,
        "updated_ms": updated_ms,
    }


def _same_values(left: dict[str, Any] | None, right: dict[str, Any]) -> bool:
    if not left:
        return False
    return all(left.get(key) == right.get(key) for key in MANUAL_KEYS)


def _record(log, events: Callable[[dict], None] | None, action: str,
            node: Any, **extra) -> None:
    name = getattr(node, "name", "?")
    record = {"ev": "control", "action": action, "node": name,
              "t_ms": int(time.time() * 1000), **extra}
    if log is not None:
        log.warning("CONTROL %s node=%s %s", action, name, extra)
    if events is not None:
        events(record)


def apply_control(node, control: dict[str, Any], log=None,
                  events: Callable[[dict], None] | None = None) -> None:
    """Apply only changed control state to ``node``; no-op for unchanged state."""
    previous = getattr(node, "_control_state", None) or {
        "paused": False,
        "override": False,
        "values": None,
    }
    target_paused = control.get("paused") is True
    target_override = control.get("override") is True
    target_values = dict(control.get("values") or {})
    old_paused = previous.get("paused") is True
    old_override = previous.get("override") is True

    if target_override and (
        not old_override or not _same_values(previous.get("values"), target_values)
    ):
        node.model.set_manual(target_values)
        _record(log, events, "override", node, values=target_values)
    elif not target_override and old_override:
        node.model.clear_manual()
        _record(log, events, "random", node)

    if target_paused != old_paused:
        node.link.set_paused(target_paused)
        _record(log, events, "pause" if target_paused else "resume", node,
                paused=target_paused)

    node._control_state = {
        "paused": target_paused,
        "override": target_override,
        "values": target_values if target_override else None,
    }
