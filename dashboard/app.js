const state = { data: null };

function money(value) {
  const n = Number(value || 0);
  return `$${n.toFixed(2)}`;
}

function pct(value) {
  const n = Number(value || 0);
  return `${(n * 100).toFixed(1)}%`;
}

function shortToken(token) {
  if (!token) return "unknown";
  return `${token.slice(0, 8)}...${token.slice(-6)}`;
}

async function markSignal(id, status) {
  await fetch(`/api/signals/${id}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  await loadDashboard();
}

function renderStats(stats) {
  const items = [
    ["Paper Balance", money(stats.balance)],
    ["Paper Trades", stats.total_trades || 0],
    ["Open Paper", stats.open_positions || 0],
    ["Signals", stats.signals || 0],
    ["Markets", stats.markets || 0],
    ["Forecast Points", stats.forecast_points || 0],
  ];
  document.getElementById("stats").innerHTML = items.map(([label, value]) => `
    <div class="stat">
      <div class="stat-label">${label}</div>
      <div class="stat-value">${value}</div>
    </div>
  `).join("");
}

function renderSignals(signals) {
  const body = document.getElementById("signals-body");
  if (!signals.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty">No signals stored yet.</td></tr>`;
    return;
  }
  body.innerHTML = signals.map(s => {
    const statusClass = s.status || "signal";
    const evClass = Number(s.ev || 0) >= 0 ? "positive" : "negative";
    return `
      <tr>
        <td>
          <div class="market-title">${s.question || s.market_id}</div>
          <div class="market-meta">
            ${s.city_name || ""} ${s.horizon || ""} ${s.date || ""} · ${s.bucket_label || ""}<br>
            Source ${String(s.forecast_src || "").toUpperCase()} · Token ${shortToken(s.yes_token_id)}
          </div>
        </td>
        <td><span class="badge signal">${s.action || "BUY YES"}</span></td>
        <td class="num">${money(s.limit_price).replace("$0.", "$0.")}</td>
        <td class="num">${money(s.amount)}</td>
        <td class="num">${Number(s.shares || 0).toFixed(2)}</td>
        <td class="num ${evClass}">${Number(s.ev || 0).toFixed(2)}</td>
        <td><span class="badge ${statusClass}">${s.status || "signal"}</span></td>
        <td>
          <div class="actions">
            ${s.event_url ? `<a class="link-btn" href="${s.event_url}" target="_blank" rel="noreferrer">Open</a>` : ""}
            <button onclick="markSignal(${s.id}, 'bought')">Bought</button>
            <button onclick="markSignal(${s.id}, 'skipped')">Skipped</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

function renderPositions(positions) {
  const el = document.getElementById("positions-list");
  if (!positions.length) {
    el.innerHTML = `<div class="empty">No open paper positions.</div>`;
    return;
  }
  el.innerHTML = positions.map(p => `
    <div class="card">
      <div class="card-title">${p.city_name} ${p.date}</div>
      <div class="card-meta">
        ${p.question || ""}<br>
        Entry ${money(p.entry_price)} · Amount ${money(p.cost)} · Shares ${Number(p.shares || 0).toFixed(2)}<br>
        EV ${Number(p.ev || 0).toFixed(2)} · ${String(p.forecast_src || "").toUpperCase()}
      </div>
    </div>
  `).join("");
}

function renderEvents(events) {
  const el = document.getElementById("events-list");
  if (!events.length) {
    el.innerHTML = `<div class="empty">No events.</div>`;
    return;
  }
  el.innerHTML = events.map(e => `
    <div class="card">
      <div class="card-title">${e.event_type}</div>
      <div class="card-meta">${e.message}<br>${new Date(e.created_at).toLocaleString()}</div>
    </div>
  `).join("");
}

async function loadDashboard() {
  const res = await fetch("/api/dashboard");
  const data = await res.json();
  state.data = data;
  renderStats(data.stats || {});
  renderSignals(data.signals || []);
  renderPositions(data.open_positions || []);
  renderEvents(data.events || []);
  document.getElementById("last-updated").textContent = new Date().toLocaleTimeString();
}

document.getElementById("refresh-btn").addEventListener("click", loadDashboard);
loadDashboard();
setInterval(loadDashboard, 30000);
