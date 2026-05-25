"""
Dashboard web de actividad de agentes. Sirve en http://127.0.0.1:8080
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from agent_tracker import tracker

_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ambrosio — Agent View</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f1117; color: #e2e8f0; font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }

  header {
    padding: 18px 24px 14px;
    border-bottom: 1px solid #1e293b;
    display: flex; align-items: center; gap: 12px;
  }
  header .icon { font-size: 22px; }
  header h1 { font-size: 17px; font-weight: 600; color: #f1f5f9; }
  header .sub { color: #64748b; font-size: 12px; margin-top: 2px; }
  #updated { margin-left: auto; color: #475569; font-size: 11px; }

  .main { padding: 20px 24px; }

  /* --- Agent cards --- */
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 12px;
    margin-bottom: 28px;
  }
  .card {
    background: #161b27;
    border: 1px solid #1e293b;
    border-radius: 10px;
    padding: 16px;
    transition: border-color 0.3s;
  }
  .card.active { border-color: #334155; }
  .card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
  .card-name { font-weight: 600; font-size: 13px; color: #94a3b8; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
  .dot-idle    { background: #334155; }
  .dot-running { background: #f59e0b; box-shadow: 0 0 6px #f59e0b80; animation: blink 0.8s infinite; }
  .dot-success { background: #22c55e; box-shadow: 0 0 4px #22c55e60; }
  .dot-error   { background: #ef4444; box-shadow: 0 0 4px #ef444460; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .stat { display: flex; justify-content: space-between; align-items: center; margin-top: 7px; }
  .stat-label { color: #475569; font-size: 12px; }
  .stat-value { font-weight: 600; font-size: 13px; color: #cbd5e1; }
  .stat-value.err { color: #ef4444; }

  /* --- Feed table --- */
  .section-title {
    font-size: 11px; font-weight: 600; color: #475569;
    text-transform: uppercase; letter-spacing: 0.07em;
    margin-bottom: 10px;
  }
  .table-wrap { overflow-x: auto; border-radius: 8px; border: 1px solid #1e293b; }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left; padding: 9px 12px;
    font-size: 11px; color: #475569;
    text-transform: uppercase; letter-spacing: 0.06em;
    background: #0d1320;
    border-bottom: 1px solid #1e293b;
  }
  td { padding: 8px 12px; border-bottom: 1px solid #111827; font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #161b27; }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.04em;
  }
  .badge-success { background: #052e16; color: #4ade80; }
  .badge-error   { background: #1f0909; color: #f87171; }
  .badge-running { background: #1c1003; color: #fbbf24; }

  .agent-tag {
    background: #0f2a47; color: #60a5fa;
    padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;
  }
  .tool-name { color: #c084fc; font-family: 'Consolas', monospace; font-size: 12px; }
  .preview   { color: #64748b; font-size: 12px; font-style: italic; }
  .summary   { color: #94a3b8; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .duration  { color: #475569; font-family: 'Consolas', monospace; font-size: 12px; white-space: nowrap; }
  .time-cell { color: #64748b; font-family: 'Consolas', monospace; font-size: 12px; white-space: nowrap; }

  .empty-row td { color: #334155; text-align: center; padding: 32px; }
</style>
</head>
<body>
<header>
  <span class="icon">🤖</span>
  <div>
    <h1>Ambrosio — Agent View</h1>
    <div class="sub">Monitor de actividad · actualiza cada 2 s</div>
  </div>
  <div id="updated">—</div>
</header>

<div class="main">
  <div id="cards" class="cards"></div>

  <div class="section-title">Actividad reciente</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Hora</th>
          <th>Agente</th>
          <th>Herramienta</th>
          <th>Entrada</th>
          <th>Estado</th>
          <th>Duración</th>
          <th>Resultado</th>
        </tr>
      </thead>
      <tbody id="feed"></tbody>
    </table>
  </div>
</div>

<script>
const AGENT_ORDER  = ["GmailReadAgent","GmailSendAgent","CalendarAgent","ReminderAgent","MarineAgent","TadoAgent"];
const AGENT_LABELS = {
  GmailReadAgent: "Gmail Read",
  GmailSendAgent: "Gmail Send",
  CalendarAgent:  "Calendar",
  ReminderAgent:  "Reminder",
  MarineAgent:    "🌊 Marine",
  TadoAgent:      "🏠 Tado",
};

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("es-ES", {hour:"2-digit",minute:"2-digit",second:"2-digit"});
}
function fmtDur(ms) {
  if (ms == null) return "—";
  return ms < 1000 ? ms.toFixed(0)+" ms" : (ms/1000).toFixed(2)+" s";
}
function esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

async function refresh() {
  let data;
  try {
    data = await fetch("/api/status").then(r => r.json());
    document.getElementById("updated").textContent = "Actualizado: " + new Date().toLocaleTimeString("es-ES");
  } catch {
    document.getElementById("updated").textContent = "⚠ Sin conexión";
    return;
  }
  renderCards(data.agents);
  renderFeed(data.recent_calls);
}

function renderCards(agents) {
  document.getElementById("cards").innerHTML = AGENT_ORDER.map(name => {
    const s = agents[name] || {total:0, errors:0, last_call:null, last_status:null};
    const dotClass =
      s.last_status === "running" ? "dot-running" :
      s.last_status === "success" ? "dot-success" :
      s.last_status === "error"   ? "dot-error"   : "dot-idle";
    return `<div class="card${s.last_status ? ' active' : ''}">
      <div class="card-header">
        <span class="dot ${dotClass}"></span>
        <span class="card-name">${AGENT_LABELS[name]}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Llamadas</span>
        <span class="stat-value">${s.total}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Errores</span>
        <span class="stat-value ${s.errors > 0 ? 'err' : ''}">${s.errors}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Último uso</span>
        <span class="stat-value">${fmtTime(s.last_call)}</span>
      </div>
    </div>`;
  }).join("");
}

function renderFeed(calls) {
  if (!calls.length) {
    document.getElementById("feed").innerHTML =
      `<tr class="empty-row"><td colspan="7">Sin actividad registrada todavía</td></tr>`;
    return;
  }
  document.getElementById("feed").innerHTML = calls.map(c => {
    const badgeClass =
      c.status === "success" ? "badge-success" :
      c.status === "error"   ? "badge-error"   : "badge-running";
    const display = c.error || c.summary || "—";
    return `<tr>
      <td class="time-cell">${fmtTime(c.started_at)}</td>
      <td><span class="agent-tag">${esc(c.agent)}</span></td>
      <td><span class="tool-name">${esc(c.tool)}</span></td>
      <td class="preview" title="${esc(c.input_preview)}">${esc(c.input_preview.substring(0,50))}${c.input_preview.length>50?"…":""}</td>
      <td><span class="badge ${badgeClass}">${c.status}</span></td>
      <td class="duration">${fmtDur(c.duration_ms)}</td>
      <td class="summary" title="${esc(display)}">${esc(display.substring(0,80))}${display.length>80?"…":""}</td>
    </tr>`;
  }).join("");
}

setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>"""


def create_dashboard_app() -> FastAPI:
    app = FastAPI(title="Ambrosio Agent View", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _HTML

    @app.get("/api/status")
    async def status() -> dict:
        return {
            "agents": tracker.get_stats(),
            "recent_calls": tracker.get_recent(50),
        }

    return app
