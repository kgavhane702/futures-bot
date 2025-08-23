async function fetchStats() {
  const r = await fetch('/stats');
  return r.json();
}

function renderPositions(positions, prices, pnl, meta, stage) {
  const tbody = document.querySelector('#positions-table tbody');
  const rows = Object.entries(positions).map(([sym, pos]) => {
    const last = prices[sym] ?? '';
    const p = pnl[sym] ?? 0;
    const m = (meta || {})[sym] || {};
    const s = (stage || {})[sym] || 0;
    const conf = m.confidence != null ? Number(m.confidence).toFixed(2) : '';
    return `<tr><td>${sym}</td><td>${pos.side}</td><td>${pos.size}</td><td>${pos.entryPrice ?? ''}</td><td>${last}</td><td>${Number(p).toFixed(4)}</td><td>${m.strategy || ''}</td><td>${conf}</td><td>${s}/3</td></tr>`
  }).join('');
  tbody.innerHTML = rows;
}

async function tick() {
  const s = await fetchStats();
  renderPositions(s.positions || {}, s.prices || {}, s.pnl || {}, s.strategy_meta || {}, s.exit_stage || {});
}

setInterval(tick, 1000);
tick();


