async function fetchStats() {
  const r = await fetch('/stats');
  return r.json();
}

function renderPrices(prices) {
  const tbody = document.querySelector('#prices-table tbody');
  tbody.innerHTML = Object.entries(prices)
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join('');
}

async function tick() {
  const s = await fetchStats();
  document.getElementById('total-pnl').textContent = Number(s.total_pnl || 0).toFixed(4);
  document.getElementById('active-symbols').textContent = Object.keys(s.prices || {}).length;
  renderPrices(s.prices || {});
}

setInterval(tick, 1000);
tick();


