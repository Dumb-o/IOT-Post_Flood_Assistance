"""Tests for tools.buffer_view, the read-only SQLite buffer viewer."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _make_db(path: Path, rows=(), *, evictions=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as c:
        c.executescript((ROOT / "edge" / "schema.sql").read_text(encoding="utf-8"))
        for seq, ts_ms, payload, breach, synced in rows:
            c.execute(
                "INSERT INTO telemetry_buffer "
                "(seq, ts_ms, payload, breach, synced, attempts) VALUES (?,?,?,?,?,1)",
                (seq, ts_ms, payload, breach, synced),
            )
        for i in range(evictions):
            c.execute(
                "INSERT INTO eviction_log "
                "(at_ms, tier, rows, oldest_ts, newest_ts, depth_after) "
                "VALUES (?,?,?,?,?,?)",
                (1000 + i, "unsynced_normal", 1, 1000, 1000, 0),
            )


def _compose_paths(tmp_path: Path, node="BID-03"):
    run = tmp_path / f"{node}-compose"
    return run / "db" / f"{node}.db", run / "events.jsonl"


def test_snapshot_reports_pending_counts_oldest_age_and_latest_rows(tmp_path):
    from tools.buffer_view import snapshot

    db, events = _compose_paths(tmp_path)
    events.parent.mkdir(parents=True)
    events.write_text("[]\n", encoding="utf-8")
    _make_db(
        db,
        [
            (1, 1000, '{"ph":7.0}', 0, 1),
            (2, 2000, '{"ph":7.1}', 0, 0),
            (3, 3000, '{"ph":6.0}', 1, 0),
            (4, 4000, '{"ph":7.2}', 0, 0),
        ],
        evictions=2,
    )

    snap = snapshot(db, now_ms=5000, rows=2)

    assert snap["node"] == "BID-03"
    assert (snap["total"], snap["synced"], snap["pending"], snap["pending_breach"]) == (4, 1, 3, 1)
    assert snap["oldest_pending_ts"] == 2000
    assert snap["oldest_pending_age_s"] == 3.0
    assert snap["last_sample_ts"] == 4000
    assert snap["last_sample_age_s"] == 1.0
    assert snap["evictions"] == 2
    assert [row["seq"] for row in snap["rows"]] == [4, 3]


def test_snapshot_handles_an_empty_buffer(tmp_path):
    from tools.buffer_view import snapshot

    db, events = _compose_paths(tmp_path)
    events.parent.mkdir(parents=True)
    events.write_text("", encoding="utf-8")
    _make_db(db)

    snap = snapshot(db, now_ms=5000)

    assert (snap["total"], snap["synced"], snap["pending"], snap["pending_breach"]) == (0, 0, 0, 0)
    assert snap["oldest_pending_ts"] is None
    assert snap["oldest_pending_age_s"] is None
    assert snap["last_sample_ts"] is None
    assert snap["last_sample_age_s"] is None
    assert snap["evictions"] == 0
    assert snap["rows"] == []
    assert snap["link"] == {}


def test_snapshot_reads_latest_link_event_for_its_own_node(tmp_path):
    from tools.buffer_view import snapshot

    db, events = _compose_paths(tmp_path)
    events.parent.mkdir(parents=True)
    records = [
        {"ev": "link", "node": "BID-03", "to": "ONLINE", "reason": "connected", "t_ms": 1000},
        {"ev": "link", "node": "BID-04", "to": "OFFLINE", "reason": "other", "t_ms": 9000},
        {"ev": "link", "node": "BID-03", "to": "OFFLINE", "reason": "blackhole", "t_ms": 4000},
        {"ev": "flush", "node": "BID-03", "rows": 5, "t_ms": 5000},
    ]
    events.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    _make_db(db)

    snap = snapshot(db, now_ms=5000)

    assert snap["link"] == {"state": "OFFLINE", "reason": "blackhole", "t_ms": 4000}


def test_discover_finds_only_compose_buffer_databases(tmp_path):
    from tools.buffer_view import discover

    runs = tmp_path / "runs"
    _make_db(runs / "BID-01-compose" / "db" / "BID-01.db")
    _make_db(runs / "BID-02-compose" / "db" / "BID-02.db")
    (runs / "notes.txt").write_text("ignore me", encoding="utf-8")

    found = discover(runs)

    assert [path.name for path in found] == ["BID-01.db", "BID-02.db"]


def test_render_text_shows_one_row_per_snapshot():
    from tools.buffer_view import render_text

    snaps = [
        {
            "node": "BID-03", "total": 3, "synced": 1, "pending": 2,
            "pending_breach": 1, "oldest_pending_age_s": 3.0,
            "last_sample_age_s": 2.0, "evictions": 4,
            "link": {"state": "OFFLINE", "reason": "blackhole", "t_ms": 1},
        }
    ]

    lines = render_text(snaps).splitlines()

    assert lines[0].split() == ["NODE", "TOTAL", "SYNCED", "PENDING", "BREACH", "OLDEST_S", "LAST_S", "LINK", "EVICT"]
    assert lines[1].split() == ["BID-03", "3", "1", "2", "1", "3.0", "2.0", "OFFLINE", "4"]


def test_sparkline_points_scales_a_series_into_an_svg_polyline():
    from tools.buffer_view import sparkline_points

    assert sparkline_points([0, 5, 10], width=10, height=4) == "0,4 5,2 10,0"


def test_render_html_escapes_payloads_and_includes_growth_history(tmp_path):
    from tools.buffer_view import render_html

    snaps = [
        {
            "node": "BID-03", "total": 3, "synced": 1, "pending": 2,
            "pending_breach": 1, "oldest_pending_age_s": 3.0,
            "last_sample_age_s": 2.0, "evictions": 4,
            "link": {"state": "OFFLINE", "reason": "blackhole", "t_ms": 1},
            "rows": [{"seq": 3, "ts_ms": 3000, "breach": 1, "synced": 0,
                      "attempts": 2, "payload": "<script>alert(1)</script>"}],
        }
    ]

    html = render_html(snaps, history={"BID-03": [0, 1, 2]}, refresh_s=2)

    assert 'content="2"' in html
    assert "BID-03" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '<polyline points="0,40 100,20 200,0"' in html


def test_collect_snapshots_discovers_databases_created_after_startup(tmp_path):
    from tools.buffer_view import collect_snapshots

    runs = tmp_path / "runs"
    runs.mkdir()
    history: dict[str, list[int]] = {}

    assert collect_snapshots(runs, rows=5, history=history) == []

    _make_db(runs / "BID-05-compose" / "db" / "BID-05.db")
    snaps = collect_snapshots(runs, rows=5, history=history)

    assert [snap["node"] for snap in snaps] == ["BID-05"]
    assert history["BID-05"] == [0]
