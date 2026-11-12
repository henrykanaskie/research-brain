/* research_brain — open questions.
 * A question is a note of type "question" that keeps collecting evidence (passages, notes, idea cards) as new
 * material arrives; the matching runs on this computer (app/questions.py) and never goes to an AI agent.
 * - Panel: the question in the display serif, its status (open / answered / parked), your answer (Markdown +
 *   LaTeX, with live preview), evidence grouped by verdict with triage buttons and keys (S supports, A against,
 *   R related, D dismiss, ↑/↓ or J/K to move; on a focused evidence row), "Mark all seen", "Look again".
 * - "New question" in the Add menu (command question.new, key Q) and the Questions list (questions.open, ⇧Q)
 *   with new-evidence badges; a quiet count on the "Questions" button in the top bar.
 * - Library settings: matching sensitivity and the daily cap (window.__rbQuestions.settingsSection).
 * Server: /api/questions*, notes API for the question itself (see API.md "Open questions"). Needs window.__rb.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.registerRenderer) return;
  const { S, h, api, toast, toastErr, truncate } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const enc = encodeURIComponent;
  const CMD = window.RB && window.RB.commands;

  const STATUSES = [['open', 'Open'], ['answered', 'Answered'], ['parked', 'Parked']];
  const VERDICTS = [['supports', 'Supports', 'S'], ['against', 'Against', 'A'], ['related', 'Related', 'R'], ['dismissed', 'Dismiss', 'D']];
  const KEY_VERDICT = { s: 'supports', a: 'against', r: 'related', d: 'dismissed' };
  const GROUPS = [['new', 'To triage'], ['supports', 'Supports'], ['against', 'Against'], ['related', 'Related'], ['dismissed', 'Dismissed']];
  const KIND = { passage: 'Passage', paper: 'Paper', note: 'Your note', card: 'Idea card', external: 'Published paper' };
  // how far a question may reach (app/scout.py)
  const REACH = [
    ['private', 'Private', 'Stays on this computer. Evidence comes from your library by local search; no AI agent and no outside service sees the question.'],
    ['library', 'Library', 'Evidence comes from your library by local search, and AI features (the evidence summary, Ask, Research) may read the question.'],
    ['scout', 'Scout', 'Also searches published papers about once a day: the question’s key words go to OpenAlex and arXiv (no AI). Papers that match closely arrive as evidence to triage. Don’t use for an unpublished idea.'],
    ['agent', 'Agent', 'Scout, plus a weekly AI agent search: your AI rewords the question, searches OpenAlex and arXiv, follows citations and proposes up to 5 papers with a reason each. Uses your AI plan (about one Research question per run).'],
  ];

  const STYLE = `
  .tdot.question { width: 7px; height: 7px; border: 1.5px solid var(--red); border-radius: 1px; transform: rotate(45deg); box-sizing: border-box; }
  #panel-body .title-input.oq-title { font-style: italic; }
  .oq-status { display: flex; align-items: center; gap: 14px; margin: 0 0 12px; flex-wrap: wrap; }
  .oq-status .oq-private { font-size: 12px; color: var(--ink-3); }
  .oq-reach-wrap { display: inline-flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .oq-scout-line { font-size: 12.5px; color: var(--ink-2); margin: 0 0 6px; }
  .oq-scout-line q { font-style: italic; color: var(--ink); }
  .oq-scout-err { color: var(--red); }
  .oq-ext { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 4px; }
  .oq-ext .oq-acc.open { color: var(--green, #3d7a52); }
  .oq-ai-why { font: 13px/1.5 var(--sans); color: var(--machine, var(--ink-2)); margin-top: 4px; }
  .oq-stance { font-weight: 600; }
  .oq-agent-note { font-style: italic; color: var(--ink-3); margin-top: 2px; }
  .oq-sec { margin-top: 28px; }
  .oq-sec > .sec-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
  .oq-sec > .sec-head .sp { flex: 1; }
  .oq-detail { width: 100%; box-sizing: border-box; min-height: 56px; font: 15px/1.55 var(--serif); color: var(--ink); background: transparent; border: 1px solid transparent; border-radius: var(--r); padding: 4px 6px; margin-left: -6px; resize: vertical; }
  .oq-detail:hover { background: var(--ground); }
  .oq-detail:focus { background: var(--sheet); border-color: var(--rule); outline: none; }
  .oq-answer textarea { width: 100%; box-sizing: border-box; min-height: 96px; }
  .oq-since { font-size: 12.5px; color: var(--red); font-variant-numeric: tabular-nums; }
  .oq-since.zero { color: var(--ink-3); }
  .oq-tools { display: flex; gap: 6px; flex-wrap: wrap; margin: 2px 0 6px; }
  .oq-tools .btn { height: 26px; font-size: 12.5px; }
  .oq-grp { margin-top: 14px; }
  .oq-grp > .g-head, .oq-grp > summary .g-head { display: flex; align-items: baseline; gap: 6px; font: 600 13px var(--sans); color: var(--ink); margin-bottom: 2px; }
  .oq-grp .g-head .n { font-weight: 400; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  details.oq-grp > summary { list-style: none; cursor: pointer; }
  details.oq-grp > summary::-webkit-details-marker { display: none; }
  details.oq-grp > summary .g-head { color: var(--ink-2); font-weight: 500; }
  .oq-ev { position: relative; padding: 10px 10px 10px 12px; margin: 0 -10px 0 -12px; border-top: 1px solid var(--rule); border-radius: 0; outline: none; }
  .oq-ev:first-of-type { border-top: 0; }
  .oq-ev:focus-visible, .oq-ev.focus { background: var(--ground); box-shadow: inset 2px 0 0 var(--ink); }
  .oq-ev .oq-src { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .oq-ev .oq-src .oq-open { all: unset; cursor: pointer; font: 14.5px/1.35 var(--serif); color: var(--ink); }
  .oq-ev .oq-src .oq-open:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--ink-2); }
  .oq-ev .oq-where { font-size: 12px; color: var(--machine); font-variant-numeric: tabular-nums; }
  .oq-ev .oq-kind { font-size: 11.5px; color: var(--ink-3); }
  .oq-ev .oq-score { font-size: 11.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; margin-left: auto; }
  .oq-ev .oq-new { width: 6px; height: 6px; border-radius: 50%; background: var(--red); display: inline-block; flex: none; transform: translateY(-1px); }
  .oq-ev .oq-snip { font: 14px/1.55 var(--serif); color: var(--ink-2); margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
  .oq-ev:focus-visible .oq-snip, .oq-ev.focus .oq-snip { -webkit-line-clamp: 12; }
  .oq-ev .oq-acts { display: flex; gap: 4px; margin-top: 8px; flex-wrap: wrap; align-items: center; }
  .oq-ev .oq-acts .btn { height: 24px; font-size: 12px; padding: 0 8px; }
  .oq-ev .oq-acts .btn.on { background: var(--ground); color: var(--ink); border-color: var(--ink-2); }
  .oq-ev .oq-acts .btn[data-v=supports].on { color: var(--moss); border-color: var(--moss); }
  .oq-ev .oq-acts .btn[data-v=against].on { color: var(--red); border-color: var(--red); }
  .oq-ev .oq-acts .btn[data-v=related].on { color: var(--violet); border-color: var(--violet); }
  .oq-ev .oq-acts kbd { font: 500 10.5px/1 var(--sans); color: var(--ink-3); border: 1px solid var(--rule); border-radius: 3px; padding: 1px 4px; margin-left: 5px; }
  .oq-ev .oq-note { flex: 1; min-width: 140px; height: 24px; font: italic 13px var(--serif); color: var(--ink); border: 1px solid transparent; background: transparent; padding: 0 6px; border-radius: var(--r); }
  .oq-ev .oq-note:hover { background: var(--sheet); }
  .oq-ev .oq-note:focus { border-color: var(--rule); background: var(--sheet); outline: none; }
  .oq-ev.v-supports { box-shadow: inset 2px 0 0 var(--moss); }
  .oq-ev.v-against { box-shadow: inset 2px 0 0 var(--red); }
  .oq-ev.v-related { box-shadow: inset 2px 0 0 var(--violet); }
  .oq-ev.v-dismissed .oq-snip, .oq-ev.v-dismissed .oq-open { color: var(--ink-3); }
  .oq-empty { font-size: 13px; color: var(--ink-2); line-height: 1.55; padding: 4px 0; }
  .oq-ai-off { font-size: 12.5px; color: var(--ink-2); line-height: 1.5; margin-top: 6px; }
  .oq-keys { font-size: 12px; color: var(--ink-3); margin-top: 10px; }
  #btn-questions { position: relative; }
  #btn-questions .oq-badge { margin-left: 6px; min-width: 16px; height: 16px; padding: 0 4px; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center;
    font: 500 11px/1 var(--sans); color: var(--sheet); background: var(--red); border-radius: 8px; font-variant-numeric: tabular-nums; }
  #btn-questions .oq-badge[hidden] { display: none; }
  .ql { display: flex; flex-direction: column; }
  .ql-tools { display: flex; gap: 8px; align-items: center; margin: 4px 0 12px; flex-wrap: wrap; }
  .ql-tools .sp { flex: 1; }
  .ql-row { all: unset; box-sizing: border-box; cursor: pointer; display: flex; align-items: baseline; gap: 12px; padding: 10px 8px; margin: 0 -8px; border-top: 1px solid var(--rule); }
  .ql-row:first-child { border-top: 0; }
  .ql-row:hover, .ql-row:focus-visible { background: var(--ground); }
  .ql-row .ql-t { flex: 1; min-width: 0; font: italic 16px/1.3 var(--display); color: var(--ink); font-variation-settings: "opsz" 18; }
  .ql-row .ql-sub { display: block; font: 12px var(--sans); font-style: normal; color: var(--ink-2); margin-top: 3px; font-variant-numeric: tabular-nums; }
  .ql-row .ql-n { flex: none; font: 500 12px var(--sans); color: var(--red); font-variant-numeric: tabular-nums; }
  .ql-row .ql-n.zero { color: var(--ink-3); font-weight: 400; }
  .ql-row.st-parked .ql-t, .ql-row.st-answered .ql-t { color: var(--ink-2); }
  .ql-sec { font-size: 12px; color: var(--ink-2); margin: 14px 0 2px; }
  .qs-row { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; font: 13px var(--sans); color: var(--ink); }
  .qs-row input[type=range] { flex: 1; min-width: 140px; accent-color: var(--ink); }
  .qs-row input[type=number] { width: 64px; height: 28px; padding: 0 6px; border: 1px solid var(--rule); border-radius: 3px; background: var(--sheet); color: var(--ink); font: 13px var(--sans); }
  .qs-row .val { font-variant-numeric: tabular-nums; color: var(--ink-2); min-width: 34px; }
  `;
  const style = document.createElement('style');
  style.id = 'rb-questions-style';
  style.textContent = STYLE;
  document.head.appendChild(style);

  // ====================================================================== state
  const Q = { evidence: new Map(), focus: null, total: 0, list: null, busy: new Set() };

  // ====================================================================== panel
  function statusRow(n) {
    const seg = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'Question status' });
    const draw = () => {
      const cur = (S.cur && S.cur.question_status) || 'open';
      seg.replaceChildren(...STATUSES.map(([v, label]) => h('button', {
        type: 'button', role: 'radio', 'aria-checked': String(cur === v), class: cur === v ? 'on' : '', 'data-qs': v, text: label,
        title: { open: 'Still collecting evidence', answered: 'You have an answer; evidence stops being collected', parked: 'Set aside for now; evidence stops being collected' }[v],
        onclick: () => { rb.queueSave({ question_status: v }); draw(); refreshBadge(800); },
      })));
    };
    draw();
    return h('div', { class: 'oq-status', id: 'oq-status' }, seg, reachControl(n));
  }

  /** Private / Library / Scout, with a line saying what the chosen one means. */
  function reachControl(n) {
    const seg = h('div', { class: 'seg oq-reach', role: 'radiogroup', 'aria-label': 'Who may see this question' });
    const say = h('span', { class: 'oq-private', id: 'oq-reach-say' });
    const cur0 = (Q.evidence.get(n.id) || {}).question;
    let cur = (cur0 && cur0.reach) || (n.private === false ? 'library' : 'private');
    const draw = () => {
      seg.replaceChildren(...REACH.map(([v, label, tip]) => h('button', {
        type: 'button', role: 'radio', 'aria-checked': String(cur === v), class: cur === v ? 'on' : '', 'data-reach': v, text: label, title: tip,
        onclick: () => setReach(n.id, v),
      })));
      say.textContent = { private: 'Only on this computer; no AI sees it', library: 'Your library; AI features may read it', scout: 'Your library, and new published papers daily', agent: 'Scout, plus a weekly AI search' }[cur];
    };
    async function setReach(qid, v) {
      if (v === cur) return;
      if (v === 'scout' && cur !== 'agent' && !confirm('Scout sends this question’s key words to OpenAlex and arXiv once a day to look for new papers. Nothing goes to an AI agent. Fine for a question you’d ask in public; keep an unpublished idea Private or Library. Turn on Scout?')) return;
      if (v === 'agent' && !confirm('Agent search sends this question’s title and description to your AI agent about once a week; the agent searches OpenAlex and arXiv (so the words also go there) and proposes papers. Each run uses about as much of your AI plan as one Research question. Your answer and notes are never sent. Turn on Agent search?')) return;
      try {
        await api('PUT', `/api/questions/${enc(qid)}/reach`, { reach: v });
        cur = v; draw();
        if (S.cur && S.cur.id === qid) S.cur.private = v === 'private';
        toast({ private: 'Private: this question stays on this computer.', library: 'Library: AI features may read this question.', scout: 'Scout on: the first search runs in a moment.', agent: 'Agent search on: the first run starts in a moment and takes a minute or two.' }[v], 'ok');
        if (v === 'scout' || v === 'agent') pollAgent(qid, 6000);
        loadEvidence(qid, { keepFocus: true });
      } catch (e) { toastErr(e, 'Could not change who may see this question'); }
    }
    draw();
    seg.__set = (v) => { cur = v; draw(); };
    return h('span', { class: 'oq-reach-wrap' }, seg, say);
  }

  function detailField(n) {
    const ta = h('textarea', { class: 'oq-detail', rows: 2, spellcheck: true, 'aria-label': 'What you mean',
      placeholder: 'What you mean, in a sentence or two. The words here are what the evidence is matched on.' });
    ta.value = n.body || '';
    const size = () => { ta.style.height = 'auto'; ta.style.height = Math.max(56, ta.scrollHeight + 2) + 'px'; };
    ta.addEventListener('input', () => { size(); rb.queueSave({ body: ta.value }); });
    setTimeout(size, 0);
    return ta;
  }

  function answerSection(n) {
    const ta = h('textarea', { class: 'field', rows: 4, spellcheck: true, 'aria-label': 'Your answer', id: 'oq-answer',
      placeholder: 'Your current answer or thinking. Markdown, $inline$ and $$display$$ math, [[links]] to notes.' });
    ta.value = n.answer || '';
    const live = h('div', { class: 'md live' });
    ta.addEventListener('input', () => rb.queueSave({ answer: ta.value }));
    rb.latex(ta, live);
    return h('div', { class: 'section oq-sec oq-answer', id: 'oq-answer-sec' },
      h('div', { class: 'sec-head' }, h('span', { class: 'sec-title', text: 'Your answer' })),
      h('div', { class: 'mine summary-box' }, ta), live);
  }

  function evidenceSection(n) {
    const sec = h('div', { class: 'section oq-sec', id: 'oq-evidence' },
      h('div', { class: 'sec-head' }, h('span', { class: 'sec-title', text: 'Evidence' }), h('span', { class: 'oq-since zero', id: 'oq-since' }), h('span', { class: 'sp' })),
      h('div', { class: 'oq-ev-body' }, rb.working ? rb.working('Loading evidence') : h('div', { class: 'oq-empty', text: 'Loading evidence' })));
    loadEvidence(n.id);
    return sec;
  }

  function aiSection(n) {
    const box = h('div', { class: 'section oq-sec', id: 'oq-ai' });
    box.__fill = (ev) => {
      const ai = (ev && ev.ai) || { available: false, reason: '' };
      const btn = h('button', { class: 'btn', type: 'button', text: 'Summarise the evidence', disabled: !ai.available,
        title: ai.available ? 'Ask your AI agent where the kept evidence stands' : ai.reason,
        onclick: () => summarise(n.id, btn, out) });
      const out = h('div');
      const dir = h('button', { class: 'btn', type: 'button', text: 'Suggest directions…', disabled: !ai.available, id: 'oq-directions',
        title: ai.available ? 'Gaps, hypotheses and experiments from the kept evidence. You see what would be sent first.' : ai.reason,
        onclick: () => window.__rbDirections && window.__rbDirections.open('question', n.id, n.title) });
      box.replaceChildren(h('div', { class: 'sec-head' }, h('span', { class: 'sec-title', text: 'AI' })),
        h('div', { class: 'oq-tools' }, btn, dir),
        ai.available ? null : h('div', { class: 'oq-ai-off', text: `Not available: ${ai.reason || 'this question is private.'}` }), out);
    };
    return box;
  }

  async function summarise(qid, btn, out) {
    btn.disabled = true;
    out.replaceChildren(h('div', { class: 'machine' }, rb.working ? rb.working('Reading your evidence') : 'Reading your evidence'));
    try {
      const r = await api('POST', `/api/questions/${enc(qid)}/summarize`);
      const left = r.left_out ? `${r.left_out} piece${r.left_out === 1 ? '' : 's'} from private notes left out` : '';
      out.replaceChildren(h('div', { class: 'machine' }, h('div', { class: 'machine-head' }, h('span', { text: `${window.RB_AI || 'AI'}'s summary, just now` }),
        left ? h('span', { text: left }) : null), rb.mdEl(r.text)));
    } catch (e) { out.replaceChildren(); toastErr(e, 'Could not summarise'); }
    finally { btn.disabled = false; }
  }

  function renderQuestion(n) {
    const title = rb.titleField(n);
    title.classList.add('oq-title');
    title.placeholder = 'Your question';
    title.setAttribute('aria-label', 'Question');
    const out = [statusRow(n), title, detailField(n), rb.renderLinked(n), answerSection(n), evidenceSection(n), aiSection(n),
      rb.section('Tags', null, rb.renderTags(n)), rb.renderBacklinks(n)];
    const danger = rb.renderDanger(n);
    const del = $('.btn-danger', danger);
    if (del) del.textContent = 'Delete question';
    out.push(danger);
    return out;
  }

  rb.registerRenderer('question', { label: 'Question', render: renderQuestion });

  // ---------------------------------------------------------------- evidence list
  async function loadEvidence(qid, { keepFocus = false } = {}) {
    let ev;
    try { ev = await api('GET', `/api/questions/${enc(qid)}/evidence`); }
    catch (e) {
      const body = S.cur && S.cur.id === qid && $('#oq-evidence .oq-ev-body');
      if (body) body.replaceChildren(h('div', { class: 'oq-empty', text: `Couldn’t load the evidence. ${e.message}` }));
      return;
    }
    Q.evidence.set(qid, ev);
    if (!S.cur || S.cur.id !== qid) return;
    const rseg = $('.oq-reach'); if (rseg && rseg.__set && ev.question.reach) rseg.__set(ev.question.reach);
    drawEvidence(qid, { keepFocus });
    const ai = $('#oq-ai'); if (ai && ai.__fill) ai.__fill(ev);
  }

  function drawEvidence(qid, { keepFocus = false, focusId = null } = {}) {
    const sec = $('#oq-evidence');
    const ev = Q.evidence.get(qid);
    if (!sec || !ev) return;
    const since = $('#oq-since', sec);
    const nNew = ev.counts.new;
    since.textContent = nNew ? `${nNew} new since you last looked` : (ev.counts.total ? 'Nothing new since you last looked' : '');
    since.classList.toggle('zero', !nNew);
    const body = $('.oq-ev-body', sec);
    const seenBtn = h('button', { class: 'btn btn-text', type: 'button', text: 'Mark all seen', id: 'oq-seen', disabled: !nNew,
      title: 'Clears the new count. Evidence you haven’t triaged stays under To triage.', onclick: () => markSeen(qid) });
    const again = h('button', { class: 'btn btn-text', type: 'button', text: 'Look again', id: 'oq-rescan',
      title: 'Search your library for this question now (on this computer)', onclick: () => rescan(qid, again) });
    const tools = [seenBtn, again];
    let scoutLine = null;
    if (ev.question.reach === 'scout' || ev.question.reach === 'agent') {
      const sc = ev.scout || {};
      const go = h('button', { class: 'btn btn-text', type: 'button', text: 'Search papers now', id: 'oq-scout',
        title: 'Search OpenAlex and arXiv for this question now', onclick: () => scoutNow(qid, go) });
      tools.push(go);
      const when = sc.last ? `last searched ${rb.ago ? rb.ago(sc.last) : new Date(sc.last).toLocaleString()}` : 'first search pending';
      scoutLine = h('div', { class: 'oq-scout-line', title: 'These words are what leaves your computer, to OpenAlex and arXiv. Edit the question to change them.' },
        h('span', { text: 'Scouting published papers for ' }), h('q', { text: sc.query || '' }),
        h('span', { text: ` · ${sc.online === false ? 'online lookups are off' : when}` }),
        sc.error ? h('span', { class: 'oq-scout-err', text: ` · ${sc.error}` }) : null);
    }
    let agentLine = null;
    if (ev.question.reach === 'agent') {
      const ag = ev.agent || {};
      const run = h('button', { class: 'btn btn-text', type: 'button', text: ag.running ? 'Agent searching…' : 'Run agent search', id: 'oq-agent', disabled: !!ag.running,
        title: 'Have your AI agent search for papers now (about one Research question of your plan)', onclick: () => agentNow(qid, run) });
      tools.push(run);
      const when = ag.running ? 'searching now' : ag.last ? `last run ${new Date(ag.last).toLocaleDateString()}: ${ag.lookups || 0} searches, ${ag.added || 0} new paper${ag.added === 1 ? '' : 's'}, about ${(ag.tokens || 0).toLocaleString()} tokens` : 'first run pending';
      agentLine = h('div', { class: 'oq-scout-line' }, h('span', { text: `AI agent search, weekly · ${when}` }),
        ag.error ? h('span', { class: 'oq-scout-err', text: ` · ${ag.error}` }) : null,
        ag.note && !ag.running ? h('div', { class: 'oq-agent-note', text: ag.note }) : null);
      if (ag.running) pollAgent(qid, 4000);
    }
    const kids = [h('div', { class: 'oq-tools' }, ...tools)];
    if (scoutLine) kids.push(scoutLine);
    if (agentLine) kids.push(agentLine);
    if (!ev.items.length) {
      kids.push(h('div', { class: 'oq-empty', text: 'No evidence yet. Passages, notes and idea cards that bear on this question appear here as you add papers and write. Matching runs on this computer.' }));
    }
    for (const [v, label] of GROUPS) {
      const items = ev.items.filter((i) => i.verdict === v);
      if (!items.length) continue;
      const head = h('div', { class: 'g-head' }, label, h('span', { class: 'n', text: String(items.length) }));
      const rows = items.map((it) => evRow(qid, it));
      kids.push(v === 'dismissed' ? h('details', { class: 'oq-grp', 'data-v': v }, h('summary', null, head), rows) : h('div', { class: 'oq-grp', 'data-v': v }, head, rows));
    }
    if (ev.items.length) kids.push(h('div', { class: 'oq-keys', text: 'On a piece of evidence: S supports, A against, R related, D dismiss; ↑ ↓ to move.' }));
    const prev = keepFocus && document.activeElement && document.activeElement.closest && document.activeElement.closest('.oq-ev');
    const prevId = focusId || (prev && prev.dataset.eid);
    body.replaceChildren(...kids);
    if (prevId) { const r = $(`.oq-ev[data-eid="${CSS.escape(prevId)}"]`, body); if (r) r.focus({ preventScroll: false }); }
  }

  function evRow(qid, it) {
    const src = it.source || {};
    const openSrc = () => {
      if (it.href && /^\/viewer\//.test(it.href)) { window.open(it.href, '_blank', 'noopener'); return; }
      if (it.href && /^https?:/i.test(it.href)) { window.open(it.href, '_blank', 'noopener,noreferrer'); return; }
      if (src.exists) rb.openNote(src.id, { center: true });
    };
    if (src.external && src.exists) it = { ...it, href: '' }; // added to the library: open the note instead
    const paperOf = src.paper ? h('span', { class: 'oq-kind', text: `in ${truncate(src.paper.title, 60)}` })
      : src.external ? h('span', { class: 'oq-kind', text: [(src.authors || [])[0] ? `${src.authors[0]}${(src.authors || []).length > 1 ? ' et al.' : ''}` : '', src.year, src.venue].filter(Boolean).join(' · ') }) : null;
    const ext = src.external ? externalActs(qid, it, src) : null;
    const where = it.where
      ? (it.href ? h('a', { class: 'oq-where', href: it.href, target: '_blank', rel: 'noopener noreferrer', text: it.where, title: 'Open the source at this place' })
        : h('span', { class: 'oq-where', text: it.where }))
      : null;
    const acts = VERDICTS.map(([v, label, key]) => h('button', {
      class: 'btn' + (it.verdict === v ? ' on' : ''), type: 'button', 'data-v': v, tabindex: -1,
      title: it.verdict === v ? `Marked ${label.toLowerCase()}. Click to put it back under To triage.` : `${label} (${key})`,
      onclick: (e) => { e.stopPropagation(); triage(qid, it.id, it.verdict === v ? 'new' : v); },
    }, label, h('kbd', { text: key })));
    const note = h('input', { class: 'oq-note', type: 'text', maxlength: 2000, placeholder: it.verdict === 'new' ? 'Why, in a few words' : 'Your note on this', 'aria-label': 'Your note on this evidence', value: it.user_note || '' });
    note.value = it.user_note || '';
    let noteT = null;
    note.addEventListener('input', () => { clearTimeout(noteT); noteT = setTimeout(() => saveNote(qid, it.id, note.value), 700); });
    note.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(noteT); saveNote(qid, it.id, note.value); note.closest('.oq-ev').focus(); } });
    const row = h('div', { class: `oq-ev v-${it.verdict}`, tabindex: 0, 'data-eid': it.id, 'data-kind': it.kind, role: 'group',
      'aria-label': `${KIND[it.kind] || 'Evidence'} from ${src.title || ''}` },
      h('div', { class: 'oq-src' },
        it.new ? h('span', { class: 'oq-new', title: 'New since you last looked' }) : null,
        h('button', { class: 'oq-open', type: 'button', tabindex: -1, text: truncate(src.title || it.title || '(deleted)', 110), onclick: openSrc, title: src.exists ? 'Open the source' : 'The source was deleted' }),
        where, paperOf,
        h('span', { class: 'oq-kind', text: KIND[it.kind] || it.kind }),
        h('span', { class: 'oq-score', title: 'How closely it matches your question (local similarity)', text: (Number(it.score) || 0).toFixed(2) })),
      it.ai && it.ai.why ? h('div', { class: 'oq-ai-why', title: 'Why the AI agent proposed it. The verdict is yours.' },
        h('span', { class: `oq-stance ${it.ai.stance}`, text: `AI: ${it.ai.stance}` }), ` ${it.ai.why}`) : null,
      h('div', { class: 'oq-snip', text: it.snippet || '' }),
      ext,
      h('div', { class: 'oq-acts' }, acts, note));
    row.addEventListener('keydown', (e) => onRowKey(e, qid, it, row));
    row.addEventListener('focus', () => { Q.focus = it.id; });
    return row;
  }

  /** A published paper the scout found: how readable it is, and Add to library (linked to the question). */
  function externalActs(qid, it, src) {
    const acc = { open: 'Free to read', free: 'Free on the publisher’s site', closed: 'Paywalled' }[src.access];
    const bits = [h('span', { class: 'oq-kind', text: src.found_via === 'AI search' ? 'Found by AI search' : `Found on ${src.found_via || 'the web'}` })];
    if (acc) bits.push(h('span', { class: `oq-kind oq-acc ${src.access}`, text: acc }));
    if (src.exists) bits.push(h('button', { class: 'btn btn-sm btn-text', type: 'button', text: 'In your library', onclick: () => rb.openNote(src.id, { center: true }) }));
    else {
      const add = h('button', { class: 'btn btn-sm', type: 'button', text: 'Add to library',
        title: 'Add it with its metadata (and a free PDF when one exists), linked to this question' });
      add.onclick = async (e) => {
        e.stopPropagation();
        add.disabled = true; add.textContent = 'Adding';
        try {
          const q = src.doi || (src.arxiv ? 'arXiv:' + src.arxiv : it.title);
          const note = await api('POST', '/api/import/lookup', { query: q, link_to: qid });
          toast(`Added “${truncate(note.title || it.title, 60)}”.`, 'ok', { action: { label: 'Open', fn: () => rb.openNote(note.id, { center: true }) } });
          loadEvidence(qid, { keepFocus: true });
        } catch (err) { add.disabled = false; add.textContent = 'Add to library'; toastErr(err, 'Could not add it'); }
      };
      bits.push(add);
    }
    return h('div', { class: 'oq-ext' }, ...bits);
  }

  let pollT = null;
  function pollAgent(qid, ms) {
    clearTimeout(pollT);
    pollT = setTimeout(() => { if (S.cur && S.cur.id === qid) loadEvidence(qid, { keepFocus: true }); }, ms);
  }
  async function agentNow(qid, btn) {
    btn.disabled = true; btn.textContent = 'Agent searching…';
    try {
      await api('POST', `/api/questions/${enc(qid)}/agent-search`);
      toast('Agent search started. Papers it proposes appear under To triage in a minute or two.', 'ok');
      pollAgent(qid, 3000);
    } catch (e) { btn.disabled = false; btn.textContent = 'Run agent search'; toastErr(e, 'Could not start the agent search'); }
  }

  async function scoutNow(qid, btn) {
    btn.disabled = true; btn.textContent = 'Searching';
    try {
      const r = await api('POST', `/api/questions/${enc(qid)}/scout`);
      toast(r.added ? `Found ${r.added} new paper${r.added === 1 ? '' : 's'} for this question.` : `Looked at ${r.looked_at} papers; nothing new matched closely enough.`, r.added ? 'ok' : 'info');
      await loadEvidence(qid, { keepFocus: true });
    } catch (e) { toastErr(e, 'Could not search published papers'); }
    finally { if (btn.isConnected) { btn.disabled = false; btn.textContent = 'Search papers now'; } }
  }

  function onRowKey(e, qid, it, row) {
    if (e.target !== row || e.metaKey || e.ctrlKey || e.altKey) return; // typing in the note field: leave it alone
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (KEY_VERDICT[k] && !e.shiftKey) {
      e.preventDefault(); // the command registry skips handled keys
      triage(qid, it.id, it.verdict === KEY_VERDICT[k] ? 'new' : KEY_VERDICT[k], { fromKey: true });
      return;
    }
    if (k === 'ArrowDown' || k === 'j' || k === 'ArrowUp' || k === 'k') {
      e.preventDefault();
      const rows = $$('#oq-evidence .oq-ev').filter((r) => r.offsetParent !== null);
      const i = rows.indexOf(row) + (k === 'ArrowDown' || k === 'j' ? 1 : -1);
      if (rows[i]) rows[i].focus();
      return;
    }
    if (k === 'Enter') { e.preventDefault(); const b = $('.oq-open', row); if (b) b.click(); }
  }

  async function triage(qid, eid, verdict, { fromKey = false } = {}) {
    const ev = Q.evidence.get(qid);
    if (!ev) return;
    const it = ev.items.find((x) => x.id === eid);
    if (!it) return;
    // where the focus goes next: the next row still to triage (keyboard flow), else stay on this one
    let next = null;
    if (fromKey && it.verdict === 'new') {
      const pending = ev.items.filter((x) => x.verdict === 'new');
      const i = pending.findIndex((x) => x.id === eid);
      next = (pending[i + 1] || pending[i - 1] || {}).id || null;
    }
    const before = it.verdict;
    it.verdict = verdict;
    if (verdict !== 'new') it.new = false;
    drawEvidence(qid, { focusId: next || eid });
    try {
      const r = await api('PUT', `/api/questions/${enc(qid)}/evidence/${enc(eid)}`, { verdict });
      ev.counts = r.counts;
      Object.assign(it, r.item);
      drawEvidence(qid, { keepFocus: true }); // keep whatever row has focus now (the user may have moved on)
      refreshBadge(0);
      if (rb.refreshGraph) setTimeout(rb.refreshGraph, 600); // kept evidence draws question links
    } catch (e) {
      it.verdict = before;
      drawEvidence(qid, { focusId: eid });
      toastErr(e, 'Could not save that');
    }
  }

  async function saveNote(qid, eid, text) {
    try {
      const r = await api('PUT', `/api/questions/${enc(qid)}/evidence/${enc(eid)}`, { user_note: text });
      const ev = Q.evidence.get(qid); const it = ev && ev.items.find((x) => x.id === eid);
      if (it) it.user_note = r.item.user_note;
    } catch (e) { toastErr(e, 'Could not save your note'); }
  }

  async function markSeen(qid) {
    try {
      const r = await api('POST', `/api/questions/${enc(qid)}/seen`);
      if (S.cur && S.cur.id === qid) S.cur.last_seen = r.last_seen;
      const ev = Q.evidence.get(qid);
      if (ev) { ev.counts = r.counts; for (const it of ev.items) it.new = false; }
      drawEvidence(qid);
      refreshBadge(0);
    } catch (e) { toastErr(e, 'Could not mark it seen'); }
  }

  async function rescan(qid, btn) {
    if (Q.busy.has(qid)) return;
    Q.busy.add(qid);
    if (btn) { btn.disabled = true; btn.textContent = 'Looking'; }
    try {
      await rb.flushSave();
      const r = await api('POST', `/api/questions/${enc(qid)}/rescan`);
      toast(r.added ? `${r.added} new piece${r.added === 1 ? '' : 's'} of evidence` : 'Nothing new that clears the bar', 'info', { ms: 2200 });
      await loadEvidence(qid, { keepFocus: true });
      refreshBadge(0);
    } catch (e) { toastErr(e, 'Could not look for evidence'); }
    finally { Q.busy.delete(qid); if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Look again'; } }
  }

  // ====================================================================== new question
  async function newQuestion() {
    rb.closeMenus && rb.closeMenus();
    const pb = $('#panel-body');
    if (document.activeElement && pb && pb.contains(document.activeElement)) document.activeElement.blur();
    await rb.flushSave();
    if (pb) pb.inert = true;
    try {
      const note = await api('POST', '/api/notes', { type: 'question', title: 'Untitled question' });
      await rb.refreshGraph();
      if (pb) pb.inert = false;
      await rb.openNote(note.id, { center: true, focusTitle: true });
      refreshBadge(0);
    } catch (e) { toastErr(e, 'Could not create the question'); }
    finally { if (pb) pb.inert = false; }
  }

  const addMenuBtn = $('#btn-new-question');
  if (addMenuBtn) addMenuBtn.addEventListener('click', () => newQuestion());

  // ====================================================================== top-bar button + badge
  const badge = h('span', { class: 'oq-badge', hidden: true, 'aria-label': 'new evidence' });
  const topBtn = h('button', { id: 'btn-questions', class: 'btn btn-text', type: 'button', title: 'Your open questions and new evidence for them' }, 'Questions', badge);
  const anchor = $('#btn-topics');
  if (anchor) anchor.after(topBtn); else { const a = $('#btn-review') || $('#btn-chat'); if (a) a.before(topBtn); }
  topBtn.addEventListener('click', () => openList());

  let badgeT = null;
  function refreshBadge(delay = 0) {
    clearTimeout(badgeT);
    badgeT = setTimeout(async () => {
      try {
        const r = await api('GET', '/api/questions');
        Q.list = r.questions;
        Q.total = r.total_new || 0;
        badge.hidden = !Q.total;
        badge.textContent = Q.total > 99 ? '99+' : String(Q.total);
        topBtn.title = Q.total ? `Your open questions: ${Q.total} new piece${Q.total === 1 ? '' : 's'} of evidence` : 'Your open questions and new evidence for them';
        if (listBody && listBody.isConnected) drawList();
      } catch { /* offline: keep the last count */ }
    }, delay);
  }
  refreshBadge(300);
  // evidence arrives in the background (the matcher waits for things to go quiet), so check now and then
  const tick = () => {
    if (document.visibilityState !== 'visible') return;
    refreshBadge(0);
    const pb = $('#panel-body');
    if (S.cur && S.cur.type === 'question' && !$('#panel').hidden && !(pb && pb.contains(document.activeElement))) loadEvidence(S.cur.id);
  };
  setInterval(tick, 45000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') setTimeout(tick, 200); });

  // ====================================================================== the Questions list
  let listBody = null;
  function openList() {
    if (!CMD || !CMD.panel) return;
    CMD.panel({ title: 'Open questions', wide: true, className: 'ql-sheet', render: (body) => { listBody = body; drawList(); refreshBadge(0); } });
  }
  function drawList() {
    if (!listBody) return;
    const rows = Q.list;
    const tools = h('div', { class: 'ql-tools' },
      h('span', { class: 'oq-empty', style: 'padding:0', text: 'Each question collects evidence as you add papers and write. Matching runs on this computer.' }),
      h('span', { class: 'sp' }),
      h('button', { class: 'btn btn-primary', type: 'button', text: 'New question', onclick: () => { CMD.close(false); newQuestion(); } }));
    if (!rows) { listBody.replaceChildren(tools, h('div', { class: 'oq-empty', text: 'Loading' })); return; }
    const kids = [tools];
    if (!rows.length) kids.push(h('div', { class: 'oq-empty', text: 'No questions yet. Write down something you want to find out, for example “Could the breathing-mode frequency be predicted from the CRM outputs?”' }));
    let lastSt = null;
    for (const q of rows) {
      if (q.question_status !== lastSt) {
        lastSt = q.question_status;
        kids.push(h('div', { class: 'ql-sec', text: { open: 'Open', parked: 'Parked', answered: 'Answered' }[lastSt] || lastSt }));
      }
      const c = q.counts || {};
      const kept = (c.supports || 0) + (c.against || 0) + (c.related || 0);
      const sub = [kept ? `${kept} kept` : '', c.untriaged ? `${c.untriaged} to triage` : '', q.answered ? 'answer written' : ''].filter(Boolean).join(', ');
      kids.push(h('button', { class: `ql-row st-${q.question_status}`, type: 'button', 'data-id': q.id,
        onclick: () => { CMD.close(false); rb.openNote(q.id, { center: true }); } },
        h('span', { class: 'ql-t' }, q.title, sub ? h('span', { class: 'ql-sub', text: sub }) : null),
        h('span', { class: 'ql-n' + (q.new_evidence ? '' : ' zero'), title: 'New evidence since you last looked', text: q.new_evidence ? `${q.new_evidence} new` : '' })));
    }
    listBody.replaceChildren(...kids);
  }

  // ====================================================================== settings (Library settings › Open questions)
  function settingsSection(say) {
    const sec = h('section', { class: 'ls-sec', id: 'ls-questions' },
      h('div', { class: 'ls-h', text: 'Open questions' }),
      h('p', { class: 'ls-p', text: 'Evidence for your questions is found on this computer, with the same local search as the rest of the library. Each question has a reach: Private (the default: only this computer), Library (AI features may read it), Scout (also searches published papers daily on OpenAlex and arXiv, with no AI) or Agent (Scout plus a weekly AI agent search).' }));
    const body = h('div', null, h('div', { class: 'ls-p', text: 'Loading' }));
    sec.append(body);
    api('GET', '/api/questions/settings').then((s) => {
      const key = s.family === 'tfidf' ? 'threshold_tfidf' : 'threshold_dense';
      const range = h('input', { type: 'range', min: '0.03', max: '0.8', step: '0.01', 'aria-label': 'Matching threshold', value: String(s[key]) });
      range.value = String(s[key]);
      const val = h('span', { class: 'val', text: Number(s[key]).toFixed(2) });
      const cap = h('input', { type: 'number', min: '1', max: '100', step: '1', 'aria-label': 'New evidence per question per day', value: String(s.daily_cap) });
      cap.value = String(s.daily_cap);
      let t = null;
      const save = () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          try { await api('PUT', '/api/questions/settings', { [key]: Number(range.value), daily_cap: Number(cap.value) }); if (say) say('Saved. New evidence uses these settings from now on.'); }
          catch (e) { if (say) say(e.message, true); }
        }, 400);
      };
      range.addEventListener('input', () => { val.textContent = Number(range.value).toFixed(2); save(); });
      cap.addEventListener('change', save);
      body.replaceChildren(
        h('div', { class: 'qs-row' }, h('span', { text: 'Match threshold' }), range, val),
        h('p', { class: 'ls-p', text: `Higher means fewer, closer matches. Default ${Number(s.defaults[key]).toFixed(2)} for the ${s.family === 'tfidf' ? 'built-in TF-IDF' : 'sentence-transformers'} search this library uses.` }),
        h('div', { class: 'qs-row' }, h('span', { text: 'At most' }), cap, h('span', { text: 'new pieces of evidence per question per day' })));
    }).catch((e) => body.replaceChildren(h('div', { class: 'ls-p', text: `Couldn’t load: ${e.message}` })));
    return sec;
  }

  // ====================================================================== commands
  const curQuestion = () => !!(S.cur && S.cur.type === 'question' && !$('#panel').hidden);
  if (CMD) {
    CMD.register({ id: 'question.new', title: 'New question', group: 'Library', keys: 'Q',
      description: 'Write down an open question; it collects evidence from your library as new material arrives.',
      aliases: ['question', 'open question', 'ask myself', 'hypothesis', 'idea', 'wonder'], run: () => newQuestion() });
    CMD.register({ id: 'questions.open', title: 'Open questions', group: 'Library', keys: 'Shift+Q',
      description: 'Your questions, with how much new evidence each has collected.',
      aliases: ['questions', 'evidence', 'hypotheses', 'list questions'], run: () => openList() });
    CMD.register({ id: 'question.seen', title: 'Mark all evidence seen', group: 'Note',
      description: 'Clears the open question’s “new since you last looked” count.', aliases: ['seen', 'read', 'clear new'],
      when: curQuestion, run: () => markSeen(S.cur.id) });
    CMD.register({ id: 'question.rescan', title: 'Look for evidence again', group: 'Note',
      description: 'Searches your library for the open question now. Matching runs on this computer.', aliases: ['rescan', 'refresh', 'search evidence'],
      when: curQuestion, run: () => rescan(S.cur.id, $('#oq-rescan')) });
    CMD.register({ id: 'question.answer', title: 'Write your answer', group: 'Note',
      description: 'Puts the cursor in the open question’s answer.', aliases: ['answer', 'thinking', 'conclusion'],
      when: curQuestion, run: () => { const t = $('#oq-answer'); if (t) { t.focus(); t.scrollIntoView({ block: 'center', behavior: 'smooth' }); } } });
    CMD.register({ id: 'question.triage', title: 'Triage evidence', group: 'Note',
      description: 'Focuses the first piece of evidence still to triage; then S, A, R or D.', aliases: ['triage', 'evidence', 'next evidence'],
      when: curQuestion, run: () => { const r = $('#oq-evidence .oq-grp[data-v="new"] .oq-ev') || $('#oq-evidence .oq-ev'); if (r) { r.focus(); r.scrollIntoView({ block: 'center', behavior: 'smooth' }); } else toast('No evidence to triage yet'); } });
    // the triage keys are handled on the focused evidence row (listed here for the shortcuts sheet)
    for (const [v, label, key] of VERDICTS) {
      CMD.register({ id: `question.${v === 'dismissed' ? 'dismiss' : v}`, title: `Evidence: ${label.toLowerCase()}`, group: 'Note', keys: key, scope: 'question', passive: true,
        description: v === 'dismissed' ? 'On a focused piece of evidence: dismiss it. It never comes back.' : `On a focused piece of evidence: keep it as ${label.toLowerCase()}. Press again to undo.`,
        aliases: ['triage', 'evidence', label.toLowerCase()] });
    }
    CMD.tooltip(topBtn, 'questions.open', 'Your open questions and new evidence for them');
    if (addMenuBtn) CMD.tooltip(addMenuBtn, 'question.new');
  }

  window.__rbQuestions = { open: openList, newQuestion, refreshBadge, settingsSection, loadEvidence, get total() { return Q.total; }, state: Q };
})();
