async function fetchStats() {
  const r = await fetch('/stats');
  return r.json();
}

function renderThreads(threads) {
  const el = document.getElementById('threads');
  if (!el) return;
  const rows = Object.entries(threads || {}).map(([name, info]) => {
    const ts = info.ts ? new Date(info.ts * 1000).toLocaleTimeString() : '';
    return `<div class="thread"><span class="name">${name}</span> <span class="status">${info.status || 'n/a'}</span> <span class="time">${ts}</span></div>`
  }).join('');
  el.innerHTML = rows || '<div class="thread">No threads</div>';
}

async function tickStatus() {
  const s = await fetchStats();
  renderThreads(s.threads || {});
}

setInterval(tickStatus, 1000);
tickStatus();


