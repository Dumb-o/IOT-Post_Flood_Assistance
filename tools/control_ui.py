"""Host-side browser control window for the additive simulator controls.

Run from the project root::

    .venv/bin/python -m tools.control_ui --db-dir runs --port 8766

Open http://localhost:8766. This tool writes only ``runs/control/<NODE>.json``;
it never touches Docker, ThingsBoard, certificates, tokens or SQLite.

The read-only ``/metrics`` tab displays the last ``tools/measure.py`` output from
``results/measure-metrics/metrics.json``. It never runs a measurement itself.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from edge.control import MANUAL_DEFAULTS, MANUAL_KEYS, read_control
from tools.buffer_view import snapshot

NODES = ("BID-01", "BID-02", "BID-03", "BID-04")
_ACTIONS = ("pause", "override", "random")
_NODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# key: (minimum, maximum, step)
SLIDERS = {
    "ph": (0, 14, 0.01),
    "turbidity_ntu": (0, 1000, 1),
    "tds_ppm": (0, 2000, 1),
    "water_temp_c": (0, 40, 0.1),
    "flow_lpm": (0, 40, 0.1),
    "rssi_dbm": (-120, 0, 1),
}
_LABELS = {
    "ph": ("pH", ""),
    "turbidity_ntu": ("Turbidity", "NTU"),
    "tds_ppm": ("TDS", "ppm"),
    "water_temp_c": ("Water temp", "°C"),
    "flow_lpm": ("Flow", "L/min"),
    "rssi_dbm": ("RSSI", "dBm"),
}


def parse_values(form: dict[str, list[str]]) -> dict[str, float | int]:
    """Clamp the six submitted slider values into their accepted ranges."""
    result: dict[str, float | int] = {}
    for key, (low, high, _step) in SLIDERS.items():
        supplied = form.get(key)
        raw = supplied[0] if supplied else MANUAL_DEFAULTS[key]
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            value = float(MANUAL_DEFAULTS[key])
        if not math.isfinite(value):
            value = float(MANUAL_DEFAULTS[key])
        value = max(float(low), min(float(high), value))
        if key in {"tds_ppm", "rssi_dbm"}:
            result[key] = int(round(value))
        else:
            result[key] = value
    return result


def _existing(control_dir: str | Path, node: str) -> dict:
    """Read the current control file, treating any corruption as no state."""
    path = Path(control_dir) / f"{node}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_control_state(control_dir: str | Path, node: str, action: str,
                        values: dict[str, float | int],
                        updated_ms: int | None = None) -> dict:
    """Atomically write one node's control state with container-readable mode."""
    if action not in _ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if not _NODE_RE.fullmatch(str(node)):
        raise ValueError(f"unsafe node name: {node}")

    old = _existing(control_dir, node)
    if action == "pause":
        # Pause only the publisher: the chosen readings stay in effect.
        paused, override = True, old.get("override") is True
    elif action == "override":
        paused, override = False, True
    else:
        paused, override = False, False

    state = {
        "node": node,
        "paused": paused,
        "override": override,
        "values": {key: values[key] for key in MANUAL_KEYS},
        "updated_ms": int(updated_ms if updated_ms is not None
                          else time.time_ns() // 1_000_000),
    }

    directory = Path(control_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{node}.json"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{node}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        # A fresh temp file starts at 0600; the simulator container must read it.
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return state


def apply_form(control_dir: str | Path, form: dict[str, list[str]]) -> dict:
    """Validate a submitted form and write the corresponding control file."""
    node = (form.get("node") or [""])[0]
    action = (form.get("action") or [""])[0]
    if node not in NODES:
        raise ValueError(f"unknown node: {node}")
    return write_control_state(control_dir, node, action, parse_values(form))


def _slider_html(key: str, value: float | int) -> str:
    low, high, step = SLIDERS[key]
    name, unit = _LABELS[key]
    label = html.escape(name)
    unit_html = f" <small>{html.escape(unit)}</small>" if unit else ""
    return (
        f'<label class="slider"><span class="slider-name">{label}{unit_html}</span>'
        f'<input type="range" name="{key}" min="{low:g}" max="{high:g}" '
        f'step="{step:g}" value="{value:g}">'
        f'<span class="slider-range numeric">{low:g}–{high:g}</span></label>'
    )


def _applied_at(updated_ms: int) -> str:
    if not updated_ms:
        return "never"
    try:
        return time.strftime("%H:%M:%S", time.localtime(updated_ms / 1000))
    except (OSError, OverflowError, ValueError):
        return "unknown"


def _panel_html(node: str, db_dir: str | Path, control_dir: str | Path) -> str:
    db_path = Path(db_dir) / f"{node}-compose" / "db" / f"{node}.db"
    snap = snapshot(db_path, rows=0) if db_path.is_file() else None
    state = read_control(control_dir, node)
    pending = snap["pending"] if snap else 0
    link = snap["link"].get("state", "-") if snap else "-"
    mode = "PAUSED" if state["paused"] else "OVERRIDE" if state["override"] else "RANDOM"
    sliders = "\n".join(_slider_html(key, state["values"][key]) for key in MANUAL_KEYS)
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>{html.escape(node)}</h2>
        <span class="state state-{mode.lower()}">{html.escape(mode)}</span>
      </div>
      <dl class="facts">
        <div><dt>MQTT link</dt><dd>{html.escape(str(link))}</dd></div>
        <div><dt>Pending</dt><dd class="numeric">{pending}</dd></div>
        <div><dt>Applied</dt><dd class="numeric">{html.escape(_applied_at(state["updated_ms"]))}</dd></div>
      </dl>
      <form method="post" action="/control">
        <input type="hidden" name="node" value="{html.escape(node)}">
        <div class="sliders">
          {sliders}
        </div>
        <div class="actions">
          <button type="submit" name="action" value="pause">Pause node</button>
          <button type="submit" name="action" value="override">Override &amp; resume</button>
          <button type="submit" name="action" value="random">Resume random</button>
        </div>
      </form>
    </section>"""


def _fmt(value, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_metrics(metrics_dir: str | Path) -> str:
    """Render the last generated metrics.json as a read-only table."""
    path = Path(metrics_dir) / "metrics.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("metrics.json is not an object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        body = (
            '<p class="empty"><strong>No metrics yet.</strong> Run the 24-minute '
            '<code>measure.json</code> scenario, then '
            '<code>python -m tools.measure --run &lt;run-dir&gt; --out '
            'results/measure-metrics</code>. This page updates on reload.</p>'
        )
    else:
        latency = data.get("latency_ms", {}).get("server_stamped_live", {})
        delivery = data.get("delivery", {})
        alarms = data.get("alarm_delay_ms", {}).get("live_threshold_alarms", {})
        flush = data.get("flush_throughput_rows_per_s", {})
        recovery = data.get("recovery", [])
        loss = data.get("data_loss", {})
        ratio = delivery.get("pilot_ratio")
        rows = [
            ("Source", html.escape(str(data.get("source", "-")))),
            ("Run", html.escape(str(data.get("run", "-")))),
            ("Latency samples (live)", _fmt(latency.get("n"), 0)),
            ("Latency mean / median (ms)",
             f"{_fmt(latency.get('mean'))} / {_fmt(latency.get('median'))}"),
            ("Latency P95 / max (ms)",
             f"{_fmt(latency.get('p95'))} / {_fmt(latency.get('max'))}"),
            ("Delivery with outage",
             f"{delivery.get('pilot_delivered', '-')} / "
             f"{delivery.get('pilot_generated', '-')} "
             f"({_fmt(100 * ratio if ratio is not None else None, 2)} %)"),
            ("Outage (s)", _fmt(delivery.get("outage_s"), 0)),
            ("Control node (no buffer)",
             _fmt(100 * delivery["control_ratio_no_store_forward"]
                  if delivery.get("control_ratio_no_store_forward") is not None else None, 2) + " %"),
            ("Alarm delay events", _fmt(alarms.get("n"), 0)),
            ("Alarm mean / median (ms)",
             f"{_fmt(alarms.get('mean'))} / {_fmt(alarms.get('median'))}"),
            ("Flush throughput (rows/s)", _fmt(flush.get("mean"))),
            ("Recovery events", str(len(recovery))),
            ("Last recovery (s)",
             _fmt(recovery[-1].get("recovery_s_from_restore") if recovery else None, 2)),
            ("Data loss (pilot)",
             f"{loss.get('pilot_lost', '-')} rows "
             f"({_fmt(loss.get('pilot_lost_pct'), 3)} %)"),
        ]
        table = "\n".join(
            f"<tr><th>{html.escape(label)}</th><td class=\"numeric\">{value}</td></tr>"
            for label, value in rows)
        body = (
            f'<table class="metrics">{table}</table>'
            '<p class="hint">Server-stamped latency is <code>rx_ms - ts</code> for live samples. '
            'Backfilled ages are reported separately in <code>metrics.md</code>.</p>'
        )
    return f"""<!doctype html>
 <html lang="en">
 <head>
   <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
   <title>JalDrishti metrics</title>
   <style>
     body {{ margin: 1.5rem; background: #071b24; color: #e8f1f2; font: 14px/1.45 system-ui, sans-serif; }}
     h1 {{ font-size: 1.35rem; margin: 0 0 1rem; }}
     nav {{ color: #a7c1c9; margin-bottom: 1rem; }}
     a {{ color: #2ec4b6; }}
     table {{ border-collapse: collapse; max-width: 760px; width: 100%; }}
     th, td {{ text-align: left; border-bottom: 1px solid #33596a; padding: .4rem .6rem; }}
     th {{ color: #a7c1c9; font-weight: 500; }}
     .numeric {{ font-family: ui-monospace, monospace; font-variant-numeric: tabular-nums; }}
     .empty {{ color: #f6c85f; max-width: 72ch; }}
     .hint {{ color: #a7c1c9; max-width: 72ch; }}
   </style>
 </head>
 <body>
   <h1>ThingsBoard measurements</h1>
   <nav><a href="/">Control</a> · <a href="/metrics">Metrics</a></nav>
   {body}
 </body>
 </html>"""


def render_page(db_dir: str | Path, control_dir: str | Path) -> str:
    """Render the left-aligned 2 × 2 control grid without external assets."""
    panels = "\n".join(_panel_html(node, db_dir, control_dir) for node in NODES)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JalDrishti simulator control</title>
  <style>
    :root {{
      color-scheme: dark;
      --navy: #071b24;
      --panel: #0f3443;
      --fog: #e8f1f2;
      --amber: #f6c85f;
      --teal: #2ec4b6;
      --red: #e5484d;
      --rule: #33596a;
      --muted: #a7c1c9;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 1.5rem;
      background: var(--navy);
      color: var(--fog);
      font: 14px/1.45 system-ui, sans-serif;
    }}
    header {{ max-width: 1160px; margin: 0 0 1.1rem; }}
    h1 {{ font-size: 1.35rem; font-weight: 600; letter-spacing: -.01em; margin: 0 0 .25rem; }}
    .hint {{ color: var(--muted); margin: 0; max-width: 68ch; }}
    nav {{ color: var(--muted); margin: 0 0 .6rem; font-size: .82rem; }}
    a {{ color: var(--teal); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .875rem;
      max-width: 1160px;
      align-items: start;
    }}
    .panel {{
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--rule);
    }}
    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: .75rem;
      padding: .6rem .8rem;
      background: var(--navy);
      border-bottom: 1px solid var(--rule);
    }}
    .panel h2 {{ margin: 0; font-size: .95rem; font-weight: 600; }}
    .state {{
      border: 1px solid currentColor;
      border-radius: 999px;
      padding: .1rem .55rem;
      font-size: .74rem;
      font-weight: 650;
      letter-spacing: .02em;
      white-space: nowrap;
    }}
    .state-paused {{ color: var(--red); }}
    .state-override {{ color: var(--amber); }}
    .state-random {{ color: var(--teal); }}
    .facts {{
      display: flex;
      flex-wrap: wrap;
      gap: .35rem 1.25rem;
      margin: 0;
      padding: .55rem .8rem;
      border-bottom: 1px solid var(--rule);
    }}
    .facts div {{ display: flex; align-items: baseline; gap: .35rem; }}
    .facts dt {{ color: var(--muted); font-size: .76rem; }}
    .facts dd {{ margin: 0; font-size: .8rem; }}
    .numeric {{ font-family: ui-monospace, monospace; font-variant-numeric: tabular-nums; }}
    form {{ padding: .7rem .8rem .8rem; }}
    .sliders {{ display: grid; gap: .35rem; }}
    .slider {{
      display: grid;
      grid-template-columns: minmax(100px, 1fr) minmax(120px, 1.6fr) auto;
      align-items: center;
      gap: .6rem;
    }}
    .slider-name {{ font-size: .8rem; }}
    .slider-name small {{ color: var(--muted); font-size: .72rem; }}
    .slider input {{ width: 100%; margin: 0; accent-color: var(--teal); }}
    .slider-range {{ color: var(--muted); font-size: .72rem; white-space: nowrap; }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: .4rem;
      margin-top: .85rem;
      padding-top: .7rem;
      border-top: 1px solid var(--rule);
    }}
    button {{
      font: inherit;
      font-size: .8rem;
      color: var(--fog);
      background: transparent;
      border: 1px solid var(--rule);
      border-radius: 0;
      padding: .42rem .7rem;
      cursor: pointer;
    }}
    button[value=override] {{ color: var(--navy); background: var(--amber); border-color: var(--amber); font-weight: 600; }}
    button[value=pause]:hover {{ color: var(--red); border-color: var(--red); }}
    button[value=random]:hover {{ color: var(--teal); border-color: var(--teal); }}
    :focus-visible {{ outline: 2px solid var(--amber); outline-offset: 2px; }}
    @media (prefers-reduced-motion: no-preference) {{
      button {{ transition: color 120ms linear, border-color 120ms linear, background-color 120ms linear; }}
    }}
    @media (max-width: 760px) {{
      body {{ margin: .75rem; }}
      .grid {{ grid-template-columns: 1fr; }}
      .slider {{ grid-template-columns: minmax(90px, 1fr) minmax(100px, 1.4fr) auto; gap: .35rem; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Simulator control</h1>
    <nav><a href="/">Control</a> · <a href="/metrics">Metrics</a></nav>
    <p class="hint">Pause one node's MQTT publisher, send exact readings, or resume the random model.
       Paused nodes keep sampling into SQLite, so the backlog grows until you override or resume.</p>
  </header>
  <main class="grid">
    {panels}
  </main>
</body>
</html>"""


def _error_page(message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Control error</title>
<style>
body {{ margin: 1.5rem; background: #071b24; color: #e8f1f2; font: 14px/1.45 system-ui, sans-serif; }}
.error {{ color: #e5484d; border: 1px solid #33596a; padding: .8rem; max-width: 60ch; }}
a {{ color: #2ec4b6; }}
</style></head>
<body><p class="error">{html.escape(message)}</p><a href="/">Back to control</a></body>
</html>"""


def _make_handler(db_dir: str | Path, control_dir: str | Path,
                  metrics_dir: str | Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str = "", *, location: str | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route == "/":
                self._send(200, render_page(db_dir, control_dir))
            elif route == "/metrics":
                self._send(200, render_metrics(metrics_dir))
            else:
                self._send(404, _error_page("Not found"))

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/control":
                self._send(404, _error_page("Not found"))
                return
            try:
                length = max(0, int(self.headers.get("Content-Length", "0")))
            except (TypeError, ValueError):
                length = 0
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = parse_qs(raw, keep_blank_values=True)
            try:
                apply_form(control_dir, form)
            except ValueError as exc:
                self._send(400, _error_page(str(exc)))
                return
            self._send(303, location="/")

        def log_message(self, *_args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual simulator control window")
    parser.add_argument("--db-dir", default="runs")
    parser.add_argument("--control-dir", default="runs/control")
    parser.add_argument("--metrics-dir", default="results/measure-metrics")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)

    Path(args.control_dir).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        (args.host, args.port),
        _make_handler(args.db_dir, args.control_dir, args.metrics_dir))
    print(f"Simulator control: http://{args.host}:{args.port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
