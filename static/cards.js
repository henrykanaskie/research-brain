/* research_brain — idea cards: a paper's claims, equations, measured quantities and methods.
 * - Panel (papers): an "Ideas" section under Fragments. Suggested cards (from the AI, each with a verbatim
 *   quote found in the paper) are quieter until you Confirm, Edit or Reject them; confirmed and edited cards
 *   are your knowledge (red margin rule). Links to the same idea in other papers, and "Disagrees with …"
 *   when a measured value conflicts. Keys while a paper with cards is open: J / K move, Y confirm,
 *   E edit, X reject. "Add card" writes your own; "Confirm all" confirms every suggestion.
 * - Ideas view (G then I, or "Open ideas"): every card in the library; quantities as a sortable table
 *   (value, unit, SI conversion, conditions, paper key, page), filter by text or quantity, export CSV.
 * Server: /api/papers/{id}/cards*, /api/cards* (app/cards_api.py). Depends on window.__rb (app.js).
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.onPanelRender) return;
  const { S, h, api, toast, toastErr, renderMD, truncate } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const enc = encodeURIComponent;
  const C = window.RB && window.RB.commands;

  const KINDS = [['claim', 'Claims'], ['equation', 'Equations'], ['quantity', 'Quantities'], ['method', 'Methods']];
  const KIND1 = { claim: 'Claim', equation: 'Equation', quantity: 'Quantity', method: 'Method' };
  const STATUS = { suggested: 'Suggested', confirmed: 'Confirmed', edited: 'Edited', rejected: 'Rejected' };

  const CSS = `
  .ic-sec .ic-acts { display: flex; gap: 4px; align-items: center; }
  .ic-note { font-size: 12.5px; color: var(--ink-2); margin: 0 0 10px; line-height: 1.5; }
  .ic-kind { font: 500 12px var(--sans); color: var(--ink-3); margin: 14px 0 4px; }
  .ic { position: relative; padding: 8px 8px 8px 12px; margin: 0 0 6px; border-left: 2px solid var(--red); outline: none; }
  .ic.sug { border-left: 2px dashed var(--machine); }
  .ic.sug .ic-text { color: var(--ink-2); }
  .ic.rej { opacity: .5; border-left-color: var(--rule); }
  .ic.on { background: var(--ground); }
  .ic-text { font: 15px/1.5 var(--serif); color: var(--ink); }
  .ic-eq { margin: 4px 0; overflow-x: auto; }
  .ic-si { color: var(--ink-2); font-size: 13.5px; }
  .iv:focus { outline: none; }
  .ic-q { font: italic 13.5px/1.5 var(--serif); color: var(--ink-2); margin-top: 4px; }
  .ic-q a { color: inherit; text-decoration: none; }
  .ic-q a:hover { text-decoration: underline; text-decoration-color: var(--ink-3); }
  .ic-meta { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; font-size: 12px; color: var(--ink-3); margin-top: 4px; }
  .ic-meta .st { color: var(--machine); }
  .ic:not(.sug) .ic-meta .st { color: var(--red); }
  .ic-meta .lock { color: var(--ink-2); }
  .ic-btns { display: flex; gap: 2px; margin-top: 4px; }
  .ic-link { all: unset; cursor: pointer; font-size: 12px; color: var(--moss); }
  .ic-link.bad { color: var(--ochre); font-weight: 500; }
  .ic-link:hover { text-decoration: underline; text-underline-offset: 3px; }
  .ic-form { display: grid; gap: 6px; margin: 6px 0 12px; padding: 10px 12px; border: 1px solid var(--rule); background: var(--sheet); }
  .ic-form label { display: grid; gap: 2px; font-size: 12px; color: var(--ink-2); }
  .ic-form .row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .ic-form textarea { font: 14.5px/1.45 var(--serif); min-height: 44px; }
  .ic-form .frow { display: flex; gap: 6px; align-items: center; }
  .ic-form .chk { display: flex; gap: 6px; align-items: center; grid-template-columns: none; }
  .iv { position: fixed; inset: var(--topbar-h) 0 0 0; z-index: 45; background: var(--sheet); overflow-y: auto; }
  .iv-col { width: min(1240px, 100%); margin: 0 auto; padding: 32px 32px 96px; }
  .iv-head { display: flex; align-items: center; gap: 12px; }
  .iv-head h1 { font: 400 28px/1.15 var(--display); margin: 0; letter-spacing: -.015em; }
  .iv-head .spacer { flex: 1; }
  .iv-lede { color: var(--ink-2); max-width: 75ch; margin: 8px 0 20px; line-height: 1.55; }
  .iv-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 14px; }
  .iv-bar input[type=search], .iv-bar input[list] { height: 28px; min-width: 220px; }
  .iv-bar .seg button.on { color: var(--ink); background: var(--ground); font-weight: 500; }
  .iv table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .iv th { text-align: left; font: 500 12px var(--sans); color: var(--ink-2); border-bottom: 1px solid var(--rule); padding: 6px 8px; cursor: pointer; white-space: nowrap; user-select: none; }
  .iv th.sorted::after { content: attr(data-dir); color: var(--ink-3); margin-left: 4px; }
  .iv td { border-bottom: 1px solid var(--rule); padding: 6px 8px; vertical-align: top; }
  .iv td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
  .iv td.txt { font-family: var(--serif); font-size: 14px; }
  .iv tr.sug td { color: var(--ink-2); }
  .iv tr:hover td { background: var(--ground); }
  .iv .bad { color: var(--ochre); font-weight: 500; white-space: nowrap; }
  .iv .key { font-family: ui-monospace, monospace; font-size: 12px; }
  .iv .open { all: unset; cursor: pointer; }
  .iv .open:hover { text-decoration: underline; text-underline-offset: 3px; }
  .iv-empty { color: var(--ink-2); padding: 32px 0; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // ====================================================================== panel: the Ideas section
  const P = { pid: null, data: null, active: -1, editing: null, adding: false, showRejected: false, job: null, poll: null, box: null };

  const visible = () => (P.data ? P.data.cards.filter((c) => P.showRejected || c.status !== 'rejected') : []);
  const ordered = () => { const v = visible(); return KINDS.flatMap(([k]) => v.filter((c) => c.kind === k)); };
  const activeCard = () => { const o = ordered(); return P.active >= 0 && P.active < o.length ? o[P.active] : null; };

  async function load(pid, keepActive) {
    const prev = activeCard();
    try { P.data = await api('GET', `/api/papers/${enc(pid)}/cards`); }
    catch (e) { P.data = { cards: [], counts: {}, agent: { available: false, why: e.message }, error: e.message }; }
    if (P.pid !== pid) return;
    if (keepActive && prev) P.active = ordered().findIndex((c) => c.id === prev.id);
    if (P.data.job && P.data.job.state === 'running' && !P.poll) watchJob(P.data.job.id, pid);
    paint();
  }

  function paint() {
    const box = P.box;
    if (!box || !box.isConnected) return;
    const d = P.data;
    const sec = h('div', { class: 'section ic-sec', dataset: { sec: 'Ideas' }, id: 'ideas-sec' });
    const n = d ? d.cards.filter((c) => c.status !== 'rejected').length : 0;
    const sugN = d ? (d.counts.suggested || 0) : 0;
    const acts = h('div', { class: 'ic-acts' });
    if (d) {
      const busy = !!P.job;
      const sugBtn = h('button', { type: 'button', class: 'btn btn-sm needs-claude' + (busy ? ' busy' : ''), 'data-act': 'ideas-suggest',
        title: 'The AI reads this paper’s own text (never your notes) and suggests its key claims, equations, measured values and methods, each with a quote from the paper.',
        text: busy ? 'Suggesting' : (n ? 'Suggest more' : 'Suggest ideas'), onclick: (e) => suggest(e.currentTarget) });
      if (!d.agent || !d.agent.available) sugBtn.disabled = true;
      acts.append(sugBtn);
      if (sugN) acts.append(h('button', { type: 'button', class: 'btn btn-sm btn-text', 'data-act': 'ideas-confirm-all', text: `Confirm all (${sugN})`, onclick: confirmAll }));
      acts.append(h('button', { type: 'button', class: 'btn btn-sm btn-text', 'data-act': 'ideas-add', text: 'Add card', onclick: () => { P.adding = true; P.editing = null; paint(); } }));
    }
    sec.append(h('div', { class: 'sec-head' }, h('span', { class: 'sec-title' }, 'Ideas', n ? h('span', { class: 'n', text: String(n) }) : null),
      h('div', { class: 'spacer' }), acts));
    if (!d) { sec.append(h('div', { class: 'empty-list', text: 'Loading ideas…' })); box.replaceChildren(sec); return; }
    if (P.job) sec.append(h('p', { class: 'ic-note', 'aria-live': 'polite', text: P.job.message || 'Suggesting ideas…' }));
    if (d.agent && !d.agent.available && !d.cards.length) {
      sec.append(h('p', { class: 'ic-note', text: `No AI agent is available to suggest ideas (${d.agent.why || 'none found'}). You can still add cards by hand.` }));
    } else if (d.paper && d.paper.private) {
      sec.append(h('p', { class: 'ic-note', text: 'This paper is private: its text isn’t sent to the AI, so add cards by hand.' }));
    } else if (!d.cards.length && !P.job) {
      sec.append(h('p', { class: 'ic-note', text: 'The key claims, equations, measured values and methods of this paper, as small cards you confirm. Suggest them from the paper, or add your own.' }));
    }
    if (P.adding) sec.append(editor(null));
    const vis = ordered();
    for (const [k, label] of KINDS) {
      const cs = vis.filter((c) => c.kind === k);
      if (!cs.length) continue;
      sec.append(h('div', { class: 'ic-kind', text: `${label} (${cs.length})` }));
      for (const c of cs) sec.append(P.editing === c.id ? editor(c) : cardEl(c, vis.indexOf(c)));
    }
    const rej = d.counts.rejected || 0;
    if (rej) sec.append(h('button', { type: 'button', class: 'btn btn-text btn-sm', text: P.showRejected ? 'Hide rejected' : `Show ${rej} rejected`,
      onclick: () => { P.showRejected = !P.showRejected; P.active = -1; paint(); } }));
    box.replaceChildren(sec);
    if (rb.applyClaude) rb.applyClaude(box);
  }

  function cardEl(c, i) {
    const cls = 'ic' + (c.status === 'suggested' ? ' sug' : c.status === 'rejected' ? ' rej' : '') + (i === P.active ? ' on' : '');
    const el = h('div', { class: cls, tabindex: '-1', 'data-card': c.id, 'data-status': c.status, onclick: () => { P.active = i; markActive(); } });
    if (c.kind === 'equation' && c.latex) el.append(h('div', { class: 'ic-eq', html: renderMD(`$$${c.latex}$$`) }));
    const q = c.quantity;
    if (c.kind === 'quantity' && q) {
      // the value as the paper gives it, its SI conversion, then the conditions
      el.append(h('div', { class: 'ic-text ic-qty' }, h('span', { html: qName(q) }), ' = ',
        h('span', { class: 'tnum', text: `${c.value_text || ''}${q.unit ? ' ' + q.unit : ''}` }),
        c.canonical_text && q.canonical_unit !== q.unit ? h('span', { class: 'ic-si tnum', text: `  (${c.canonical_text})` }) : null,
        q.conditions ? h('span', { class: 'ic-si', text: `, ${q.conditions}` }) : null));
      if (c.custom_text) el.append(h('div', { class: 'ic-q', html: renderMD(c.text, { inline: true }) }));
    } else if (c.text) el.append(h('div', { class: 'ic-text', html: renderMD(c.text, { inline: true }) }));
    const loc = c.locator || {};
    if (loc.quote) {
      const q = `“${truncate(loc.quote, 300)}”`;
      el.append(h('div', { class: 'ic-q' }, c.href ? h('a', { href: c.href, target: '_blank', rel: 'noopener noreferrer', title: `Open the paper at ${c.where || 'this passage'}`, text: q }) : q));
    }
    const meta = h('div', { class: 'ic-meta' },
      h('span', { class: 'st', text: c.origin === 'user' ? 'Yours' : STATUS[c.status] }),
      c.where ? (c.href ? h('a', { href: c.href, target: '_blank', rel: 'noopener noreferrer', class: 'tnum', text: c.where }) : h('span', { class: 'tnum', text: c.where })) : null,
      c.private ? h('span', { class: 'lock', title: 'Private: never sent to the AI', text: 'Private' }) : null,
      ...(c.links || []).slice(0, 4).map((l) => h('button', { type: 'button', class: 'ic-link' + (l.conflict ? ' bad' : ''),
        title: `${l.paper_title}${l.where ? ', ' + l.where : ''}${l.value_text ? ': ' + l.value_text : ''}\n${l.text || ''}`,
        text: `${l.conflict ? 'Disagrees with' : 'Same idea in'} ${truncate(l.citekey || l.paper_title, 28)}${l.where ? ` (${l.where})` : ''}`,
        onclick: (e) => { e.stopPropagation(); rb.openNote(l.paper, { center: true }); } })));
    el.append(meta);
    const btns = h('div', { class: 'ic-btns' });
    if (c.status === 'suggested') btns.append(h('button', { type: 'button', class: 'btn btn-sm', 'data-act': 'confirm', text: 'Confirm', onclick: (e) => { e.stopPropagation(); setStatus(c, 'confirmed'); } }));
    btns.append(h('button', { type: 'button', class: 'btn btn-sm btn-text', 'data-act': 'edit', text: 'Edit', onclick: (e) => { e.stopPropagation(); startEdit(c); } }));
    if (c.status === 'rejected') btns.append(h('button', { type: 'button', class: 'btn btn-sm btn-text', text: 'Restore', onclick: (e) => { e.stopPropagation(); setStatus(c, 'suggested'); } }));
    else if (c.origin === 'user') btns.append(h('button', { type: 'button', class: 'btn btn-sm btn-text', text: 'Delete', onclick: (e) => { e.stopPropagation(); del(c); } }));
    else btns.append(h('button', { type: 'button', class: 'btn btn-sm btn-text', 'data-act': 'reject', text: 'Reject', onclick: (e) => { e.stopPropagation(); setStatus(c, 'rejected'); } }));
    el.append(btns);
    return el;
  }

  /** "electron temperature T_e" with the symbol typeset (KaTeX) */
  function qName(q) {
    const name = q.name ? escHtml(q.name) : '';
    const sym = q.symbol ? renderMD('$' + q.symbol.replace(/^\$+|\$+$/g, '') + '$', { inline: true }) : '';
    return [name, sym].filter(Boolean).join(' ');
  }
  const escHtml = (s) => String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  function markActive() {
    if (!P.box) return;
    const els = [...P.box.querySelectorAll('.ic[data-card]')];
    els.forEach((e, i) => e.classList.toggle('on', i === P.active));
    const el = els[P.active];
    if (el) { el.scrollIntoView({ block: 'nearest' }); }
  }

  function editor(c) {
    const isNew = !c;
    const q = (c && c.quantity) || {};
    const form = h('form', { class: 'ic-form', autocomplete: 'off', 'data-editor': c ? c.id : 'new' });
    const kind = h('select', { 'aria-label': 'Kind' }, KINDS.map(([k]) => h('option', { value: k, text: KIND1[k], selected: (c ? c.kind : 'claim') === k })));
    const text = h('textarea', { rows: 2, placeholder: 'The claim, what the equation relates, or the method, in a sentence' });
    text.value = c ? (c.text || '') : '';
    const latexIn = h('input', { type: 'text', placeholder: 'n_e = n_0 \\exp(e\\phi / k T_e)', spellcheck: 'false' });
    latexIn.value = c ? (c.latex || '') : '';
    const mk = (ph, v) => { const i = h('input', { type: 'text', placeholder: ph, spellcheck: 'false' }); i.value = v == null ? '' : String(v); return i; };
    const qn = mk('electron temperature', q.name), qs = mk('T_e', q.symbol), qv = mk('20', q.value), qu = mk('2', q.uncertainty), qunit = mk('eV', q.unit), qc = mk('300 V, 1 m downstream, xenon', q.conditions);
    const quote = h('textarea', { rows: 2, placeholder: 'A passage from the paper (optional; it is matched to its page)' });
    quote.value = c ? ((c.locator || {}).quote || '') : '';
    const page = mk('page', (c && c.locator && c.locator.page) || '');
    const priv = h('input', { type: 'checkbox' });
    priv.checked = c ? !!c.private : !!(P.data && P.data.default_private);
    const eqRow = h('label', null, 'LaTeX', latexIn);
    const qRows = h('div', { class: 'ic-qf' },
      h('div', { class: 'row' }, h('label', null, 'Quantity', qn), h('label', null, 'Symbol', qs), h('label', null, 'Unit', qunit)),
      h('div', { class: 'row' }, h('label', null, 'Value', qv), h('label', null, '± uncertainty', qu), h('label', null, 'Page', page)),
      h('label', null, 'Conditions', qc));
    const sync = () => { eqRow.hidden = kind.value !== 'equation'; qRows.hidden = kind.value !== 'quantity'; };
    kind.addEventListener('change', sync);
    sync();
    const save = h('button', { class: 'btn btn-primary btn-sm', type: 'submit', text: isNew ? 'Add card' : 'Save' });
    const cancel = h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Cancel', onclick: () => stopEdit() });
    form.append(isNew ? h('label', null, 'Kind', kind) : null, h('label', null, 'Text', text), eqRow, qRows, h('label', null, 'Quote', quote),
      h('label', { class: 'chk' }, priv, 'Private: never sent to the AI'), h('div', { class: 'frow' }, save, cancel));
    if (rb.latex) { try { rb.latex(text, null); } catch (e) { /* optional */ } }
    form.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); stopEdit(); }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const k = isNew ? kind.value : c.kind;
      const body = { text: text.value, private: priv.checked };
      if (isNew) body.kind = k;
      if (k === 'equation') body.latex = latexIn.value;
      if (k === 'quantity') body.quantity = { name: qn.value, symbol: qs.value, value: qv.value, uncertainty: qu.value || null, unit: qunit.value, conditions: qc.value };
      const loc = { quote: quote.value };
      const pg = parseInt(page.value, 10);
      if (pg > 0) loc.page = pg; else if (c && c.locator && c.locator.section) loc.section = c.locator.section;
      if (isNew ? quote.value.trim() : (quote.value !== ((c.locator || {}).quote || '') || (pg || null) !== ((c.locator || {}).page || null))) body.locator = loc;
      save.disabled = true;
      try {
        if (isNew) await api('POST', `/api/papers/${enc(P.pid)}/cards`, body);
        else await api('PATCH', `/api/cards/${enc(c.id)}`, body);
        P.adding = false; P.editing = null;
        await load(P.pid, true);
        toast(isNew ? 'Card added' : 'Card saved', 'ok');
      } catch (err) { toastErr(err, isNew ? 'Could not add the card' : 'Could not save the card'); save.disabled = false; }
    });
    setTimeout(() => { (isNew ? kind : text).focus(); }, 0);
    return form;
  }

  function startEdit(c) { P.editing = c.id; P.adding = false; paint(); }
  function stopEdit() { P.editing = null; P.adding = false; paint(); markActive(); }

  async function setStatus(c, status) {
    try {
      const o = ordered();
      const at = o.findIndex((x) => x.id === c.id);
      await api('PATCH', `/api/cards/${enc(c.id)}`, { status });
      await load(P.pid, false);
      const o2 = ordered();
      // move on to the next card still waiting for review (or stay in place)
      let next = o2.findIndex((x, i) => i >= Math.min(at, o2.length - 1) && x.status === 'suggested');
      if (next < 0) next = Math.min(at, o2.length - 1);
      P.active = next;
      paint(); markActive();
    } catch (e) { toastErr(e, 'Could not update the card'); }
  }

  async function del(c) {
    const ok = rb.confirmDialog ? await rb.confirmDialog('Delete this card?', 'It is removed from this paper’s ideas.', 'Delete') : window.confirm('Delete this card?');
    if (!ok) return;
    try { await api('DELETE', `/api/cards/${enc(c.id)}`); await load(P.pid, false); } catch (e) { toastErr(e, 'Could not delete the card'); }
  }

  async function confirmAll() {
    try {
      const r = await api('POST', `/api/papers/${enc(P.pid)}/cards/confirm-all`, {});
      toast(`${r.confirmed} card${r.confirmed === 1 ? '' : 's'} confirmed`, 'ok');
      await load(P.pid, true);
    } catch (e) { toastErr(e, 'Could not confirm'); }
  }

  async function suggest(btn) {
    if (rb.claudeBlocked && rb.claudeBlocked(btn)) return;
    if (P.job) return;
    const pid = P.pid;
    try {
      const r = await api('POST', `/api/papers/${enc(pid)}/cards/extract`);
      watchJob(r.job_id, pid);
    } catch (e) { toastErr(e, 'Could not suggest ideas'); }
  }

  function watchJob(jid, pid) {
    P.job = { id: jid, message: 'Suggesting ideas from the paper…' };
    paint();
    clearInterval(P.poll);
    P.poll = setInterval(async () => {
      let j;
      try { j = await api('GET', `/api/cards/jobs/${enc(jid)}`); } catch (e) { j = { state: 'error', message: e.message }; }
      if (P.pid !== pid) { if (j.state !== 'running') { clearInterval(P.poll); P.poll = null; P.job = null; } return; }
      if (j.state === 'running') { P.job.message = `${j.message || 'Suggesting ideas'}…`; paint(); return; }
      clearInterval(P.poll); P.poll = null; P.job = null;
      if (j.state === 'done') toast(j.message || 'Done', j.added ? 'ok' : 'info');
      else toast(j.message || 'Suggesting ideas failed', 'error');
      await load(pid, false);
      if (window.__rb && rb.refreshGraph) rb.refreshGraph();
    }, 700);
  }

  rb.onPanelRender((n, body) => {
    if (!n || n.type !== 'paper') { P.pid = null; P.box = null; return; }
    const box = h('div', { id: 'ideas-slot' });
    const frags = body.querySelector('[data-sec="Fragments"]');
    if (frags) frags.after(box); else body.append(box);
    if (P.pid !== n.id) { P.active = -1; P.editing = null; P.adding = false; P.showRejected = false; if (!P.poll) P.job = null; }
    P.pid = n.id; P.box = box;
    paint();
    load(n.id, true);
  });

  // ====================================================================== keyboard (panel)
  const panelOpen = () => !!(S.cur && S.cur.type === 'paper' && !$('#panel').hidden && P.box && P.box.isConnected && P.pid === S.cur.id);
  const reviewable = () => panelOpen() && !IV.view && !P.editing && !P.adding && ordered().length > 0;
  const withActive = () => reviewable() && !!activeCard();
  function move(d) {
    const o = ordered();
    if (!o.length) return;
    P.active = P.active < 0 ? (d > 0 ? 0 : o.length - 1) : Math.max(0, Math.min(o.length - 1, P.active + d));
    markActive();
  }

  // ====================================================================== the Ideas view
  const IV = { view: null, kind: 'quantity', q: '', quantity: '', status: '', rows: [], quantities: [], sort: null, dir: 1, t: null };

  function ivParams() {
    const p = new URLSearchParams();
    if (IV.kind) p.set('kind', IV.kind);
    if (IV.q.trim()) p.set('q', IV.q.trim());
    if (IV.quantity.trim() && IV.kind === 'quantity') p.set('quantity', IV.quantity.trim());
    if (IV.status) p.set('status', IV.status);
    return p;
  }
  async function ivLoad() {
    try {
      const r = await api('GET', '/api/cards?' + ivParams().toString());
      IV.rows = r.cards || []; IV.quantities = r.quantities || [];
    } catch (e) { toastErr(e, "Couldn't load ideas"); IV.rows = []; }
    ivPaint();
  }
  function ivOpen(opts = {}) {
    if (opts.quantity !== undefined) { IV.kind = 'quantity'; IV.quantity = opts.quantity; }
    if (!IV.view) {
      IV.view = h('section', { class: 'iv', 'aria-label': 'Ideas', tabindex: '-1' });
      document.body.appendChild(IV.view);
      IV.view.focus();
    }
    ivPaint(); ivLoad();
  }
  function ivClose() { if (IV.view) { IV.view.remove(); IV.view = null; } }

  const COLS = {
    quantity: [['name', 'Quantity'], ['value', 'Value', 'num'], ['unit', 'Unit'], ['si', 'SI', 'num'], ['conditions', 'Conditions'],
      ['paper', 'Paper'], ['where', 'Where', 'num'], ['status', 'Status'], ['conflicts', '']],
    other: [['kind', 'Kind'], ['text', 'Idea', 'txt'], ['paper', 'Paper'], ['where', 'Where', 'num'], ['status', 'Status'], ['conflicts', '']],
  };
  function cell(c, key) {
    const q = c.quantity || {};
    switch (key) {
      case 'name': return q.name || q.symbol || '';
      case 'value': return q.value;
      case 'unit': return q.unit || '';
      case 'si': return q.canonical_unit_value;
      case 'conditions': return q.conditions || '';
      case 'paper': return c.citekey || c.paper_title;
      case 'where': return (c.locator && c.locator.page) || c.where || '';
      case 'status': return c.origin === 'user' ? 'yours' : c.status;
      case 'conflicts': return c.conflicts || 0;
      case 'kind': return c.kind;
      case 'text': return c.text || c.latex || '';
      default: return '';
    }
  }
  function ivPaint() {
    if (!IV.view) return;
    const head = h('div', { class: 'iv-head' }, h('h1', { text: 'Ideas' }), h('div', { class: 'spacer' }),
      h('a', { class: 'btn btn-sm', 'data-act': 'ideas-csv', href: '/api/cards/export.csv?' + ivParams().toString(), download: '', text: 'Export CSV' }),
      h('button', { type: 'button', class: 'btn btn-sm', text: 'Close', title: 'Close (Esc)', onclick: ivClose }));
    const lede = h('p', { class: 'iv-lede', text: 'Claims, equations, measured quantities and methods from your papers. Suggested cards came from the AI, each with a quote found in its paper; confirmed and edited ones are yours. Values are converted to SI for comparison; a badge marks a value that disagrees with another paper under comparable conditions.' });
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Kind' },
      [['quantity', 'Quantities'], ['claim', 'Claims'], ['equation', 'Equations'], ['method', 'Methods'], ['', 'All']].map(([k, label]) =>
        h('button', { type: 'button', class: IV.kind === k ? 'on' : '', text: label, onclick: () => { IV.kind = k; IV.sort = null; ivPaint(); ivLoad(); } })));
    const qIn = h('input', { type: 'search', placeholder: 'Filter: Hall thruster plume', 'aria-label': 'Filter ideas', value: IV.q });
    qIn.addEventListener('input', () => { IV.q = qIn.value; clearTimeout(IV.t); IV.t = setTimeout(ivLoad, 250); });
    const dl = h('datalist', { id: 'iv-quantities' }, IV.quantities.map((x) => h('option', { value: x.name, text: `${x.count}` })));
    const qtyIn = h('input', { type: 'text', list: 'iv-quantities', placeholder: 'Quantity: electron temperature', 'aria-label': 'Quantity', value: IV.quantity, hidden: IV.kind !== 'quantity' });
    qtyIn.addEventListener('input', () => { IV.quantity = qtyIn.value; clearTimeout(IV.t); IV.t = setTimeout(ivLoad, 250); });
    const st = h('select', { 'aria-label': 'Status' }, [['', 'All but rejected'], ['known', 'Confirmed and edited'], ['suggested', 'Suggested'], ['rejected', 'Rejected']]
      .map(([v, l]) => h('option', { value: v, text: l, selected: IV.status === v })));
    st.addEventListener('change', () => { IV.status = st.value; ivLoad(); });
    const bar = h('div', { class: 'iv-bar' }, seg, qIn, qtyIn, dl, st);
    const cols = IV.kind === 'quantity' ? COLS.quantity : COLS.other;
    let rows = IV.rows.slice();
    if (IV.sort) {
      const k = IV.sort;
      rows.sort((a, b) => {
        const x = cell(a, k), y = cell(b, k);
        if (typeof x === 'number' || typeof y === 'number') return ((x ?? -Infinity) - (y ?? -Infinity)) * IV.dir;
        return String(x).localeCompare(String(y)) * IV.dir;
      });
    }
    const thead = h('thead', null, h('tr', null, cols.map(([k, label]) => h('th', { class: IV.sort === k ? 'sorted' : '', 'data-col': k, 'data-dir': IV.dir > 0 ? '↑' : '↓', text: label,
      onclick: () => { if (IV.sort === k) IV.dir = -IV.dir; else { IV.sort = k; IV.dir = 1; } ivPaint(); } }))));
    const tbody = h('tbody', null, rows.map((c) => {
      const q = c.quantity || {};
      const tds = cols.map(([k, , cls]) => {
        let v;
        if (k === 'value') v = c.value_text || '';
        else if (k === 'si') v = c.canonical_text || '';
        else if (k === 'paper') v = h('button', { type: 'button', class: 'open key', title: c.paper_title, text: c.citekey || truncate(c.paper_title, 40), onclick: () => { ivClose(); rb.openNote(c.paper, { center: true }); } });
        else if (k === 'where') v = c.href ? h('a', { href: c.href, target: '_blank', rel: 'noopener noreferrer', title: (c.locator || {}).quote || '', text: c.where || 'open' }) : (c.where || '');
        else if (k === 'conflicts') v = c.conflicts ? h('span', { class: 'bad', title: (c.links || []).filter((l) => l.conflict).map((l) => `${l.citekey || l.paper_title} ${l.where}: ${l.value_text}`).join('\n'), text: `Disagrees (${c.conflicts})` }) : '';
        else if (k === 'text') v = h('span', { html: renderMD(c.kind === 'equation' && c.latex ? `$${c.latex}$ ${c.text || ''}` : (c.text || ''), { inline: true }) });
        else if (k === 'name') v = h('span', { html: qName(q) });
        else v = String(cell(c, k) ?? '');
        return h('td', { class: cls || '' }, v);
      });
      return h('tr', { class: c.status === 'suggested' ? 'sug' : '', 'data-card': c.id }, tds);
    }));
    const table = rows.length ? h('table', null, thead, tbody)
      : h('div', { class: 'iv-empty', text: IV.q || IV.quantity ? 'No ideas match. Try fewer words, or another quantity.' : 'No ideas yet. Open a paper and choose Suggest ideas, or add a card by hand.' });
    const top = IV.view.scrollTop;
    const focused = document.activeElement === qIn ? 'q' : document.activeElement === qtyIn ? 'qty' : '';
    IV.view.replaceChildren(h('div', { class: 'iv-col' }, head, lede, bar, table));
    IV.view.scrollTop = top;
    const keep = focused === 'q' ? IV.view.querySelector('input[type=search]') : focused === 'qty' ? IV.view.querySelector('input[list]') : null;
    if (keep) { keep.focus(); const v = keep.value; keep.setSelectionRange(v.length, v.length); }
  }

  // ====================================================================== commands
  if (C && C.register) {
    const reg = (c) => { try { C.register(c); } catch (e) { console.error(e); } };
    reg({ id: 'ideas.open', title: 'Open ideas', group: 'Library', keys: 'G then I',
      description: 'Every claim, equation, measured quantity and method from your papers; quantities as a table you can sort and export.',
      aliases: ['idea cards', 'quantities', 'claims', 'equations', 'measurements', 'table', 'csv'], run: () => (IV.view ? ivClose() : ivOpen()) });
    reg({ id: 'ideas.next', title: 'Next idea card', group: 'Note', keys: 'J', when: reviewable, run: () => move(1),
      description: 'Moves to the next card in the open paper’s Ideas.' });
    reg({ id: 'ideas.prev', title: 'Previous idea card', group: 'Note', keys: 'K', when: reviewable, run: () => move(-1),
      description: 'Moves to the previous card in the open paper’s Ideas.' });
    reg({ id: 'ideas.confirm', title: 'Confirm the idea card', group: 'Note', keys: 'Y', when: withActive,
      run: () => { const c = activeCard(); if (c && c.status !== 'confirmed') setStatus(c, 'confirmed'); }, description: 'Marks the selected card as yours.' });
    reg({ id: 'ideas.edit', title: 'Edit the idea card', group: 'Note', keys: 'E', when: withActive,
      run: () => { const c = activeCard(); if (c) startEdit(c); }, description: 'Edit its text, value or quote. An edited card counts as confirmed.' });
    reg({ id: 'ideas.reject', title: 'Reject the idea card', group: 'Note', keys: 'X', when: withActive,
      run: () => { const c = activeCard(); if (c) setStatus(c, c.status === 'rejected' ? 'suggested' : 'rejected'); }, description: 'Hides a suggestion you don’t want (Show rejected brings it back).' });
    reg({ id: 'ideas.suggest', title: 'Suggest ideas from this paper', group: 'Note', when: panelOpen,
      run: () => suggest($('[data-act="ideas-suggest"]')), description: 'The AI reads the paper’s own text and suggests cards, each with a quote from the paper.', aliases: ['extract', 'claims', 'cards'] });
    reg({ id: 'ideas.add', title: 'Add an idea card', group: 'Note', when: panelOpen,
      run: () => { P.adding = true; P.editing = null; paint(); }, description: 'Write a claim, equation, quantity or method of this paper yourself.' });
    reg({ id: 'ideas.confirmAll', title: 'Confirm all suggested ideas', group: 'Note', when: () => panelOpen() && !!(P.data && P.data.counts.suggested),
      run: confirmAll, description: 'Confirms every suggested card of the open paper.' });
  }
  if (C && C.layer) {
    try { C.layer({ id: 'ideas.view', order: 80, isOpen: () => !!IV.view, close: ivClose }); } catch (e) { console.error(e); }
  }

  window.__rbCards = { open: ivOpen, close: ivClose, isOpen: () => !!IV.view, reload: () => P.pid && load(P.pid, true), state: P, view: IV };
})();
