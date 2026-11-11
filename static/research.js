/* research_brain — Research mode for "Ask your library".
 * Quick (the default) is the one-shot answer from pre-retrieved passages. Research lets Claude search
 * the library itself with research_brain's own read-only tools: several searches, opening pages,
 * following citations, consulting memory. Steps stream in as a compact trail above the answer
 * (POST /api/research/stream, see API.md), and the trail folds away once the answer is done.
 * Claude can only read the library here; the notice under the toggle says so.
 * Depends on window.__rb from app.js (renderChat calls renderMsg for research messages; sendChat
 * hands over to send() when Research is on).
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { S, h, toast, toastErr, renderMD } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('rb.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rb.' + k, JSON.stringify(v)); } catch { /* private window */ } },
  };
  const NOTICE = () => `${window.RB_AI || 'Claude'} can only read your library here — it can’t browse, click, or touch anything else.`;

  const CSS = `
  .rs-mode { display: flex; align-items: center; gap: 12px; margin: 0 0 8px; flex-wrap: wrap; }
  .rs-seg { display: inline-flex; border: 1px solid var(--rule); border-radius: 3px; overflow: hidden; }
  .rs-seg button { all: unset; cursor: pointer; font: 500 12.5px/1 var(--sans); color: var(--ink-2); padding: 7px 12px; }
  .rs-seg button + button { border-left: 1px solid var(--rule); }
  .rs-seg button[aria-pressed="true"] { background: var(--ink); color: var(--sheet); }
  .rs-seg button:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
  .rs-fast { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); cursor: pointer; }
  .rs-notice { flex-basis: 100%; font-size: 12px; color: var(--ink-2); line-height: 1.4; }
  .rs-trail { margin: 0 0 10px; font-size: 12.5px; color: var(--ink-2); }
  .rs-trail > summary { cursor: pointer; list-style: none; display: flex; gap: 8px; align-items: baseline; padding: 2px 0; }
  .rs-trail > summary::-webkit-details-marker { display: none; }
  .rs-trail > summary::before { content: ''; width: 0; height: 0; border: 4px solid transparent; border-left: 5px solid var(--ink-2); transform: translateY(1px); }
  .rs-trail[open] > summary::before { transform: rotate(90deg) translateX(1px); }
  .rs-steps { list-style: none; margin: 4px 0 0; padding: 0 0 0 2px; display: flex; flex-direction: column; gap: 3px; }
  .rs-step { display: grid; grid-template-columns: 14px 1fr auto; gap: 6px; align-items: baseline; line-height: 1.4; }
  .rs-step .mk { width: 6px; height: 6px; border-radius: 50%; border: 1px solid var(--machine); transform: translateY(-1px); }
  .rs-step.done .mk { background: var(--machine); }
  .rs-step.error .mk { border-color: var(--red); background: var(--red); }
  .rs-step.running .mk { animation: rsPulse 1s ease-in-out infinite alternate; }
  @keyframes rsPulse { from { opacity: .25; } }
  @media (prefers-reduced-motion: reduce) { .rs-step.running .mk { animation: none; } }
  .rs-step .lb { color: var(--ink); overflow-wrap: anywhere; }
  .rs-step .lb .cite { font-size: 12px; }
  .rs-step .nt { color: var(--ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .rs-step.error .nt { color: var(--red); white-space: normal; }
  .rs-time { font-variant-numeric: tabular-nums; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // ====================================================================== mode toggle
  const R = { mode: LS.get('chatMode', 'quick') === 'research' ? 'research' : 'quick', fast: !!LS.get('researchFast', false) };
  const form = $('#chat-form');
  const bar = h('div', { class: 'rs-mode' });
  const seg = h('div', { class: 'rs-seg', role: 'group', 'aria-label': 'Answer mode' });
  const bQuick = h('button', { type: 'button', text: 'Quick', title: 'One answer from the most relevant passages (fast)', onclick: () => setMode('quick') });
  const bRes = h('button', { type: 'button', text: 'Research', title: `${window.RB_AI || 'Claude'} searches your library itself: several searches, opens pages, follows citations`, onclick: () => setMode('research') });
  seg.append(bQuick, bRes);
  const fastBox = h('input', { type: 'checkbox' });
  fastBox.checked = R.fast;
  fastBox.addEventListener('change', () => { R.fast = fastBox.checked; LS.set('researchFast', R.fast); });
  const fast = h('label', { class: 'rs-fast', title: 'Use a faster, lighter model for this research (RB_AGENT_FAST_MODEL, default haiku)' }, fastBox, 'Faster model');
  const notice = h('div', { class: 'rs-notice', text: NOTICE() });
  bar.append(seg, fast, notice);
  if (form) form.insertBefore(bar, form.firstChild);

  function setMode(m) {
    R.mode = m === 'research' ? 'research' : 'quick';
    LS.set('chatMode', R.mode);
    paintMode();
  }
  function paintMode() {
    bQuick.setAttribute('aria-pressed', String(R.mode === 'quick'));
    bRes.setAttribute('aria-pressed', String(R.mode === 'research'));
    fast.hidden = notice.hidden = R.mode !== 'research';
    const ta = $('#chat-input');
    const scoped = window.__rb && window.__rb.S && window.__rb.S.chatPaper;
    if (ta && scoped) ta.placeholder = 'Ask anything about this paper';
    else if (ta) ta.placeholder = R.mode === 'research' ? `Ask a question for ${window.RB_AI || 'Claude'} to research in your library` : 'Ask about your papers and notes';
  }
  paintMode();

  // ====================================================================== the stream
  /** POST + read the research SSE stream. on(event, data) for step / delta / reset; resolves with `done`. */
  async function stream(body, on) {
    const res = await fetch('/api/research/stream', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(body) });
    if (!res.ok) {
      let d = null;
      try { d = await res.json(); } catch { /* not json */ }
      throw new Error((d && typeof d.detail === 'string') ? d.detail : `Research failed (HTTP ${res.status})`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '', done = null;
    const handle = (frame) => {
      let ev = 'message';
      const data = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (!data.length) return;
      let d;
      try { d = JSON.parse(data.join('\n')); } catch { return; }
      if (ev === 'done') done = d;
      else if (ev === 'error') throw new Error(d.detail || 'Research failed');
      else on(ev, d);
    };
    for (;;) {
      const { value, done: end } = await reader.read();
      if (value) buf += dec.decode(value, { stream: true });
      buf = buf.replace(/\r\n?/g, '\n');
      let i;
      while ((i = buf.indexOf('\n\n')) !== -1) { const f = buf.slice(0, i); buf = buf.slice(i + 2); handle(f); }
      if (end) break;
    }
    if (buf.trim()) handle(buf);
    if (!done) throw new Error('The research stream ended early');
    return done;
  }

  function wants(opts) {
    if (opts && opts.mode) return opts.mode === 'research';
    return R.mode === 'research';
  }

  async function send(question, { fast: f } = {}) {
    if (S.chatBusy) return;
    if (rb.claudeBlocked && rb.claudeBlocked()) return;
    const history = S.chat.filter((m) => !m.pending && !m.error && !m.failed).map((m) => ({ role: m.role, content: m.content }));
    const userMsg = { role: 'user', content: question };
    const msg = { role: 'assistant', mode: 'research', pending: true, start: Date.now(), steps: [], live: '', question };
    S.chat.push(userMsg, msg);
    S.chatBusy = true;
    rb.renderChat();
    const log = $('#chat-log');
    const live = rb.streamRenderer((text) => {
      const el = log && log.querySelector('.rs-live:last-of-type');
      const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      if (!el) { rb.renderChat(); return; }
      el.innerHTML = rb.partialMD(text);
      if (atEnd) log.scrollTop = log.scrollHeight;
    });
    let text = '';
    try {
      const r = await stream({ question, history, fast: f === undefined ? R.fast : !!f }, (ev, d) => {
        if (ev === 'step') {
          const i = msg.steps.findIndex((s) => s.id === d.id);
          if (i === -1) msg.steps.push(d); else msg.steps[i] = d;
          paintTrail(msg);
        } else if (ev === 'delta') {
          const first = !text;
          text += d.text;
          msg.live = text;
          if (first) rb.renderChat(); else live.push(text);
        } else if (ev === 'reset') {
          text = ''; msg.live = ''; live.cancel(); rb.renderChat();
        }
      });
      live.flush();
      Object.assign(msg, { pending: false, live: '', content: r.answer || text, sources: r.sources || [], passages: r.passages || [],
        steps: r.steps || msg.steps, timing: r.timing || {}, ended: r.ended, elapsed: Date.now() - msg.start });
    } catch (e) {
      live.cancel();
      userMsg.failed = true;
      Object.assign(msg, { pending: false, error: true, live: '', content: e.message });
      toastErr(e, "Research couldn't finish");
    } finally {
      S.chatBusy = false;
      rb.renderChat();
    }
  }

  // ====================================================================== rendering
  function stepRow(s) {
    const note = s.status === 'running' ? '' : (s.note || '');
    return h('li', { class: `rs-step ${s.status || 'done'}` }, h('span', { class: 'mk', 'aria-hidden': 'true' }),
      h('span', { class: 'lb', html: renderMD(s.label, { inline: true }) }),
      h('span', { class: 'nt', text: note }));
  }
  function summaryText(m) {
    const calls = (m.steps || []).filter((s) => !['seed', 'finish', 'init'].includes(s.tool)).length;
    const secs = ((m.timing && m.timing.total_ms) || m.elapsed || 0) / 1000;
    const how = calls ? `${calls} tool call${calls === 1 ? '' : 's'}` : 'answered from the first search';
    const end = m.ended === 'budget' ? ', time budget reached' : m.ended === 'max_turns' ? ', step limit reached' : '';
    return `Researched in ${secs.toFixed(1)} s: ${how}${end}`;
  }
  function trail(m) {
    const d = h('details', { class: 'rs-trail' });
    d.open = !!m.pending || !!m.trailOpen;
    d.addEventListener('toggle', () => { if (!m.pending) m.trailOpen = d.open; });
    const sum = h('summary', {}, m.pending ? h('span', {}, 'Researching ', h('span', { class: 'rs-time', 'data-start': m.start, text: '0 s' })) : h('span', { text: summaryText(m) }));
    d.append(sum, h('ol', { class: 'rs-steps' }, (m.steps || []).map(stepRow)));
    return d;
  }
  function paintTrail(m) {
    const els = document.querySelectorAll('#chat-log .rs-trail');
    const el = els[els.length - 1];
    if (!el || !m.pending) { rb.renderChat(); return; }
    el.querySelector('.rs-steps').replaceChildren(...m.steps.map(stepRow));
    const log = $('#chat-log');
    if (log.scrollHeight - log.scrollTop - log.clientHeight < 80) log.scrollTop = log.scrollHeight;
  }
  setInterval(() => {
    for (const t of document.querySelectorAll('.rs-time[data-start]')) t.textContent = `${Math.round((Date.now() - Number(t.dataset.start)) / 1000)} s`;
  }, 500);

  function renderMsg(m, i) {
    if (m.role !== 'assistant') return null;
    const head = (label) => h('div', { class: 'machine-head' }, h('span', { text: label }));
    if (m.error) {
      return h('div', { class: 'msg error' }, h('div', { class: 'msg-role', text: "Research couldn't finish" }), h('div', { class: 'msg-body', text: m.content }),
        (m.steps || []).length ? trail(m) : null,
        h('div', { class: 'actions' }, h('button', { class: 'btn btn-sm needs-claude', type: 'button', text: 'Retry', onclick: () => retry(i) })));
    }
    if (m.pending) {
      return h('div', { class: 'msg assistant' }, h('div', { class: 'machine streaming', 'aria-live': 'polite' },
        head(m.live ? `${window.RB_AI || 'Claude'}, writing…` : `${window.RB_AI || 'Claude'}, researching…`), trail(m),
        m.live ? h('div', { class: 'msg-body md rs-live', html: rb.partialMD(m.live) }) : null));
    }
    const src = (m.sources || []).length ? h('div', { class: 'sources' }, h('span', { class: 'lbl', text: 'Sources' }),
      m.sources.map((s) => h('button', { type: 'button', class: 'src', title: s.title, onclick: () => rb.openNote(s.id, { center: true }) },
        h('span', { class: 'tdot ' + rb.nodeType(s.id) }), h('span', { class: 't', text: s.title || s.id }),
        s.cited ? h('span', { class: 's', text: 'cited' }) : null))) : null;
    return h('div', { class: 'msg assistant' }, h('div', { class: 'machine' },
      head(`${window.RB_AI || 'Claude'}, researched`), trail(m),
      h('div', { class: 'msg-body md', html: renderMD(m.content) }), src, rb.chatPassages(m)));
  }
  function retry(i) {
    const m = S.chat[i];
    if (!m || !m.error || S.chatBusy) return;
    S.chat.splice(i - 1, 2);
    send(m.question);
  }

  // ====================================================================== commands (static/commands.js, if present)
  function register() {
    const reg = window.RB && window.RB.commands && window.RB.commands.register;
    if (!reg) return;
    const cmds = [
      { id: 'research.toggle', title: 'Toggle research mode', group: 'Research', keys: 'Alt+R', allowInInput: true,
        description: 'Switches Ask your library between Quick (one answer) and Research (the AI searches your library itself).',
        aliases: ['agent', 'deep', 'quick', 'mode', 'search library'],
        run: () => { setMode(R.mode === 'research' ? 'quick' : 'research'); rb.openChat(); toast(R.mode === 'research' ? `Research mode: ${window.RB_AI || 'Claude'} searches your library itself.` : 'Quick mode.', 'info'); } },
      { id: 'research.ask', title: 'Ask with research', group: 'Research',
        description: 'Sends the question in the chat box in Research mode, whatever the toggle says.',
        aliases: ['research question', 'investigate', 'deep search'],
        when: () => !S.chatBusy,
        run: () => {
          rb.openChat();
          const ta = $('#chat-input');
          const q = ta && ta.value.trim();
          if (!q) { setMode('research'); ta && ta.focus(); return; }
          ta.value = ''; ta.style.height = '';
          send(q);
        } },
    ];
    for (const c of cmds) { try { reg(c); } catch (e) { console.error(e); } }
  }
  register();

  window.__rbResearch = { wants, send, renderMsg, setMode, get mode() { return R.mode; }, get NOTICE() { return NOTICE(); } };
})();
