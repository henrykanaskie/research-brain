/* research_brain — region changes the chat proposes (app/region_edit.py).
 * Ask the chat to change your regions and its answer ends with a ```rb-regions block of JSON. That block isn't
 * shown as text: under the answer is a card listing each change (papers by title), with Apply and Dismiss.
 * Nothing changes until you click Apply; after it, Undo puts every region back. What you chose is kept on the
 * message (regionEdit: {state: 'applied' | 'dismissed' | 'undone', token}), so a reopened chat shows it.
 * Endpoints: POST /api/regions/changes/preview, /apply, /undo. Depends on window.__rb from app.js.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { h, api, toast, toastErr } = rb;

  const CSS = `
  .re-card { margin: 12px 0 2px; border: 1px solid var(--rule); border-radius: var(--r, 6px); background: var(--sheet); padding: 10px 12px 12px; }
  .re-card .re-head { font: 600 12.5px var(--sans); color: var(--ink); margin-bottom: 6px; }
  .re-card ul { margin: 0; padding: 0; list-style: none; }
  .re-card li { padding: 5px 0; border-top: 1px solid var(--rule); font-size: 13px; line-height: 1.4; }
  .re-card li:first-child { border-top: 0; }
  .re-card .re-op { font-weight: 600; }
  .re-card .re-papers { color: var(--ink-2); margin-top: 2px; }
  .re-card .re-bad { color: var(--red); }
  .re-card .re-new { color: var(--ink-2); font-size: 12px; }
  .re-card .re-acts { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
  .re-card .re-done { color: var(--ink-2); font-size: 12.5px; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  const BLOCK = /```rb-regions[^\n]*\n([\s\S]*?)```/i;
  /** The answer without its rb-regions block (and without a half-written one while it streams). */
  function strip(text) {
    return String(text || '').replace(/\s*```rb-regions[^\n]*\n[\s\S]*?```\s*/gi, '\n\n').replace(/\s*```rb-regions[\s\S]*$/i, '');
  }
  function changesOf(text) {
    const m = BLOCK.exec(String(text || ''));
    if (!m) return null;
    try {
      const d = JSON.parse(m[1]);
      const list = Array.isArray(d) ? d : d && d.changes;
      return Array.isArray(list) && list.length ? list : null;
    } catch { return null; }
  }

  const q = (s) => `“${s}”`;
  function line(c) {
    const n = (c.papers || []).length;
    const what = n === 1 ? q(rb.truncate(c.papers[0].title, 70)) : `${n} papers`;
    let head;
    if (c.op === 'rename') head = `Rename ${q(c.region)} to ${q(c.to)}`;
    else if (c.op === 'move') head = `Move ${what} to ${q(c.to)}` + (c.from ? ` (out of ${q(c.from)})` : ' (out of its other regions)');
    else if (c.op === 'add') head = `Also put ${what} in ${q(c.to)}`;
    else if (c.op === 'remove') head = `Take ${what} out of ${q(c.from)}`;
    else if (c.op === 'main') head = `Draw ${what} in ${q(c.region)} (its main region)`;
    else head = c.op;
    const kids = [h('div', {}, h('span', { class: 're-op', text: head }),
      c.new ? h('span', { class: 're-new', text: '  a new region' }) : null)];
    if (n > 1) kids.push(h('div', { class: 're-papers', text: c.papers.slice(0, 8).map((p) => rb.truncate(p.title, 60)).join(' · ') + (n > 8 ? ` · and ${n - 8} more` : '') }));
    if (c.problem) kids.push(h('div', { class: 're-bad', text: `Can't: ${c.problem}` }));
    return h('li', {}, ...kids);
  }

  /** The card under answer m, or null when the answer proposes nothing. */
  function card(m) {
    const changes = changesOf(m.content);
    if (!changes) return null;
    const st = m.regionEdit || {};
    const box = h('div', { class: 're-card', 'data-state': st.state || 'proposed' },
      h('div', { class: 're-head', text: 'Region changes' }));
    const list = h('ul', {}, h('li', { class: 're-papers', text: 'Checking…' }));
    const acts = h('div', { class: 're-acts' });
    box.append(list, acts);
    const save = (next) => { m.regionEdit = next; rb.persistChat(); };
    const paint = (rows) => {
      list.replaceChildren(...rows.map(line));
      const ok = rows.some((r) => !r.problem);
      const s = (m.regionEdit || {}).state;
      if (s === 'applied') {
        acts.replaceChildren(h('span', { class: 're-done', text: 'Applied.' }),
          h('button', { class: 'btn btn-sm', type: 'button', text: 'Undo', onclick: async (e) => {
            e.currentTarget.disabled = true;
            try {
              await api('POST', '/api/regions/changes/undo', { token: m.regionEdit.token });
              save({ state: 'undone' });
              toast('Your regions are back as they were.', 'ok');
              await after();
            } catch (err) { toastErr(err, 'Could not undo it'); e.currentTarget.disabled = false; }
          } }));
      } else if (s === 'dismissed' || s === 'undone') {
        acts.replaceChildren(h('span', { class: 're-done', text: s === 'undone' ? 'Undone.' : 'Not applied.' }),
          h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Apply after all', disabled: !ok, onclick: () => go() }));
      } else {
        acts.replaceChildren(
          h('button', { class: 'btn btn-primary btn-sm', type: 'button', text: 'Apply', disabled: !ok,
            title: ok ? 'Make these changes to your regions (you can undo it)' : 'None of these can be done', onclick: () => go() }),
          h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Dismiss', onclick: () => { save({ state: 'dismissed' }); paint(rows); } }));
      }
    };
    const after = async () => {
      await rb.refreshGraph();
      window.dispatchEvent(new CustomEvent('rb:topics-changed', { detail: {} }));  // static/topics.js reloads
      if (rb.reloadCur && rb.S && rb.S.cur) rb.reloadCur();
    };
    let rows = [];
    const go = async () => {
      for (const b of acts.querySelectorAll('button')) b.disabled = true;
      try {
        const r = await api('POST', '/api/regions/changes/apply', { changes });
        save({ state: 'applied', token: r.token });
        const n = (r.done || []).length, skipped = (r.skipped || []).length;
        toast(`Made ${n} region change${n === 1 ? '' : 's'}${skipped ? `; ${skipped} couldn't be done` : ''}.`, 'ok');
        await after();
        paint(rows);
      } catch (err) { toastErr(err, 'Could not change the regions'); paint(rows); }
    };
    api('POST', '/api/regions/changes/preview', { changes })
      .then((r) => { rows = r.changes || []; paint(rows); })
      .catch((err) => { list.replaceChildren(h('li', { class: 're-bad', text: `These changes can't be read: ${err.message || err}` })); });
    return box;
  }

  window.__rbRegionEdit = { strip, card, changesOf };
  if (rb.renderChat) rb.renderChat();  // answers drawn before this loaded get their card
})();
