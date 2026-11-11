/* research_brain — Library settings: profile, profiles, open-access PDFs, export/import of the whole
 * library, and keeping UI state in the library (vault/state/ui.json) instead of only in this browser.
 *
 * Loaded after app.js. Registers commands (General / Library groups); adds "Library settings" to the
 * View menu and a small profile label next to the brand when the profile isn't "default".
 * Endpoints: /api/library, /api/profiles(/switch), /api/settings, /api/ui-state, /api/vault/export,
 * /api/vault/import, /api/library/move-legacy, /api/agents, /api/agents/active (see API.md).
 */
(() => {
  'use strict';
  const rb = () => window.__rb || null;

  const CSS = `
  .ls-card { width: min(560px, 100%); max-height: calc(100vh - 48px); overflow: auto; }
  .ls-card .modal-title { font-family: var(--display, var(--serif)); }
  .ls-sec { border-top: 1px solid var(--rule); margin-top: 16px; padding-top: 14px; }
  .ls-sec:first-of-type { border-top: 0; margin-top: 8px; padding-top: 0; }
  .ls-h { font: 600 12px/1.4 var(--sans); color: var(--ink); letter-spacing: .01em; margin-bottom: 6px; }
  .ls-p { font: 12.5px/1.55 var(--sans); color: var(--ink-2); margin: 4px 0 8px; }
  .ls-path { font: 11.5px/1.5 var(--mono); color: var(--ink); background: var(--ground); padding: 6px 9px;
    border-radius: 3px; overflow-wrap: anywhere; user-select: all; }
  .ls-row { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
  .ls-row input[type=text], .ls-row input[type=email] { flex: 1; min-width: 160px; height: 30px; padding: 0 9px;
    border: 1px solid var(--rule); border-radius: 3px; background: var(--sheet); color: var(--ink); font: 13px var(--sans); }
  .ls-row input:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: 1px; }
  .ls-check { display: flex; gap: 8px; align-items: flex-start; font: 13px/1.45 var(--sans); color: var(--ink); cursor: pointer; margin-top: 6px; }
  .ls-check input[type=radio] { flex: none; }
  .ls-check input { accent-color: var(--ink); margin: 2px 0 0; }
  .ls-profiles { list-style: none; margin: 4px 0 0; padding: 0; }
  .ls-profiles li { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--rule); font: 13px var(--sans); }
  .ls-profiles li:last-child { border-bottom: 0; }
  .ls-profiles .nm { color: var(--ink); font-weight: 500; }
  .ls-profiles .n { color: var(--ink-3); font-size: 12px; }
  .ls-profiles .cur { color: var(--red); font-size: 11.5px; }
  .ls-profiles .grow { flex: 1; }
  .ls-msg { font: 12.5px/1.5 var(--sans); color: var(--ink-2); margin-top: 8px; }
  .ls-usage { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; align-items: end; height: 76px; margin: 10px 0 4px; }
  .ls-usage .b { display: flex; flex-direction: column; justify-content: flex-end; align-items: stretch; height: 100%; gap: 3px; }
  .ls-usage .bar { background: var(--ink); min-height: 1px; border-radius: 1px 1px 0 0; }
  .ls-usage .bar.zero { background: var(--rule); }
  .ls-usage .d { font: 10.5px/1 var(--sans); color: var(--ink-3); text-align: center; font-variant-numeric: tabular-nums; }
  .ls-usage-sum { font: 12.5px/1.5 var(--sans); color: var(--ink); font-variant-numeric: tabular-nums; }
  .ls-usage-sum .m { color: var(--ink-2); }
  .ls-msg.err { color: var(--red); }
  .rb-profile-tag { margin-left: 8px; font: 500 11.5px/1 var(--sans); color: var(--ink-2); border: 1px solid var(--rule);
    border-radius: 3px; padding: 3px 6px; cursor: pointer; background: transparent; }
  .rb-profile-tag:hover { color: var(--ink); }
  .ls-agents { list-style: none; margin: 6px 0 0; padding: 0; }
  .ls-agents li { padding: 8px 0; border-bottom: 1px solid var(--rule); font: 13px/1.45 var(--sans); }
  .ls-agents li:last-child { border-bottom: 0; }
  .ls-agents .top { display: flex; align-items: center; gap: 8px; }
  .ls-agents input[type=radio] { accent-color: var(--ink); margin: 0; }
  .ls-agents .nm { color: var(--ink); font-weight: 500; }
  .ls-agents .st { font-size: 11.5px; }
  .ls-agents .st.ok { color: var(--moss); }
  .ls-agents .st.warn { color: var(--ochre); }
  .ls-agents .st.no { color: var(--ink-3); }
  .ls-agents .sub { color: var(--ink-2); font-size: 12px; margin: 2px 0 0 22px; }
  .ls-agents .sub a { color: var(--machine); }
  .ls-agents .row { margin: 6px 0 0 22px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .ls-agents select, .ls-agents input[type=text] { height: 28px; padding: 0 8px; border: 1px solid var(--rule); border-radius: 3px;
    background: var(--sheet); color: var(--ink); font: 12.5px var(--sans); min-width: 140px; }
  .ls-agents li.off .nm { color: var(--ink-2); }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, '');
      else if (v !== false && v != null) e.setAttribute(k, v);
    }
    for (const c of kids) if (c != null) e.append(c);
    return e;
  }
  function toast(msg, kind = 'info') {
    const t = rb() && rb().toast;
    if (t) return t(msg, kind);
    const box = document.getElementById('toasts');
    if (box) { const n = el('div', { class: `toast ${kind}` }, el('span', { class: 't-msg', text: msg })); box.appendChild(n); setTimeout(() => n.remove(), 5000); }
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    let data = null; try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) { const e = new Error((data && data.detail) || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return data;
  }
  const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  let LIB = null;

  // ---------------------------------------------------------------- profile label
  function renderTag() {
    const brand = document.querySelector('#topbar .brand');
    if (!brand || !LIB) return;
    let tag = document.getElementById('rb-profile-tag');
    const show = LIB.profile && LIB.profile !== 'default';
    if (!show) { if (tag) tag.remove(); document.title = 'research_brain'; return; }
    if (!tag) {
      tag = el('button', { id: 'rb-profile-tag', class: 'rb-profile-tag', type: 'button', title: 'Library profile. Click to switch.', onclick: () => openSettings() });
      brand.after(tag);
    }
    tag.textContent = LIB.profile;
    document.title = `research_brain · ${LIB.profile}`;
  }

  // ---------------------------------------------------------------- restart after a switch
  async function waitForRestart(msgEl) {
    const t0 = Date.now();
    await new Promise((r) => setTimeout(r, 1200));
    while (Date.now() - t0 < 60000) {
      try { const r = await fetch('/api/library', { cache: 'no-store' }); if (r.ok) { location.reload(); return; } } catch { /* restarting */ }
      await new Promise((r) => setTimeout(r, 500));
    }
    if (msgEl) { msgEl.textContent = 'research_brain didn’t come back. Start it again from the terminal.'; msgEl.classList.add('err'); }
  }

  // ---------------------------------------------------------------- dialog
  let open = null;
  function close() { if (open) { open.remove(); open = null; } }

  async function openSettings(focus) {
    close();
    let lib, prof, set;
    try {
      [lib, prof, set] = await Promise.all([api('/api/library'), api('/api/profiles'), api('/api/settings')]);
    } catch (e) { toast(`Couldn’t load settings. ${e.message}`, 'error'); return; }
    LIB = lib; renderTag();
    const msg = el('div', { class: 'ls-msg', role: 'status' });
    const say = (t, err) => { msg.textContent = t; msg.classList.toggle('err', !!err); };

    // Library
    const libSec = el('section', { class: 'ls-sec' },
      el('div', { class: 'ls-h', text: lib.pinned ? 'Library (fixed folder)' : `Library: ${lib.profile}` }),
      el('div', { class: 'ls-path', text: lib.vault }),
      el('p', { class: 'ls-p', text: `Saved automatically on this computer. research_brain ${lib.version}, library format ${lib.schema ?? '?'}. Your computer account is your login: anyone who can use this account can open this library.` }));
    if (lib.legacy) {
      const mv = el('button', { class: 'btn', type: 'button', text: lib.move_legacy_pending ? 'Move scheduled' : 'Move it on next start' });
      mv.disabled = !!lib.move_legacy_pending;
      mv.addEventListener('click', async () => {
        try {
          const r = await api('/api/library/move-legacy', { method: 'POST' });
          mv.disabled = true;
          if (r.restarting) { say('Moving your library and restarting…'); waitForRestart(msg); }
          else say(`Scheduled. Restart research_brain to move it, or stop it and run: ${r.command}`);
        } catch (e) { say(e.message, true); }
      });
      libSec.append(el('p', { class: 'ls-p', text: 'This library is inside the app folder, where an update or a re-download could lose it. Move it to your data folder (it is copied and checked first; the old folder is kept, renamed).' }), el('div', { class: 'ls-row' }, mv));
    }

    // Profiles
    const list = el('ul', { class: 'ls-profiles' });
    for (const p of prof.profiles) {
      const btn = el('button', { class: 'btn', type: 'button', text: p.current ? 'Open' : 'Switch' });
      btn.disabled = p.current || prof.pinned;
      btn.addEventListener('click', () => switchTo(p.name));
      list.append(el('li', null,
        el('span', { class: 'nm', text: p.name }),
        el('span', { class: 'n', text: `${p.notes} ${p.notes === 1 ? 'note' : 'notes'}` }),
        p.current ? el('span', { class: 'cur', text: 'open now' }) : null,
        el('span', { class: 'grow' }), p.current ? null : btn));
    }
    const newName = el('input', { type: 'text', placeholder: 'new profile name, e.g. thesis', spellcheck: 'false', 'aria-label': 'New profile name' });
    const create = el('button', { class: 'btn', type: 'button', text: 'Create' });
    create.addEventListener('click', async () => {
      const name = newName.value.trim().toLowerCase();
      if (!name) { newName.focus(); return; }
      try { await api('/api/profiles', json('POST', { name })); say(`Created “${name}”.`); openSettings('profiles'); }
      catch (e) { say(e.message, true); }
    });
    async function switchTo(name) {
      try {
        const r = await api('/api/profiles/switch', json('POST', { name }));
        if (r.restarting) { say(`Opening “${name}”…`); waitForRestart(msg); }
        else if (r.restart_required) say(`“${name}” opens the next time research_brain starts. Restart it now to switch.`);
      } catch (e) { say(e.message, true); }
    }
    const profSec = el('section', { class: 'ls-sec', id: 'ls-profiles' },
      el('div', { class: 'ls-h', text: 'Profiles' }),
      el('p', { class: 'ls-p', text: prof.pinned
        ? 'This server was started with a fixed library folder, so profiles can’t be switched here.'
        : 'Separate libraries on this computer, for example one per project or per person on a shared machine.' }),
      prof.pinned ? null : list, prof.pinned ? null : el('div', { class: 'ls-row' }, newName, create));

    // Open-access PDFs
    const auto = el('input', { type: 'checkbox' }); auto.checked = !!set.auto_open_access;
    auto.addEventListener('change', async () => {
      try { await api('/api/settings', json('PUT', { auto_open_access: auto.checked })); say(auto.checked ? 'Free PDFs will be downloaded when you add papers.' : 'Free PDFs won’t be downloaded automatically.'); }
      catch (e) { say(e.message, true); }
    });
    const email = el('input', { type: 'email', value: set.unpaywall_email || '', placeholder: 'you@university.edu', spellcheck: 'false', 'aria-label': 'Contact email for Unpaywall' });
    const saveOA = el('button', { class: 'btn', type: 'button', text: 'Save' });
    saveOA.addEventListener('click', async () => {
      try { await api('/api/settings', json('PUT', { unpaywall_email: email.value.trim() })); say(email.value.trim() ? 'Saved. Unpaywall will be searched by DOI.' : 'Saved. Unpaywall won’t be searched.'); }
      catch (e) { say(e.message, true); }
    });
    const oaSec = el('section', { class: 'ls-sec' },
      el('div', { class: 'ls-h', text: 'Open-access PDFs' }),
      el('label', { class: 'ls-check' }, auto, el('span', { text: 'When I add a paper by DOI, arXiv id or title, download a free legal copy if one exists (arXiv, Semantic Scholar, Unpaywall).' })),
      el('p', { class: 'ls-p', text: 'Unpaywall asks for a contact email with each lookup. It is sent only to Unpaywall. For paywalled papers, use “Save PDF to research_brain” in the browser extension while signed in to the publisher.' }),
      el('div', { class: 'ls-row' }, email, saveOA));

    // Saving from publisher pages (the browser extension's primary button)
    const modeSec = el('section', { class: 'ls-sec', id: 'ls-save-mode' },
      el('div', { class: 'ls-h', text: 'When saving from a publisher page' }));
    for (const [val, label] of [['link', 'Link + text (default). Keeps the article’s link, full text and metadata. Search, citations and the AI features all work; you can get the PDF later with one click.'],
      ['pdf', 'Link + text + PDF. Also saves the PDF file, downloaded in your browser with your own access.']]) {
      const r = el('input', { type: 'radio', name: 'ls-save-mode', value: val });
      r.checked = (set.save_mode || 'link') === val;
      r.addEventListener('change', async () => {
        if (!r.checked) return;
        try { await api('/api/settings', json('PUT', { save_mode: val })); say(val === 'pdf' ? 'The extension will save the PDF too.' : 'The extension will save the link and text, not the PDF.'); }
        catch (e) { say(e.message, true); }
      });
      modeSec.append(el('label', { class: 'ls-check' }, r, el('span', { text: label })));
    }
    modeSec.append(el('p', { class: 'ls-p', text: 'The research_brain browser extension reads the page you are looking at, in your own browser session, one paper per click. research_brain never uses your logins.' }));

    // Export / import
    const withPdfs = el('input', { type: 'checkbox' }); withPdfs.checked = true;
    const exp = el('button', { class: 'btn', type: 'button', text: 'Export library (.zip)' });
    exp.addEventListener('click', () => exportZip(withPdfs.checked));
    const imp = el('button', { class: 'btn', type: 'button', text: 'Import a library zip…' });
    imp.addEventListener('click', () => importZip(say));
    const ioSec = el('section', { class: 'ls-sec' },
      el('div', { class: 'ls-h', text: 'Backup and moving computers' }),
      el('p', { class: 'ls-p', text: 'The zip holds your notes, PDFs, review history, memory and topics. Import it on another computer; it becomes a new profile, so nothing is overwritten. It is a full backup, so private notes are in it too: keep it as private as the library.' }),
      el('label', { class: 'ls-check' }, withPdfs, el('span', { text: 'Include PDFs' })),
      el('div', { class: 'ls-row' }, exp, imp));

    // AI agent
    const agentSec = el('section', { class: 'ls-sec', id: 'ls-agent' },
      el('div', { class: 'ls-h', text: 'AI agent' }),
      el('p', { class: 'ls-p', text: 'research brain uses the AI tool you already have on this computer. It never needs an API key.' }));
    const agentList = el('ul', { class: 'ls-agents' });
    agentSec.append(agentList, el('p', { class: 'ls-p', text: 'Whatever you pick gets no tools: it can’t run commands, open files or browse. It only reads the text research_brain sends it and writes an answer.' }));
    renderAgents(agentList, say);
    renderUsage(agentSec, say);

    // Citations (static/cite.js): default style, copy format, your .csl styles, auto-updating .bib files
    const citeSec = window.__rbCite ? window.__rbCite.settingsSection(say) : null;
    // Privacy (static/privacy.js): private-by-default types, local-AI exception, what was sent to AI
    const privSec = window.__rbPrivacy ? window.__rbPrivacy.settingsSection(say) : null;
    // Open questions (static/questions.js): matching threshold and the daily cap
    const qSec = window.__rbQuestions ? window.__rbQuestions.settingsSection(say) : null;
    // Phones and tablets (static/mobile.js): pair a phone with a QR code, paired devices, revoke
    const pairSec = window.__rbPair ? window.__rbPair.settingsSection(say) : null;

    const done = el('button', { class: 'btn btn-primary', type: 'button', text: 'Done', onclick: close });
    const card = el('div', { class: 'modal-card ls-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'ls-title' },
      el('div', { class: 'modal-title', id: 'ls-title', text: 'Library settings' }),
      libSec, agentSec, privSec, qSec, pairSec, profSec, citeSec, modeSec, oaSec, ioSec, msg, el('div', { class: 'modal-actions' }, done));
    const wrap = el('div', { class: 'modal', id: 'library-settings' }, card);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    document.body.appendChild(wrap);
    open = wrap;
    setTimeout(() => {
      if (focus === 'profiles') profSec.scrollIntoView({ block: 'nearest' });
      if (focus === 'agent') agentSec.scrollIntoView({ block: 'start' });
      if (focus === 'citations' && citeSec) citeSec.scrollIntoView({ block: 'start' });
      if (focus === 'privacy' && privSec) privSec.scrollIntoView({ block: 'start' });
      (focus === 'profiles' && !prof.pinned ? newName : done).focus({ preventScroll: true });
    }, 0);
  }

  // ---------------------------------------------------------------- AI usage (app/budget.py)
  // How much each request may send, so research brain doesn't eat your plan's limits. Nothing runs the AI
  // by itself: every request starts from something you did.
  const USAGE_LEVELS = [
    ['light', 'Light', 'The smallest requests. Answers lean on the best passages, chat remembers less.'],
    ['balanced', 'Balanced', 'Recommended. A paper is sent whole only when it is short.'],
    ['thorough', 'Thorough', 'The most context. Uses your plan’s limits fastest.'],
  ];
  const fmtTok = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
  const PURPOSE = { chat: 'Chat', critique: 'Critiques', research: 'Research', 'research tool': 'Research lookups',
    explain: 'Link explanations', 'review check': 'Review checks', 'card-extraction': 'Idea cards', 'question summary': 'Question summaries' };
  function renderUsage(sec, say) {
    const box = el('div', {});
    sec.append(el('div', { class: 'ls-h', text: 'AI usage', style: 'margin-top:14px' }),
      el('p', { class: 'ls-p', text: 'Every request uses your AI plan’s limits. Choose how much research brain may send each time. The AI only runs when you ask for something.' }), box);
    let cur = null;
    async function save(patch, msg) {
      try { cur = await api('/api/ai-usage', json('PUT', patch)); paint(); say(msg); }
      catch (e) { say(e.message, true); }
    }
    function paint() {
      box.replaceChildren();
      for (const [id, name, desc] of USAGE_LEVELS) {
        const r = el('input', { type: 'radio', name: 'ls-ai-usage', value: id, id: `ls-ai-usage-${id}` });
        r.checked = cur.level === id;
        r.addEventListener('change', () => { if (r.checked) save({ level: id }, `AI usage: ${name}.`); });
        box.append(el('label', { class: 'ls-check', for: `ls-ai-usage-${id}` }, r,
          el('span', {}, el('b', { text: name }), document.createTextNode(` · ${desc}`))));
      }
      const lm = el('input', { type: 'checkbox', id: 'ls-light-model' });
      lm.checked = !!cur.light_model_small_jobs;
      lm.addEventListener('change', () => save({ light_model_small_jobs: lm.checked },
        lm.checked ? 'Small jobs use the lighter model.' : 'Small jobs use your chosen model.'));
      box.append(el('label', { class: 'ls-check', for: 'ls-light-model' }, lm,
        el('span', { text: 'Use a lighter model for small jobs: explaining a link, checking a review answer, suggesting idea cards (Claude: Haiku, Gemini: Flash).' })));
      const u = cur.usage || {};
      const days = u.by_day || [];
      const max = Math.max(1, ...days.map((d) => d.tokens));
      const bars = el('div', { class: 'ls-usage', role: 'img', 'aria-label': `About ${fmtTok(u.tokens || 0)} tokens sent in the last 7 days` });
      for (const d of days) {
        const pct = Math.round((d.tokens / max) * 100);
        const wd = new Date(d.day + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short' });
        bars.append(el('div', { class: 'b', title: `${d.day}: about ${fmtTok(d.tokens)} tokens` },
          el('div', { class: 'bar' + (d.tokens ? '' : ' zero'), style: `height:${d.tokens ? `calc((100% - 16px) * ${Math.max(4, pct) / 100})` : '1px'}` }),
          el('div', { class: 'd', text: wd })));
      }
      const top = (u.by_purpose || []).slice(0, 3).map((p) => `${PURPOSE[p.purpose] || p.purpose} ${fmtTok(p.tokens)}`).join(' · ');
      box.append(el('div', { class: 'ls-h', text: 'Last 7 days', style: 'margin-top:12px' }), bars,
        el('div', { class: 'ls-usage-sum' }, document.createTextNode(`About ${fmtTok(u.tokens || 0)} tokens in ${u.requests || 0} request${u.requests === 1 ? '' : 's'}`),
          top ? el('span', { class: 'm', text: ` · ${top}` }) : null),
        el('p', { class: 'ls-p', text: 'An estimate of what research brain sent (characters ÷ 4). Your agent’s replies and its own overhead are not included.' }));
    }
    api('/api/ai-usage').then((d) => { cur = d; paint(); }).catch((e) => box.replaceChildren(el('p', { class: 'ls-p', text: `Couldn’t load AI usage: ${e.message}` })));
  }

  // ---------------------------------------------------------------- AI agent choice
  function agentStatus(a) {
    const d = a.detection || {};
    if (!d.available) return ['no', a.kind === 'local-server' ? 'not running' : 'not found'];
    if (d.signed_in === false) return ['warn', 'installed, not signed in'];
    if (d.signed_in === true) return ['ok', a.kind === 'local-server' ? 'running' : 'installed, signed in'];
    return ['ok', 'installed'];
  }

  async function renderAgents(list, say, fresh) {
    list.replaceChildren(el('li', { class: 'sub', text: 'Checking which AI tools are on this computer…' }));
    let data;
    try { data = await api('/api/agents' + (fresh ? '?fresh=true' : '')); }
    catch (e) { list.replaceChildren(el('li', { class: 'sub', text: `Couldn’t check. ${e.message}` })); return; }
    list.replaceChildren();
    for (const a of data.agents) {
      const d = a.detection || {};
      const [cls, label] = agentStatus(a);
      const radio = el('input', { type: 'radio', name: 'rb-agent', value: a.id, 'aria-label': a.name });
      radio.checked = a.id === data.active;
      const choose = async (extra = {}) => {
        try {
          const r = await api('/api/agents/active', json('PUT', Object.assign({ id: a.id }, extra)));
          const ok = r.agent && r.agent.detection && r.agent.detection.available;
          say(ok ? `research_brain now uses ${a.name}.` : `Saved: ${a.name}. It isn’t ready yet. ${(r.agent.detection || {}).note || ''}`, !ok);
          const app = rb(); if (app && app.loadStatus) app.loadStatus();
          renderAgents(list, say);
        } catch (e) { say(e.message, true); renderAgents(list, say); }
      };
      radio.addEventListener('change', () => { if (radio.checked) choose(); });
      const li = el('li', { class: d.available ? '' : 'off' },
        el('label', { class: 'top' }, radio, el('span', { class: 'nm', text: a.name }),
          el('span', { class: `st ${cls}`, text: label + (d.version ? ` · ${d.version}` : '') })),
        el('div', { class: 'sub', text: a.cost_note || '' }));
      if (d.note) li.append(el('div', { class: 'sub', text: d.note }));
      if (!d.available && a.home) {
        li.append(el('div', { class: 'sub' }, el('a', { href: a.home, target: '_blank', rel: 'noopener noreferrer', text: a.kind === 'local-server' ? 'How to get it' : 'How to install' })));
      }
      if (a.kind === 'local-server') {
        const row = el('div', { class: 'row' });
        if ((a.models || []).length) {
          const sel = el('select', { 'aria-label': `${a.name} model` }, el('option', { value: '', text: `Default (${a.models[0]})` }),
            ...a.models.map((m) => el('option', { value: m, text: m })));
          sel.value = a.models.includes(a.model) ? a.model : '';
          sel.addEventListener('change', () => choose({ model: sel.value }));
          row.append(sel);
        }
        const url = el('input', { type: 'text', value: a.url || '', spellcheck: 'false', 'aria-label': `${a.name} address`, placeholder: 'http://127.0.0.1:…' });
        const save = el('button', { class: 'btn', type: 'button', text: 'Use this address' });
        save.addEventListener('click', () => choose({ url: url.value.trim() }));
        row.append(url, save);
        li.append(row);
      }
      list.append(li);
    }
    const again = el('button', { class: 'btn', type: 'button', text: 'Check again' });
    again.addEventListener('click', () => renderAgents(list, say, true));
    list.append(el('li', null, el('div', { class: 'ls-row' }, again)));
  }

  function exportZip(pdfs) {
    const a = el('a', { href: `/api/vault/export?pdfs=${pdfs ? 'true' : 'false'}`, download: '' });
    document.body.appendChild(a); a.click(); a.remove();
    toast('Preparing your library zip. Your browser will download it in a moment.');
  }

  function importZip(say = (t, err) => toast(t, err ? 'error' : 'info')) {
    const input = el('input', { type: 'file', accept: '.zip,application/zip' });
    input.addEventListener('change', async () => {
      const f = input.files && input.files[0];
      if (!f) return;
      const suggested = (f.name.replace(/\.zip$/i, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)) || 'imported';
      const name = (window.prompt('Name for the imported library (a new profile):', suggested) || '').trim().toLowerCase();
      if (!name) return;
      say(`Importing ${f.name}…`);
      try {
        const r = await api(`/api/vault/import?profile=${encodeURIComponent(name)}`, { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: f });
        say(`Imported ${r.notes} ${r.notes === 1 ? 'note' : 'notes'} into profile “${name}”. Switch to it under Profiles.`);
        if (open) openSettings('profiles');
      } catch (e) { say(`Import failed. ${e.message}`, true); }
    });
    input.click();
  }

  // ---------------------------------------------------------------- UI state kept in the library
  // These localStorage keys (the "rb." prefix is the app's) are mirrored to vault/state/ui.json, so a new
  // browser or a cleared cache gets them back. Restoring needs one reload, because app.js reads them at boot.
  const SYNC = /^(panelW|theme|showSim|showCites|ambient|threshold\..*|graphFilter|graphViews|chatMode|researchFast|tour\.done|cmd\.recent)$/;
  const snapshot = () => {
    const out = {};
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith('rb.') || !SYNC.test(k.slice(3))) continue;
        try { out[k.slice(3)] = JSON.parse(localStorage.getItem(k)); } catch { /* not ours */ }
      }
    } catch { /* storage blocked */ }
    const S = rb() && rb().S;
    if (S && S.selected) out.lastNote = S.selected;
    return out;
  };
  let pushed = null;
  async function push() {
    const now = snapshot();
    if (pushed === null) return;
    const diff = {};
    for (const [k, v] of Object.entries(now)) if (JSON.stringify(v) !== JSON.stringify(pushed[k])) diff[k] = v;
    for (const k of Object.keys(pushed)) if (!(k in now) && k !== 'lastNote') diff[k] = null;
    if (!Object.keys(diff).length) return;
    try { await api('/api/ui-state', json('PUT', { state: diff })); pushed = Object.assign({}, pushed, diff); for (const k in diff) if (diff[k] === null) delete pushed[k]; }
    catch { /* offline: try again next tick */ }
  }
  async function restore() {
    let srv;
    try { srv = await api('/api/ui-state'); } catch { return; }
    const state = (srv && srv.state) || {};
    pushed = state;
    if (srv.restore === false) return;
    let hasLocal = false;
    try { for (let i = 0; i < localStorage.length; i++) if ((localStorage.key(i) || '').startsWith('rb.')) { hasLocal = true; break; } } catch { return; }
    const keys = Object.keys(state).filter((k) => SYNC.test(k));
    let once = false; try { once = sessionStorage.getItem('rb.ui-restored') === '1'; } catch { /* ignore */ }
    if (!hasLocal && keys.length && !once) {  // a fresh browser for this library: bring its settings back
      try {
        for (const k of keys) localStorage.setItem('rb.' + k, JSON.stringify(state[k]));
        sessionStorage.setItem('rb.ui-restored', '1');
        if (state.lastNote && !/note=/.test(location.hash)) sessionStorage.setItem('rb.ui-reopen', String(state.lastNote));
      } catch { return; }
      location.reload();
      return;
    }
    let reopen = null; try { reopen = sessionStorage.getItem('rb.ui-reopen'); sessionStorage.removeItem('rb.ui-reopen'); } catch { /* ignore */ }
    if (reopen && rb() && rb().openNote) setTimeout(() => { try { rb().openNote(reopen, { center: true }); } catch { /* gone */ } }, 600);
  }
  restore();
  setInterval(push, 3000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') push(); });

  // ---------------------------------------------------------------- menu item, label, notices
  const exportBtn = document.getElementById('btn-export');
  if (exportBtn && !document.getElementById('btn-library-settings')) {
    const item = el('button', { id: 'btn-library-settings', class: 'menu-item', type: 'button' }, 'Library settings',
      el('span', { class: 'menu-hint', text: 'AI agent, profiles, citations, open-access PDFs, backup' }));
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      const m = document.getElementById('view-menu'); if (m) { m.hidden = true; const b = document.getElementById('btn-view-menu'); if (b) b.setAttribute('aria-expanded', 'false'); }
      openSettings();
    });
    exportBtn.before(item);
  }
  api('/api/library').then((lib) => {
    LIB = lib; renderTag();
    let seen = false; try { seen = sessionStorage.getItem('rb.legacy-notice') === '1'; sessionStorage.setItem('rb.legacy-notice', '1'); } catch { /* ignore */ }
    if (lib.legacy && !seen) toast('Your library is in the app folder. Open View › Library settings to move it somewhere safe from updates.');
  }).catch(() => { /* offline; status shows it */ });

  // ---------------------------------------------------------------- commands
  const C = window.RB && window.RB.commands;
  if (C) {
    C.register({ id: 'library.settings', title: 'Library settings', group: 'General',
      description: 'Profile, where your library is saved, open-access PDFs, export and import.',
      aliases: ['settings', 'preferences', 'profile', 'unpaywall', 'email', 'storage', 'data folder', 'vault'], run: () => openSettings() });
    C.register({ id: 'agent.choose', title: 'Choose AI agent', group: 'General',
      description: 'Pick which AI tool on this computer research_brain uses: Claude Code, Codex, Gemini, Copilot, Ollama and more. No API key.',
      aliases: ['ai', 'agent', 'model', 'claude', 'codex', 'gemini', 'copilot', 'ollama', 'lm studio', 'llm', 'backend'], run: () => openSettings('agent') });
    C.register({ id: 'library.switchProfile', title: 'Switch profile', group: 'Library',
      description: 'Open another library on this computer, or create one.',
      aliases: ['profile', 'library', 'account', 'user'], run: () => openSettings('profiles') });
    C.register({ id: 'library.exportZip', title: 'Export library as a zip', group: 'Library',
      description: 'Download your whole library (notes, PDFs, review history) to back it up or move computers.',
      aliases: ['backup', 'download', 'move', 'archive'], run: () => exportZip(true) });
    C.register({ id: 'library.importZip', title: 'Import a library zip', group: 'Library',
      description: 'Unpack an exported library into a new profile.',
      aliases: ['restore', 'upload', 'move', 'archive'], run: () => importZip() });
    if (C.layer) C.layer({ id: 'library-settings', order: 100, isOpen: () => !!open, close });
  }

  window.__rbSettings = { open: openSettings, exportZip, importZip, push, snapshot };
})();
