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
  .rb-update-chip { margin-left: 8px; font: 500 11.5px/1 var(--sans); color: var(--red); border: 1px solid currentColor;
    border-radius: 3px; padding: 3px 6px; cursor: pointer; background: transparent; opacity: .85; }
  .rb-update-chip:hover { opacity: 1; }
  .ls-code { font: 12.5px/1.4 ui-monospace, Menlo, Consolas, monospace; background: var(--sheet, rgba(0,0,0,.04)); padding: 4px 8px; border-radius: 3px; }
  .ls-notes pre { white-space: pre-wrap; font: 12.5px/1.5 var(--sans); color: var(--ink-2); margin: 6px 0 0; max-height: 160px; overflow: auto; }
  .ls-h2 { font: 600 12.5px var(--sans); color: var(--ink); margin: 14px 0 2px; }
  .ls-scan-list { list-style: none; margin: 4px 0 6px; padding: 0; max-height: 200px; overflow: auto; font: 12.5px/1.6 var(--sans); }
  .ls-scan-list .m { color: var(--ink-3); }
  .ls-check { display: flex; gap: 6px; align-items: center; }
  .ls-notes summary { font: 12.5px var(--sans); color: var(--ink-2); cursor: pointer; }
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

    // Paper lookup: optional free keys for Semantic Scholar and OpenAlex (app/apikeys.py)
    const keySec = el('section', { class: 'ls-sec', id: 'ls-lookup' },
      el('div', { class: 'ls-h', text: 'Paper lookup' }),
      el('p', { class: 'ls-p', text: 'Related papers, citations and metadata come from Semantic Scholar, with OpenAlex as a backup when it is busy. Both are free. Without a key, Semantic Scholar shares one allowance among everyone, so it sometimes says “rate limited”; a free personal key fixes that. Keys stay on this computer and are sent only to their own service.' }));
    renderKeys(keySec, say);

    // Papers folder (app/papers.py)
    const docSec = el('section', { class: 'ls-sec', id: 'ls-documents' }, el('div', { class: 'ls-h', text: 'Your documents' }));
    renderDocuments(docSec, say);
    const papSec = el('section', { class: 'ls-sec', id: 'ls-papers' }, el('div', { class: 'ls-h', text: 'Papers folder' }));
    renderPapers(papSec, say);

    // Updates (app/updates.py)
    const updSec = el('section', { class: 'ls-sec', id: 'ls-updates' },
      el('div', { class: 'ls-h', text: 'Updates' }));
    renderUpdates(updSec, say);

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
      libSec, agentSec, privSec, qSec, pairSec, profSec, citeSec, modeSec, oaSec, docSec, papSec, keySec, updSec, ioSec, msg, el('div', { class: 'modal-actions' }, done));
    const wrap = el('div', { class: 'modal', id: 'library-settings' }, card);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    document.body.appendChild(wrap);
    open = wrap;
    setTimeout(() => {
      if (focus === 'profiles') profSec.scrollIntoView({ block: 'nearest' });
      if (focus === 'agent') agentSec.scrollIntoView({ block: 'start' });
      if (focus === 'citations' && citeSec) citeSec.scrollIntoView({ block: 'start' });
      if (focus === 'privacy' && privSec) privSec.scrollIntoView({ block: 'start' });
      if (focus === 'lookup') keySec.scrollIntoView({ block: 'start' });
      if (focus === 'updates') updSec.scrollIntoView({ block: 'start' });
      if (focus === 'papers') papSec.scrollIntoView({ block: 'start' });
      (focus === 'profiles' && !prof.pinned ? newName : done).focus({ preventScroll: true });
    }, 0);
  }

  // ---------------------------------------------------------------- your documents (app/layout.py)
  async function renderDocuments(sec, say) {
    let d;
    try { d = await api('/api/documents-folder'); } catch { return; }
    const n = (k, one, many) => `${d[k]} ${d[k] === 1 ? one : many}`;
    sec.append(
      el('div', { class: 'ls-path', id: 'ls-documents-path', text: d.folder }),
      el('p', { class: 'ls-p', text: `Everything you keep, as files you can open in any app: ${n('papers', 'paper', 'papers')} in Papers, ${n('notes', 'note', 'notes')} in Notes, ${n('questions', 'question', 'questions')} in Questions, ${n('highlights', 'highlight', 'highlights')} in Highlights (a folder per paper), and your topics and PDFs. Each file is named after its title and follows it when you rename. You can file notes into your own subfolders of Notes.` }),
      el('div', { class: 'ls-row' }, el('button', { class: 'btn', type: 'button', id: 'ls-documents-open', text: 'Open folder',
        onclick: () => api('/api/documents-folder/reveal', json('POST', {})).catch((e) => say(e.message, true)) })));
  }

  // ---------------------------------------------------------------- papers folder (app/papers.py)
  const fmtMB = (b) => (b / 1e6 < 10 ? (b / 1e6).toFixed(1) : Math.round(b / 1e6)) + ' MB';
  async function renderPapers(sec, say) {
    let f;
    try { f = await api('/api/papers-folder'); } catch { return; }
    const where = el('div', { class: 'ls-path', id: 'ls-papers-path', text: f.folder });
    const count = el('p', { class: 'ls-p' });
    const setCount = (x) => { count.textContent = `${x.pdfs} PDF${x.pdfs === 1 ? '' : 's'}, one file per paper, named after it. Every PDF you add lands here, and the same file is never stored twice.${x.removed ? ` ${x.removed} in “Removed from library”.` : ''}`; };
    setCount(f);
    const open = el('button', { class: 'btn', type: 'button', text: 'Open folder', onclick: () => api('/api/papers-folder/reveal', json('POST', {})).catch((e) => say(e.message, true)) });
    const dest = el('input', { type: 'text', id: 'ls-papers-dest', spellcheck: 'false', value: f.default ? f.suggested : f.folder, 'aria-label': 'Papers folder' });
    const move = el('button', { class: 'btn', type: 'button', text: 'Move here' });
    move.addEventListener('click', async () => {
      const to = dest.value.trim();
      if (!to || to === f.folder) return;
      move.disabled = true;
      try {
        const r = await api('/api/papers-folder', json('PUT', { folder: to }));
        f = r; where.textContent = r.folder; setCount(r);
        say(`Moved ${r.moved} PDF${r.moved === 1 ? '' : 's'} to ${r.folder}.`);
      } catch (e) { say(e.message, true); } finally { move.disabled = false; }
    });
    sec.append(where, count, el('div', { class: 'ls-row' }, open),
      el('label', { class: 'ls-p', for: 'ls-papers-dest', text: f.default ? 'Keep them somewhere you can find them, like your Documents folder or a synced folder:' : 'Move them somewhere else:' }),
      el('div', { class: 'ls-row' }, dest, move));

    // check a folder you pick for papers you already have, or haven't added
    const scanIn = el('input', { type: 'text', id: 'ls-scan-folder', spellcheck: 'false', value: f.downloads, 'aria-label': 'Folder to check' });
    const deep = el('input', { type: 'checkbox', id: 'ls-scan-deep' });
    const scanBtn = el('button', { class: 'btn', type: 'button', text: 'Check' });
    const out = el('div', { class: 'ls-scan-out', id: 'ls-scan-out' });
    scanBtn.addEventListener('click', async () => {
      scanBtn.disabled = true; out.replaceChildren(el('p', { class: 'ls-p', text: 'Reading the PDFs in that folder…' }));
      try { drawScan(out, await api('/api/papers/scan', json('POST', { folder: scanIn.value.trim(), recursive: deep.checked })), say); }
      catch (e) { out.replaceChildren(); say(e.message, true); } finally { scanBtn.disabled = false; }
    });
    sec.append(el('div', { class: 'ls-h2', text: 'Papers already on this computer' }),
      el('p', { class: 'ls-p', text: 'Only when you ask, and only the folder you pick: finds PDFs that are already in your library (the same file) and ones that aren’t yet.' }),
      el('div', { class: 'ls-row' }, scanIn, scanBtn),
      el('label', { class: 'ls-p ls-check', for: 'ls-scan-deep' }, deep, ' Include folders inside it'), out);
  }
  function drawScan(out, r, say) {
    const kids = [];
    if (r.in_library.length) {
      const trash = el('button', { class: 'btn', type: 'button', text: `Move ${r.in_library.length} extra cop${r.in_library.length === 1 ? 'y' : 'ies'} to Trash` });
      trash.addEventListener('click', async () => {
        trash.disabled = true;
        try {
          const t = await api('/api/papers/trash-copies', json('POST', { paths: r.in_library.map((x) => x.path) }));
          say(`Moved ${t.trashed.length} cop${t.trashed.length === 1 ? 'y' : 'ies'} to the Trash${t.refused.length ? `; left ${t.refused.length} that no longer match` : ''}.`);
          trash.textContent = 'Done';
        } catch (e) { trash.disabled = false; say(e.message, true); }
      });
      kids.push(el('div', { class: 'ls-p' }, el('b', { text: `${r.in_library.length} already in your library` }), ' (the same file):'),
        el('ul', { class: 'ls-scan-list' }, r.in_library.slice(0, 50).map((x) => el('li', { title: x.path }, el('span', { text: x.name }), el('span', { class: 'm', text: ` → ${x.title}` })))),
        el('div', { class: 'ls-row' }, trash));
    }
    if (r.new.length) {
      const boxes = r.new.map((x) => el('input', { type: 'checkbox', checked: true, 'data-path': x.path, 'aria-label': x.name }));
      const copy = el('input', { type: 'checkbox', id: 'ls-scan-copy' });
      const add = el('button', { class: 'btn', type: 'button', text: 'Add selected' });
      add.addEventListener('click', async () => {
        const paths = boxes.filter((b) => b.checked).map((b) => b.dataset.path);
        if (!paths.length) return;
        add.disabled = true; add.textContent = `Adding ${paths.length}…`;
        try {
          const a = await api('/api/papers/import-files', json('POST', { paths, mode: copy.checked ? 'copy' : 'move' }));
          say(`Added ${a.added.length}${a.duplicates.length ? `, ${a.duplicates.length} were already in your library` : ''}${a.failed.length ? `, ${a.failed.length} couldn’t be read` : ''}.`);
          add.textContent = 'Done';
          if (window.__rb && window.__rb.refreshGraph) window.__rb.refreshGraph();
        } catch (e) { add.disabled = false; add.textContent = 'Add selected'; say(e.message, true); }
      });
      kids.push(el('div', { class: 'ls-p' }, el('b', { text: `${r.new.length} not in your library` }), ':'),
        el('ul', { class: 'ls-scan-list' }, r.new.slice(0, 200).map((x, i) => el('li', null, el('label', { title: x.path }, boxes[i], ` ${x.name} `, el('span', { class: 'm', text: fmtMB(x.size) }))))),
        el('label', { class: 'ls-p ls-check', for: 'ls-scan-copy' }, copy, ' Leave the originals where they are (otherwise they move into your papers folder, and the old copy goes to the Trash)'),
        el('div', { class: 'ls-row' }, add));
    }
    if (!kids.length) kids.push(el('p', { class: 'ls-p', text: `No PDFs in ${r.folder}.` }));
    out.replaceChildren(...kids);
  }

  // ---------------------------------------------------------------- updates (app/updates.py)
  function updateLine(u) {
    if (!u.enabled) return 'Update checks are off (RB_UPDATE_CHECK=0).';
    if (u.newer) return `research_brain ${u.latest} is available (you have ${u.current}).`;
    if (u.latest) return `You have the newest version, ${u.current}.`;
    return u.source === 'github'
      ? `You have ${u.current}. No release found: the repository is private, so set your lab’s update folder below (or sign in with the gh command).`
      : `You have ${u.current}. Nothing readable at the update source yet.`;
  }
  async function renderUpdates(sec, say) {
    let u;
    try { u = await api('/api/update'); } catch { return; }
    const line = el('p', { class: 'ls-p', text: updateLine(u) });
    const how = el('div', { class: 'ls-upd-how', hidden: !u.newer },
      el('p', { class: 'ls-p', text: 'Quit research brain, then run this in a terminal (it keeps your library as it is):' }),
      el('div', { class: 'ls-row' }, el('code', { class: 'ls-code', text: u.command }),
        el('button', { class: 'btn btn-text', type: 'button', text: 'Copy', onclick: () => navigator.clipboard.writeText(u.command).then(() => say('Copied.')) })));
    const notes = u.notes ? el('details', { class: 'ls-notes' }, el('summary', { text: `What’s new in ${u.latest}` }), el('pre', { text: u.notes })) : null;
    const inp = el('input', { type: 'text', id: 'ls-upd-src', spellcheck: 'false', value: u.source === 'github' ? '' : u.source,
      placeholder: 'Lab update folder (e.g. a shared drive) or an https URL of latest.json', 'aria-label': 'Update source' });
    const save = el('button', { class: 'btn', type: 'button', text: 'Save' });
    const check = el('button', { class: 'btn btn-text', type: 'button', text: 'Check now' });
    const refresh = (nu) => { u = nu; line.textContent = updateLine(u); how.hidden = !u.newer; how.querySelector('code').textContent = u.command; renderUpdateChip(u); };
    save.addEventListener('click', async () => {
      try { refresh(await api('/api/update/source', json('PUT', { source: inp.value.trim() }))); say(inp.value.trim() ? 'Update source saved.' : 'Using GitHub for updates.'); }
      catch (e) { say(e.message, true); }
    });
    check.addEventListener('click', async () => {
      check.disabled = true;
      try { refresh(await api('/api/update?refresh=true')); say(updateLine(u)); } catch (e) { say(e.message, true); } finally { check.disabled = false; }
    });
    sec.append(line, how, notes || '',
      el('label', { class: 'ls-p', for: 'ls-upd-src', text: 'Where your lab publishes releases. The folder only needs to be synced to this computer; no GitHub account required.' }),
      el('div', { class: 'ls-row' }, inp, save, check));
  }
  /** A small "Update" chip beside the logo when a newer release exists; it opens Settings → Updates. */
  function renderUpdateChip(u) {
    const brand = document.querySelector('#topbar .brand');
    let chip = document.getElementById('rb-update-chip');
    if (!brand || !u || !u.newer) { if (chip) chip.remove(); return; }
    if (!chip) {
      chip = el('button', { id: 'rb-update-chip', class: 'rb-update-chip', type: 'button', onclick: () => openSettings('updates') });
      (document.getElementById('rb-profile-tag') || brand).after(chip);
    }
    chip.textContent = `Update ${u.latest}`;
    chip.title = `research_brain ${u.latest} is available (you have ${u.current}). Click for how to update.`;
  }
  setTimeout(() => { api('/api/update').then(renderUpdateChip).catch(() => {}); }, 4000);

  // ---------------------------------------------------------------- paper lookup keys (app/apikeys.py)
  const KEY_HELP = {
    s2: 'Request one on Semantic Scholar’s API page (a short form; the key arrives by email).',
    openalex: 'Recommended: without a key, OpenAlex shares one small daily allowance with everyone on your network, and runs out. A free key (sign up at openalex.org, then Settings → API) gives this app its own.',
  };
  async function renderKeys(sec, say) {
    let st;
    try { st = await api('/api/lookup-keys'); } catch { return; }
    for (const [sid, k] of Object.entries(st)) {
      const inp = el('input', { type: 'password', autocomplete: 'off', spellcheck: 'false', id: `ls-key-${sid}`,
        placeholder: k.set ? `Saved (ends ${k.hint.replace('…', '')})` : `${k.name} API key`, 'aria-label': `${k.name} API key` });
      const save = el('button', { class: 'btn', type: 'button', text: 'Save' });
      const forget = el('button', { class: 'btn btn-text', type: 'button', text: 'Remove', hidden: !k.set || k.from_env });
      save.addEventListener('click', async () => {
        try {
          const out = await api('/api/lookup-keys', json('PUT', { service: sid, key: inp.value.trim() }));
          inp.value = ''; inp.placeholder = out[sid].set ? `Saved (ends ${out[sid].hint.replace('…', '')})` : `${k.name} API key`;
          forget.hidden = !out[sid].set;
          say(out[sid].set ? `${k.name} key saved.` : `${k.name} key removed.`);
        } catch (e) { say(e.message, true); }
      });
      forget.addEventListener('click', async () => {
        try { await api('/api/lookup-keys', json('PUT', { service: sid, key: '' })); inp.placeholder = `${k.name} API key`; forget.hidden = true; say(`${k.name} key removed.`); }
        catch (e) { say(e.message, true); }
      });
      sec.append(el('div', { class: 'ls-key' },
        el('label', { class: 'ls-p', for: `ls-key-${sid}` }, el('b', { text: k.name + ' ' }), KEY_HELP[sid] || '', ' ',
          el('a', { href: k.get_url, target: '_blank', rel: 'noopener noreferrer', text: 'Get a free key' }),
          k.from_env ? el('span', { text: ` (set by the ${k.env} environment variable)` }) : null),
        el('div', { class: 'ls-row' }, inp, save, forget)));
    }
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
