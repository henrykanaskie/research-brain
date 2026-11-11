/* research brain: the AI agent pill in the top bar.
 * The pill shows the active agent (and model) with a green or red dot. Clicking it opens a menu to
 * switch agent and model right there; the same choices are in Library settings.
 */
(() => {
  const C = window.RB && window.RB.commands;
  const pill = document.getElementById('status');
  if (!pill) return;

  const css = document.createElement('style');
  css.textContent = `
  #status { cursor: pointer; border-radius: 999px; padding: 4px 10px 4px 8px; border: 1px solid transparent; }
  #status:hover, #status[aria-expanded="true"] { border-color: var(--rule); background: var(--sheet); }
  #status .st-model { color: var(--ink-3); margin-left: 4px; }
  .agent-menu { position: fixed; z-index: 60; width: 300px; background: var(--sheet); border: 1px solid var(--rule);
    border-radius: 6px; padding: 6px; box-shadow: 0 12px 32px -12px color-mix(in srgb, var(--ink) 22%, transparent);
    font: 13px/1.35 var(--sans); color: var(--ink); }
  .agent-menu h4 { margin: 8px 8px 4px; font: italic 500 14px var(--display); color: var(--ink-2); }
  .agent-menu button.am-row { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; border: 0;
    background: none; padding: 6px 8px; border-radius: 3px; color: var(--ink); font: inherit; cursor: pointer; }
  .agent-menu button.am-row:hover, .agent-menu button.am-row:focus-visible { background: var(--ground); outline: none; }
  .agent-menu button.am-row[disabled] { color: var(--ink-3); cursor: default; }
  .agent-menu button.am-row[disabled]:hover { background: none; }
  .agent-menu .am-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--red); }
  .agent-menu .am-dot.on { background: var(--moss); }
  .agent-menu .am-check { margin-left: auto; color: var(--ink-2); }
  .agent-menu .am-sub { color: var(--ink-3); font-size: 12px; }
  .agent-menu .am-sep { border-top: 1px solid var(--rule); margin: 6px 4px; }
  .agent-menu .am-other { display: flex; gap: 6px; padding: 4px 8px 6px; }
  .agent-menu .am-other input { flex: 1; height: 28px; border: 1px solid var(--rule); border-radius: 2px; background: transparent;
    color: var(--ink); padding: 0 8px; font: inherit; }
  `;
  document.head.appendChild(css);

  let menu = null, data = null;
  pill.setAttribute('role', 'button');
  pill.setAttribute('aria-haspopup', 'menu');
  pill.setAttribute('aria-expanded', 'false');

  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v; else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null) n.append(c);
    return n;
  };
  const api = async (method, path, body) => {
    const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    return d;
  };
  const toast = (m, k) => (window.__rb && window.__rb.toast ? window.__rb.toast(m, k) : null);

  async function choose(id, model) {
    try {
      await api('PUT', '/api/agents/active', model === undefined ? { id } : { id, model });
      close();
      fetchAgents().catch(() => {});
      if (window.__rb && window.__rb.loadStatus) await window.__rb.loadStatus();
    } catch (e) { toast(`Couldn't switch: ${e.message}`, 'error'); }
  }

  function render() {
    const act = data.agents.find((a) => a.id === data.active);
    const kids = [el('h4', { text: 'AI agent' })];
    for (const a of data.agents) {
      const det = a.detection || {};
      const ok = det.available && det.signed_in !== false;
      const ver = (String(det.version || '').match(/\d+(?:\.\d+)+/) || [''])[0];
      const sub = !det.available ? 'not found' : det.signed_in === false ? 'signed out' : ver;
      kids.push(el('button', { class: 'am-row', type: 'button', role: 'menuitemradio', 'aria-checked': String(a.id === data.active),
        disabled: !det.available, title: det.note || a.cost_note || '', onclick: () => choose(a.id) },
        el('span', { class: 'am-dot' + (ok ? ' on' : '') }), el('span', { text: a.name }),
        sub ? el('span', { class: 'am-sub', text: sub }) : null,
        a.id === data.active ? el('span', { class: 'am-check', text: '✓' }) : null));
    }
    if (act && (act.detection || {}).available) {
      kids.push(el('div', { class: 'am-sep' }), el('h4', { text: `Model for ${act.name}` }));
      const cur = act.model || '';
      const opts = [''].concat(act.models || []);
      if (cur && !opts.includes(cur)) opts.push(cur);
      for (const m of opts) {
        kids.push(el('button', { class: 'am-row', type: 'button', role: 'menuitemradio', 'aria-checked': String(m === cur),
          onclick: () => choose(act.id, m) },
          el('span', { text: m || 'Default' }), m === cur ? el('span', { class: 'am-check', text: '✓' }) : null));
      }
      if (act.kind !== 'local-server') {
        const inp = el('input', { type: 'text', placeholder: 'Other model name', 'aria-label': 'Other model name', maxlength: '120' });
        inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && inp.value.trim()) choose(act.id, inp.value.trim()); });
        kids.push(el('div', { class: 'am-other' }, inp));
      }
    }
    kids.push(el('div', { class: 'am-sep' }), el('button', { class: 'am-row', type: 'button', text: 'Library settings…',
      onclick: () => { close(); if (C && C.run) C.run('library.settings'); } }));
    menu.replaceChildren(...kids);
  }

  // detection runs each CLI's --version, which takes a few seconds; fetch it once in the background so
  // the menu opens instantly, and refresh it whenever the menu is opened
  let pending = null;
  const fetchAgents = () => (pending = pending || api('GET', '/api/agents').then((d) => { data = d; return d; }).finally(() => { pending = null; }));
  setTimeout(() => fetchAgents().catch(() => {}), 1500);

  async function open() {
    if (menu) return close();
    menu = el('div', { class: 'agent-menu', role: 'menu' }, el('div', { class: 'am-sub', text: 'Checking agents…', style: 'padding:8px' }));
    if (data) setTimeout(() => menu && render(), 0);
    document.body.append(menu);
    const r = pill.getBoundingClientRect();
    menu.style.top = `${r.bottom + 6}px`;
    menu.style.left = `${Math.max(8, Math.min(r.right - 300, window.innerWidth - 308))}px`;
    pill.setAttribute('aria-expanded', 'true');
    try { await fetchAgents(); if (menu) render(); }
    catch (e) { if (menu) menu.replaceChildren(el('div', { class: 'am-sub', text: `Couldn't list agents: ${e.message}`, style: 'padding:8px' })); }
  }
  function close() {
    if (!menu) return;
    menu.remove(); menu = null;
    pill.setAttribute('aria-expanded', 'false');
  }
  pill.addEventListener('click', open);
  pill.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
  document.addEventListener('mousedown', (e) => { if (menu && !menu.contains(e.target) && !pill.contains(e.target)) close(); });
  if (C && C.layer) C.layer({ id: 'agent-menu', order: 90, isOpen: () => !!menu, close });
  if (C && C.register) {
    C.register({ id: 'agent.menu', title: 'Switch AI agent or model', group: 'General',
      description: 'Opens the agent menu in the top bar to pick an agent and its model.',
      aliases: ['model', 'agent', 'claude', 'codex', 'gemini', 'ollama'], run: () => open() });
  }
})();
