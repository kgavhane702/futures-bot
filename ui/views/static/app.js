async function refresh() {
  const r = await fetch('/stats');
  const s = await r.json();
  document.getElementById('stats').innerHTML = renderStats(s);
  document.getElementById('logs').textContent = (s.logs || []).join('\n');
}

function renderStats(s) {
  const prices = s.prices || {}; const positions = s.positions || {}; const pnl = s.pnl || {}; const total = s.total_pnl || 0;
  const priceRows = Object.entries(prices).map(([k,v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');
  const posRows = Object.entries(positions).map(([k,v]) => `<tr><td>${k}</td><td>${v.side}</td><td>${v.size}</td><td>${v.entryPrice ?? ''}</td></tr>`).join('');
  const pnlRows = Object.entries(pnl).map(([k,v]) => `<tr><td>${k}</td><td>${Number(v).toFixed(4)}</td></tr>`).join('');
  return `
    <h2>PNL Total: ${Number(total).toFixed(4)} USDT</h2>
    <h3>Prices</h3>
    <table><tr><th>Symbol</th><th>Price</th></tr>${priceRows}</table>
    <h3>Positions</h3>
    <table><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Entry</th></tr>${posRows}</table>
    <h3>PNL</h3>
    <table><tr><th>Symbol</th><th>PNL (USDT)</th></tr>${pnlRows}</table>
  `;
}

setInterval(refresh, 1000);
refresh();


