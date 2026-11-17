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
  .bk-card { width: min(620px, 100%); max-height: min(86vh, 900px); display: flex; flex-direction: column; background: var(--sheet); border: 1px solid var(--rule); padding: 20px 22px 16px; }
  .bk-card h2 { font: 600 19px/1.3 var(--serif); margin: 0 0 6px; }
  .bk-card p { color: var(--ink-2); line-height: 1.55; margin: 0 0 10px; font-size: 14px; }
  .bk-opts { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 12px; font-size: 14px; }
  .bk-opts label { display: flex; gap: 8px; align-items: baseline; }
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
    const opts = [['folders', subs.size ? `A topic for each subfolder (${[...subs].slice(0, 3).join(', ')}${subs.size > 3 ? '…' : ''}), like bookmark folders` : `One topic, “${root}”`],
      ['ai', 'Let the AI sort them into topics by what they’re about (it reads each paper’s title and opening, and reuses your topics where they fit)'],
      ['one', `Everything in one topic, “${root}”`], ['none', 'No topics']];
    const radios = opts.map(([v]) => mode(v));
    (subs.size ? radios[0] : radios[1]).checked = true;  // subfolders: keep them; one flat folder: let the AI sort it
    const status = h('span', { class: 'bk-status' });
    const bar = h('i');
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: `Import ${pdfs.length}` });
    const cancel = h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => { stop = true; d.close(); } });
    let stop = false;
    d.card.append(h('div', { class: 'bk-opts', role: 'radiogroup', 'aria-label': 'Topics' }, opts.map(([v, label], i) => h('label', null, radios[i], label))),
      h('div', { class: 'bk-bar' }, bar), h('div', { class: 'bk-acts' }, status, cancel, go));
    go.addEventListener('click', async () => {
      const m = (radios.find((r) => r.checked) || radios[0]).value;
      go.disabled = true; radios.forEach((r) => { r.disabled = true; }); d.setBusy(true);
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
        status.textContent = `${done} Sorting into topics…`;
        cancel.disabled = true;
        try {
          const r = await api('POST', '/api/topics/auto-sort', { ids: res.ids });
          const list = (r.topics || []).map((t) => `${t.name} (${t.count}${t.new ? ', new' : ''})`).join(', ');
          status.textContent = r.sorted ? `${done} Sorted into ${r.topics.length} topic${r.topics.length === 1 ? '' : 's'}${r.by === 'regions' ? ' by similarity (no AI answer)' : ''}: ${list}.` : `${done} ${r.note || ''}`;
          rb.refreshGraph();
          window.dispatchEvent(new CustomEvent('rb:topics-changed'));
        } catch (e) { status.textContent = `${done} Couldn’t sort them into topics: ${e.message}`; }
        finally { cancel.disabled = false; }
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
    const d = dialog('Import bookmarks', `${total} bookmark${total === 1 ? '' : 's'} in ${groups.length} folder${groups.length === 1 ? '' : 's'}. ${papers} look like papers and are ticked. Each folder becomes a topic. Links with a DOI or arXiv id are added from their records; links to PDFs are downloaded.`);
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
    d.card.append(h('p', { text: 'Each folder becomes a topic. Change a name, or clear it to leave that folder out of topics.' }));
    const topics = new Set((pv.topics || []).map((t) => t.toLowerCase()));
    const inputs = new Map();
    const notes = new Map();
    const refresh = () => {
      const by = new Map();
      for (const [dir, inp] of inputs) { const v = inp.value.trim().toLowerCase(); if (v) by.set(v, (by.get(v) || []).concat(dir)); }
      for (const [dir, inp] of inputs) {
        const v = inp.value.trim(), lv = v.toLowerCase();
        const shared = (by.get(lv) || []).filter((x) => x !== dir).map((x) => pv.folders.find((f) => f.dir === x).label);
        notes.get(dir).textContent = !v ? 'no topic' : shared.length ? `shared with ${shared.slice(0, 2).join(', ')}${shared.length > 2 ? '…' : ''}`
          : topics.has(lv) ? 'joins your topic' : 'new topic';
      }
    };
    const rows = pv.folders.map((f) => {
      const inp = h('input', { type: 'text', class: 'bk-name', value: f.topic, 'aria-label': `Topic for ${f.label}`, spellcheck: 'false', oninput: refresh });
      const note = h('span', { class: 'why' });
      inputs.set(f.dir, inp); notes.set(f.dir, note);
      return h('div', { class: 'bk-lrow' }, h('span', { class: 'bk-ldir', title: f.label, text: f.label }), h('span', { class: 'n', text: `${f.pdfs}` }), inp, note);
    });
    const list = h('div', { class: 'bk-list bk-link' }, h('div', { class: 'bk-lrow bk-lhead' }, h('span', { text: 'Folder' }), h('span', { text: 'PDFs' }), h('span', { text: 'Topic' }), h('span')), ...rows);
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
    d.card.append(list, h('div', { class: 'bk-acts bk-acts-l' }, suggest, reset), h('div', { class: 'bk-bar' }, bar), h('div', { class: 'bk-acts' }, status, cancel, go));
    go.addEventListener('click', async () => {
      const names = {};
      for (const [dir, inp] of inputs) names[dir] = inp.value.trim();
      go.disabled = true; suggest.disabled = true; inputs.forEach((i) => { i.disabled = true; });
      let job;
      try { job = (await api('POST', '/api/linked-folders', { path: pv.path, names })).job; }
      catch (e) { status.textContent = e.message; go.disabled = false; suggest.disabled = false; inputs.forEach((i) => { i.disabled = false; }); return; }
      cancel.textContent = 'Close (keeps going)';
      cancel.onclick = () => { d.close(); watchImports(); };
      while (job && (job.state === 'running' || job.state === 'paused')) {
        if (!d.card.isConnected) { watchImports(); return; }  // closed: the gauge in the corner follows it from here
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
    });
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
  let watching = null;
  async function watchImports() {
    if (watching) return;
    const tick = async () => {
      let r;
      try { r = await api('GET', '/api/linked-folders'); } catch { watching = null; return; }
      let busy = false;
      for (const f of r.folders || []) {
        const j = f.job;
        if (!j) continue;
        const live = ['running', 'paused'].includes(j.state);
        busy = busy || live;
        if (live || tray.has(j.id)) {
          tray.set(j.id, { ...j, name: j.name || f.name, stop: () => api('POST', `/api/linked-folders/jobs/${j.id}/stop`).catch(() => {}) });
        }
      }
      watching = busy ? setTimeout(tick, 1500) : null;
      if (!busy) { rb.refreshGraph(); window.dispatchEvent(new CustomEvent('rb:topics-changed')); }
    };
    watching = setTimeout(tick, 0);
  }
  setTimeout(watchImports, 2500);       // an import that was running when the app was opened again
  setInterval(watchImports, 60000);      // and the app's own checks every few minutes

  window.__rbBulk = { pickFolder: () => picker.click(), pickBookmarks: () => bmInput.click(), parseBookmarks, folderDialog, bookmarksDialog,
    pickLinkFolder, linkDialog, tray, watchImports };
  const C = window.RB && window.RB.commands;
  if (C && C.register) {
    C.register({ id: 'import.folder', title: 'Import a folder of PDFs', group: 'Add', description: 'Add every PDF in a folder; its subfolders can become topics.',
      aliases: ['bulk', 'folder', 'directory', 'many pdfs', 'import all'], run: () => picker.click() });
    C.register({ id: 'import.linkFolder', title: 'Add folders of papers (keep them where they are)', group: 'Add',
      description: 'Link a folder you keep PDFs in, such as one in OneDrive: nothing is copied, and each subfolder becomes a topic.',
      aliases: ['onedrive', 'link folder', 'watch folder', 'subfolders', 'in place', 'dropbox', 'icloud'], run: () => pickLinkFolder() });
    C.register({ id: 'import.bookmarks', title: 'Import browser bookmarks', group: 'Add', description: 'Add papers from a bookmarks export; each bookmark folder becomes a topic.',
      aliases: ['bookmarks', 'favorites', 'chrome', 'firefox', 'safari'], run: () => bmInput.click() });
  }
})();
