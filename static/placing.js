/* research_brain: a paper you add goes into its region.
 *
 * POST /api/regions/place-new (app/placing.py) looks at the papers added since it last looked, once each: one that
 * clearly belongs in a region is put there, and this says so (Undo, or Move to another region); one that could go in
 * a few is asked about here (its likely regions, any other, or Leave it). Asked when new papers reach the map, a few
 * seconds after the app opens (papers added from the phone or the browser extension meanwhile), and every few
 * minutes (a paper is looked at once its abstract or text is in).
 */
(() => {
  'use strict';
  const rb = () => window.__rb || null;
  const CSS = `
  .place-card { cursor: default; flex-direction: column; align-items: stretch; gap: 8px; max-width: 380px; }
  .place-card .t-msg b { font-weight: 600; }
  .place-row { display: flex; flex-wrap: wrap; gap: 6px; }
  .place-row select { font: inherit; font-size: 12px; max-width: 100%; }
  `;
  const style = document.createElement('style'); style.textContent = CSS; document.head.appendChild(style);

  async function call(method, path, body) {
    const r = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  const changed = () => { const r = rb(); if (r) r.refreshGraph(); window.dispatchEvent(new CustomEvent('rb:topics-changed')); };
  const short = (t) => (t.length > 70 ? t.slice(0, 68).trimEnd() + '…' : t) || 'This paper';
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); kids.flat().forEach((k) => k && e.append(k)); return e; };
  const btn = (text, fn, cls = 'btn btn-sm') => el('button', { type: 'button', className: cls, textContent: text, onclick: (e) => { e.stopPropagation(); fn(); } });

  let regions = null;
  async function allRegions() {
    if (!regions) regions = call('GET', '/api/topics').catch(() => []);
    return regions;
  }
  window.addEventListener('rb:topics-changed', () => { regions = null; });

  function card(kind) {
    const box = document.getElementById('toasts');
    const c = el('div', { className: `toast place-card ${kind}`, role: 'status' });
    c.addEventListener('click', (e) => e.stopPropagation());
    box.appendChild(c);
    const close = () => { c.style.opacity = '0'; c.style.transition = 'opacity .2s'; setTimeout(() => c.remove(), 200); };
    return { c, close };
  }
  async function put(id, slug, from) {
    if (from && from !== slug) await call('DELETE', `/api/topics/${encodeURIComponent(from)}/notes/${encodeURIComponent(id)}`);
    if (slug) await call('POST', `/api/topics/${encodeURIComponent(slug)}/notes`, { ids: [id] });
    changed();
  }
  /** A list of every region, to put it in one that wasn't offered. */
  async function picker(id, from, done) {
    const list = await allRegions();
    const sel = el('select', { 'aria-label': 'Region' }, el('option', { value: '', textContent: 'Another region…' }),
      list.filter((t) => t.slug !== from).map((t) => el('option', { value: t.slug, textContent: t.name })));
    sel.addEventListener('change', async () => { if (!sel.value) return; try { await put(id, sel.value, from); done(list.find((t) => t.slug === sel.value)); } catch (e) { say(e); } });
    return sel;
  }
  const say = (e) => { const r = rb(); if (r && r.toastErr) r.toastErr(e, 'Could not change its region'); };

  async function placed(p) {
    const { c, close } = card('ok');
    const msg = el('span', { className: 't-msg' }, 'Put ', el('b', { textContent: short(p.title) }), ' in the region ', el('b', { textContent: p.name }), '.');
    const row = el('div', { className: 'place-row' },
      btn('Undo', async () => { try { await put(p.id, '', p.slug); close(); } catch (e) { say(e); } }),
      await picker(p.id, p.slug, (t) => { msg.replaceChildren('Moved to ', el('b', { textContent: t.name }), '.'); setTimeout(close, 2500); }));
    c.append(msg, row);
    let t = setTimeout(close, 12000);
    c.addEventListener('mouseenter', () => clearTimeout(t));
    c.addEventListener('mouseleave', () => { clearTimeout(t); t = setTimeout(close, 6000); });
  }
  async function ask(p) {
    const { c, close } = card('ask');
    const done = (name) => { c.replaceChildren(el('span', { className: 't-msg' }, 'Put in ', el('b', { textContent: name }), '.')); setTimeout(close, 2500); };
    c.append(el('span', { className: 't-msg' }, 'Which region for ', el('b', { textContent: short(p.title) }), '?'),
      el('div', { className: 'place-row' },
        p.options.map((o) => btn(o.name, async () => { try { await put(p.id, o.slug); done(o.name); } catch (e) { say(e); } })),
        await picker(p.id, '', (t) => done(t.name)),
        btn('Leave it', close, 'btn btn-sm btn-text')));
  }

  let busy = false, again = false, soon = null;
  async function look() {
    if (busy) { again = true; return; }
    busy = true;
    try {
      const r = await call('POST', '/api/regions/place-new');
      if (r.placed.length) changed();
      for (const p of r.placed.slice(0, 4)) await placed(p);
      for (const p of r.ask.slice(0, 4)) await ask(p);
      const more = r.placed.length + r.ask.length - Math.min(4, r.placed.length) - Math.min(4, r.ask.length);
      if (more > 0 && rb() && rb().toast) rb().toast(`${more} more new papers were sorted or left where they are; see Regions.`);
    } catch { /* the server is away: the next look */ }
    busy = false;
    if (again) { again = false; look(); }
  }
  const later = (ms) => { clearTimeout(soon); soon = setTimeout(look, ms); };
  window.addEventListener('rb:papers-added', () => later(1500));
  setTimeout(look, 4000);
  setInterval(look, 3 * 60000);
  window.__rbPlacing = { look };
})();
