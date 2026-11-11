/* research brain: the pairing page a phone opens from the QR code (served by app/mobile_api.py at /pair).
 * Trades the one-time code in the URL for this device's token (an HttpOnly cookie set by the server). */
(() => {
  'use strict';
  const code = new URLSearchParams(location.search).get('code') || '';
  const go = document.getElementById('go');
  const name = document.getElementById('name');
  const msg = document.getElementById('msg');
  const say = (t, err) => { msg.textContent = t; msg.className = err ? 'err' : ''; };
  // keep the code out of the history and of anything that reads the address bar later
  try { history.replaceState(null, '', '/pair'); } catch (e) { /* ignore */ }
  if (!code) { go.disabled = true; say('This link has no pairing code. Scan the code on your computer again.', true); return; }
  if (/iPhone/.test(navigator.userAgent)) name.placeholder = 'iPhone';
  else if (/Android/.test(navigator.userAgent)) name.placeholder = 'Android phone';
  go.addEventListener('click', async () => {
    go.disabled = true; say('Pairing…');
    try {
      const r = await fetch('/api/pair/claim', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name: name.value.trim() || name.placeholder }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { say(d.detail || `Pairing failed (HTTP ${r.status}).`, true); return; }
      say('Paired. Opening your library…');
      location.replace('/');
    } catch (e) { go.disabled = false; say('Could not reach your computer. Check that research brain is running and try again.', true); }
  });
})();
