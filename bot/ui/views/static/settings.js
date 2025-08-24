async function loadSettings() {
  const r = await fetch('/settings');
  const s = await r.json();
  document.getElementById('USE_TESTNET').value = String(s.USE_TESTNET);
  document.getElementById('DRY_RUN').value = String(s.DRY_RUN);
  document.getElementById('LEVERAGE').value = s.LEVERAGE;
  document.getElementById('UNIVERSE_SIZE').value = s.UNIVERSE_SIZE;
  document.getElementById('MAX_POSITIONS').value = s.MAX_POSITIONS;
  const sel = document.getElementById('STRATEGIES');
  sel.innerHTML = '';
  const enabled = (s.STRATEGIES || '').split(',').map(x => x.trim()).filter(Boolean);
  s.available_strategies.forEach(id => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = id;
    if (enabled.includes(id)) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function saveSettings(ev) {
  ev.preventDefault();
  const body = {
    USE_TESTNET: document.getElementById('USE_TESTNET').value,
    DRY_RUN: document.getElementById('DRY_RUN').value,
    LEVERAGE: document.getElementById('LEVERAGE').value,
    UNIVERSE_SIZE: document.getElementById('UNIVERSE_SIZE').value,
    MAX_POSITIONS: document.getElementById('MAX_POSITIONS').value,
  };
  const strategiesSel = document.getElementById('STRATEGIES');
  const picked = Array.from(strategiesSel.selectedOptions).map(o => o.value);
  if (picked.length > 0) {
    body.STRATEGIES = picked.join(',');
  } else {
    body.STRATEGIES = 'auto';
  }
  const r = await fetch('/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (r.ok) {
    alert('Saved. Restart the container to apply changes.');
  } else {
    alert('Failed to save');
  }
}

document.getElementById('settings-form').addEventListener('submit', saveSettings);
loadSettings();


