"""Tests for the read-only metrics view in the control UI."""
from __future__ import annotations

import json


def test_metrics_page_explains_how_to_generate_metrics_when_missing(tmp_path):
    from tools.control_ui import render_metrics

    page = render_metrics(tmp_path)

    assert "No metrics yet" in page
    assert "tools.measure" in page
    assert "/\" back" in page or 'href="/"' in page


def test_metrics_page_shows_the_six_mandatory_values(tmp_path):
    from tools.control_ui import render_metrics

    metrics = {
        "source": "thingsboard",
        "run": "measure-20261126-000140",
        "latency_ms": {"server_stamped_live": {
            "n": 210, "mean": 39.5, "median": 35.0, "p95": 49.0, "max": 554.0}},
        "delivery": {"pilot_ratio": 1.0, "pilot_delivered": 720,
                     "pilot_generated": 720, "outage_s": 180,
                     "control_ratio_no_store_forward": 0.5},
        "alarm_delay_ms": {"live_threshold_alarms": {
            "n": 11, "mean": 397.2, "median": 203.0, "max": 1111.0}},
        "flush_throughput_rows_per_s": {"n": 1, "mean": 180.0},
        "recovery": [{"recovery_s_from_restore": 12.5, "rows_flushed": 90}],
        "data_loss": {"pilot_lost": 0, "pilot_lost_pct": 0.0},
    }
    (tmp_path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    page = render_metrics(tmp_path)

    for expected in ("measure-20261126-000140", "210", "39.5", "49.0",
                     "720 / 720", "180", "11", "397.2", "180.0", "12.5", "0"):
        assert expected in page, expected
    assert "Latency" in page and "Delivery" in page and "Alarm delay" in page
    assert "Flush throughput" in page and "Recovery" in page and "Data loss" in page


def test_control_page_links_to_metrics():
    from tools.control_ui import render_page

    assert 'href="/metrics"' in render_page("runs", "runs/control")
