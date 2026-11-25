"""Read-only live viewer for the edge SQLite buffers.

The simulator owns each database and writes it in WAL mode, so this viewer can
open the same file read-only while samples are still arriving::

    python -m tools.buffer_view --db-dir runs --port 8765

Open http://localhost:8765 in a browser.  For a one-shot terminal table::

    python -m tools.buffer_view --db-dir runs --once

Nothing is written back to the databases and no ThingsBoard entity is created.
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def discover(db_dir: str | Path, pattern: str = "*-compose/db/*.db") -> list[Path]:
    """Return the per-node SQLite files under a compose run directory."""
    return sorted(Path(db_dir).glob(pattern))


def _link_state(events_path: Path, node: str) -> dict:
    """Read the newest MQTT link transition belonging to this node."""
    if not events_path.is_file():
        return {}
    state: dict = {}
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("ev") == "link" and rec.get("node") == node:
            state = {
                "state": rec.get("to", "-"),
                "reason": rec.get("reason", ""),
                "t_ms": rec.get("t_ms", 0),
            }
    return state


def _age_s(ts_ms: int | None, now_ms: int) -> float | None:
    if ts_ms is None:
        return None
    return round(max(0, now_ms - int(ts_ms)) / 1000, 1)


def snapshot(db_path: str | Path, *, now_ms: int | None = None, rows: int = 10,
             events_path: str | Path | None = None) -> dict:
    """Take a read-only snapshot of one node's buffer and latest link event."""
    path = Path(db_path)
    now = time.time_ns() // 1_000_000 if now_ms is None else int(now_ms)
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2)
    try:
        total, synced, pending, pending_breach = conn.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(CASE WHEN synced=1 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN synced=0 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN synced=0 AND breach=1 THEN 1 ELSE 0 END),0) "
            "FROM telemetry_buffer"
        ).fetchone()
        oldest = conn.execute(
            "SELECT MIN(ts_ms) FROM telemetry_buffer WHERE synced=0"
        ).fetchone()[0]
        latest_ts = conn.execute("SELECT MAX(ts_ms) FROM telemetry_buffer").fetchone()[0]
        evictions = conn.execute(
            "SELECT COALESCE(SUM(rows),0) FROM eviction_log WHERE tier!='synced'"
        ).fetchone()[0]
        latest = [
            dict(zip(("seq", "ts_ms", "breach", "synced", "attempts", "payload"), rec))
            for rec in conn.execute(
                "SELECT seq, ts_ms, breach, synced, attempts, payload "
                "FROM telemetry_buffer ORDER BY seq DESC LIMIT ?", (int(rows),)
            )
        ]
    finally:
        conn.close()

    event_file = Path(events_path) if events_path is not None else path.parent.parent / "events.jsonl"
    return {
        "node": path.stem,
        "db": str(path),
        "total": int(total),
        "synced": int(synced),
        "pending": int(pending),
        "pending_breach": int(pending_breach),
        "oldest_pending_ts": oldest,
        "oldest_pending_age_s": _age_s(oldest, now),
        "last_sample_ts": latest_ts,
        "last_sample_age_s": _age_s(latest_ts, now),
        "evictions": int(evictions),
        "link": _link_state(event_file, path.stem),
        "rows": latest,
    }


def collect_snapshots(db_dir: str | Path, *, rows: int = 10,
                      history: dict[str, list[int]] | None = None) -> list[dict]:
    """Snapshot every buffer currently present, recording pending-depth history."""
    snaps = []
    for path in discover(db_dir):
        if not path.is_file():
            continue
        snap = snapshot(path, rows=rows)
        if history is not None:
            series = history.setdefault(snap["node"], [])
            series.append(snap["pending"])
            del series[:-120]
        snaps.append(snap)
    return snaps


def render_text(snaps: list[dict]) -> str:
    """Render the compact terminal table used for evidence logs."""
    header = ("NODE", "TOTAL", "SYNCED", "PENDING", "BREACH", "OLDEST_S", "LAST_S", "LINK", "EVICT")
    lines = [
        f"{header[0]:<10} {header[1]:>7} {header[2]:>7} {header[3]:>7} "
        f"{header[4]:>7} {header[5]:>9} {header[6]:>7} {header[7]:<8} {header[8]:>6}"
    ]
    for snap in snaps:
        oldest = snap["oldest_pending_age_s"]
        last = snap["last_sample_age_s"]
        link = snap["link"].get("state", "-")
        lines.append(
            f"{snap['node']:<10} {snap['total']:>7} {snap['synced']:>7} {snap['pending']:>7} "
            f"{snap['pending_breach']:>7} {'-' if oldest is None else f'{oldest:.1f}':>9} "
            f"{'-' if last is None else f'{last:.1f}':>7} {link:<8} {snap['evictions']:>6}"
        )
    return "\n".join(lines)


def sparkline_points(values: list[int], width: float = 200, height: float = 40) -> str:
    """Map pending depths onto an SVG polyline, with zero at the bottom."""
    if not values:
        return ""
    top = max(values) or 1
    step = width / max(len(values) - 1, 1)
    return " ".join(
        f"{round(i * step, 1):g},{round(height - (value / top) * height, 1):g}"
        for i, value in enumerate(values)
    )


def _card(snap: dict, points: str) -> str:
    link = snap["link"]
    oldest = snap["oldest_pending_age_s"]
    last = snap["last_sample_age_s"]
    rows = "\n".join(
        "<tr><td>{seq}</td><td>{ts}</td><td>{breach}</td><td>{synced}</td>"
        "<td>{attempts}</td><td class='payload'>{payload}</td></tr>".format(
            seq=html.escape(str(row["seq"])),
            ts=html.escape(str(row["ts_ms"])),
            breach="yes" if row["breach"] else "no",
            synced="yes" if row["synced"] else "NO",
            attempts=html.escape(str(row["attempts"])),
            payload=html.escape(str(row["payload"])),
        )
        for row in snap["rows"]
    )
    return f"""
    <section class="card">
      <h2>{html.escape(str(snap["node"]))}</h2>
      <div class="metrics">
        <div><b>{snap["pending"]}</b><span>pending</span></div>
        <div><b>{snap["synced"]}</b><span>synced</span></div>
        <div><b>{snap["total"]}</b><span>total rows</span></div>
        <div><b>{snap["pending_breach"]}</b><span>pending breaches</span></div>
        <div><b>{'-' if oldest is None else f'{oldest:.1f}s'}</b><span>oldest pending</span></div>
        <div><b>{'-' if last is None else f'{last:.1f}s'}</b><span>last sample</span></div>
        <div><b>{html.escape(str(link.get('state', '-')))}</b><span>MQTT link</span></div>
        <div><b>{snap["evictions"]}</b><span>evicted rows</span></div>
      </div>
      <p class="reason">{html.escape(str(link.get('reason', '')))}</p>
      <svg class="spark" viewBox="0 0 200 40" preserveAspectRatio="none">
        <polyline points="{points}" fill="none" stroke="#0d47a1" stroke-width="2"/>
      </svg>
      <table>
        <thead><tr><th>seq</th><th>ts_ms</th><th>breach</th><th>synced</th><th>tries</th><th>payload</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>"""


def render_html(snaps: list[dict], history: dict[str, list[int]], refresh_s: float = 1) -> str:
    """Render a dependency-free auto-refreshing HTML page."""
    cards = "\n".join(
        _card(snap, sparkline_points(history.get(snap["node"], []))) for snap in snaps
    ) or "<p>No SQLite buffers found yet. Start the edge services under <code>runs/</code>.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{refresh_s:g}">
  <title>JalDrishti SQLite buffer viewer</title>
  <style>
    body {{ font: 14px/1.4 system-ui, sans-serif; margin: 1.5rem; color: #17324d; background: #f4f7fa; }}
    h1 {{ font-size: 1.4rem; margin-bottom: .2rem; }}
    .hint {{ color: #557; margin-top: 0; }}
    .card {{ background: white; border: 1px solid #d5e0ea; border-radius: 8px; margin: 1rem 0; padding: 1rem; }}
    h2 {{ margin: 0 0 .6rem; font-size: 1.1rem; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: .5rem; }}
    .metrics div {{ background: #eef4fa; border-radius: 6px; padding: .5rem; }}
    .metrics b {{ display: block; font-size: 1.25rem; }}
    .metrics span {{ font-size: .75rem; color: #557; }}
    .reason {{ color: #a33; min-height: 1.2rem; }}
    .spark {{ width: 100%; height: 40px; background: #fafdff; border: 1px solid #e0e8f0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .78rem; }}
    th, td {{ border-bottom: 1px solid #e3e9ef; padding: .25rem; text-align: left; }}
    .payload {{ font-family: monospace; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>JalDrishti SQLite buffer viewer</h1>
  <p class="hint">Read-only view of <code>runs/&lt;node&gt;-compose/db/&lt;node&gt;.db</code>.
     Pending rows grow during a netgate blackhole and drain after restore.</p>
  {cards}
</body>
</html>"""


def _make_handler(refresh, history: dict, refresh_s: float):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            snaps = refresh()
            body = render_html(snaps, history, refresh_s).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live read-only view of edge SQLite buffers")
    parser.add_argument("--db-dir", default="runs", help="directory containing <node>-compose/db files")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--refresh", type=float, default=1.0, help="browser refresh interval in seconds")
    parser.add_argument("--rows", type=int, default=10, help="latest rows shown per node")
    parser.add_argument("--once", action="store_true", help="print one terminal snapshot and exit")
    args = parser.parse_args(argv)

    if args.once:
        print(render_text(collect_snapshots(args.db_dir, rows=args.rows)))
        return 0

    history: dict[str, list[int]] = {}

    def refresh() -> list[dict]:
        return collect_snapshots(args.db_dir, rows=args.rows, history=history)

    server = ThreadingHTTPServer((args.host, args.port), _make_handler(refresh, history, args.refresh))
    print(f"SQLite buffer viewer: http://{args.host}:{args.port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
