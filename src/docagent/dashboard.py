"""Small dependency-free operations dashboard served by the API."""

# ruff: noqa: E501

from __future__ import annotations

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DocAgent dashboard</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 24px; background: #f5f7fb; color: #172033; }
    main { max-width: 1100px; margin: auto; }
    header { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
    h1 { margin: 0; } h2 { font-size: 1.05rem; margin: 0 0 12px; }
    .muted { color: #5c667a; } .card { background: white; border: 1px solid #dce2ed; border-radius: 12px; padding: 18px; margin-top: 18px; box-shadow: 0 2px 8px #17203312; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
    .metric { background: #eef3ff; border-radius: 9px; padding: 12px; } .metric strong { display: block; font-size: 1.6rem; }
    input, button { font: inherit; padding: 9px 11px; border: 1px solid #b9c3d4; border-radius: 7px; }
    button { cursor: pointer; background: #2857d9; color: white; border-color: #2857d9; }
    input { min-width: 260px; background: white; color: #172033; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .table-wrap { overflow-x: auto; } table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #e2e6ef; white-space: nowrap; }
    .status { font-weight: 700; } .status-succeeded { color: #147a43; } .status-failed, .status-blocked { color: #b42318; } .status-running { color: #9a6700; }
    #message { min-height: 1.4em; margin-top: 10px; }
    @media (prefers-color-scheme: dark) { body { background: #111722; color: #e8edf7; } .muted { color: #aeb8ca; } .card { background: #1a2230; border-color: #303b4f; } .metric { background: #202e4d; } input { background: #111722; color: #e8edf7; border-color: #53617a; } }
  </style>
</head>
<body>
<main>
  <header><div><h1>DocAgent dashboard</h1><div class="muted">Live operational view. Refreshes every 10 seconds.</div></div><button id="refresh">Refresh now</button></header>
  <section class="card"><h2>Admin access</h2><div class="toolbar"><input id="token" type="password" autocomplete="off" placeholder="DOCAGENT_ADMIN_TOKEN"><span class="muted">The token stays in this page and is not saved.</span></div><div id="message" role="status"></div></section>
  <section class="card"><h2>Run counts</h2><div id="metrics" class="metrics"><div class="muted">Loading...</div></div></section>
  <section class="card"><h2>Recent runs</h2><div class="table-wrap"><table><thead><tr><th>Repository</th><th>Event</th><th>Commit</th><th>Status</th><th>Delivery</th></tr></thead><tbody id="runs"><tr><td colspan="5" class="muted">Enter the admin token to load runs.</td></tr></tbody></table></div></section>
</main>
<script>
const token = document.getElementById('token');
const message = document.getElementById('message');
const metrics = document.getElementById('metrics');
const runs = document.getElementById('runs');
function setMessage(text, error = false) { message.textContent = text; message.style.color = error ? '#b42318' : ''; }
async function loadMetrics() {
  try {
    const response = await fetch('/metrics');
    const values = await response.json();
    metrics.replaceChildren(...Object.entries(values).map(([key, value]) => { const card = document.createElement('div'); card.className = 'metric'; const strong = document.createElement('strong'); strong.textContent = value; const label = document.createElement('span'); label.textContent = key.replace('docagent_runs_', ''); card.append(strong, label); return card; }));
  } catch (_) { metrics.textContent = 'Metrics unavailable'; }
}
async function loadRuns() {
  if (!token.value.trim()) return;
  try {
    const response = await fetch('/admin/runs?limit=50', { headers: { 'X-DocAgent-Admin-Token': token.value.trim() } });
    if (!response.ok) throw new Error(response.status === 401 ? 'Invalid admin token' : 'Runs unavailable');
    const values = await response.json();
    runs.replaceChildren(...values.map(run => { const row = document.createElement('tr'); for (const value of [run.repo, run.event_type, run.head_sha.slice(0, 12), run.status, run.delivery_id]) { const cell = document.createElement('td'); cell.textContent = value; if (value === run.status) cell.className = 'status status-' + value; row.appendChild(cell); } return row; }));
    setMessage('Runs updated.');
  } catch (error) { setMessage(error.message, true); }
}
async function refresh() { await loadMetrics(); await loadRuns(); }
document.getElementById('refresh').addEventListener('click', refresh);
token.addEventListener('change', loadRuns);
refresh(); setInterval(refresh, 10000);
</script>
</body>
</html>"""
