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
      ['one', `Everything in one topic, “${root}”`], ['none', 'No topics']];
    const radios = opts.map(([v]) => mode(v));
    radios[0].checked = true;
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
      const res = { added: 0, dup: 0, failed: [] };
      await pool(pdfs, 2, async (f) => {
        if (stop) return;
        const t = topicFor(f, m, root);
        try {
          const n = await rb.upload(`/api/import/pdf?quick=1${t ? `&topic=${enc(t)}` : ''}`, f);
          if (n.duplicate) res.dup++; else res.added++;
        } catch (e) { res.failed.push(`${f.name}: ${e.message}`); }
      }, (done) => { bar.style.width = `${Math.round((done / pdfs.length) * 100)}%`; status.textContent = `${done} of ${pdfs.length}`; });
      d.setBusy(false);
      status.textContent = `${res.added} added, ${res.dup} already in your library${res.failed.length ? `, ${res.failed.length} couldn’t be read` : ''}${stop ? ' (stopped)' : ''}.`;
      if (res.failed.length) status.title = res.failed.slice(0, 20).join('\n');
      go.hidden = true; cancel.textContent = 'Done'; cancel.onclick = d.close;
      rb.refreshGraph();
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

  window.__rbBulk = { pickFolder: () => picker.click(), pickBookmarks: () => bmInput.click(), parseBookmarks, folderDialog, bookmarksDialog };
  const C = window.RB && window.RB.commands;
  if (C && C.register) {
    C.register({ id: 'import.folder', title: 'Import a folder of PDFs', group: 'Add', description: 'Add every PDF in a folder; its subfolders can become topics.',
      aliases: ['bulk', 'folder', 'directory', 'many pdfs', 'import all'], run: () => picker.click() });
    C.register({ id: 'import.bookmarks', title: 'Import browser bookmarks', group: 'Add', description: 'Add papers from a bookmarks export; each bookmark folder becomes a topic.',
      aliases: ['bookmarks', 'favorites', 'chrome', 'firefox', 'safari'], run: () => bmInput.click() });
  }
})();
