/* research_brain — bulk imports: a folder of PDFs, or a browser's bookmarks.
 * - Folder: the browser's own folder picker (<input webkitdirectory>), so nothing on the computer is read
 *   that you didn't pick. Each PDF is uploaded to /api/import/pdf?quick=1 (no Semantic Scholar lookup per
 *   file: the background metadata resolver identifies them), two at a time, with ?topic= so the folder
 *   structure becomes topics: one per subfolder, or all in one topic named after the folder, or none.
 *   The same file twice is stored once (app/papers.py).
 * - Bookmarks: the HTML file every browser exports (Chrome/Edge: Bookmarks › Export; Firefox: Library ›
 *   Import and Backup › Export; Safari: File › Export Bookmarks). It's parsed here, in the page; you pick
 *   which folders and links to import, each folder becoming a topic. Links with a DOI or arXiv id become
 *   papers from their records; links to PDFs are downloaded; other links are left unticked.
 * Needs window.__rb (h, api, upload, toast, refreshGraph).
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.h) return;
  const { h, api, toast, toastErr, truncate } = rb;
  const enc = encodeURIComponent;

  const CSS = `
  .bk-wrap { position: fixed; inset: 0; z-index: 72; background: color-mix(in srgb, var(--ground) 70%, transparent); display: flex; align-items: center; justify-content: center; padding: 16px; }
  .bk-card { width: min(620px, 100%); max-height: min(86vh, 900px); display: flex; flex-direction: column; overflow-y: auto; overscroll-behavior: contain; background: var(--sheet); border: 1px solid var(--rule); padding: 20px 22px 16px; }
  /* on a small window the lists give up their height first (they scroll inside), then the whole card scrolls,
     with its buttons pinned to the bottom edge (bottom: -16px and the margins cover the card's bottom padding) */
  .bk-card > *, .bk-step > * { flex: none; }
  .bk-card > .bk-list, .bk-step > .bk-list { flex: 0 1 auto; min-height: 0; }
  /* a long list keeps a few rows in view (a short one is never stretched to this) */
  .bk-card > .bk-list:has(> :nth-child(5)), .bk-step > .bk-list:has(> :nth-child(5)) { min-height: 96px; }
  .bk-step { display: flex; flex-direction: column; flex: 0 1 auto; min-height: 0; }
  .bk-step[hidden] { display: none; }
  .bk-card > .bk-acts { position: sticky; bottom: -16px; z-index: 1; background: var(--sheet); margin: 0 -22px -16px; padding: 4px 22px 16px; }
  .bk-card h2 { font: 600 19px/1.3 var(--serif); margin: 0 0 6px; }
  .bk-card p { color: var(--ink-2); line-height: 1.55; margin: 0 0 10px; font-size: 14px; }
  .bk-opts { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 12px; font-size: 14px; }
  .bk-opts label { display: flex; gap: 8px; align-items: baseline; }
  .bk-opts[role=radiogroup] { gap: 2px; }
  .bk-opts[role=radiogroup] > label { color: var(--ink-2); padding: 6px 8px; margin: 0 -9px; border: 1px solid transparent; border-radius: var(--r); cursor: pointer; }
  .bk-opts[role=radiogroup] > label:hover { color: var(--ink); background: color-mix(in srgb, var(--ink) 4%, transparent); }
  .bk-opts[role=radiogroup] > label:has(input:checked) { color: var(--ink); font-weight: 500; border-color: var(--ink); background: color-mix(in srgb, var(--ink) 6%, transparent); }
  .bk-opts input[type=radio], .bk-opts input[type=checkbox] { accent-color: var(--ink); }
  .bk-list { overflow: auto; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 6px 0; margin-bottom: 12px; font-size: 13.5px; }
  .bk-folder { margin: 6px 0 2px; font-weight: 600; display: flex; gap: 8px; align-items: baseline; }
  .bk-folder .n, .bk-item .why { color: var(--ink-3); font-weight: 400; font-size: 12px; }
  .bk-item { display: flex; gap: 8px; align-items: baseline; padding: 1px 0 1px 18px; }
  .bk-link { display: flex; flex-direction: column; }
  .bk-lrow { display: grid; grid-template-columns: minmax(0, 1.3fr) 44px minmax(0, 1fr) 118px; gap: 8px; align-items: center; padding: 3px 0; }
  .bk-lhead { color: var(--ink-3); font-size: 12px; }
  .bk-ldir { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
  .bk-lrow .n { text-align: right; color: var(--ink-3); }
  .bk-name { font: inherit; padding: 3px 6px; border: 1px solid var(--rule); background: var(--ground); color: var(--ink); min-width: 0; }
  .bk-acts-l { justify-content: flex-start; margin-bottom: 8px; }
  @media (max-width: 560px) { .bk-lrow { grid-template-columns: minmax(0, 1fr) 36px minmax(0, 1fr); } .bk-lrow > :last-child { display: none; } }
  .bk-item .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; flex: 1; }
  .bk-acts { display: flex; justify-content: flex-end; gap: 8px; align-items: center; }
  .bk-acts .bk-status { margin-right: auto; color: var(--ink-2); font-size: 13px; font-variant-numeric: tabular-nums; }
  .bk-bar { height: 2px; background: var(--rule); margin: 0 0 12px; }
  .bk-sub { font: 600 13px/1.3 var(--sans); color: var(--ink-2); margin: 10px 0 4px; }
  .bk-sections { width: 100%; min-height: 84px; resize: vertical; font: inherit; font-size: 14px; padding: 6px 8px; border: 1px solid var(--rule); background: var(--ground); color: var(--ink); box-sizing: border-box; margin: 2px 0 6px; }
  .bk-group { border-bottom: 1px solid var(--rule); padding: 6px 0; }
  .bk-group > .bk-ghead { display: grid; grid-template-columns: minmax(0, 1fr) 44px 110px; gap: 8px; align-items: center; }
  .bk-group .n { text-align: right; color: var(--ink-3); font-variant-numeric: tabular-nums; }
  .bk-group details { font-size: 13px; color: var(--ink-2); margin: 3px 0 0; }
  .bk-group details summary { cursor: pointer; }
  .bk-group details li { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bk-group ul { margin: 4px 0 2px; padding-left: 18px; }
  .bk-note { color: var(--ink-2); font-size: 13px; line-height: 1.5; margin: 8px 0 0; }
  .bk-subopts { display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center; margin: -2px 0 4px 26px; font-size: 13.5px; color: var(--ink-2); }
  .bk-subopts label { display: flex; gap: 6px; align-items: center; }
  .bk-count { width: 58px; font: inherit; padding: 2px 6px; border: 1px solid var(--rule); background: var(--ground); color: var(--ink); }
  .bk-count:disabled { opacity: .5; }
  .bk-bar i { display: block; height: 100%; width: 0; background: var(--ink-2); transition: width .2s; }
  `;
  document.head.appendChild(h('style', { id: 'rb-bulk-style', text: CSS }));

  function dialog(title, intro) {
    const card = h('div', { class: 'bk-card', role: 'dialog', 'aria-modal': 'true', 'aria-label': title }, h('h2', { text: title }), intro ? h('p', { text: intro }) : null);
    const wrap = h('div', { class: 'bk-wrap' }, card);
    let busy = false;
    const close = () => { if (!busy) { wrap.remove(); document.removeEventListener('keydown', onKey, true); } };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
    document.body.appendChild(wrap);
    return { card, close, setBusy: (b) => { busy = b; } };
  }

  /** Run fn over items, `n` at a time; onEach(done) after each. */
  async function pool(items, n, fn, onEach) {
    let i = 0, done = 0;
    const worker = async () => { while (i < items.length) { const it = items[i++]; await fn(it); onEach(++done); } };
    await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  }

  /** How the AI sorts: a number of topics (blank: it decides), and, with `existing`, "only into my existing
   *  topics" (shown once your topics have loaded, if you have any). value() -> {count, existing_only}. */
  function sortChoices(existing = true) {
    const count = h('input', { type: 'number', class: 'bk-count', min: '1', max: '40', step: '1', placeholder: 'any', 'aria-label': 'Number of regions' });
    const only = h('input', { type: 'checkbox' });
    const onlyLabel = h('label', { hidden: true }, only, h('span', { text: 'Only into my existing regions' }));
    only.addEventListener('change', () => { count.disabled = only.checked; });
    const row = h('div', { class: 'bk-subopts' }, h('label', null, 'Number of regions', count, h('span', { class: 'why', text: 'blank: the AI decides' })), existing ? onlyLabel : null);
    if (existing) api('GET', '/api/topics').then((ts) => {
      if (ts && ts.length) { onlyLabel.lastChild.textContent = `Only into my existing regions (${ts.length})`; onlyLabel.hidden = false; }
    }).catch(() => {});
    return {
      row,
      value: () => ({ count: only.checked ? 0 : Math.max(0, Math.min(40, parseInt(count.value, 10) || 0)), existing_only: only.checked }),
      disable: (b) => { count.disabled = b || only.checked; only.disabled = b; },
    };
  }
  /** What sorting did, in a sentence. */
  function sortedText(r, sc) {
    if (!r.sorted) return r.note || (sc && sc.existing_only ? 'None of them fit your existing regions.' : '');
    const list = (r.topics || []).map((t) => `${t.name} (${t.count}${t.new ? ', new' : ''})`).join(', ');
    return `Sorted into ${plural(r.topics.length, 'region')}${r.by === 'regions' ? ' by similarity (no AI answer)' : ''}: ${list}.`
      + (r.unplaced ? ` ${plural(r.unplaced, 'paper')} fit none of your regions and have none.` : '');
  }

  // ---------------------------------------------------------------- a folder of PDFs
  const picker = h('input', { type: 'file', multiple: true, hidden: true, id: 'bulk-folder-input' });
  picker.setAttribute('webkitdirectory', '');
  document.body.appendChild(picker);
  picker.addEventListener('change', () => { const fs = Array.from(picker.files || []); picker.value = ''; if (fs.length) folderDialog(fs); });

  function topicFor(file, mode, root) {
    const parts = (file.webkitRelativePath || file.name).split('/');
    if (mode === 'none') return '';
    if (mode === 'one') return root;
    return parts.length > 2 ? parts[1] : root; // a file directly in the folder goes to the folder's topic
  }

  function folderDialog(all) {
    const pdfs = all.filter((f) => /\.pdf$/i.test(f.name) && f.size > 0);
    const root = ((all[0] && all[0].webkitRelativePath) || '').split('/')[0] || 'Imported';
    const subs = new Set(pdfs.map((f) => (f.webkitRelativePath || '').split('/')).filter((p) => p.length > 2).map((p) => p[1]));
    const d = dialog(`Import “${root}”`, pdfs.length
      ? `${pdfs.length} PDF${pdfs.length === 1 ? '' : 's'}${subs.size ? ` in ${subs.size} subfolder${subs.size === 1 ? '' : 's'}` : ''}. Each is copied into your papers folder; a file you already have is skipped. Titles, authors and DOIs are filled in over the next minutes.`
      : 'There are no PDFs in that folder.');
    if (!pdfs.length) { d.card.append(h('div', { class: 'bk-acts' }, h('button', { class: 'btn', type: 'button', text: 'Close', onclick: d.close }))); return; }
    const mode = (v) => h('input', { type: 'radio', name: 'bk-topic', value: v });
    const opts = [['folders', subs.size ? `A region for each subfolder (${[...subs].slice(0, 3).join(', ')}${subs.size > 3 ? '…' : ''}), like bookmark folders` : `One region, “${root}”`],
      ['ai', 'Let the AI sort them into regions by what they’re about (it reads each paper’s title and opening, and reuses your regions where they fit)'],
      ['one', `Everything in one region, “${root}”`], ['none', 'No regions']];
    const radios = opts.map(([v]) => mode(v));
    (subs.size ? radios[0] : radios[1]).checked = true;  // subfolders: keep them; one flat folder: let the AI sort it
    const status = h('span', { class: 'bk-status' });
    const bar = h('i');
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: `Import ${pdfs.length}` });
    const cancel = h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => { stop = true; d.close(); } });
    let stop = false;
    const conn = connectBox(pdfs.length > 1);
    const sc = sortChoices();
    const onMode = () => { sc.row.hidden = !radios[1].checked; };
    radios.forEach((r) => r.addEventListener('change', onMode)); onMode();
    d.card.append(h('div', { class: 'bk-opts', role: 'radiogroup', 'aria-label': 'Regions' }, opts.map(([v, label], i) => [h('label', null, radios[i], label), i === 1 ? sc.row : null])),
      pdfs.length > 1 ? h('div', { class: 'bk-opts' }, conn.row) : null,
      h('div', { class: 'bk-bar' }, bar), h('div', { class: 'bk-acts' }, status, cancel, go));
    go.addEventListener('click', async () => {
      const m = (radios.find((r) => r.checked) || radios[0]).value;
      go.disabled = true; radios.forEach((r) => { r.disabled = true; }); conn.box.disabled = true; sc.disable(true); d.setBusy(true);
      const how = sc.value();
      cancel.textContent = 'Stop'; cancel.onclick = () => { stop = true; status.textContent = 'Stopping…'; };
      const res = { added: 0, dup: 0, failed: [], ids: [] };
      // a long one can go on in the background, with the gauge in the corner
      const gid = `copy-${Date.now()}`, started = Date.now() / 1000;
      let bg = false;
      const gauge = (state) => tray.set(gid, { kind: 'copy', name: root, state, total: pdfs.length, done: res.added + res.dup + res.failed.length,
        added: res.added, duplicate: res.dup, failed: res.failed, started, stop: () => { stop = true; } }, { force: true });
      const hide = h('button', { class: 'btn btn-text', type: 'button', text: 'Keep going in the background',
        onclick: () => { bg = true; d.setBusy(false); d.close(); gauge('running'); } });
      if (pdfs.length >= 10) cancel.before(hide);
      await pool(pdfs, 2, async (f) => {
        if (stop) return;
        const t = m === 'ai' ? '' : topicFor(f, m, root);
        try {
          const n = await rb.upload(`/api/import/pdf?quick=1${t ? `&topic=${enc(t)}` : ''}`, f);
          if (n.duplicate) res.dup++; else res.added++;
          if (n.id) res.ids.push(n.id);
        } catch (e) { res.failed.push(`${f.name}: ${e.message}`); }
      }, (done) => { bar.style.width = `${Math.round((done / pdfs.length) * 100)}%`; status.textContent = `${done} of ${pdfs.length}`; if (bg) gauge('running'); });
      hide.remove();
      if (bg) gauge(stop ? 'stopped' : 'done');
      d.setBusy(false);
      status.textContent = `${res.added} added, ${res.dup} already in your library${res.failed.length ? `, ${res.failed.length} couldn’t be read` : ''}${stop ? ' (stopped)' : ''}.`;
      if (res.failed.length) status.title = res.failed.slice(0, 20).join('\n');
      go.hidden = true; cancel.textContent = 'Done'; cancel.onclick = d.close;
      rb.refreshGraph();
      if (m === 'ai' && res.ids.length > 1 && !stop) {
        const done = status.textContent;
        status.textContent = `${done} Sorting into regions…`;
        cancel.disabled = true;
        try {
          const r = await api('POST', '/api/topics/auto-sort', { ids: res.ids, ...how });
          status.textContent = `${done} ${sortedText(r, how)}`;
          rb.refreshGraph();
          window.dispatchEvent(new CustomEvent('rb:topics-changed'));
        } catch (e) { status.textContent = `${done} Couldn’t sort them into regions: ${e.message}`; }
        finally { cancel.disabled = false; }
      }
      if (conn.box.checked && res.ids.length > 1 && !stop) {
        const line = h('p', { class: 'bk-note' });
        status.closest('.bk-acts').before(line);
        const job = await findConnections({ ids: res.ids }, (j) => { line.textContent = connectText(j); }, d.card);
        const u = undoLinksButton(job, line);
        if (u) line.append(' ', u);
      }
    });
  }

  // ---------------------------------------------------------------- browser bookmarks
  const bmInput = h('input', { type: 'file', accept: '.html,.htm,text/html', hidden: true, id: 'bulk-bookmarks-input' });
  document.body.appendChild(bmInput);
  bmInput.addEventListener('change', async () => {
    const f = bmInput.files && bmInput.files[0]; bmInput.value = '';
    if (!f) return;
    try { bookmarksDialog(parseBookmarks(await f.text())); } catch (e) { toastErr(e, 'Could not read that bookmarks file'); }
  });

  const PAPERISH = /(arxiv\.org\/(abs|pdf)\/|doi\.org\/10\.|\/doi\/(abs\/|full\/|pdf\/)?10\.|10\.\d{4,9}\/|\.pdf($|[?#])|semanticscholar\.org\/paper|openreview\.net\/(forum|pdf)|ncbi\.nlm\.nih\.gov\/pmc|biorxiv\.org\/content|sciencedirect\.com\/science\/article|ieeexplore\.ieee\.org\/(abstract\/)?document|link\.springer\.com\/(article|chapter)|nature\.com\/articles|journals\.aps\.org|pubs\.aip\.org|iopscience\.iop\.org\/article|arc\.aiaa\.org\/doi)/i;

  /** Netscape bookmark HTML -> [{folder: "A / B", links: [{url, title}]}] (every browser exports this). */
  function parseBookmarks(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const out = new Map();
    const walk = (dl, path) => {
      for (const dt of Array.from(dl.children)) {
        if (dt.tagName !== 'DT') continue;
        const a = dt.querySelector(':scope > a'), h3 = dt.querySelector(':scope > h3');
        if (a && /^https?:/i.test(a.getAttribute('href') || '')) {
          const key = path.join(' / ') || 'Bookmarks';
          if (!out.has(key)) out.set(key, []);
          out.get(key).push({ url: a.getAttribute('href'), title: (a.textContent || '').trim() });
        } else if (h3) {
          const sub = dt.querySelector(':scope > dl') || (dt.nextElementSibling && dt.nextElementSibling.tagName === 'DL' ? dt.nextElementSibling : null);
          if (sub) walk(sub, [...path, (h3.textContent || '').trim()]);
        }
      }
    };
    const top = doc.querySelector('dl');
    if (!top) throw new Error('That isn’t a bookmarks export (no bookmark list in it).');
    walk(top, []);
    // the browser's own top folders ("Bookmarks bar") aren't topics: drop the first level when it's one of them
    return [...out.entries()].map(([folder, links]) => ({ folder: folder.replace(/^(bookmarks bar|bookmarks toolbar|bookmarks menu|other bookmarks|favorites bar|favourites)( \/ )?/i, '') || folder, links }));
  }

  function bookmarksDialog(groups) {
    const total = groups.reduce((s, g) => s + g.links.length, 0);
    const papers = groups.reduce((s, g) => s + g.links.filter((l) => PAPERISH.test(l.url)).length, 0);
    const d = dialog('Import bookmarks', `${total} bookmark${total === 1 ? '' : 's'} in ${groups.length} folder${groups.length === 1 ? '' : 's'}. ${papers} look like papers and are ticked. Each folder becomes a region. Links with a DOI or arXiv id are added from their records; links to PDFs are downloaded.`);
    const rows = [];
    const list = h('div', { class: 'bk-list' });
    for (const g of groups) {
      const items = g.links.map((l) => {
        const box = h('input', { type: 'checkbox', 'aria-label': l.title || l.url });
        box.checked = PAPERISH.test(l.url);
        rows.push({ box, link: l, topic: g.folder });
        return h('label', { class: 'bk-item', title: l.url }, box, h('span', { class: 't', text: l.title || l.url }),
          h('span', { class: 'why', text: (() => { try { return new URL(l.url).hostname.replace(/^www\./, ''); } catch { return ''; } })() }));
      });
      const all = h('input', { type: 'checkbox', 'aria-label': `All in ${g.folder}` });
      const mine = rows.slice(rows.length - items.length);
      all.checked = mine.length > 0 && mine.every((r) => r.box.checked);
      all.addEventListener('change', () => mine.forEach((r) => { r.box.checked = all.checked; }));
      list.append(h('label', { class: 'bk-folder' }, all, h('span', { text: g.folder }), h('span', { class: 'n', text: `${g.links.length}` })), ...items);
    }
    const status = h('span', { class: 'bk-status' });
    const bar = h('i');
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: 'Import ticked' });
    const cancel = h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => { stop = true; d.close(); } });
    let stop = false;
    d.card.append(list, h('div', { class: 'bk-bar' }, bar), h('div', { class: 'bk-acts' }, status, cancel, go));
    go.addEventListener('click', async () => {
      const pick = rows.filter((r) => r.box.checked);
      if (!pick.length) return;
      go.disabled = true; d.setBusy(true); rows.forEach((r) => { r.box.disabled = true; });
      cancel.textContent = 'Stop'; cancel.onclick = () => { stop = true; status.textContent = 'Stopping…'; };
      const res = { added: 0, dup: 0, failed: 0 };
      await pool(pick, 2, async (r) => {
        if (stop) return;
        try {
          const n = await api('POST', '/api/import/bookmark', { url: r.link.url, title: r.link.title, topic: r.topic });
          if (n.duplicate) res.dup++; else res.added++;
        } catch { res.failed++; }
      }, (done) => { bar.style.width = `${Math.round((done / pick.length) * 100)}%`; status.textContent = `${done} of ${pick.length}`; });
      d.setBusy(false);
      status.textContent = `${res.added} added, ${res.dup} already in your library${res.failed ? `, ${res.failed} failed` : ''}${stop ? ' (stopped)' : ''}.`;
      go.hidden = true; cancel.textContent = 'Done'; cancel.onclick = d.close;
      rb.refreshGraph();
    });
  }

  // ---------------------------------------------------------------- folders of papers, kept where they are (app/linked.py)
  async function pickLinkFolder() {
    let r;
    try { r = await api('POST', '/api/pick-folder', { prompt: 'Choose a folder of papers (it can have subfolders)' }); }
    catch (e) { toastErr(e, 'Could not open the folder picker'); return; }
    if (r && r.path) linkDialog(r.path);
  }

  async function linkDialog(path) {
    let pv;
    try { pv = await api('POST', '/api/linked-folders/preview', { path }); }
    catch (e) { toastErr(e, 'Could not read that folder'); return; }
    const nFolders = pv.folders.length;
    const d = dialog(`Add “${pv.name}”`, pv.pdfs
      ? `${pv.pdfs} PDF${pv.pdfs === 1 ? '' : 's'}${nFolders > 1 ? ` in ${nFolders} folders` : ''}${pv.limit ? ' (the first 20,000)' : ''}. They stay where they are: research brain links to each one and never moves, renames or deletes them. New PDFs you put in these folders later are added too.`
      : 'There are no PDFs in that folder or its subfolders.');
    if (!pv.pdfs) { d.card.append(h('div', { class: 'bk-acts' }, h('button', { class: 'btn', type: 'button', text: 'Close', onclick: d.close }))); return; }
    if (pv.online) d.card.append(h('p', { text: `${pv.online === pv.pdfs ? 'All of them are' : `${pv.online} of them are`} online only${pv.cloud ? ` in ${pv.cloud}` : ''}, and stay that way: nothing is downloaded now. Each is named from its file name and its details are looked up online; its text is read the first time you open it.` }));
    if (pv.known) d.card.append(h('p', { text: `${pv.known} of them are already in your library and stay as they are.` }));
    const byFolder = h('input', { type: 'radio', name: 'bk-ltopic', value: 'folders' });
    const byAI = h('input', { type: 'radio', name: 'bk-ltopic', value: 'ai' });
    (nFolders > 1 ? byFolder : byAI).checked = true;  // subfolders: keep them; one flat folder: let the AI sort it
    const conn = connectBox(pv.pdfs > 1);
    const sc = sortChoices();
    d.card.append(h('div', { class: 'bk-opts', role: 'radiogroup', 'aria-label': 'Regions' },
      h('label', null, byFolder, nFolders > 1 ? 'A region for each folder (change a name below, or clear it to leave that folder out of regions)' : `One region for the folder (its name below)`),
      h('label', null, byAI, 'Let the AI sort them into regions by what they’re about, whatever folder they’re in (it reads each paper’s title and opening, and reuses your regions where they fit)'),
      sc.row),
      pv.pdfs > 1 ? h('div', { class: 'bk-opts' }, conn.row) : null);
    const topics = new Set((pv.topics || []).map((t) => t.toLowerCase()));
    const inputs = new Map();
    const notes = new Map();
    const refresh = () => {
      const by = new Map();
      for (const [dir, inp] of inputs) { const v = inp.value.trim().toLowerCase(); if (v) by.set(v, (by.get(v) || []).concat(dir)); }
      for (const [dir, inp] of inputs) {
        const v = inp.value.trim(), lv = v.toLowerCase();
        const shared = (by.get(lv) || []).filter((x) => x !== dir).map((x) => pv.folders.find((f) => f.dir === x).label);
        notes.get(dir).textContent = !v ? 'no region' : shared.length ? `shared with ${shared.slice(0, 2).join(', ')}${shared.length > 2 ? '…' : ''}`
          : topics.has(lv) ? 'joins your region' : 'new region';
      }
    };
    const rows = pv.folders.map((f) => {
      const inp = h('input', { type: 'text', class: 'bk-name', value: f.topic, 'aria-label': `Region for ${f.label}`, spellcheck: 'false', oninput: refresh });
      const note = h('span', { class: 'why' });
      inputs.set(f.dir, inp); notes.set(f.dir, note);
      return h('div', { class: 'bk-lrow' }, h('span', { class: 'bk-ldir', title: f.label, text: f.label }), h('span', { class: 'n', text: `${f.pdfs}` }), inp, note);
    });
    const list = h('div', { class: 'bk-list bk-link' }, h('div', { class: 'bk-lrow bk-lhead' }, h('span', { text: 'Folder' }), h('span', { text: 'PDFs' }), h('span', { text: 'Region' }), h('span')), ...rows);
    refresh();
    const status = h('span', { class: 'bk-status' });
    const suggest = h('button', { class: 'btn', type: 'button', text: 'Suggest names with AI', title: 'Sends the folder names and file names to your AI. Nothing is downloaded or read.' });
    const reset = h('button', { class: 'btn btn-text', type: 'button', text: 'Use folder names', onclick: () => { for (const f of pv.folders) inputs.get(f.dir).value = f.default; refresh(); } });
    suggest.addEventListener('click', async () => {
      suggest.disabled = true; status.textContent = 'Asking your AI…';
      try {
        const r = await api('POST', '/api/linked-folders/suggest-names', { path: pv.path });
        let n = 0;
        for (const [dir, name] of Object.entries(r.names || {})) { const inp = inputs.get(dir); if (inp && name) { inp.value = name; n++; } }
        refresh(); status.textContent = `${n} name${n === 1 ? '' : 's'} suggested. Change any you like.`;
      } catch (e) { status.textContent = e.message; } finally { suggest.disabled = false; }
    });
    const bar = h('i');
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: `Add ${pv.pdfs - pv.known} PDF${pv.pdfs - pv.known === 1 ? '' : 's'}` });
    const cancel = h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => d.close() });
    const nameTools = h('div', { class: 'bk-acts bk-acts-l' }, suggest, reset);
    const onMode = () => { list.hidden = nameTools.hidden = byAI.checked; sc.row.hidden = !byAI.checked; };
    byFolder.addEventListener('change', onMode); byAI.addEventListener('change', onMode); onMode();
    d.card.append(list, nameTools, h('div', { class: 'bk-bar' }, bar), h('div', { class: 'bk-acts' }, status, cancel, go));
    go.addEventListener('click', async () => {
      const ai = byAI.checked, connect = conn.box.checked && pv.pdfs > 1;
      const names = {};
      for (const [dir, inp] of inputs) names[dir] = ai ? '' : inp.value.trim();  // the AI files them instead
      go.disabled = true; suggest.disabled = true; inputs.forEach((i) => { i.disabled = true; });
      byFolder.disabled = byAI.disabled = conn.box.disabled = true; sc.disable(true);
      const how = sc.value();
      let job;
      try { job = (await api('POST', '/api/linked-folders', { path: pv.path, names })).job; }
      catch (e) { status.textContent = e.message; go.disabled = false; suggest.disabled = false; inputs.forEach((i) => { i.disabled = false; }); return; }
      document.dispatchEvent(new CustomEvent('rb:linked-changed'));  // Library settings lists it now
      cancel.textContent = 'Close (keeps going)';
      cancel.onclick = () => { d.close(); watchImports(); };
      let watched = false;
      while (job && (job.state === 'running' || job.state === 'paused')) {
        if (!d.card.isConnected && !watched) {  // closed: the gauge in the corner follows it from here
          watched = true; watchImports();
          if (!ai && !connect) return;  // nothing more to do when it's done
        }
        bar.style.width = job.total ? `${Math.round((job.done / job.total) * 100)}%` : '0%';
        status.textContent = job.state === 'paused' ? job.message : job.total ? `${job.done} of ${job.total}${job.current ? `: ${job.current.split('/').pop()}` : ''}` : 'Looking through the folders…';
        await new Promise((r) => setTimeout(r, 900));
        try { job = await api('GET', `/api/linked-folders/jobs/${job.id}`); } catch { break; }
      }
      bar.style.width = '100%';
      if (job) {
        const bits = [`${job.added} added`];
        if (job.linked) bits.push(`${job.linked} matched papers you had`);
        if (job.duplicate) bits.push(`${job.duplicate} already in your library`);
        if (job.moved) bits.push(`${job.moved} moved`);
        if (job.unread) bits.push(`${job.unread} waiting for space to be read`);
        if (job.failed.length) bits.push(`${job.failed.length} couldn’t be read`);
        status.textContent = job.state === 'error' ? job.message : bits.join(', ') + '.';
        if (job.failed.length) status.title = job.failed.slice(0, 20).join('\n');
      }
      go.hidden = true; cancel.textContent = 'Done'; cancel.onclick = d.close;
      rb.refreshGraph();
      window.dispatchEvent(new CustomEvent('rb:topics-changed'));
      if (!job || job.state === 'error' || job.state === 'stopped') return;
      const line = h('p', { class: 'bk-note' });
      if (ai || connect) status.closest('.bk-acts').before(line);
      if (ai) {
        line.textContent = 'Sorting them into regions…';
        try {
          const r = await api('POST', '/api/topics/auto-sort', { folder: job.folder, ...how });
          line.textContent = sortedText(r, how);
          rb.refreshGraph();
          window.dispatchEvent(new CustomEvent('rb:topics-changed'));
        } catch (e) { line.textContent = `Couldn’t sort them into regions: ${e.message}`; }
        if (!d.card.isConnected && line.textContent) toast(line.textContent, 'ok');
      }
      if (connect) {
        const line2 = h('p', { class: 'bk-note' });
        line.after(line2);
        const cj = await findConnections({ folder: job.folder }, (j) => { line2.textContent = connectText(j); }, d.card);
        const u = undoLinksButton(cj, line2);
        if (u) line2.append(' ', u);
      }
    });
  }

  // ---------------------------------------------------------------- AI connection analysis (app/connect_api.py)
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;
  function connectText(j) {
    if (!j) return '';
    if (j.state === 'waiting') return 'Waiting for another connection check to finish…';
    if (j.state === 'running') return j.batches ? `Finding connections: ${j.done_batches} of ${j.batches} batches read, ${plural(j.linked, 'link')} so far…` : 'Finding connections…';
    if (j.state === 'error') return `Couldn’t find connections: ${j.message}`;
    if (j.undone != null && !j.links.length) return `Took back ${plural(j.undone, 'link')}.`;
    const bits = [j.linked ? `Found ${plural(j.linked, 'connection')} between papers that belong together (faint dashed lines on the map)` : (j.message || `The AI found no new connections among ${plural(j.checked, 'likely pair')}`)];
    if (j.left) bits.push(`${j.left} more likely pairs are left: run it again to check them`);
    return bits.join('. ') + (j.state === 'stopped' ? ' (stopped).' : '.');
  }
  /** Start a connection analysis over `body` ({ids} | {folder} | {topics, unsorted}) and follow it; show(job)
   *  on every change. When the dialog it reports to is gone, the result comes as a toast. Resolves with the job. */
  async function findConnections(body, show, where) {
    let job;
    try { job = await api('POST', '/api/links/ai-connect', body); }
    catch (e) { const j = { state: 'error', message: e.message }; show(j); return j; }
    while (job.state === 'waiting' || job.state === 'running') {
      show(job);
      await new Promise((r) => setTimeout(r, 1200));
      try { job = await api('GET', `/api/links/ai-connect/jobs/${job.id}`); } catch { break; }
    }
    show(job);
    rb.refreshGraph();
    if (where && !where.isConnected) toast(connectText(job), job.state === 'error' ? 'error' : 'ok');
    return job;
  }
  function undoLinksButton(job, status) {
    if (!job || !job.id || !job.linked) return null;
    const b = h('button', { class: 'btn btn-text', type: 'button', text: 'Undo links', title: 'Take back the links it just made',
      onclick: async () => {
        b.disabled = true;
        try { const j = await api('POST', `/api/links/ai-connect/jobs/${job.id}/undo`); status.textContent = connectText(j); b.remove(); rb.refreshGraph(); }
        catch (e) { status.textContent = e.message; b.disabled = false; }
      } });
    return b;
  }
  const connectBox = (checked = true) => {
    const box = h('input', { type: 'checkbox' });
    box.checked = checked;
    return { box, row: h('label', null, box, 'Have the AI find connections between them: it reads the papers that look alike (and the papers filed together) and marks the ones that really belong together. These show as faint dashed lines, not as links of yours') };
  };

  // ---------------------------------------------------------------- reorganizing topics (POST /api/topics/reorganize*)
  async function reorganizeDialog(opts = {}) {
    let topics;
    try { topics = await api('GET', '/api/topics'); } catch (e) { toastErr(e, 'Could not load your regions'); return; }
    const d = dialog('Reorganize regions', 'Your AI reads each paper’s title and opening and groups the papers by what they’re about, not by how they’re filed now. You see the new regions before anything changes, and can undo it after. Private papers aren’t sent and stay where they are.');
    const only = opts.topics ? new Set(opts.topics) : null;
    const tBoxes = topics.map((t) => { const b = h('input', { type: 'checkbox', value: t.slug }); b.checked = !only || only.has(t.slug); return [b, t]; });
    const noTopic = h('input', { type: 'checkbox' }); noTopic.checked = !only;
    const pickAll = h('input', { type: 'checkbox', 'aria-label': 'All regions' });
    const syncAll = () => { pickAll.checked = tBoxes.every(([b]) => b.checked) && noTopic.checked; };
    pickAll.addEventListener('change', () => { tBoxes.forEach(([b]) => { b.checked = pickAll.checked; }); noTopic.checked = pickAll.checked; });
    tBoxes.forEach(([b]) => b.addEventListener('change', syncAll)); noTopic.addEventListener('change', syncAll); syncAll();
    const scope = h('div', { class: 'bk-list' },
      h('label', { class: 'bk-folder' }, pickAll, h('span', { text: 'Everything' })),
      tBoxes.map(([b, t]) => h('label', { class: 'bk-item' }, b, h('span', { class: 't', text: t.name }), h('span', { class: 'why', text: String(t.count) }))),
      h('label', { class: 'bk-item' }, noTopic, h('span', { class: 't', text: 'Papers with no region' })));
    const free = h('input', { type: 'radio', name: 'bk-reorg', value: 'free' }); free.checked = true;
    const named = h('input', { type: 'radio', name: 'bk-reorg', value: 'named' });
    const sections = h('textarea', { class: 'bk-sections', 'aria-label': 'Your regions', spellcheck: 'true', placeholder: 'One region per line, for example:\nSheath physics\nHall thruster erosion\nDiagnostics' });
    sections.hidden = true;
    const rc = sortChoices(false);
    const onMode = () => { sections.hidden = !named.checked; rc.row.hidden = named.checked; if (named.checked) sections.focus(); };
    free.addEventListener('change', onMode); named.addEventListener('change', onMode);
    const conn = connectBox(true);
    const step1 = h('div', { class: 'bk-step' },
      h('div', { class: 'bk-sub', text: 'Which papers' }), scope,
      h('div', { class: 'bk-sub', text: 'Regions' }),
      h('div', { class: 'bk-opts', role: 'radiogroup', 'aria-label': 'Regions' },
        h('label', null, free, 'Let the AI choose the regions'), rc.row,
        h('label', null, named, 'Use my own regions (the AI puts each paper in the one it fits best)')),
      sections,
      h('div', { class: 'bk-opts' }, conn.row));
    const status = h('span', { class: 'bk-status' });
    const bar = h('i');
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: 'Suggest regions' });
    const back = h('button', { class: 'btn', type: 'button', text: 'Back', hidden: true });
    const cancel = h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => d.close() });
    const step2 = h('div', { class: 'bk-list', hidden: true });
    const notes = h('div');
    d.card.append(step1, step2, notes, h('div', { class: 'bk-bar' }, bar), h('div', { class: 'bk-acts' }, status, cancel, back, go));
    let plan = null, inputs = [];
    const busy = (b) => { d.setBusy(b); go.disabled = b; back.disabled = b; cancel.disabled = b; };

    back.addEventListener('click', () => { plan = null; step2.hidden = true; step1.hidden = false; notes.replaceChildren(); back.hidden = true; go.textContent = 'Suggest regions'; status.textContent = ''; });
    go.addEventListener('click', async () => {
      if (!plan) return suggest();
      return apply();
    });

    async function suggest() {
      const body = { topics: tBoxes.filter(([b]) => b.checked).map(([, t]) => t.slug), unsorted: noTopic.checked,
        names: named.checked ? sections.value.split('\n') : [], count: named.checked ? 0 : rc.value().count };
      if (named.checked && !sections.value.trim()) { status.textContent = 'Write the regions you want, one per line.'; sections.focus(); return; }
      if (!body.topics.length && !body.unsorted) { status.textContent = 'Tick some papers to reorganize.'; return; }
      busy(true); status.textContent = 'Asking your AI to group the papers… (this can take a minute)';
      try { plan = await api('POST', '/api/topics/reorganize/plan', body); }
      catch (e) { status.textContent = e.message; busy(false); return; }
      busy(false);
      inputs = plan.groups.map((g) => {
        const inp = h('input', { type: 'text', class: 'bk-name', value: g.name, 'aria-label': `Name for ${g.name}`, spellcheck: 'true' });
        return [inp, g];
      });
      step2.replaceChildren(...inputs.map(([inp, g]) => h('div', { class: 'bk-group' },
        h('div', { class: 'bk-ghead' }, inp, h('span', { class: 'n', text: String(g.count) }), h('span', { class: 'why', text: g.topic ? 'joins your region' : 'new region' })),
        g.papers.length ? h('details', null, h('summary', { text: 'Papers' }), h('ul', null, g.papers.map((p) => h('li', { title: p.title, text: p.title })))) : null)));
      const bits = [];
      if (plan.by === 'regions') bits.push(h('p', { class: 'bk-note', text: 'Your AI didn’t give a usable answer, so these groups come from text similarity.' }));
      if (plan.unplaced.length) bits.push(h('details', { class: 'bk-note' }, h('summary', { text: `${plural(plan.unplaced.length, 'paper')} fit none of these and will have no region` }),
        h('ul', null, plan.unplaced.map((p) => h('li', { text: p.title })))));
      if (plan.removed.length) bits.push(h('p', { class: 'bk-note', text: `Left empty, so they go to the trash: ${plan.removed.join(', ')}.` }));
      bits.push(h('p', { class: 'bk-note', text: `Rename any region you like. ${plural(plan.ids.length, 'paper')} will be moved.` }));
      notes.replaceChildren(...bits);
      step1.hidden = true; step2.hidden = false; back.hidden = false; go.textContent = 'Reorganize';
      status.textContent = `${plural(plan.groups.length, 'region')}.`;
    }

    async function apply() {
      const groups = inputs.map(([inp, g]) => ({ name: inp.value.trim() || g.name, ids: g.ids }));
      busy(true); status.textContent = 'Reorganizing…';
      let r;
      try { r = await api('POST', '/api/topics/reorganize', { groups, ids: plan.ids, replace: plan.replace }); }
      catch (e) { status.textContent = e.message; busy(false); return; }
      inputs.forEach(([inp]) => { inp.disabled = true; });
      rb.refreshGraph();
      window.dispatchEvent(new CustomEvent('rb:topics-changed'));
      const made = `Filed ${plural(r.moved, 'paper')} into ${plural(r.topics.length, 'region')}${r.deleted.length ? `; ${r.deleted.join(', ')} went to the trash` : ''}.`;
      status.textContent = made;
      go.hidden = true; back.hidden = true;
      const undo = h('button', { class: 'btn btn-text', type: 'button', text: 'Undo', title: 'Put every region back the way it was', onclick: async () => {
        undo.disabled = true;
        try {
          await api('POST', '/api/topics/reorganize/undo', { token: r.undo });
          status.textContent = 'Your regions are back the way they were.'; undo.remove();
          rb.refreshGraph(); window.dispatchEvent(new CustomEvent('rb:topics-changed'));
        } catch (e) { status.textContent = e.message; undo.disabled = false; }
      } });
      cancel.before(undo);
      cancel.textContent = 'Done'; cancel.disabled = false; d.setBusy(false); cancel.onclick = d.close;
      if (conn.box.checked && plan.ids.length > 1) {
        const line = h('p', { class: 'bk-note' });
        notes.replaceChildren(line);
        // papers filed into the same new topic are always checked against each other
        const job = await findConnections({ ids: plan.ids, groups: groups.map((g) => g.ids) }, (j) => {
          line.textContent = connectText(j);
          bar.style.width = j.batches ? `${Math.round((j.done_batches / j.batches) * 100)}%` : '0%';
        }, d.card);
        const u = undoLinksButton(job, line);
        if (u) line.after(h('div', { class: 'bk-acts bk-acts-l' }, u));
      } else notes.replaceChildren();
    }
  }

  // ---------------------------------------------------------------- the import gauge: long imports, bottom right
  // One plate per running import (a folder of papers being read, a folder of PDFs being copied in): a ruled scale
  // with an ink fill and a red needle at its edge, the count in display numerals, the time left, the file being
  // read. Paused (waiting for you to free space) turns it ochre and hatched; done turns it moss, then it goes.
  // Collapses to a small dial. Imports of fewer than 3 papers don't show one.
  const tray = (() => {
    const css = `
    .rbg-tray { position: fixed; right: 16px; bottom: 16px; z-index: 60; display: flex; flex-direction: column; gap: 8px; align-items: flex-end; pointer-events: none; }
    .rbg { pointer-events: auto; width: 340px; max-width: calc(100vw - 32px); background: var(--sheet); border: 1px solid var(--rule); padding: 12px 14px 11px;
      font: 12.5px/1.45 var(--sans); color: var(--ink); box-shadow: 0 14px 34px -18px color-mix(in srgb, var(--ink) 35%, transparent);
      animation: rbg-in .32s cubic-bezier(.2,.8,.2,1); }
    @keyframes rbg-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    .rbg-top { display: flex; align-items: baseline; gap: 8px; }
    .rbg-k { font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); font-stretch: var(--narrow); }
    .rbg-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); }
    .rbg-x { border: 0; background: none; color: var(--ink-3); cursor: pointer; padding: 0 2px; font: inherit; line-height: 1; }
    .rbg-x:hover { color: var(--ink); }
    .rbg-num { display: flex; align-items: baseline; gap: 6px; margin: 4px 0 6px; }
    .rbg-n { font: 400 30px/1 var(--display); font-variant-numeric: lining-nums tabular-nums; letter-spacing: -.01em; }
    .rbg-of { color: var(--ink-2); font: 400 15px/1 var(--serif); }
    .rbg-eta { margin-left: auto; color: var(--ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .rbg-scale { position: relative; height: 16px; margin: 2px 0 4px; }
    .rbg-track { position: absolute; left: 0; right: 0; top: 6px; height: 4px; background: var(--rule); }
    .rbg-fill { position: absolute; left: 0; top: 6px; height: 4px; width: 0; background: var(--ink); transition: width .6s cubic-bezier(.2,.8,.2,1); }
    .rbg-needle { position: absolute; top: 1px; width: 1.5px; height: 14px; background: var(--red); left: 0; transform: translateX(-50%); transition: left .6s cubic-bezier(.2,.8,.2,1); }
    .rbg-needle::after { content: ''; position: absolute; left: 50%; top: -3px; width: 5px; height: 5px; border-radius: 50%; background: var(--red); transform: translateX(-50%);
      animation: rbg-pulse 1.6s ease-in-out infinite; }
    @keyframes rbg-pulse { 50% { box-shadow: 0 0 0 4px color-mix(in srgb, var(--red) 18%, transparent); } }
    .rbg-ticks { position: absolute; left: 0; right: 0; top: 11px; height: 5px;
      background: repeating-linear-gradient(90deg, var(--ink-3) 0 1px, transparent 1px calc(10% - .1px)); background-size: calc(100% + 1px) 100%; opacity: .55; }
    .rbg-ticks::before { content: ''; position: absolute; inset: 0 0 auto 0; height: 3px;
      background: repeating-linear-gradient(90deg, var(--ink-3) 0 1px, transparent 1px calc(2% - .02px)); background-size: calc(100% + 1px) 100%; opacity: .6; }
    .rbg-file { color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-height: 18px; }
    .rbg-msg { color: var(--ochre); margin-top: 2px; }
    .rbg-foot { display: flex; align-items: center; gap: 10px; margin-top: 6px; color: var(--ink-2); font-size: 12px; font-variant-numeric: tabular-nums; }
    .rbg-foot .sp { flex: 1; }
    .rbg-foot button { border: 0; background: none; color: var(--ink-2); cursor: pointer; font: inherit; padding: 0; text-decoration: underline; text-underline-offset: 2px; }
    .rbg-foot button:hover { color: var(--ink); }
    .rbg.paused .rbg-fill { background: repeating-linear-gradient(135deg, var(--ochre) 0 4px, color-mix(in srgb, var(--ochre) 45%, transparent) 4px 8px); }
    .rbg.paused .rbg-needle, .rbg.paused .rbg-needle::after { background: var(--ochre); animation: none; }
    .rbg.done .rbg-fill { background: var(--moss); }
    .rbg.done .rbg-needle { opacity: 0; }
    .rbg.done .rbg-n { color: var(--moss); }
    .rbg.failed .rbg-fill { background: var(--red); }
    .rbg-mini { pointer-events: auto; display: flex; align-items: center; gap: 8px; background: var(--sheet); border: 1px solid var(--rule); padding: 5px 10px 5px 6px;
      font: 12px/1 var(--sans); color: var(--ink-2); cursor: pointer; font-variant-numeric: tabular-nums; animation: rbg-in .25s ease-out; }
    .rbg-mini:hover { color: var(--ink); }
    .rbg-mini svg { width: 22px; height: 22px; transform: rotate(-90deg); }
    .rbg-mini .bg { stroke: var(--rule); } .rbg-mini .fg { stroke: var(--ink); transition: stroke-dashoffset .6s ease; }
    .rbg-mini.paused .fg { stroke: var(--ochre); } .rbg-mini.done .fg { stroke: var(--moss); }
    @media (prefers-reduced-motion: reduce) { .rbg, .rbg-mini { animation: none; } .rbg-fill, .rbg-needle { transition: none; } .rbg-needle::after { animation: none; } }
    @media (max-width: 560px) { .rbg-tray { right: 16px; left: 16px; bottom: calc(16px + var(--tabbar-h, 0px) + var(--safe-b, 0px)); align-items: stretch; } .rbg { width: auto; } }`;
    document.head.appendChild(h('style', { text: css }));
    const box = h('div', { class: 'rbg-tray', 'aria-live': 'polite' });
    document.body.appendChild(box);
    const items = new Map();   // id -> {job, el, mini, collapsed, hidden, doneAt}
    const fmtLeft = (sec) => sec < 50 ? 'under a minute left' : sec < 90 * 60 ? `about ${Math.round(sec / 60)} min left` : `about ${(sec / 3600).toFixed(1)} h left`;

    function eta(j) {
      if (!j.started || j.done < 2 || !j.total) return '';
      const spent = Date.now() / 1000 - j.started - (j.paused_s || 0);
      if (spent < 4) return '';
      return fmtLeft(((j.total - j.done) / j.done) * spent);
    }
    function render(id) {
      const it = items.get(id);
      if (!it) return;
      const j = it.job, total = Math.max(j.total || 0, 0), done = Math.min(j.done || 0, total || j.done || 0);
      const frac = total ? done / total : 0;
      const finished = !['running', 'paused'].includes(j.state);
      const cls = finished ? (j.state === 'error' ? 'failed' : 'done') : j.state === 'paused' ? 'paused' : '';
      if (it.collapsed) {
        const C = 2 * Math.PI * 9;
        it.el.replaceChildren(); it.el.hidden = true;
        it.mini.className = `rbg-mini ${cls}`; it.mini.hidden = false;
        it.mini.innerHTML = `<svg viewBox="0 0 22 22" aria-hidden="true"><circle class="bg" cx="11" cy="11" r="9" fill="none" stroke-width="2.4"/><circle class="fg" cx="11" cy="11" r="9" fill="none" stroke-width="2.4" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - frac)}"/></svg>`;
        it.mini.append(h('span', { text: `${j.name || 'Import'} · ${finished ? 'done' : j.state === 'paused' ? 'paused' : Math.round(frac * 100) + '%'}` }));
        return;
      }
      it.mini.hidden = true; it.el.hidden = false;
      it.el.className = `rbg ${cls}`;
      const kind = j.kind === 'copy' ? 'Copying in' : 'Reading papers';
      const bits = [];
      if (j.added) bits.push(`${j.added} added`);
      if (j.read) bits.push(`${j.read} read`);
      if (j.linked) bits.push(`${j.linked} matched`);
      if (j.duplicate) bits.push(`${j.duplicate} already had`);
      if (j.moved) bits.push(`${j.moved} moved`);
      if (j.failed && j.failed.length) bits.push(`${j.failed.length} unreadable`);
      const left = finished ? '' : j.state === 'paused' ? 'paused' : eta(j);
      const actions = [];
      if (!finished && j.stop) actions.push(h('button', { type: 'button', text: 'Stop', onclick: () => j.stop() }));
      const fileLine = j.state === 'paused' ? h('div', { class: 'rbg-msg', text: j.message || 'Paused.' })
        : finished ? h('div', { class: 'rbg-file', text: j.state === 'error' ? (j.message || 'Stopped with an error.') : j.state === 'stopped' ? 'Stopped. What was read is in your library.' : `Done: everything from ${j.name || 'the folder'} is in your library.` })
          : h('div', { class: 'rbg-file', title: j.current || '', text: j.current ? (j.current.split('/').pop()) : total ? 'Starting…' : 'Looking through the folders…' });
      it.el.replaceChildren(
        h('div', { class: 'rbg-top' }, h('span', { class: 'rbg-k', text: finished ? 'Import' : kind }), h('span', { class: 'rbg-name', text: j.name || '' }),
          h('button', { class: 'rbg-x', type: 'button', title: finished ? 'Close' : 'Make smaller', 'aria-label': finished ? 'Close' : 'Make smaller', text: finished ? '×' : '–',
            onclick: () => { if (finished) drop(id); else { it.collapsed = true; render(id); } } })),
        h('div', { class: 'rbg-num' }, h('span', { class: 'rbg-n', text: String(done) }), h('span', { class: 'rbg-of', text: total ? `of ${total}` : '' }), h('span', { class: 'rbg-eta', text: left })),
        h('div', { class: 'rbg-scale', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(total || 1), 'aria-valuenow': String(done), 'aria-label': `${kind} ${j.name || ''}` },
          h('div', { class: 'rbg-track' }), h('div', { class: 'rbg-fill', style: { width: `${frac * 100}%` } }), h('div', { class: 'rbg-ticks' }),
          h('div', { class: 'rbg-needle', style: { left: `${frac * 100}%` } })),
        fileLine,
        h('div', { class: 'rbg-foot' }, h('span', { text: bits.join(' · ') }), h('span', { class: 'sp' }), ...actions));
    }
    function drop(id) {
      const it = items.get(id);
      if (!it) return;
      it.el.remove(); it.mini.remove(); items.delete(id);
    }
    /** Show or update one import. job: {state, total, done, name, current, added, ..., started (s), kind?, stop?()} */
    function set(id, job, { force = false } = {}) {
      let it = items.get(id);
      if (!it) {
        if (!force && (job.total || 0) < 3) return;  // a check that found a PDF or two: no gauge
        if (!['running', 'paused'].includes(job.state) && !force) return;
        const el = h('div', { class: 'rbg' });
        const mini = h('div', { class: 'rbg-mini', hidden: true, role: 'button', tabindex: '0', title: 'Show the import' });
        mini.addEventListener('click', () => { it.collapsed = false; render(id); });
        mini.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); it.collapsed = false; render(id); } });
        box.append(el, mini);
        it = { job, el, mini, collapsed: false };
        items.set(id, it);
      }
      it.job = job;
      if (!['running', 'paused'].includes(job.state) && !it.doneAt) {
        it.doneAt = Date.now();
        setTimeout(() => drop(id), job.state === 'done' ? 9000 : 30000);
      }
      render(id);
    }
    return { set, drop, has: (id) => items.has(id) };
  })();

  // folders of papers: follow running jobs on the server (started here, by Check now, or by the app's own checks)
  // The map is fetched again only when a job has finished since the last look: every minute regardless, a big
  // library laid its whole map out again each time anything had changed (heavy on a slow computer, left alone).
  let watching = null, seen = null;  // seen: job id -> finished, from the last look (null before the first)
  async function watchImports() {
    if (watching) return;
    const tick = async () => {
      let r;
      try { r = await api('GET', '/api/linked-folders'); } catch { watching = null; return; }
      let busy = false, ended = false;
      const now = new Map();
      for (const f of r.folders || []) {
        const j = f.job;
        if (!j) continue;
        const live = ['running', 'paused'].includes(j.state);
        busy = busy || live;
        now.set(j.id, !live);
        if (!live && seen && seen.get(j.id) !== true) ended = true;  // it finished since the last look
        if (live || tray.has(j.id)) {
          tray.set(j.id, { ...j, name: j.name || f.name, stop: () => api('POST', `/api/linked-folders/jobs/${j.id}/stop`).catch(() => {}) });
        }
      }
      seen = now;
      watching = busy ? setTimeout(tick, 1500) : null;
      if (!busy && ended) { rb.refreshGraph(); window.dispatchEvent(new CustomEvent('rb:topics-changed')); }
    };
    watching = setTimeout(tick, 0);
  }
  setTimeout(watchImports, 2500);       // an import that was running when the app was opened again
  setInterval(watchImports, 60000);      // and the app's own checks every few minutes

  window.__rbBulk = { pickFolder: () => picker.click(), pickBookmarks: () => bmInput.click(), parseBookmarks, folderDialog, bookmarksDialog,
    pickLinkFolder, linkDialog, tray, watchImports, reorganize: reorganizeDialog, findConnections };
  const reorgBtn = document.getElementById('btn-reorganize');
  if (reorgBtn) reorgBtn.addEventListener('click', () => reorganizeDialog());
  const C = window.RB && window.RB.commands;
  if (C && C.register) {
    C.register({ id: 'import.folder', title: 'Import a folder of PDFs', group: 'Add', description: 'Add every PDF in a folder; its subfolders can become regions.',
      aliases: ['bulk', 'folder', 'directory', 'many pdfs', 'import all'], run: () => picker.click() });
    C.register({ id: 'import.linkFolder', title: 'Add folders of papers (keep them where they are)', group: 'Add',
      description: 'Link a folder you keep PDFs in, such as one in OneDrive: nothing is copied, and each subfolder becomes a region.',
      aliases: ['onedrive', 'link folder', 'watch folder', 'subfolders', 'in place', 'dropbox', 'icloud'], run: () => pickLinkFolder() });
    C.register({ id: 'topics.reorganize', title: 'Reorganize regions', group: 'Library',
      description: 'Your AI regroups papers by what they’re about (or into regions you name), shows you the new regions first, and can link papers that belong together.',
      aliases: ['region', 'reorganize', 'reorganise', 'regroup', 'recluster', 'cluster', 'sections', 'rearrange topics', 'find connections', 'link similar papers'],
      run: () => reorganizeDialog() });
    C.register({ id: 'import.bookmarks', title: 'Import browser bookmarks', group: 'Add', description: 'Add papers from a bookmarks export; each bookmark folder becomes a region.',
      aliases: ['bookmarks', 'favorites', 'chrome', 'firefox', 'safari'], run: () => bmInput.click() });
  }
})();
