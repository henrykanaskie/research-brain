/* research_brain: the "Import notes" dialog (Add menu, or drop note files on it).
 *   Notes from another app: .md/.txt (Obsidian, Bear, Logseq), .docx (Word, Google Docs), .html (Notion,
 *   Evernote, OneNote, Apple Notes exports, web pages), or a .zip of them -> POST /api/import/notes
 *   Handwritten or scanned pages: a PDF, or photos of the pages (one note, a page each) kept as they are,
 *   optionally written out as text by the AI -> POST /api/import/handwritten, then /api/notes/{id}/transcribe
 * Uses window.__rb (api, toast, refreshGraph, openNote, S) from app.js. See DESIGN.md for the visual rules.
 */
(() => {
  'use strict';

  const CSS = `
  .ni-modal { position: fixed; inset: 0; z-index: 72; display: flex; align-items: center; justify-content: center; padding: 16px;
    background: color-mix(in srgb, var(--ground) 72%, transparent); }
  .ni-card { width: min(560px, 100%); max-height: calc(100vh - 32px); overflow-y: auto; overscroll-behavior: contain;
    background: var(--sheet); border: 1px solid var(--rule); color: var(--ink); }
  .ni-head { display: flex; align-items: flex-start; gap: 12px; padding: 28px 32px 0; }
  .ni-title { font: 600 24px/1.15 var(--serif); letter-spacing: -.01em; margin: 0; }
  .ni-sub { margin: 8px 0 0; color: var(--ink-2); font-size: 13px; line-height: 1.5; }
  .ni-body { padding: 20px 32px 0; }
  .ni-opt { border: 1px solid var(--rule); border-radius: 3px; padding: 14px 16px; margin: 0 0 12px; }
  .ni-opt.drag { border-color: var(--ink); background: var(--ground); }
  .ni-opt h3 { font: 600 14px/1.3 var(--sans); margin: 0 0 4px; }
  .ni-opt p { margin: 0 0 10px; color: var(--ink-2); font-size: 12.5px; line-height: 1.5; }
  .ni-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .ni-row input[type=text] { flex: 1; min-width: 160px; height: 30px; padding: 0 8px; font: 13px var(--sans); }
  .ni-check { display: flex; align-items: flex-start; gap: 8px; margin: 10px 0 0; font-size: 13px; cursor: pointer; }
  .ni-check input { accent-color: var(--ink); margin: 2px 0 0; flex: none; }
  .ni-check .menu-hint { display: block; margin-top: 1px; }
  .ni-tip { margin: 4px 0 0; color: var(--ink-2); font-size: 12px; line-height: 1.5; }
  .ni-result { font: 400 16px/1.45 var(--serif); margin: 0 0 8px; }
  .ni-list { list-style: none; margin: 0; padding: 0; max-height: 220px; overflow: auto; border-top: 1px solid var(--rule); }
  .ni-list li { border-bottom: 1px solid var(--rule); }
  .ni-list button { all: unset; display: block; width: 100%; box-sizing: border-box; padding: 6px 4px; cursor: pointer; font: 14px/1.35 var(--serif); }
  .ni-list button:hover, .ni-list button:focus-visible { background: var(--ground); }
  .ni-skip { margin: 12px 0 0; padding: 0 0 0 12px; border-left: 2px solid var(--rule); font-size: 12.5px; color: var(--ink-2); }
  .ni-skip ul { margin: 6px 0 0; padding-left: 16px; max-height: 120px; overflow: auto; }
  .ni-error { margin: 0 0 12px; padding: 2px 0 2px 12px; border-left: 2px solid var(--red); font-size: 13px; line-height: 1.5; }
  .ni-busy { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--ink-2); margin: 4px 0 12px; }
  .ni-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 20px 32px 24px; }
  @media (max-width: 560px) { .ni-head, .ni-body, .ni-actions { padding-left: 20px; padding-right: 20px; } }
  `;
  const NOTE_ACCEPT = '.md,.markdown,.txt,.text,.docx,.html,.htm,.zip,text/markdown,text/plain,text/html,application/zip,application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  const SCAN_ACCEPT = 'application/pdf,.pdf,image/*,.heic,.heif';

  const rb = () => window.__rb || {};
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : (many || one + 's')}`;
  const $ = (s, r) => (r || document).querySelector(s);
  let root = null, lastFocus = null, busy = false;

  function agentName() {
    const st = (rb().S || {}).status || {};
    return (st.agent && !st.agent.none && st.agent.name) || 'your AI';
  }
  function agentReadsImages() {
    const st = (rb().S || {}).status || {};
    return !!(st.agent && !st.agent.none && st.agent.reads_images);  // app/agents: Backend.reads_images
  }

  function build() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);
    root = document.createElement('div');
    root.className = 'ni-modal';
    root.hidden = true;
    root.innerHTML = `
      <div class="ni-card" role="dialog" aria-modal="true" aria-labelledby="ni-title">
        <div class="ni-head">
          <div style="flex:1;min-width:0">
            <h2 id="ni-title" class="ni-title">Import notes</h2>
            <p class="ni-sub">Bring in notes you wrote somewhere else. Each one becomes a note here, with its bullets, headings and links kept.</p>
          </div>
          <button class="btn btn-icon ni-x" type="button" title="Close (Esc)" aria-label="Close">
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" stroke-width="1.3"/></svg>
          </button>
        </div>
        <div class="ni-body"></div>
        <div class="ni-actions"><button class="btn ni-done" type="button">Close</button></div>
      </div>`;
    document.body.appendChild(root);
    $('.ni-x', root).addEventListener('click', close);
    $('.ni-done', root).addEventListener('click', close);
    root.addEventListener('pointerdown', (e) => { if (e.target === root && !busy) close(); });
    root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !busy) { e.stopPropagation(); close(); } });
  }

  function renderChoices(error) {
    const body = $('.ni-body', root);
    const vision = agentReadsImages();
    body.innerHTML = `
      ${error ? `<div class="ni-error">${esc(error)}</div>` : ''}
      <section class="ni-opt" data-kind="notes">
        <h3>Notes from another app</h3>
        <p>Markdown or text files (Obsidian, Bear, Logseq), Word files (Google Docs: File → Download → .docx), web pages
          and Notion, Evernote or OneNote exports. A .zip of a whole folder works too.</p>
        <div class="ni-row"><button class="btn btn-primary ni-pick-notes" type="button">Choose files…</button>
          <span class="menu-hint">or drop them here</span></div>
        <input class="ni-notes-input" type="file" multiple accept="${NOTE_ACCEPT}" hidden>
      </section>
      <section class="ni-opt" data-kind="scan">
        <h3>Handwritten or scanned pages</h3>
        <p>A PDF, or photos of your pages (several photos become one note, a page each). They're kept just as they are.</p>
        <div class="ni-row">
          <input class="ni-title-in" type="text" placeholder="Title (optional)" aria-label="Title of the note">
          <button class="btn btn-primary ni-pick-scan" type="button">Choose PDF or photos…</button>
        </div>
        <label class="ni-check"><input class="ni-ocr" type="checkbox" ${vision ? '' : 'disabled'}>
          <span>Also write it out as text<span class="menu-hint">${vision
            ? `${esc(agentName())} reads the pictures of the pages (they're sent to it, this once) and writes them under your notes: nested bullets, equations in LaTeX. You can also do this later from the note.`
            : `Needs an AI that reads images: Claude Code, or a local vision model (Ollama, LM Studio). Choose one in Settings → AI.`}</span></span></label>
        <input class="ni-scan-input" type="file" multiple accept="${SCAN_ACCEPT}" hidden>
      </section>
      <p class="ni-tip">Tip: you can also paste straight into a note. Bullets, headings, bold and links from Google Docs,
        Word, Apple Notes and Notion come along. Figures and screenshots can be pasted or dropped in too.</p>`;
    const nIn = $('.ni-notes-input', body), sIn = $('.ni-scan-input', body);
    $('.ni-pick-notes', body).addEventListener('click', () => nIn.click());
    $('.ni-pick-scan', body).addEventListener('click', () => sIn.click());
    nIn.addEventListener('change', () => { const f = Array.from(nIn.files); nIn.value = ''; if (f.length) importNotes(f); });
    sIn.addEventListener('change', () => { const f = Array.from(sIn.files); sIn.value = ''; if (f.length) importScan(f); });
    for (const sec of body.querySelectorAll('.ni-opt')) {
      sec.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); sec.classList.add('drag'); });
      sec.addEventListener('dragleave', () => sec.classList.remove('drag'));
      sec.addEventListener('drop', (e) => {
        e.preventDefault(); e.stopPropagation(); sec.classList.remove('drag');
        const f = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
        if (!f.length) return;
        if (sec.dataset.kind === 'notes') importNotes(f); else importScan(f);
      });
    }
    setTimeout(() => $('.ni-pick-notes', body).focus(), 0);
  }

  function renderBusy(text) {
    busy = true;
    $('.ni-body', root).innerHTML = `<div class="ni-busy"><span class="spin"></span><span>${esc(text)}</span></div>`;
    $('.ni-done', root).disabled = true;
  }
  function idle() { busy = false; $('.ni-done', root).disabled = false; }

  function renderResult(created, skipped, extra) {
    idle();
    const body = $('.ni-body', root);
    const head = created.length ? `Imported ${plural(created.length, 'note')}.` : 'Nothing was imported.';
    body.innerHTML = `<p class="ni-result">${esc(head)}</p>
      ${extra ? `<p class="ni-tip">${esc(extra)}</p>` : ''}
      ${created.length ? `<ul class="ni-list">${created.slice(0, 300).map((c) => `<li><button type="button" data-id="${esc(c.id)}">${esc(c.title)}</button></li>`).join('')}</ul>` : ''}
      ${skipped.length ? `<details class="ni-skip" ${created.length ? '' : 'open'}><summary>${esc(plural(skipped.length, 'file'))} left out</summary>
        <ul>${skipped.slice(0, 200).map((s) => `<li>${esc(s.name)}: ${esc(s.why)}</li>`).join('')}</ul></details>` : ''}
      <div class="ni-row" style="margin-top:14px"><button class="btn ni-more" type="button">Import more</button></div>`;
    body.querySelectorAll('.ni-list button').forEach((b) => b.addEventListener('click', () => { close(); rb().openNote && rb().openNote(b.dataset.id, { center: true }); }));
    $('.ni-more', body).addEventListener('click', () => renderChoices());
    if (created.length && rb().refreshGraph) rb().refreshGraph();
  }

  async function post(path, form) {
    let r;
    try { r = await fetch(path, { method: 'POST', body: form }); }
    catch (e) { throw new Error("Couldn't reach research brain. Is it still running?"); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(rb().errText ? rb().errText(data, r.status) : ((data && data.detail) || `The server answered ${r.status}.`));
    return data;
  }

  async function importNotes(files) {
    renderBusy(`Reading ${plural(files.length, 'file')}…`);
    const form = new FormData();
    for (const f of files) form.append('files', f, f.name);
    try {
      const out = await post('/api/import/notes', form);
      renderResult(out.created || [], out.skipped || []);
    } catch (e) { idle(); renderChoices(e.message); }
  }

  /** photos the server can wrap in a PDF: JPEG; anything else (HEIC, PNG, WebP) drawn again as JPEG here */
  async function asJpeg(file) {
    if (file.type === 'image/jpeg' || /\.jpe?g$/i.test(file.name)) return file;
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 3000 / Math.max(bmp.width, bmp.height));
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    const g = c.getContext('2d');
    g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
    g.drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.9));
    if (!blob) throw new Error(`${file.name} couldn't be read as a picture`);
    return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' });
  }

  async function importScan(files) {
    const pdfs = files.filter((f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name));
    const imgs = files.filter((f) => !pdfs.includes(f));
    if (pdfs.length && imgs.length) { renderChoices('Choose one PDF, or only photos.'); return; }
    if (pdfs.length > 1) { renderChoices('Add one PDF at a time (each becomes its own note).'); return; }
    const title = ($('.ni-title-in', root) || {}).value || '';
    const ocr = !!(($('.ni-ocr', root) || {}).checked);
    renderBusy(imgs.length ? `Making ${plural(imgs.length, 'page')} into one note…` : 'Adding your pages…');
    try {
      const form = new FormData();
      if (pdfs.length) form.append('files', pdfs[0], pdfs[0].name);
      else {
        // photos in the order of their names (IMG_0001, IMG_0002...), the order the pages were taken
        imgs.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
        for (const f of imgs) { const j = await asJpeg(f); form.append('files', j, j.name); }
      }
      form.append('title', title);
      const note = await post('/api/import/handwritten', form);
      let extra = 'The pages are kept as they are; open the note to see them.';
      if (ocr) {
        renderBusy(`${agentName()} is reading your handwriting… (about half a minute a page)`);
        try {
          const r = await fetch(`/api/notes/${encodeURIComponent(note.id)}/transcribe`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ send_private: true }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(rb().errText ? rb().errText(d, r.status) : (d.detail || 'failed'));
          extra = 'Written out as text under the pages, which are kept as they are.';
        } catch (e) {
          extra = `The pages are kept, but writing them out didn't work: ${e.message} You can try again from the note.`;
        }
      }
      renderResult([{ id: note.id, title: note.title }], [], extra);
    } catch (e) { idle(); renderChoices(e.message); }
  }

  function closeAddMenu() {
    const m = document.getElementById('add-menu');
    if (m && !m.hidden) {
      m.hidden = true;
      const b = document.getElementById('btn-add-menu');
      if (b) b.setAttribute('aria-expanded', 'false');
    }
  }

  function open() {
    closeAddMenu();
    if (!root) build();
    lastFocus = document.activeElement;
    root.hidden = false;
    const S = rb().S;
    if (S) S.modalClose = close;
    if (!busy) renderChoices();
  }
  function close() {
    if (!root || root.hidden || busy) return;
    root.hidden = true;
    const S = rb().S;
    if (S && S.modalClose === close) S.modalClose = null;
    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  function wire() {
    const btn = document.getElementById('btn-import-notes');
    if (btn) btn.addEventListener('click', (e) => { e.stopPropagation(); open(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();
  window.__rbNoteImport = { open, close };
})();
