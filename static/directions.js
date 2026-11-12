/* research_brain — research directions (app/directions.py), for an open question or a topic.
 * Two steps, so nothing reaches the AI without you seeing it first:
 *   1. the preview (dry=true): every source, ticked or left out and why (private, no summary yet, over the
 *      usage level's limit), the approximate size, and options: what you want directions about, and (for a
 *      question) whether your current answer goes too (off by default);
 *   2. "Send to <agent>": the answer, with citations that open the passage (the page's MutationObserver
 *      pins them), "Save as a note" (a private note, like any note you write) and the last few runs.
 * Needs window.__rb (h, api, renderMD, toast).
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.h) return;
  const { h, api, toast, toastErr, truncate } = rb;
  const enc = encodeURIComponent;

  document.head.appendChild(h('style', { text: `
  .dr-wrap { position: fixed; inset: 0; z-index: 72; background: color-mix(in srgb, var(--ground) 70%, transparent); display: flex; align-items: center; justify-content: center; padding: 16px; }
  .dr-card { width: min(720px, 100%); max-height: min(90vh, 980px); display: flex; flex-direction: column; background: var(--sheet); border: 1px solid var(--rule); padding: 20px 22px 16px; }
  .dr-card h2 { font: 600 19px/1.3 var(--serif); margin: 0 0 4px; }
  .dr-sub { color: var(--ink-2); font-size: 13.5px; line-height: 1.5; margin: 0 0 10px; }
  .dr-body { overflow: auto; min-height: 0; flex: 1; }
  .dr-src { border-top: 1px solid var(--rule); padding: 8px 0; font-size: 13.5px; }
  .dr-src label { display: flex; gap: 8px; align-items: baseline; padding: 2px 0; }
  .dr-src .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dr-src .k { color: var(--ink-3); font-size: 12px; white-space: nowrap; }
  .dr-src .off { color: var(--ink-3); }
  .dr-h { font: 600 12.5px var(--sans); color: var(--ink-2); margin: 10px 0 4px; }
  .dr-opts { display: flex; flex-direction: column; gap: 8px; margin: 10px 0; font-size: 13.5px; }
  .dr-opts input[type=text] { font: 14px var(--sans); padding: 6px 8px; border: 1px solid var(--rule-strong); background: var(--sheet); color: var(--ink); }
  .dr-opts label { display: flex; gap: 8px; align-items: baseline; }
  .dr-acts { display: flex; gap: 8px; justify-content: flex-end; align-items: center; margin-top: 12px; }
  .dr-acts .dr-size { margin-right: auto; color: var(--ink-2); font-size: 12.5px; }
  .dr-out { font: 15px/1.6 var(--serif); border-left: 1px solid var(--machine); padding-left: 14px; }
  .dr-out h2 { font: 600 15px var(--sans); margin: 14px 0 4px; }
  .dr-runs { font-size: 12.5px; color: var(--ink-2); margin-top: 10px; }
  .dr-runs button { background: none; border: 0; color: var(--ink-2); text-decoration: underline; cursor: pointer; padding: 0 4px; }
  ` }));

  let agentName = 'your AI agent';
  api('GET', '/api/agents').then((a) => { const cur = a && (a.agents || []).find((x) => x.id === a.active); if (cur && cur.name) agentName = cur.name; }).catch(() => {});

  /** kind: 'question' | 'topic'; id: question id or topic slug */
  function open(kind, id, title) {
    const url = kind === 'question' ? `/api/questions/${enc(id)}/directions` : `/api/topics/${enc(id)}/directions`;
    const card = h('div', { class: 'dr-card', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Research directions' });
    const wrap = h('div', { class: 'dr-wrap' }, card);
    let busy = false;
    const close = () => { if (!busy) { wrap.remove(); document.removeEventListener('keydown', onKey, true); } };
    const onKey = (e) => { if (e.key === 'Escape' && !e.target.closest('input')) { e.stopPropagation(); close(); } };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
    document.body.appendChild(wrap);

    const exclude = new Set();
    const focus = h('input', { type: 'text', placeholder: 'Optional: what you want directions on (e.g. experiments we could run in our chamber)', 'aria-label': 'What you want directions on' });
    const answer = h('input', { type: 'checkbox', id: 'dr-answer' });
    const body = h('div', { class: 'dr-body' });
    const size = h('span', { class: 'dr-size' });
    const send = h('button', { class: 'btn btn-primary', type: 'button', text: 'Send to AI' });
    const cancel = h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: close });
    card.append(h('h2', { text: 'Research directions' }),
      h('p', { class: 'dr-sub', text: `${kind === 'question' ? 'For the question' : 'For the topic'} “${truncate(title || '', 90)}”. Below is everything that would go to the AI. Private notes and papers never do.` }),
      body, h('div', { class: 'dr-acts' }, size, cancel, send));

    let pv = null, timer = 0;
    const refresh = () => { clearTimeout(timer); timer = setTimeout(preview, 150); };
    focus.addEventListener('input', refresh);
    answer.addEventListener('change', refresh);

    async function preview() {
      try {
        pv = await api('POST', url, { dry: true, exclude: [...exclude], include_answer: answer.checked, focus: focus.value });
      } catch (e) { body.replaceChildren(h('p', { class: 'dr-sub', text: e.message })); send.disabled = true; return; }
      drawPreview();
    }
    function drawPreview() {
      const rows = [];
      const used = new Set(pv.used.map((s) => s.id));
      const all = [...pv.used, ...pv.left_out.filter((s) => s.why === 'you unticked it' || s.why.startsWith('over '))];
      rows.push(h('div', { class: 'dr-h', text: `Goes to the AI (${pv.used.length})` }));
      rows.push(h('div', { class: 'dr-src' }, h('label', null, h('input', { type: 'checkbox', checked: true, disabled: true }),
        h('span', { class: 't', text: kind === 'question' ? 'The question’s title and description' : 'The topic’s name and description' }))),
        ...all.map((s) => {
          const box = h('input', { type: 'checkbox', 'aria-label': s.title });
          box.checked = used.has(s.id);
          box.addEventListener('change', () => { if (box.checked) exclude.delete(s.id); else exclude.add(s.id); refresh(); });
          return h('div', { class: 'dr-src' }, h('label', { title: s.why || '' }, box, h('span', { class: 't', text: s.title }),
            h('span', { class: 'k', text: [s.verdict, s.kind, used.has(s.id) ? '' : s.why].filter(Boolean).join(' · ') })));
        }));
      const kept = pv.left_out.filter((s) => s.why !== 'you unticked it' && !s.why.startsWith('over '));
      if (kept.length) {
        rows.push(h('div', { class: 'dr-h', text: `Never sent (${kept.length})` }),
          ...kept.map((s) => h('div', { class: 'dr-src' }, h('span', { class: 'off t', text: `${s.title} · ${s.why}` }))));
      }
      const opts = [focus];
      if (pv.has_answer) opts.push(h('label', { for: 'dr-answer' }, answer, 'Also send my current answer (off unless you tick it)'));
      body.replaceChildren(h('div', { class: 'dr-opts' }, ...opts), ...rows, runsList(pv.runs || []));
      size.textContent = `About ${pv.tokens.toLocaleString()} tokens at the ${pv.level} usage level`;
      send.disabled = !pv.used.length;
      send.textContent = `Send to ${agentName}`;
    }
    function runsList(runs) {
      if (!runs.length) return '';
      return h('div', { class: 'dr-runs' }, 'Earlier: ', ...runs.map((r) => h('button', { type: 'button', text: new Date(r.at).toLocaleString(), title: r.focus || '', onclick: () => showRun(r, runs) })));
    }
    function showRun(run, runs) {
      const out = h('div', { class: 'dr-out md' });
      out.innerHTML = rb.renderMD(run.text || '');
      const save = h('button', { class: 'btn', type: 'button', text: 'Save as a note', title: 'A private note, linked to this ' + kind });
      save.addEventListener('click', async () => {
        save.disabled = true;
        try {
          const link = kind === 'question' ? `\n\nFor [[${id}]].` : '';
          const n = await api('POST', '/api/notes', { type: 'note', title: `Directions: ${truncate(title || '', 80)}`,
            body: `${run.text}${link}\n\n*Suggested by ${agentName} on ${new Date(run.at).toLocaleDateString()} from ${run.used} sources${run.focus ? `, about: ${run.focus}` : ''}.*` });
          toast('Saved as a private note.', 'ok', { action: { label: 'Open', fn: () => { busy = false; close(); rb.openNote(n.id, { center: true }); } } });
          rb.refreshGraph();
        } catch (e) { save.disabled = false; toastErr(e, 'Could not save it'); }
      });
      const copy = h('button', { class: 'btn btn-text', type: 'button', text: 'Copy', onclick: () => navigator.clipboard.writeText(run.text).then(() => toast('Copied.', 'ok')) });
      body.replaceChildren(h('p', { class: 'dr-sub', text: `From ${run.used} source${run.used === 1 ? '' : 's'}${run.left_out ? `, ${run.left_out} left out` : ''}${run.focus ? `, about “${run.focus}”` : ''}${run.answer_included ? ', with your answer' : ''}. Citations open the passage they rest on. Points marked (speculative) go beyond the sources.` }),
        out, runsList(runs.filter((r) => r !== run)));
      send.hidden = true; size.textContent = '';
      cancel.textContent = 'Close';
      card.querySelector('.dr-acts').prepend(save, copy);
      const back = h('button', { class: 'btn btn-text', type: 'button', text: 'Run again…', onclick: () => { save.remove(); copy.remove(); back.remove(); send.hidden = false; cancel.textContent = 'Cancel'; preview(); } });
      card.querySelector('.dr-acts').prepend(back);
    }
    send.addEventListener('click', async () => {
      busy = true; send.disabled = true; send.textContent = 'Thinking…';
      try {
        const r = await api('POST', url, { exclude: [...exclude], include_answer: answer.checked, focus: focus.value });
        busy = false;
        showRun(r.run, r.runs || [r.run]);
      } catch (e) {
        busy = false; send.disabled = false; send.textContent = `Send to ${agentName}`;
        toastErr(e, 'The AI couldn’t suggest directions');
      }
    });
    body.replaceChildren(h('p', { class: 'dr-sub', text: 'Gathering what would be sent…' }));
    preview();
  }

  window.__rbDirections = { open };
})();
