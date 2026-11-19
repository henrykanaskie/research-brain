/* research_brain — the chat's layout (the side window's "Ask" panel), arranged after the other chat scripts have
 * added their controls (app.js, research.js, voice.js, latex-editor.js):
 *   header    the title, then one row of matching icon buttons: Past chats, New chat, Memory, Continue on
 *             your phone, Close (each keeps its id, title and what it does)
 *   composer  one rounded box: the question on top; under it Quick/Research and Symbols on the left, and the
 *             mic, voice mode and a round send button on the right
 * Only moves and dresses existing elements; their handlers stay where they were written. Styles: style.css,
 * "Chat makeover".
 */
(() => {
  'use strict';
  const $ = (sel, root = document) => root.querySelector(sel);
  const chat = $('#chat');
  if (!chat) return;
  const svg = (paths, w = 18) => `<svg width="${w}" height="${w}" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const ICON = {
    history: svg('<path d="M3.5 10a6.5 6.5 0 1 0 1.9-4.6"/><path d="M3.2 3.6v2.7h2.7"/><path d="M10 6.5V10l2.4 1.6"/>'),
    newchat: svg('<path d="M10.5 3.5H5a1.5 1.5 0 0 0-1.5 1.5v10A1.5 1.5 0 0 0 5 16.5h10a1.5 1.5 0 0 0 1.5-1.5V9.5"/><path d="M14.6 2.9a1.5 1.5 0 0 1 2.1 2.1L10.5 11.2l-2.8.7.7-2.8z"/>'),
    memory: svg('<path d="M7.2 3.8A2.6 2.6 0 0 0 4.6 6.4a2.6 2.6 0 0 0-1.1 4.2 2.7 2.7 0 0 0 2.4 4.1A2.4 2.4 0 0 0 10 16V5a2.3 2.3 0 0 0-2.8-1.2z"/><path d="M12.8 3.8A2.6 2.6 0 0 1 15.4 6.4a2.6 2.6 0 0 1 1.1 4.2 2.7 2.7 0 0 1-2.4 4.1A2.4 2.4 0 0 1 10 16"/>'),
    phone: svg('<rect x="6" y="2.5" width="8" height="15" rx="1.8"/><path d="M9 14.8h2"/>'),
    send: svg('<path d="M10 15.5V4.5"/><path d="M5.5 9 10 4.5 14.5 9"/>', 18),
  };
  function dress(btn, icon, label) {
    if (!btn) return;
    btn.classList.remove('btn-text', 'btn-sm');
    btn.classList.add('btn-icon', 'ch-icon');
    btn.innerHTML = icon;
    btn.setAttribute('aria-label', label);
    if (!btn.title) btn.title = label;
  }

  // ---- header: title, then the icon buttons in one row
  const head = $('.chat-head', chat);
  if (head) {
    dress($('#btn-chat-history'), ICON.history, 'Past chats');
    dress($('#btn-chat-clear'), ICON.newchat, 'New chat');
    dress($('#btn-memory'), ICON.memory, 'Memory');
    const ho = $('#btn-chat-handoff');
    if (ho) { ho.classList.add('ch-icon'); ho.innerHTML = ICON.phone; }
    const close = $('#btn-chat-close'); if (close) close.classList.add('ch-icon');
    const tools = document.createElement('div');
    tools.className = 'ch-tools';
    for (const id of ['#btn-chat-history', '#btn-chat-clear', '#btn-memory', '#btn-chat-handoff', '#btn-chat-close']) {
      const b = $(id, head) || $(id); if (b) tools.appendChild(b);
    }
    const sp = $('.spacer', head); if (sp) sp.remove();
    head.appendChild(tools);
  }

  // ---- composer: one rounded box around the question and its controls
  const form = $('#chat-form'), row = $('.chat-row', form || chat), ta = $('#chat-input');
  if (form && row && ta) {
    const box = document.createElement('div');
    box.className = 'composer';
    const bar = document.createElement('div');
    bar.className = 'cmp-bar';
    const left = document.createElement('div'); left.className = 'cmp-left';
    const right = document.createElement('div'); right.className = 'cmp-right';
    const mode = $('.rs-mode', form);
    const seg = mode && $('.rs-seg', mode);
    if (seg) left.appendChild(seg);
    const symbols = $('.le-tools', row); if (symbols) left.appendChild(symbols);
    for (const id of ['#btn-chat-mic', '#btn-chat-voice', '#btn-chat-send']) { const b = $(id, form); if (b) right.appendChild(b); }
    const send = $('#btn-chat-send', right);
    if (send) {
      send.classList.add('cmp-send');
      send.setAttribute('aria-label', 'Ask');
      if (!send.title) send.title = 'Ask (Enter)';
      send.innerHTML = ICON.send;
    }
    bar.append(left, right);
    const preview = $('#chat-preview', form);
    box.append(ta, bar);
    row.replaceWith(box);
    if (preview) form.insertBefore(preview, box);
    if (mode) form.insertBefore(mode, box);  // what's left of it: the Research notices, above the box
    // the box shows focus as a whole
    ta.addEventListener('focus', () => box.classList.add('focus'));
    ta.addEventListener('blur', () => box.classList.remove('focus'));
    box.addEventListener('mousedown', (e) => { if (e.target === box || e.target === bar || e.target === left || e.target === right) { e.preventDefault(); ta.focus(); } });
  }
  chat.classList.add('chat-v2');
})();
