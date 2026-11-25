"""Tests for the additive browser control UI."""
from __future__ import annotations

import json
import stat


def test_parse_values_clamps_to_accepted_slider_ranges():
    from tools.control_ui import parse_values

    values = parse_values({
        "ph": ["99"],
        "turbidity_ntu": ["-5"],
        "tds_ppm": ["9999"],
        "water_temp_c": ["100"],
        "flow_lpm": ["-1"],
        "rssi_dbm": ["20"],
    })

    assert values == {
        "ph": 14.0,
        "turbidity_ntu": 0.0,
        "tds_ppm": 2000,
        "water_temp_c": 40.0,
        "flow_lpm": 0.0,
        "rssi_dbm": 0,
    }


def test_write_control_state_is_atomic_and_world_readable(tmp_path):
    from tools.control_ui import write_control_state

    values = {
        "ph": 6.1,
        "turbidity_ntu": 260.0,
        "tds_ppm": 780,
        "water_temp_c": 24.5,
        "flow_lpm": 2.0,
        "rssi_dbm": -95,
    }

    state = write_control_state(tmp_path, "BID-03", "override", values)

    path = tmp_path / "BID-03.json"
    assert json.loads(path.read_text(encoding="utf-8")) == state
    assert stat.S_IMODE(path.stat().st_mode) & 0o044 == 0o044
    assert list(tmp_path.glob("*.tmp")) == []


def test_apply_form_pause_keeps_override_state(tmp_path):
    from tools.control_ui import apply_form

    values = {
        "ph": "6.1",
        "turbidity_ntu": "260",
        "tds_ppm": "780",
        "water_temp_c": "24.5",
        "flow_lpm": "2",
        "rssi_dbm": "-95",
    }
    form = {"node": ["BID-03"], "action": ["override"], **{k: [v] for k, v in values.items()}}
    apply_form(tmp_path, form)

    form["action"] = ["pause"]
    state = apply_form(tmp_path, form)

    assert state["paused"] is True
    assert state["override"] is True
    assert state["values"]["ph"] == 6.1


def test_render_page_has_four_panels_buttons_and_sliders(tmp_path):
    from tools.control_ui import render_page

    page = render_page(tmp_path / "runs", tmp_path / "control")

    assert page.count('<section class="panel">') == 4
    for node in ("BID-01", "BID-02", "BID-03", "BID-04"):
        assert node in page
    for action in ("pause", "override", "random"):
        assert f'value="{action}"' in page
    for key in ("ph", "turbidity_ntu", "tds_ppm",
                "water_temp_c", "flow_lpm", "rssi_dbm"):
        assert f'name="{key}"' in page
