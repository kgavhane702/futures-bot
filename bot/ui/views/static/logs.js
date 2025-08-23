async function fetchStats() {
  const r = await fetch('/stats');
  return r.json();
}

async function tick() {
  const s = await fetchStats();
  document.getElementById('logs').textContent = (s.logs || []).join('\n');
}

setInterval(tick, 1000);
tick();


