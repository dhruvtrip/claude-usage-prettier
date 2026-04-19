"""
dashboard.py - Local web dashboard served on localhost:8080.
"""

import json
import os
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".claude" / "usage.db"


def get_dashboard_data(db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "Database not found. Run: python cli.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── All models (for filter UI) ────────────────────────────────────────────
    model_rows = conn.execute("""
        SELECT COALESCE(model, 'unknown') as model
        FROM turns
        GROUP BY model
        ORDER BY SUM(input_tokens + output_tokens) DESC
    """).fetchall()
    all_models = [r["model"] for r in model_rows]

    # ── Daily per-model, ALL history (client filters by range) ────────────────
    daily_rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)   as day,
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as input,
            SUM(output_tokens)         as output,
            SUM(cache_read_tokens)     as cache_read,
            SUM(cache_creation_tokens) as cache_creation,
            COUNT(*)                   as turns
        FROM turns
        GROUP BY day, model
        ORDER BY day, model
    """).fetchall()

    daily_by_model = [{
        "day":            r["day"],
        "model":          r["model"],
        "input":          r["input"] or 0,
        "output":         r["output"] or 0,
        "cache_read":     r["cache_read"] or 0,
        "cache_creation": r["cache_creation"] or 0,
        "turns":          r["turns"] or 0,
    } for r in daily_rows]

    # ── All sessions (client filters by range and model) ──────────────────────
    session_rows = conn.execute("""
        SELECT
            session_id, project_name, first_timestamp, last_timestamp,
            total_input_tokens, total_output_tokens,
            total_cache_read, total_cache_creation, model, turn_count
        FROM sessions
        ORDER BY last_timestamp DESC
    """).fetchall()

    sessions_all = []
    for r in session_rows:
        try:
            t1 = datetime.fromisoformat(r["first_timestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(r["last_timestamp"].replace("Z", "+00:00"))
            duration_min = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            duration_min = 0
        sessions_all.append({
            "session_id":    r["session_id"][:8],
            "project":       r["project_name"] or "unknown",
            "last":          (r["last_timestamp"] or "")[:16].replace("T", " "),
            "last_date":     (r["last_timestamp"] or "")[:10],
            "duration_min":  duration_min,
            "model":         r["model"] or "unknown",
            "turns":         r["turn_count"] or 0,
            "input":         r["total_input_tokens"] or 0,
            "output":        r["total_output_tokens"] or 0,
            "cache_read":    r["total_cache_read"] or 0,
            "cache_creation": r["total_cache_creation"] or 0,
        })

    conn.close()

    return {
        "all_models":     all_models,
        "daily_by_model": daily_by_model,
        "sessions_all":   sessions_all,
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Usage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:            #f5f4ed;
    --card:          #faf9f5;
    --border:        #f0eee6;
    --border-strong: #e8e6dc;
    --text:          #141413;
    --muted:         #87867f;
    --secondary:     #5e5d59;
    --accent:        #c96442;
    --accent-bg:     rgba(201,100,66,0.10);
    --warm-sand:     #e8e6dc;
    --charcoal:      #4d4c48;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; font-size: 14px; line-height: 1.6; }

  header { background: var(--card); border-bottom: 1px solid var(--border-strong); padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 20px; font-weight: 500; font-family: Georgia, serif; color: var(--text); letter-spacing: -0.01em; }
  header .meta { color: var(--muted); font-size: 12px; }
  #rescan-btn { background: var(--accent); border: none; color: #faf9f5; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 500; box-shadow: #c96442 0px 0px 0px 0px, #c96442 0px 0px 0px 1px; transition: opacity 0.15s; }
  #rescan-btn:hover { opacity: 0.88; }
  #rescan-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  #filter-bar { background: var(--card); border-bottom: 1px solid var(--border-strong); padding: 10px 24px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .filter-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); white-space: nowrap; }
  .filter-sep { width: 1px; height: 22px; background: var(--border-strong); flex-shrink: 0; }
  #model-checkboxes { display: flex; flex-wrap: wrap; gap: 6px; }
  .model-cb-label { display: flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 20px; border: 1px solid var(--border-strong); cursor: pointer; font-size: 12px; color: var(--muted); transition: border-color 0.15s, color 0.15s, background 0.15s; user-select: none; }
  .model-cb-label:hover { border-color: var(--accent); color: var(--text); }
  .model-cb-label.checked { background: var(--accent-bg); border-color: var(--accent); color: var(--text); }
  .model-cb-label input { display: none; }
  .filter-btn { padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--warm-sand); color: var(--charcoal); font-size: 11px; cursor: pointer; white-space: nowrap; }
  .filter-btn:hover { border-color: var(--accent); color: var(--accent); }
  .range-group { display: flex; border: 1px solid var(--border-strong); border-radius: 8px; overflow: hidden; flex-shrink: 0; }
  .range-btn { padding: 4px 13px; background: transparent; border: none; border-right: 1px solid var(--border-strong); color: var(--muted); font-size: 12px; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .range-btn:last-child { border-right: none; }
  .range-btn:hover { background: var(--warm-sand); color: var(--text); }
  .range-btn.active { background: var(--accent-bg); color: var(--accent); font-weight: 600; }

  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }
  .stat-card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .stat-card .value { font-size: 24px; font-weight: 500; font-family: Georgia, serif; color: var(--text); line-height: 1.1; }
  .stat-card .sub { color: var(--muted); font-size: 11px; margin-top: 6px; }

  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card h2 { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 240px; }
  .chart-wrap.tall { height: 300px; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); border-bottom: 1px solid var(--border-strong); white-space: nowrap; background: var(--bg); }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: var(--accent); }
  .sort-icon { font-size: 9px; opacity: 0.8; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; color: var(--text); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(201,100,66,0.04); }
  .model-tag { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; background: var(--accent-bg); color: var(--accent); }
  .cost { color: var(--secondary); font-family: monospace; }
  .cost-na { color: var(--muted); font-family: monospace; font-size: 11px; }
  .num { font-family: monospace; }
  .muted { color: var(--muted); }
  .section-title { font-family: Georgia, serif; font-size: 14px; font-weight: 500; color: var(--text); margin-bottom: 12px; }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .section-header .section-title { margin-bottom: 0; }
  .export-btn { background: var(--warm-sand); border: 1px solid var(--border-strong); color: var(--charcoal); padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 11px; }
  .export-btn:hover { border-color: var(--accent); color: var(--accent); }
  .table-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; overflow-x: auto; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }

  footer { border-top: 1px solid var(--border-strong); padding: 24px; margin-top: 8px; background: var(--card); }
  .footer-content { max-width: 1400px; margin: 0 auto; }
  .footer-content p { color: var(--muted); font-size: 12px; line-height: 1.7; margin-bottom: 4px; }
  .footer-content p:last-child { margin-bottom: 0; }
  .footer-content a { color: var(--accent); text-decoration: none; }
  .footer-content a:hover { text-decoration: underline; }

  @media (max-width: 768px) { .charts-grid { grid-template-columns: 1fr; } .chart-card.wide { grid-column: 1; } }

  /* ── View toggle ──────────────────────────────────────────────────────── */
  .view-toggle { display: inline-flex; border: 1px solid var(--border-strong); border-radius: 8px; overflow: hidden; margin-right: 8px; }
  .view-toggle button { padding: 4px 12px; background: transparent; border: none; border-right: 1px solid var(--border-strong); color: var(--muted); font-size: 12px; cursor: pointer; font-weight: 500; }
  .view-toggle button:last-child { border-right: none; }
  .view-toggle button:hover { background: var(--warm-sand); color: var(--text); }
  .view-toggle button.active { background: var(--accent-bg); color: var(--accent); }
  .header-controls { display: flex; align-items: center; gap: 10px; }

  /* ── Mode-specific visibility ─────────────────────────────────────────── */
  body.view-simple .advanced-only { display: none !important; }
  body.view-advanced .simple-only { display: none !important; }

  /* ── Simple mode: plan picker chip ───────────────────────────────────── */
  .plan-picker { background: var(--card); border: 1px solid var(--border-strong); border-radius: 12px; padding: 14px 18px; margin-bottom: 20px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .plan-picker .prompt { font-size: 13px; color: var(--secondary); font-weight: 500; }
  .plan-picker .plan-options { display: flex; gap: 6px; flex-wrap: wrap; }
  .plan-chip { padding: 5px 12px; border-radius: 16px; border: 1px solid var(--border-strong); background: transparent; color: var(--secondary); font-size: 12px; cursor: pointer; transition: all 0.15s; }
  .plan-chip:hover { border-color: var(--accent); color: var(--accent); }
  .plan-chip.selected { background: var(--accent); color: var(--card); border-color: var(--accent); }
  .plan-picker .dismiss { margin-left: auto; background: transparent; border: none; color: var(--muted); font-size: 12px; cursor: pointer; padding: 4px 8px; }
  .plan-picker .dismiss:hover { color: var(--text); }

  /* ── Simple mode: headline banner ────────────────────────────────────── */
  .headline { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px 28px; margin-bottom: 20px; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }
  .headline .eyebrow { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 10px; }
  .headline .summary { font-family: Georgia, serif; font-size: 22px; line-height: 1.4; color: var(--text); font-weight: 500; }
  .headline .summary strong { color: var(--accent); font-weight: 600; }
  .headline .intensity-row { margin-top: 14px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .intensity-pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 14px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  .intensity-pill.light { background: #dbeafe; color: #1e40af; }
  .intensity-pill.typical { background: #dcfce7; color: #166534; }
  .intensity-pill.heavy { background: #fef3c7; color: #92400e; }
  .intensity-pill.power { background: var(--accent-bg); color: var(--accent); }
  .intensity-desc { font-size: 12px; color: var(--muted); }

  /* ── Simple mode: plan comparison ────────────────────────────────────── */
  .plan-comparison { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }
  .plan-comparison .title { font-family: Georgia, serif; font-size: 14px; font-weight: 500; color: var(--text); margin-bottom: 4px; }
  .plan-comparison .subtitle { font-size: 12px; color: var(--muted); margin-bottom: 16px; }
  .plan-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
  .plan-card { border: 1px solid var(--border-strong); border-radius: 10px; padding: 14px; position: relative; transition: border-color 0.15s, background 0.15s; }
  .plan-card.current { border-color: var(--accent); background: var(--accent-bg); }
  .plan-card .plan-name { font-size: 12px; font-weight: 600; color: var(--secondary); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .plan-card .plan-price { font-family: Georgia, serif; font-size: 22px; font-weight: 500; color: var(--text); line-height: 1.1; }
  .plan-card .plan-sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .plan-card .plan-status { font-size: 11px; margin-top: 10px; display: flex; align-items: center; gap: 5px; }
  .plan-card .plan-status.ok { color: #166534; }
  .plan-card .plan-status.warn { color: #92400e; }
  .plan-card .plan-status.exceed { color: #b91c1c; }
  .plan-card .current-badge { position: absolute; top: -8px; right: 10px; background: var(--accent); color: var(--card); font-size: 10px; padding: 2px 8px; border-radius: 10px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }

  /* ── Simple mode: human-scale stats ──────────────────────────────────── */
  .simple-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }
  .simple-stat { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }
  .simple-stat .icon { font-size: 20px; margin-bottom: 8px; color: var(--accent); }
  .simple-stat .simple-label { font-size: 13px; font-weight: 500; color: var(--secondary); margin-bottom: 6px; }
  .simple-stat .simple-value { font-family: Georgia, serif; font-size: 26px; font-weight: 500; color: var(--text); line-height: 1.1; }
  .simple-stat .simple-sub { font-size: 11px; color: var(--muted); margin-top: 6px; font-family: monospace; }



  /* ── Simple mode: reframed projects ──────────────────────────────────── */
  .projects-simple { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: rgba(0,0,0,0.04) 0px 2px 12px; }
  .projects-simple .title { font-family: Georgia, serif; font-size: 14px; font-weight: 500; color: var(--text); margin-bottom: 4px; }
  .projects-simple .subtitle { font-size: 12px; color: var(--muted); margin-bottom: 16px; }
  .project-bar { display: grid; grid-template-columns: minmax(120px, 200px) 1fr auto; gap: 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); }
  .project-bar:last-child { border-bottom: none; }
  .project-bar .proj-name { font-size: 13px; color: var(--text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .project-bar .proj-track { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
  .project-bar .proj-fill { height: 100%; background: var(--accent); border-radius: 4px; }
  .project-bar .proj-count { font-size: 12px; color: var(--muted); font-family: monospace; white-space: nowrap; }

  /* ── Tooltip (?) for jargon ───────────────────────────────────────────── */
  .jargon { position: relative; cursor: help; border-bottom: 1px dotted var(--muted); }
  .jargon:hover::after, .jargon:focus::after {
    content: attr(data-tooltip);
    position: absolute; bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%);
    background: var(--text); color: var(--card); padding: 8px 12px; border-radius: 6px;
    font-size: 11px; font-weight: normal; line-height: 1.5; white-space: normal;
    width: max-content; max-width: 260px; z-index: 100; text-transform: none; letter-spacing: 0;
    box-shadow: rgba(0,0,0,0.2) 0px 4px 12px;
  }
  .jargon:hover::before, .jargon:focus::before {
    content: ''; position: absolute; bottom: calc(100% + 2px); left: 50%; transform: translateX(-50%);
    border: 4px solid transparent; border-top-color: var(--text); z-index: 100;
  }
  .help-icon { display: inline-flex; align-items: center; justify-content: center; width: 14px; height: 14px; border-radius: 50%; background: var(--warm-sand); color: var(--secondary); font-size: 10px; font-weight: 600; margin-left: 3px; cursor: help; }

  @media (max-width: 768px) {
.headline .summary { font-size: 18px; }
  }
</style>
</head>
<body>
<header>
  <h1>Claude Code Usage Dashboard</h1>
  <div class="meta" id="meta">Loading...</div>
  <div class="header-controls">
    <div class="view-toggle" role="tablist" aria-label="Dashboard view mode">
      <button id="view-simple-btn" role="tab" aria-selected="true" onclick="setViewMode('simple')">Simple</button>
      <button id="view-advanced-btn" role="tab" aria-selected="false" onclick="setViewMode('advanced')">Advanced</button>
    </div>
    <button id="rescan-btn" onclick="triggerRescan()" title="Rebuild the database from scratch by re-scanning all JSONL files. Use if data looks stale or costs seem wrong.">&#x21bb; Rescan</button>
  </div>
</header>

<div id="filter-bar">
  <div class="advanced-only filter-label">Models</div>
  <div class="advanced-only" id="model-checkboxes"></div>
  <button class="advanced-only filter-btn" onclick="selectAllModels()">All</button>
  <button class="advanced-only filter-btn" onclick="clearAllModels()">None</button>
  <div class="advanced-only filter-sep"></div>
  <div class="filter-label">Range</div>
  <div class="range-group">
    <button class="range-btn" data-range="7d"  onclick="setRange('7d')">7d</button>
    <button class="range-btn" data-range="30d" onclick="setRange('30d')">30d</button>
    <button class="range-btn" data-range="90d" onclick="setRange('90d')">90d</button>
    <button class="range-btn" data-range="all" onclick="setRange('all')">All</button>
  </div>
</div>

<div class="container">
  <!-- ── Simple mode ───────────────────────────────────────────────── -->
  <div class="simple-only" id="simple-view">
    <div class="plan-picker" id="plan-picker">
      <span class="prompt">Which plan are you on?</span>
      <div class="plan-options">
        <button class="plan-chip" data-plan="pro" onclick="setUserPlan('pro')">Pro</button>
        <button class="plan-chip" data-plan="max5" onclick="setUserPlan('max5')">Max 5&times;</button>
        <button class="plan-chip" data-plan="max20" onclick="setUserPlan('max20')">Max 20&times;</button>
        <button class="plan-chip" data-plan="api" onclick="setUserPlan('api')">API</button>
      </div>
      <button class="dismiss" onclick="dismissPlanPicker()" aria-label="Dismiss plan picker">&times; Dismiss</button>
    </div>

    <div class="headline">
      <div class="eyebrow" id="headline-range">Last 30 days</div>
      <div class="summary" id="headline-summary">&nbsp;</div>
      <div class="intensity-row">
        <span class="intensity-pill" id="intensity-pill">&nbsp;</span>
        <span class="intensity-desc" id="intensity-desc">&nbsp;</span>
      </div>
    </div>

    <div class="plan-comparison">
      <div class="title" id="plan-comparison-title">What this would cost on each plan</div>
      <div class="subtitle">Subscription prices are fixed monthly. API is pay-per-use, based on the tokens you consumed. Rate-limit status is approximate.</div>
      <div class="plan-grid" id="plan-grid"></div>
    </div>

    <div class="simple-stats" id="simple-stats"></div>

<div class="projects-simple">
      <div class="title">Where you spent the most time</div>
      <div class="subtitle">Projects ranked by number of conversations.</div>
      <div id="simple-projects"></div>
    </div>
  </div>

  <!-- ── Advanced mode (original dashboard) ───────────────────────── -->
  <div class="advanced-only stats-row" id="stats-row"></div>
  <div class="advanced-only charts-grid">
    <div class="chart-card wide">
      <h2 id="daily-chart-title">Daily Token Usage</h2>
      <div class="chart-wrap tall"><canvas id="chart-daily"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>By Model</h2>
      <div class="chart-wrap"><canvas id="chart-model"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>Top Projects by Tokens</h2>
      <div class="chart-wrap"><canvas id="chart-project"></canvas></div>
    </div>
  </div>
  <div class="advanced-only table-card">
    <div class="section-title">Cost by Model</div>
    <table>
      <thead><tr>
        <th>Model</th>
        <th class="sortable" onclick="setModelSort('turns')">Turns <span class="sort-icon" id="msort-turns"></span></th>
        <th class="sortable" onclick="setModelSort('input')">Input <span class="sort-icon" id="msort-input"></span></th>
        <th class="sortable" onclick="setModelSort('output')">Output <span class="sort-icon" id="msort-output"></span></th>
        <th class="sortable" onclick="setModelSort('cache_read')">Cache Read <span class="sort-icon" id="msort-cache_read"></span></th>
        <th class="sortable" onclick="setModelSort('cache_creation')">Cache Creation <span class="sort-icon" id="msort-cache_creation"></span></th>
        <th class="sortable" onclick="setModelSort('cost')">Est. Cost <span class="sort-icon" id="msort-cost"></span></th>
      </tr></thead>
      <tbody id="model-cost-body"></tbody>
    </table>
  </div>
  <div class="advanced-only table-card">
    <div class="section-header"><div class="section-title">Recent Sessions</div><button class="export-btn" onclick="exportSessionsCSV()" title="Export all filtered sessions to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Session</th>
        <th>Project</th>
        <th class="sortable" onclick="setSessionSort('last')">Last Active <span class="sort-icon" id="sort-icon-last"></span></th>
        <th class="sortable" onclick="setSessionSort('duration_min')">Duration <span class="sort-icon" id="sort-icon-duration_min"></span></th>
        <th>Model</th>
        <th class="sortable" onclick="setSessionSort('turns')">Turns <span class="sort-icon" id="sort-icon-turns"></span></th>
        <th class="sortable" onclick="setSessionSort('input')">Input <span class="sort-icon" id="sort-icon-input"></span></th>
        <th class="sortable" onclick="setSessionSort('output')">Output <span class="sort-icon" id="sort-icon-output"></span></th>
        <th class="sortable" onclick="setSessionSort('cost')">Est. Cost <span class="sort-icon" id="sort-icon-cost"></span></th>
      </tr></thead>
      <tbody id="sessions-body"></tbody>
    </table>
  </div>
  <div class="advanced-only table-card">
    <div class="section-header"><div class="section-title">Cost by Project</div><button class="export-btn" onclick="exportProjectsCSV()" title="Export all projects to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Project</th>
        <th class="sortable" onclick="setProjectSort('sessions')">Sessions <span class="sort-icon" id="psort-sessions"></span></th>
        <th class="sortable" onclick="setProjectSort('turns')">Turns <span class="sort-icon" id="psort-turns"></span></th>
        <th class="sortable" onclick="setProjectSort('input')">Input <span class="sort-icon" id="psort-input"></span></th>
        <th class="sortable" onclick="setProjectSort('output')">Output <span class="sort-icon" id="psort-output"></span></th>
        <th class="sortable" onclick="setProjectSort('cost')">Est. Cost <span class="sort-icon" id="psort-cost"></span></th>
      </tr></thead>
      <tbody id="project-cost-body"></tbody>
    </table>
  </div>
</div>

<footer>
  <div class="footer-content">
    <p>Cost estimates based on Anthropic API pricing (<a href="https://claude.com/pricing#api" target="_blank">claude.com/pricing#api</a>) as of April 2026. Only models containing <em>opus</em>, <em>sonnet</em>, or <em>haiku</em> in the name are included in cost calculations. Actual costs for Max/Pro subscribers differ from API pricing.</p>
    <p>
      GitHub: <a href="https://github.com/phuryn/claude-usage" target="_blank">https://github.com/phuryn/claude-usage</a>
      &nbsp;&middot;&nbsp;
      Created by: <a href="https://www.productcompass.pm" target="_blank">The Product Compass Newsletter</a>
      &nbsp;&middot;&nbsp;
      License: MIT
    </p>
  </div>
</footer>

<script>
// ── Helpers ────────────────────────────────────────────────────────────────
function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ── State ──────────────────────────────────────────────────────────────────
let rawData = null;
let selectedModels = new Set();
let selectedRange = '30d';
let charts = {};
let sessionSortCol = 'last';
let modelSortCol = 'cost';
let modelSortDir = 'desc';
let projectSortCol = 'cost';
let projectSortDir = 'desc';
let lastFilteredSessions = [];
let lastByProject = [];
let sessionSortDir = 'desc';
let viewMode = 'simple';
let userPlan = null;

// ── Subscription plan config ───────────────────────────────────────────────
// Prices and approximate limits reflect publicly documented Anthropic tiers.
// Limits are approximations based on API-equivalent monthly cost headroom.
const PLANS = [
  { id: 'pro',   name: 'Pro',     price: 20,  subtitle: '/month',        okUpTo: 150,   warnUpTo: 500 },
  { id: 'max5',  name: 'Max 5\u00d7',  price: 100, subtitle: '/month',   okUpTo: 750,   warnUpTo: 2500 },
  { id: 'max20', name: 'Max 20\u00d7', price: 200, subtitle: '/month',   okUpTo: 3000,  warnUpTo: 10000 },
  { id: 'api',   name: 'API',     price: null, subtitle: 'pay-as-you-go', okUpTo: Infinity, warnUpTo: Infinity },
];

function planStatus(plan, apiEquivCost) {
  if (plan.id === 'api') return { kind: 'ok', text: 'pay only for what you use' };
  if (apiEquivCost <= plan.okUpTo)   return { kind: 'ok',     text: 'within typical limits' };
  if (apiEquivCost <= plan.warnUpTo) return { kind: 'warn',   text: 'may hit rate limits' };
  return                                    { kind: 'exceed', text: 'would exceed limits' };
}

// ── Pricing (Anthropic API, April 2026) ────────────────────────────────────
const PRICING = {
  'claude-opus-4-6':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-opus-4-5':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-sonnet-4-6': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-sonnet-4-5': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-haiku-4-5':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
  'claude-haiku-4-6':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
};

function isBillable(model) {
  if (!model) return false;
  const m = model.toLowerCase();
  return m.includes('opus') || m.includes('sonnet') || m.includes('haiku');
}

function getPricing(model) {
  if (!model) return null;
  if (PRICING[model]) return PRICING[model];
  for (const key of Object.keys(PRICING)) {
    if (model.startsWith(key)) return PRICING[key];
  }
  const m = model.toLowerCase();
  if (m.includes('opus'))   return PRICING['claude-opus-4-6'];
  if (m.includes('sonnet')) return PRICING['claude-sonnet-4-6'];
  if (m.includes('haiku'))  return PRICING['claude-haiku-4-5'];
  return null;
}

function calcCost(model, inp, out, cacheRead, cacheCreation) {
  if (!isBillable(model)) return 0;
  const p = getPricing(model);
  if (!p) return 0;
  return (
    inp           * p.input       / 1e6 +
    out           * p.output      / 1e6 +
    cacheRead     * p.cache_read  / 1e6 +
    cacheCreation * p.cache_write / 1e6
  );
}

// ── Formatting ─────────────────────────────────────────────────────────────
function fmt(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n.toLocaleString();
}
function fmtCost(c)    { return '$' + c.toFixed(4); }
function fmtCostBig(c) { return '$' + c.toFixed(2); }

// ── Chart colors ───────────────────────────────────────────────────────────
Chart.defaults.color = '#87867f';
Chart.defaults.borderColor = '#f0eee6';

const TOKEN_COLORS = {
  input:          'rgba(201,100,66,0.75)',
  output:         'rgba(94,93,89,0.75)',
  cache_read:     'rgba(176,174,165,0.65)',
  cache_creation: 'rgba(232,230,220,0.90)',
};
const MODEL_COLORS = ['#c96442','#5e5d59','#87867f','#b0aea5','#d97757','#4d4c48','#e8e6dc','#3d3d3a'];

// ── Time range ─────────────────────────────────────────────────────────────
const RANGE_LABELS = { '7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', 'all': 'All Time' };
const RANGE_TICKS  = { '7d': 7, '30d': 15, '90d': 13, 'all': 12 };

function getRangeCutoff(range) {
  if (range === 'all') return null;
  const days = range === '7d' ? 7 : range === '30d' ? 30 : 90;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function readURLRange() {
  const p = new URLSearchParams(window.location.search).get('range');
  return ['7d', '30d', '90d', 'all'].includes(p) ? p : '30d';
}

function setRange(range) {
  selectedRange = range;
  document.querySelectorAll('.range-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.range === range)
  );
  updateURL();
  applyFilter();
}

// ── Model filter ───────────────────────────────────────────────────────────
function modelPriority(m) {
  const ml = m.toLowerCase();
  if (ml.includes('opus'))   return 0;
  if (ml.includes('sonnet')) return 1;
  if (ml.includes('haiku'))  return 2;
  return 3;
}

function readURLModels(allModels) {
  const param = new URLSearchParams(window.location.search).get('models');
  if (!param) return new Set(allModels.filter(m => isBillable(m)));
  const fromURL = new Set(param.split(',').map(s => s.trim()).filter(Boolean));
  return new Set(allModels.filter(m => fromURL.has(m)));
}

function isDefaultModelSelection(allModels) {
  const billable = allModels.filter(m => isBillable(m));
  if (selectedModels.size !== billable.length) return false;
  return billable.every(m => selectedModels.has(m));
}

function buildFilterUI(allModels) {
  const sorted = [...allModels].sort((a, b) => {
    const pa = modelPriority(a), pb = modelPriority(b);
    return pa !== pb ? pa - pb : a.localeCompare(b);
  });
  selectedModels = readURLModels(allModels);
  const container = document.getElementById('model-checkboxes');
  container.innerHTML = sorted.map(m => {
    const checked = selectedModels.has(m);
    return `<label class="model-cb-label ${checked ? 'checked' : ''}" data-model="${esc(m)}">
      <input type="checkbox" value="${esc(m)}" ${checked ? 'checked' : ''} onchange="onModelToggle(this)">
      ${esc(m)}
    </label>`;
  }).join('');
}

function onModelToggle(cb) {
  const label = cb.closest('label');
  if (cb.checked) { selectedModels.add(cb.value);    label.classList.add('checked'); }
  else            { selectedModels.delete(cb.value); label.classList.remove('checked'); }
  updateURL();
  applyFilter();
}

function selectAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = true; selectedModels.add(cb.value); cb.closest('label').classList.add('checked');
  });
  updateURL(); applyFilter();
}

function clearAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = false; selectedModels.delete(cb.value); cb.closest('label').classList.remove('checked');
  });
  updateURL(); applyFilter();
}

// ── URL persistence ────────────────────────────────────────────────────────
function updateURL() {
  const allModels = Array.from(document.querySelectorAll('#model-checkboxes input')).map(cb => cb.value);
  const params = new URLSearchParams();
  if (selectedRange !== '30d') params.set('range', selectedRange);
  if (!isDefaultModelSelection(allModels)) params.set('models', Array.from(selectedModels).join(','));
  const search = params.toString() ? '?' + params.toString() : '';
  history.replaceState(null, '', window.location.pathname + search);
}

// ── Session sort ───────────────────────────────────────────────────────────
function setSessionSort(col) {
  if (sessionSortCol === col) {
    sessionSortDir = sessionSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sessionSortCol = col;
    sessionSortDir = 'desc';
  }
  updateSortIcons();
  applyFilter();
}

function updateSortIcons() {
  document.querySelectorAll('.sort-icon').forEach(el => el.textContent = '');
  const icon = document.getElementById('sort-icon-' + sessionSortCol);
  if (icon) icon.textContent = sessionSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortSessions(sessions) {
  return [...sessions].sort((a, b) => {
    let av, bv;
    if (sessionSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation);
    } else if (sessionSortCol === 'duration_min') {
      av = parseFloat(a.duration_min) || 0;
      bv = parseFloat(b.duration_min) || 0;
    } else {
      av = a[sessionSortCol] ?? 0;
      bv = b[sessionSortCol] ?? 0;
    }
    if (av < bv) return sessionSortDir === 'desc' ? 1 : -1;
    if (av > bv) return sessionSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

// ── Aggregation & filtering ────────────────────────────────────────────────
function applyFilter() {
  if (!rawData) return;

  const cutoff = getRangeCutoff(selectedRange);

  // Filter daily rows by model + date range
  const filteredDaily = rawData.daily_by_model.filter(r =>
    selectedModels.has(r.model) && (!cutoff || r.day >= cutoff)
  );

  // Daily chart: aggregate by day
  const dailyMap = {};
  for (const r of filteredDaily) {
    if (!dailyMap[r.day]) dailyMap[r.day] = { day: r.day, input: 0, output: 0, cache_read: 0, cache_creation: 0 };
    const d = dailyMap[r.day];
    d.input          += r.input;
    d.output         += r.output;
    d.cache_read     += r.cache_read;
    d.cache_creation += r.cache_creation;
  }
  const daily = Object.values(dailyMap).sort((a, b) => a.day.localeCompare(b.day));

  // By model: aggregate tokens + turns from daily data
  const modelMap = {};
  for (const r of filteredDaily) {
    if (!modelMap[r.model]) modelMap[r.model] = { model: r.model, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0 };
    const m = modelMap[r.model];
    m.input          += r.input;
    m.output         += r.output;
    m.cache_read     += r.cache_read;
    m.cache_creation += r.cache_creation;
    m.turns          += r.turns;
  }

  // Filter sessions by model + date range
  const filteredSessions = rawData.sessions_all.filter(s =>
    selectedModels.has(s.model) && (!cutoff || s.last_date >= cutoff)
  );

  // Add session counts into modelMap
  for (const s of filteredSessions) {
    if (modelMap[s.model]) modelMap[s.model].sessions++;
  }

  const byModel = Object.values(modelMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // By project: aggregate from filtered sessions
  const projMap = {};
  for (const s of filteredSessions) {
    if (!projMap[s.project]) projMap[s.project] = { project: s.project, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0, cost: 0 };
    const p = projMap[s.project];
    p.input          += s.input;
    p.output         += s.output;
    p.cache_read     += s.cache_read;
    p.cache_creation += s.cache_creation;
    p.turns          += s.turns;
    p.sessions++;
    p.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
  }
  const byProject = Object.values(projMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // Totals
  const totals = {
    sessions:       filteredSessions.length,
    turns:          byModel.reduce((s, m) => s + m.turns, 0),
    input:          byModel.reduce((s, m) => s + m.input, 0),
    output:         byModel.reduce((s, m) => s + m.output, 0),
    cache_read:     byModel.reduce((s, m) => s + m.cache_read, 0),
    cache_creation: byModel.reduce((s, m) => s + m.cache_creation, 0),
    cost:           byModel.reduce((s, m) => s + calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation), 0),
  };

  // Update daily chart title
  document.getElementById('daily-chart-title').textContent = 'Daily Token Usage \u2014 ' + RANGE_LABELS[selectedRange];

  renderStats(totals);
  renderSimpleHeadline(totals, byModel, byProject);
  renderPlanComparison(totals);
  renderSimpleStats(totals);
  renderSimpleProjects(byProject);
  renderDailyChart(daily);
  renderModelChart(byModel);
  renderProjectChart(byProject);
  lastFilteredSessions = sortSessions(filteredSessions);
  lastByProject = sortProjects(byProject);
  renderSessionsTable(lastFilteredSessions.slice(0, 20));
  renderModelCostTable(byModel);
  renderProjectCostTable(lastByProject.slice(0, 20));
}

// ── View mode toggle ───────────────────────────────────────────────────────
function setViewMode(mode) {
  viewMode = mode === 'advanced' ? 'advanced' : 'simple';
  document.body.classList.toggle('view-simple', viewMode === 'simple');
  document.body.classList.toggle('view-advanced', viewMode === 'advanced');
  document.getElementById('view-simple-btn').classList.toggle('active', viewMode === 'simple');
  document.getElementById('view-simple-btn').setAttribute('aria-selected', viewMode === 'simple');
  document.getElementById('view-advanced-btn').classList.toggle('active', viewMode === 'advanced');
  document.getElementById('view-advanced-btn').setAttribute('aria-selected', viewMode === 'advanced');
  try { localStorage.setItem('cud_view_mode', viewMode); } catch(e) {}
}

// ── User plan selection ────────────────────────────────────────────────────
function setUserPlan(planId) {
  userPlan = planId;
  try { localStorage.setItem('cud_user_plan', planId); } catch(e) {}
  document.querySelectorAll('.plan-chip').forEach(c =>
    c.classList.toggle('selected', c.dataset.plan === planId)
  );
  if (rawData) applyFilter();
}

function dismissPlanPicker() {
  document.getElementById('plan-picker').style.display = 'none';
  try { localStorage.setItem('cud_plan_picker_dismissed', '1'); } catch(e) {}
}

// ── Human-scale conversions ────────────────────────────────────────────────
// ~0.75 words per token (English text); ~500 words per printed page.
function tokensToPages(tokens) { return tokens * 0.75 / 500; }

function fmtPages(tokens) {
  const p = tokensToPages(tokens);
  if (p < 1)    return '<1 page';
  if (p < 10)   return p.toFixed(1) + ' pages';
  if (p < 1000) return Math.round(p).toLocaleString() + ' pages';
  if (p < 1e6)  return (p/1000).toFixed(1) + 'K pages';
  return (p/1e6).toFixed(1) + 'M pages';
}

// ── Intensity classification ───────────────────────────────────────────────
// Thresholds reflect typical monthly API-equivalent cost.
// We normalize by selected range so the rating is always "per month equivalent."
function intensityFor(apiEquivCost, rangeDays) {
  const perMonth = apiEquivCost * (30 / Math.max(rangeDays, 1));
  if (perMonth < 20)   return { key: 'light',   label: 'Light use',    desc: 'About what we see from users who try Claude a few times a week.' };
  if (perMonth < 150)  return { key: 'typical', label: 'Typical use',  desc: 'About what we see from users who use Claude a few hours a week.' };
  if (perMonth < 500)  return { key: 'heavy',   label: 'Heavy use',    desc: 'About what we see from users who use Claude daily for work.' };
  return                      { key: 'power',   label: 'Power user',   desc: 'Among the heaviest users \u2014 Claude is a core part of your workflow.' };
}

function rangeDays() {
  if (selectedRange === '7d')  return 7;
  if (selectedRange === '30d') return 30;
  if (selectedRange === '90d') return 90;
  // "all": estimate from first/last day present in filtered data
  if (!rawData || !rawData.daily_by_model.length) return 30;
  const days = new Set(rawData.daily_by_model.map(r => r.day));
  return Math.max(days.size, 1);
}

// ── Simple-mode renderers ─────────────────────────────────────────────────
function dominantModel(byModel) {
  if (!byModel.length) return null;
  const sorted = [...byModel].sort((a, b) =>
    (b.input + b.output + b.cache_read) - (a.input + a.output + a.cache_read)
  );
  return sorted[0].model;
}

function friendlyModelName(model) {
  const m = (model || '').toLowerCase();
  if (m.includes('opus'))   return 'Opus';
  if (m.includes('sonnet')) return 'Sonnet';
  if (m.includes('haiku'))  return 'Haiku';
  return model || 'an unknown model';
}

function renderSimpleHeadline(totals, byModel, byProject) {
  document.getElementById('headline-range').textContent = RANGE_LABELS[selectedRange];
  const convos = totals.sessions.toLocaleString();
  const projects = byProject.length;
  const model = friendlyModelName(dominantModel(byModel));
  const summary = `You had <strong>${convos} conversations</strong> with Claude across <strong>${projects} ${projects === 1 ? 'project' : 'projects'}</strong>, mostly using <strong>${esc(model)}</strong>.`;
  document.getElementById('headline-summary').innerHTML = summary;

  const intensity = intensityFor(totals.cost, rangeDays());
  const pill = document.getElementById('intensity-pill');
  pill.className = 'intensity-pill ' + intensity.key;
  pill.textContent = intensity.label;
  document.getElementById('intensity-desc').textContent = intensity.desc;
}

function renderPlanComparison(totals) {
  const apiCost = totals.cost;
  const rangeDayCount = rangeDays();
  // Normalize to monthly equivalent for comparison
  const monthly = apiCost * (30 / Math.max(rangeDayCount, 1));

  // Reframe headline if user picked a plan
  const title = document.getElementById('plan-comparison-title');
  if (userPlan && userPlan !== 'api') {
    const plan = PLANS.find(p => p.id === userPlan);
    const saved = monthly - plan.price;
    if (saved > 0) {
      title.innerHTML = `Your <strong>${esc(plan.name)}</strong> plan saved you about <strong>$${saved.toFixed(0)}</strong> vs pay-per-use API pricing this month.`;
    } else {
      title.innerHTML = `Your <strong>${esc(plan.name)}</strong> plan at $${plan.price}/mo &mdash; API pay-per-use would have cost about <strong>$${monthly.toFixed(0)}</strong>.`;
    }
  } else if (userPlan === 'api') {
    title.innerHTML = `You're on API pay-per-use. This month's usage cost about <strong>$${apiCost.toFixed(2)}</strong>.`;
  } else {
    title.textContent = 'What this would cost on each plan';
  }

  const grid = document.getElementById('plan-grid');
  grid.innerHTML = PLANS.map(plan => {
    const st = planStatus(plan, monthly);
    const priceText = plan.price !== null
      ? '$' + plan.price
      : '$' + apiCost.toFixed(apiCost < 10 ? 2 : 0);
    const isCurrent = userPlan === plan.id;
    const icon = st.kind === 'ok' ? '\u2713' : st.kind === 'warn' ? '\u26a0' : '\u2717';
    return `<div class="plan-card${isCurrent ? ' current' : ''}">
      ${isCurrent ? '<span class="current-badge">Your plan</span>' : ''}
      <div class="plan-name">${esc(plan.name)}</div>
      <div class="plan-price">${priceText}</div>
      <div class="plan-sub">${esc(plan.subtitle)}</div>
      <div class="plan-status ${st.kind}">${icon} ${esc(st.text)}</div>
    </div>`;
  }).join('');
}

function renderSimpleStats(totals) {
  const cards = [
    {
      label: 'Text Claude wrote for you',
      value: fmtPages(totals.output),
      sub: fmt(totals.output) + ' output tokens',
      tip: 'An estimate of how many printed pages of text Claude produced, at roughly 500 words per page.',
    },
    {
      label: 'Text Claude read',
      value: fmtPages(totals.input + totals.cache_read),
      sub: fmt(totals.input + totals.cache_read) + ' input tokens',
      tip: 'How much text Claude processed in total \u2014 your messages plus the files and project context it re-reads on every turn. The same file counted many times if it was in context for many turns, which is why this number can be very large.',
    },
    {
      label: 'Conversations',
      value: totals.sessions.toLocaleString(),
      sub: 'separate chats',
      tip: 'Each conversation is one Claude Code session \u2014 from when you start until you close it.',
    },
    {
      label: 'Messages exchanged',
      value: fmt(totals.turns),
      sub: 'back-and-forths',
      tip: 'One message from you plus one reply from Claude counts as one exchange.',
    },
  ];
  document.getElementById('simple-stats').innerHTML = cards.map(c => `
    <div class="simple-stat">
      <div class="simple-label">${esc(c.label)}<span class="jargon help-icon" data-tooltip="${esc(c.tip)}" tabindex="0">?</span></div>
      <div class="simple-value">${c.value}</div>
      <div class="simple-sub">${esc(c.sub)}</div>
    </div>
  `).join('');
}

function renderSimpleProjects(byProject) {
  const top = [...byProject].sort((a, b) => b.sessions - a.sessions).slice(0, 8);
  if (!top.length) {
    document.getElementById('simple-projects').innerHTML = '<div class="muted" style="padding:12px 0;font-size:13px;">No project activity in this range.</div>';
    return;
  }
  const maxSessions = Math.max(...top.map(p => p.sessions));
  function shortName(path) {
    if (!path) return 'unknown';
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts.slice(-1)[0] || path;
  }
  document.getElementById('simple-projects').innerHTML = top.map(p => {
    const pct = Math.max(4, Math.round((p.sessions / maxSessions) * 100));
    return `<div class="project-bar" title="${esc(p.project)}">
      <div class="proj-name">${esc(shortName(p.project))}</div>
      <div class="proj-track"><div class="proj-fill" style="width:${pct}%"></div></div>
      <div class="proj-count">${p.sessions} ${p.sessions === 1 ? 'conversation' : 'conversations'}</div>
    </div>`;
  }).join('');
}

// ── Renderers ──────────────────────────────────────────────────────────────
function renderStats(t) {
  const rangeLabel = RANGE_LABELS[selectedRange].toLowerCase();
  const stats = [
    { label: 'Sessions',       value: t.sessions.toLocaleString(), sub: rangeLabel },
    { label: 'Turns',          value: fmt(t.turns),                sub: rangeLabel },
    { label: 'Input Tokens',   value: fmt(t.input),                sub: rangeLabel },
    { label: 'Output Tokens',  value: fmt(t.output),               sub: rangeLabel },
    { label: 'Cache Read',     value: fmt(t.cache_read),           sub: 'from prompt cache' },
    { label: 'Cache Creation', value: fmt(t.cache_creation),       sub: 'writes to prompt cache' },
    { label: 'Est. Cost',      value: fmtCostBig(t.cost),          sub: 'API pricing, Apr 2026', color: '#c96442' },
  ];
  document.getElementById('stats-row').innerHTML = stats.map(s => `
    <div class="stat-card">
      <div class="label">${s.label}</div>
      <div class="value" style="${s.color ? 'color:' + s.color : ''}">${esc(s.value)}</div>
      ${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}
    </div>
  `).join('');
}

function renderDailyChart(daily) {
  const ctx = document.getElementById('chart-daily').getContext('2d');
  if (charts.daily) charts.daily.destroy();
  charts.daily = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.day),
      datasets: [
        { label: 'Input',          data: daily.map(d => d.input),          backgroundColor: TOKEN_COLORS.input,          stack: 'tokens' },
        { label: 'Output',         data: daily.map(d => d.output),         backgroundColor: TOKEN_COLORS.output,         stack: 'tokens' },
        { label: 'Cache Read',     data: daily.map(d => d.cache_read),     backgroundColor: TOKEN_COLORS.cache_read,     stack: 'tokens' },
        { label: 'Cache Creation', data: daily.map(d => d.cache_creation), backgroundColor: TOKEN_COLORS.cache_creation, stack: 'tokens' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#87867f', boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: '#87867f', maxTicksLimit: RANGE_TICKS[selectedRange] }, grid: { color: '#f0eee6', lineWidth: 1 }, border: { display: false, dash: [] } },
        y: { ticks: { color: '#87867f', callback: v => fmt(v) }, grid: { color: '#f0eee6', lineWidth: 1 }, border: { display: false, dash: [] } },
      }
    }
  });
}

function renderModelChart(byModel) {
  const ctx = document.getElementById('chart-model').getContext('2d');
  if (charts.model) charts.model.destroy();
  if (!byModel.length) { charts.model = null; return; }
  charts.model = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: byModel.map(m => m.model),
      datasets: [{ data: byModel.map(m => m.input + m.output), backgroundColor: MODEL_COLORS, borderWidth: 3, borderColor: '#faf9f5' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#87867f', boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)} tokens` } }
      }
    }
  });
}

function renderProjectChart(byProject) {
  const top = byProject.slice(0, 10);
  const ctx = document.getElementById('chart-project').getContext('2d');
  if (charts.project) charts.project.destroy();
  if (!top.length) { charts.project = null; return; }
  charts.project = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top.map(p => p.project.length > 22 ? '\u2026' + p.project.slice(-20) : p.project),
      datasets: [
        { label: 'Input',  data: top.map(p => p.input),  backgroundColor: TOKEN_COLORS.input },
        { label: 'Output', data: top.map(p => p.output), backgroundColor: TOKEN_COLORS.output },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#87867f', boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: '#87867f', callback: v => fmt(v) }, grid: { color: '#f0eee6', lineWidth: 1 }, border: { display: false, dash: [] } },
        y: { ticks: { color: '#87867f', font: { size: 11 } }, grid: { color: '#f0eee6', lineWidth: 1 }, border: { display: false, dash: [] } },
      }
    }
  });
}

function renderSessionsTable(sessions) {
  document.getElementById('sessions-body').innerHTML = sessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    const costCell = isBillable(s.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td class="muted" style="font-family:monospace">${esc(s.session_id)}&hellip;</td>
      <td>${esc(s.project)}</td>
      <td class="muted">${esc(s.last)}</td>
      <td class="muted">${esc(s.duration_min)}m</td>
      <td><span class="model-tag">${esc(s.model)}</span></td>
      <td class="num">${s.turns}</td>
      <td class="num">${fmt(s.input)}</td>
      <td class="num">${fmt(s.output)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

function setModelSort(col) {
  if (modelSortCol === col) {
    modelSortDir = modelSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    modelSortCol = col;
    modelSortDir = 'desc';
  }
  updateModelSortIcons();
  applyFilter();
}

function updateModelSortIcons() {
  document.querySelectorAll('[id^="msort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('msort-' + modelSortCol);
  if (icon) icon.textContent = modelSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortModels(byModel) {
  return [...byModel].sort((a, b) => {
    let av, bv;
    if (modelSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation);
    } else {
      av = a[modelSortCol] ?? 0;
      bv = b[modelSortCol] ?? 0;
    }
    if (av < bv) return modelSortDir === 'desc' ? 1 : -1;
    if (av > bv) return modelSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderModelCostTable(byModel) {
  document.getElementById('model-cost-body').innerHTML = sortModels(byModel).map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation);
    const costCell = isBillable(m.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td><span class="model-tag">${esc(m.model)}</span></td>
      <td class="num">${fmt(m.turns)}</td>
      <td class="num">${fmt(m.input)}</td>
      <td class="num">${fmt(m.output)}</td>
      <td class="num">${fmt(m.cache_read)}</td>
      <td class="num">${fmt(m.cache_creation)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

// ── Project cost table sorting ────────────────────────────────────────────
function setProjectSort(col) {
  if (projectSortCol === col) {
    projectSortDir = projectSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    projectSortCol = col;
    projectSortDir = 'desc';
  }
  updateProjectSortIcons();
  applyFilter();
}

function updateProjectSortIcons() {
  document.querySelectorAll('[id^="psort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('psort-' + projectSortCol);
  if (icon) icon.textContent = projectSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortProjects(byProject) {
  return [...byProject].sort((a, b) => {
    const av = a[projectSortCol] ?? 0;
    const bv = b[projectSortCol] ?? 0;
    if (av < bv) return projectSortDir === 'desc' ? 1 : -1;
    if (av > bv) return projectSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderProjectCostTable(byProject) {
  document.getElementById('project-cost-body').innerHTML = sortProjects(byProject).map(p => {
    return `<tr>
      <td>${esc(p.project)}</td>
      <td class="num">${p.sessions}</td>
      <td class="num">${fmt(p.turns)}</td>
      <td class="num">${fmt(p.input)}</td>
      <td class="num">${fmt(p.output)}</td>
      <td class="cost">${fmtCost(p.cost)}</td>
    </tr>`;
  }).join('');
}

// ── CSV Export ────────────────────────────────────────────────────────────
function csvField(val) {
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function csvTimestamp() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
    + '_' + String(d.getHours()).padStart(2,'0') + String(d.getMinutes()).padStart(2,'0');
}

function downloadCSV(reportType, header, rows) {
  const lines = [header.map(csvField).join(',')];
  for (const row of rows) {
    lines.push(row.map(csvField).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = reportType + '_' + csvTimestamp() + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportSessionsCSV() {
  const header = ['Session', 'Project', 'Last Active', 'Duration (min)', 'Model', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastFilteredSessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    return [s.session_id, s.project, s.last, s.duration_min, s.model, s.turns, s.input, s.output, s.cache_read, s.cache_creation, cost.toFixed(4)];
  });
  downloadCSV('sessions', header, rows);
}

function exportProjectsCSV() {
  const header = ['Project', 'Sessions', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByProject.map(p => {
    return [p.project, p.sessions, p.turns, p.input, p.output, p.cache_read, p.cache_creation, p.cost.toFixed(4)];
  });
  downloadCSV('projects', header, rows);
}

// ── Rescan ────────────────────────────────────────────────────────────────
async function triggerRescan() {
  const btn = document.getElementById('rescan-btn');
  btn.disabled = true;
  btn.textContent = '\u21bb Scanning...';
  try {
    const resp = await fetch('/api/rescan', { method: 'POST' });
    const d = await resp.json();
    btn.textContent = '\u21bb Rescan (' + d.new + ' new, ' + d.updated + ' updated)';
    await loadData();
  } catch(e) {
    btn.textContent = '\u21bb Rescan (error)';
    console.error(e);
  }
  setTimeout(() => { btn.textContent = '\u21bb Rescan'; btn.disabled = false; }, 3000);
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadData() {
  try {
    const resp = await fetch('/api/data');
    const d = await resp.json();
    if (d.error) {
      document.body.innerHTML = '<div style="padding:40px;color:#f87171">' + esc(d.error) + '</div>';
      return;
    }
    document.getElementById('meta').textContent = 'Updated: ' + d.generated_at + ' \u00b7 Auto-refresh in 30s';

    const isFirstLoad = rawData === null;
    rawData = d;

    if (isFirstLoad) {
      // Restore view mode (default: simple)
      let savedMode = 'simple';
      try { savedMode = localStorage.getItem('cud_view_mode') || 'simple'; } catch(e) {}
      setViewMode(savedMode);
      // Restore user plan selection
      try { userPlan = localStorage.getItem('cud_user_plan'); } catch(e) {}
      if (userPlan) {
        document.querySelectorAll('.plan-chip').forEach(c =>
          c.classList.toggle('selected', c.dataset.plan === userPlan)
        );
      }
      // Restore plan picker dismissed state (also treat a chosen plan as dismissed)
      let dismissed = false;
      try { dismissed = localStorage.getItem('cud_plan_picker_dismissed') === '1'; } catch(e) {}
      if (dismissed || userPlan) {
        document.getElementById('plan-picker').style.display = 'none';
      }
      // Restore range from URL, mark active button
      selectedRange = readURLRange();
      document.querySelectorAll('.range-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.range === selectedRange)
      );
      // Build model filter (reads URL for model selection too)
      buildFilterUI(d.all_models);
      updateSortIcons();
      updateModelSortIcons();
      updateProjectSortIcons();
    }

    applyFilter();
  } catch(e) {
    console.error(e);
  }
}

loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

        elif self.path == "/api/data":
            data = get_dashboard_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/rescan":
            # Full rebuild: delete DB and rescan from scratch
            if DB_PATH.exists():
                DB_PATH.unlink()
            from scanner import scan
            result = scan(verbose=False)
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def serve(host=None, port=None):
    host = host or os.environ.get("HOST", "localhost")
    port = port or int(os.environ.get("PORT", "8080"))
    server = HTTPServer((host, port), DashboardHandler)
    print(f"Dashboard running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
