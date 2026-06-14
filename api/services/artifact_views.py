from __future__ import annotations

import html


def page_shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#050505">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg:#050505; --panel:#0d0f12; --panel2:#141922; --line:rgba(255,255,255,.12);
      --text:#f4f7fb; --soft:#c7d0dc; --muted:#8491a3; --cyan:#22d3ee;
      --blue:#3b82f6; --violet:#8b5cf6; --emerald:#34d399; --amber:#fbbf24; --rose:#fb7185;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; color:var(--text); font-family:Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      background:
        radial-gradient(circle at 18% 0%, rgba(34,211,238,.12), transparent 28rem),
        radial-gradient(circle at 82% 4%, rgba(232,121,249,.11), transparent 26rem),
        linear-gradient(180deg,#050505,#080a0d 48%,#050505);
    }}
    .shell {{ max-width:1180px; margin:0 auto; padding:28px; }}
    .top {{ display:flex; align-items:flex-start; justify-content:space-between; gap:18px; margin-bottom:22px; }}
    h1 {{ margin:0; font-size:24px; letter-spacing:0; }}
    h2 {{ margin:0 0 14px; font-size:16px; color:var(--text); }}
    .sub {{ margin-top:6px; color:var(--muted); font-size:13px; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .card {{ background:linear-gradient(180deg,rgba(18,22,27,.98),rgba(10,12,15,.98)); border:1px solid var(--line); border-radius:8px; box-shadow:0 18px 60px rgba(0,0,0,.42); padding:18px; margin-bottom:16px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }}
    .stat {{ border:1px solid var(--line); border-radius:8px; padding:15px; background:rgba(255,255,255,.045); }}
    .label {{ color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }}
    .value {{ margin-top:6px; font-size:26px; font-weight:800; }}
    .accent {{ background:linear-gradient(90deg,var(--cyan),var(--violet),var(--rose)); -webkit-background-clip:text; background-clip:text; color:transparent; }}
    .badge {{ display:inline-flex; align-items:center; border:1px solid currentColor; border-radius:999px; padding:3px 9px; font-size:11px; font-weight:800; }}
    .PASSED {{ color:#86efac; background:rgba(52,211,153,.14); }}
    .FAILED,.ERROR {{ color:#fda4af; background:rgba(251,113,133,.14); }}
    .SLOW,.WARN,.WARNING {{ color:#fcd34d; background:rgba(251,191,36,.14); }}
    .INFO {{ color:#93c5fd; background:rgba(59,130,246,.14); }}
    .DEBUG {{ color:#c4b5fd; background:rgba(139,92,246,.14); }}
    .TRACE {{ color:#cbd5e1; background:rgba(148,163,184,.12); }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; overflow:hidden; border-radius:8px; }}
    th {{ text-align:left; color:var(--muted); background:rgba(255,255,255,.055); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }}
    th,td {{ padding:10px 12px; border-bottom:1px solid rgba(255,255,255,.08); vertical-align:top; }}
    td {{ color:var(--soft); }}
    tr:hover td {{ background:rgba(34,211,238,.055); }}
    .bar {{ height:10px; background:rgba(255,255,255,.08); border-radius:999px; overflow:hidden; }}
    .bar span {{ display:block; height:100%; background:linear-gradient(90deg,var(--emerald),var(--cyan),var(--violet)); }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:10px; align-items:end; }}
    input,select,button {{ border-radius:8px; border:1px solid var(--line); background:rgba(5,8,12,.94); color:var(--text); padding:9px 11px; font:inherit; }}
    button,.btn {{ cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; gap:7px; border-radius:8px; border:1px solid rgba(103,232,249,.4); background:linear-gradient(135deg,var(--cyan),var(--blue)); color:#020617; font-weight:800; padding:9px 13px; }}
    .btn-secondary {{ background:rgba(255,255,255,.05); color:var(--soft); border-color:var(--line); }}
    .logline {{ white-space:pre-wrap; word-break:break-word; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; line-height:1.55; }}
    .logrow {{ border-left:3px solid rgba(148,163,184,.3); }}
    .logrow.ERROR {{ border-left-color:var(--rose); }}
    .logrow.WARNING,.logrow.WARN {{ border-left-color:var(--amber); }}
    .logrow.INFO {{ border-left-color:var(--blue); }}
    .logrow.DEBUG {{ border-left-color:var(--violet); }}
    .empty {{ color:var(--muted); padding:28px; text-align:center; }}
    @media (max-width:760px) {{ .shell {{ padding:16px; }} .top {{ flex-direction:column; }} table {{ display:block; overflow-x:auto; }} }}
  </style>
</head>
<body>
  <main class="shell">{body}</main>
</body>
</html>"""


def status_badge(status: str | None) -> str:
    safe = html.escape(status or "UNKNOWN")
    return f'<span class="badge {safe}">{safe}</span>'


def render_metrics_html(metrics: dict) -> str:
    tests = metrics.get("tests") or []
    total = int(metrics.get("total_tests") or len(tests) or 0)
    passed = int(metrics.get("passed") or 0)
    failed = int(metrics.get("failed") or 0)
    slow = int(metrics.get("slow") or 0)
    pass_rate = round((passed / total * 100), 1) if total else 0
    duration = float(metrics.get("total_duration_seconds") or 0)
    rows = []
    for test in tests:
        issues = int(test.get("total_issues") or 0)
        duration_s = float(test.get("duration_seconds") or 0)
        rows.append(
            "<tr>"
            f"<td class=\"mono\">{html.escape(str(test.get('name') or ''))}</td>"
            f"<td>{status_badge(str(test.get('status') or 'UNKNOWN'))}</td>"
            f"<td>{duration_s:.3f}s</td>"
            f"<td>{test.get('source_row_count', 0)}</td>"
            f"<td>{test.get('target_row_count', 0)}</td>"
            f"<td>{issues}</td>"
            "</tr>"
        )
    table = "\n".join(rows) if rows else '<tr><td colspan="6" class="empty">No per-test metrics were recorded.</td></tr>'
    body = f"""
<div class="top">
  <div>
    <h1>Run Metrics <span class="accent">Dashboard</span></h1>
    <div class="sub mono">{html.escape(str(metrics.get('run_id') or ''))}</div>
    <div class="sub">Generated {html.escape(str(metrics.get('generated_at') or 'unknown'))}</div>
  </div>
  <a class="btn btn-secondary" href="?format=json">Raw JSON</a>
</div>
<section class="grid">
  <div class="stat"><div class="label">Total Tests</div><div class="value">{total}</div></div>
  <div class="stat"><div class="label">Passed</div><div class="value" style="color:var(--emerald)">{passed}</div></div>
  <div class="stat"><div class="label">Failed</div><div class="value" style="color:var(--rose)">{failed}</div></div>
  <div class="stat"><div class="label">Slow</div><div class="value" style="color:var(--amber)">{slow}</div></div>
  <div class="stat"><div class="label">Duration</div><div class="value">{duration:.3f}s</div></div>
  <div class="stat"><div class="label">Pass Rate</div><div class="value">{pass_rate}%</div><div class="bar"><span style="width:{pass_rate}%"></span></div></div>
</section>
<section class="card">
  <h2>Per-Test Performance</h2>
  <table>
    <thead><tr><th>Test</th><th>Status</th><th>Duration</th><th>Source Rows</th><th>Target Rows</th><th>Issues</th></tr></thead>
    <tbody>{table}</tbody>
  </table>
</section>
"""
    return page_shell("Run Metrics", body)


def render_logs_html(
    run_id: str,
    lines: list[dict],
    query: str,
    level: str,
    total_lines: int,
    total_events: int,
    scope: str,
) -> str:
    level_options = []
    for opt in ("", "ERROR", "WARNING", "INFO", "DEBUG"):
        selected = " selected" if opt == level else ""
        label = opt or "All levels"
        level_options.append(f'<option value="{opt}"{selected}>{label}</option>')
    scope_options = []
    for opt, label in (("run", "This run"), ("all", "All logs")):
        selected = " selected" if opt == scope else ""
        scope_options.append(f'<option value="{opt}"{selected}>{label}</option>')
    rows = []
    for row in lines:
        level_name = html.escape(row["level"])
        rows.append(
            f'<tr class="logrow {level_name}">'
            f'<td class="mono" style="color:var(--muted)">{row["number"]}</td>'
            f'<td>{status_badge(level_name)}</td>'
            f'<td class="logline">{html.escape(row["text"])}</td>'
            "</tr>"
        )
    table = "\n".join(rows) if rows else '<tr><td colspan="3" class="empty">No log events match the current search.</td></tr>'
    body = f"""
<div class="top">
  <div>
    <h1>Error Log <span class="accent">Explorer</span></h1>
    <div class="sub mono">{html.escape(run_id)}</div>
    <div class="sub">{len(lines)} matching events from {total_events} events / {total_lines} total lines</div>
  </div>
  <a class="btn btn-secondary" href="?format=text&amp;scope={html.escape(scope)}">Raw text</a>
</div>
<section class="card">
  <form class="toolbar" method="get">
    <div><div class="label">Search</div><input name="q" value="{html.escape(query)}" placeholder="exception, run id, module, message"></div>
    <div><div class="label">Level</div><select name="level">{''.join(level_options)}</select></div>
    <div><div class="label">Scope</div><select name="scope">{''.join(scope_options)}</select></div>
    <div><div class="label">Limit</div><input name="limit" type="number" min="1" max="5000" value="{len(lines) or 500}"></div>
    <button type="submit">Search Logs</button>
  </form>
</section>
<section class="card">
  <h2>Log Events</h2>
  <table>
    <thead><tr><th>Line</th><th>Level</th><th>Message</th></tr></thead>
    <tbody>{table}</tbody>
  </table>
</section>
"""
    return page_shell("Run Logs", body)
