/* research_brain — talking with the AI: voice mode and Read aloud.
 *
 * Voice mode (the waveform button by the question): you talk, it answers out loud, and then it listens again,
 * like Claude's voice mode.
 *   Listening   in the Mac app, macOS dictation types what you say into the question (app/macwindow.py); in a
 *               browser, its speech recognition. A pause of about 1.5 s after you speak sends the question.
 *   Thinking    the question goes to the chat as if you typed it (same scope, history, memory).
 *   Speaking    the answer is read out sentence by sentence while it's being written, in your Mac's own voice
 *               (the browser's speech synthesis, which uses the system voices; on a Mac without it, the server's
 *               `say`). Code, tables, diagrams and citations are skipped; math is read plainly.
 *   Tap the orb to interrupt it (it stops talking and listens), or to pause listening. End leaves voice mode.
 * Read aloud (on every answer): the same voice, for one answer.
 * Nothing is recorded or kept: speech goes to macOS dictation (or the browser's recognizer), and what you said
 * stays as the typed question in the chat. Needs window.__rb (static/app.js) and window.__rbLive (live.js).
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { S, h, api, toast } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const isMac = () => { const l = window.__rbLive; if (l) return l.app === 'mac'; try { return sessionStorage.getItem('rb.appWindow') === 'mac'; } catch { return false; } };
  const synth = 'speechSynthesis' in window ? window.speechSynthesis : null;

  // ====================================================================== speaking
  /** What to say for a piece of an answer: prose only (code, tables, diagrams and citations skipped, math plain). */
  function speakable(md) {
    let t = String(md || '');
    t = t.replace(/<remember>[\s\S]*?<\/remember>/gi, '').replace(/<remember>[\s\S]*$/i, '');
    t = t.replace(/```(mermaid|svg)[\s\S]*?```/gi, ' (There’s a diagram in the chat.) ');
    t = t.replace(/```[\s\S]*?```/g, ' (There’s some code in the chat.) ');
    t = t.replace(/^\s*\|.*\|\s*$/gm, '').replace(/(?:\n\s*){2,}(?=\S)/g, '\n\n');
    t = t.replace(/\$\$([\s\S]*?)\$\$/g, (_, m) => ` ${plainMath(m)} `).replace(/\\\[([\s\S]*?)\\\]/g, (_, m) => ` ${plainMath(m)} `);
    t = t.replace(/\$([^$\n]+)\$/g, (_, m) => plainMath(m)).replace(/\\\(([\s\S]*?)\\\)/g, (_, m) => plainMath(m));
    t = t.replace(/\[\[[^\]]+\]\](\s*(p\.|§)\s*[\w.]+)?/g, '');          // citations
    t = t.replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');  // images, links
    t = t.replace(/https?:\/\/\S+/g, 'a link');
    t = t.replace(/^#{1,6}\s*/gm, '').replace(/^\s*[-*+]\s+/gm, '').replace(/^\s*\d+\.\s+/gm, '').replace(/^>\s?/gm, '');
    t = t.replace(/(\*\*|__|\*|_|~~|`)/g, '');
    return t.replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
  }
  function plainMath(m) {
    return String(m).replace(/\\(?:mathrm|text|mathbf|mathit|operatorname)\{([^}]*)\}/g, '$1')
      .replace(/\\(alpha|beta|gamma|delta|epsilon|theta|lambda|mu|nu|pi|rho|sigma|tau|phi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Sigma|Phi|Psi|Omega)/g, ' $1 ')
      .replace(/\\frac\{([^}]*)\}\{([^}]*)\}/g, '$1 over $2').replace(/\\sqrt\{([^}]*)\}/g, 'root of $1')
      .replace(/\^\{?2\}?/g, ' squared').replace(/\^\{?3\}?/g, ' cubed').replace(/\^\{?([^}\s]+)\}?/g, ' to the $1')
      .replace(/_\{?([^}\s]+)\}?/g, ' $1').replace(/\\approx/g, ' approximately ').replace(/\\times/g, ' times ')
      .replace(/\\(le|leq)/g, ' at most ').replace(/\\(ge|geq)/g, ' at least ').replace(/\\[a-zA-Z]+/g, ' ')
      .replace(/[{}\\]/g, ' ').replace(/=/g, ' equals ').replace(/\s+/g, ' ').trim();
  }
  function pickVoice() {
    if (!synth) return null;
    const vs = synth.getVoices();
    if (!vs.length) return null;
    const lang = (navigator.language || 'en-US').toLowerCase();
    const same = vs.filter((v) => v.lang && v.lang.toLowerCase().replace('_', '-').startsWith(lang.slice(0, 2)));
    const good = ['Ava', 'Samantha', 'Allison', 'Susan', 'Serena', 'Daniel', 'Karen', 'Moira', 'Tessa', 'Alex'];
    for (const g of good) { const v = same.find((x) => x.name.startsWith(g) && /premium|enhanced/i.test(x.name)); if (v) return v; }
    for (const g of good) { const v = same.find((x) => x.name.startsWith(g)); if (v) return v; }
    return same.find((v) => v.localService) || same[0] || vs[0];
  }
  if (synth && synth.addEventListener) synth.addEventListener('voiceschanged', () => {});
  let serverSay = null;  // the server can speak with macOS `say` (checked once)
  async function canServerSay() {
    if (serverSay !== null) return serverSay;
    try { serverSay = !!(await api('GET', '/api/voice')).say; } catch { serverSay = false; }
    return serverSay;
  }
  canServerSay();
  const useSynth = () => !!(synth && synth.getVoices().length);
  const canSpeak = () => useSynth() || serverSay === true;

  /** A queue of sentences, spoken one after another; onIdle runs when it has nothing left to say. */
  const speech = {
    queue: [], busy: false, epoch: 0, onIdle: null,
    say(text) {
      const t = String(text || '').trim();
      if (!t || !/[\p{L}\p{N}]/u.test(t)) return;
      this.queue.push(t);
      if (!this.busy) this.next();
    },
    async next() {
      const t = this.queue.shift();
      if (t == null) { this.busy = false; const f = this.onIdle; if (f) f(); return; }
      this.busy = true;
      const ep = this.epoch;
      const done = () => { if (ep === this.epoch) this.next(); };
      if (useSynth()) {
        const u = new SpeechSynthesisUtterance(t);
        const v = pickVoice(); if (v) { u.voice = v; u.lang = v.lang; }
        u.rate = 1.03;
        u.onend = done; u.onerror = done;
        synth.speak(u);
      } else if (await canServerSay()) {
        try { await api('POST', '/api/voice/say', { text: t }); } catch { /* stopped, or failed: go on */ }
        done();
      } else done();
    },
    stop() {
      this.epoch++; this.queue = []; this.busy = false;
      if (synth) synth.cancel();
      if (serverSay) api('POST', '/api/voice/stop', {}).catch(() => {});
    },
    get speaking() { return this.busy || this.queue.length > 0; },
  };

  /** Feeds a growing answer to the speech queue, a sentence at a time, never inside code or display math. */
  function sentenceFeeder() {
    let from = 0;
    return (raw, final) => {
      const text = String(raw || '');
      for (;;) {
        const before = text.slice(0, from);
        const fences = (before.match(/```/g) || []).length;
        const rest = text.slice(from);
        if (fences % 2 === 1) {  // inside a code block: skip to its end
          const close = rest.indexOf('```');
          if (close < 0) return;
          from += close + 3; continue;
        }
        const dd = (before.match(/\$\$/g) || []).length;
        if (dd % 2 === 1) { const close = rest.indexOf('$$'); if (close < 0) return; from += close + 2; continue; }
        let limit = rest.length;
        const fence = rest.indexOf('```'); if (fence >= 0) limit = Math.min(limit, fence);
        const dm = rest.indexOf('$$'); if (dm >= 0) limit = Math.min(limit, dm);
        let end = -1;
        if (final || limit < rest.length) end = limit;
        else {
          const re = /[.!?](?=\s)|\n\n/g; let m;
          while ((m = re.exec(rest.slice(0, limit)))) end = m.index + m[0].length;
        }
        if (end <= 0) {
          if (limit < rest.length) {  // a block starts right here: say what it is, then step over it
            const blk = rest.slice(limit);
            const close = blk.startsWith('```') ? blk.indexOf('```', 3) : blk.indexOf('$$', 2);
            if (close < 0) return;
            const whole = blk.slice(0, close + 3);
            speech.say(speakable(whole));
            from += limit + close + (blk.startsWith('```') ? 3 : 2); continue;
          }
          return;
        }
        speech.say(speakable(rest.slice(0, end)));
        from += end;
        if (end >= rest.length) return;
      }
    };
  }

  // ---- Read aloud (one answer)
  let reading = null;  // the message being read
  function readAloud(m) {
    if (reading === m) { speech.stop(); reading = null; rb.renderChat(); return; }
    stopVoice(false);
    speech.stop();
    reading = m;
    speech.onIdle = () => { if (reading === m) { reading = null; rb.renderChat(); } };
    for (const part of speakable(m.content).split(/(?<=[.!?])\s+|\n+/)) speech.say(part);
    rb.renderChat();
  }

  // ====================================================================== listening
  const listen = {
    on: false, rec: null, quietT: 0, giveUpT: 0, onHeard: null, lastVal: '',
    start(onHeard) {
      this.stop();
      this.on = true; this.onHeard = onHeard;
      const ta = $('#chat-input');
      ta.value = ''; ta.dispatchEvent(new Event('input'));
      ta.focus();
      this.giveUpT = setTimeout(() => { if (this.on && !ta.value.trim()) { this.stop(); setState('paused', 'Didn’t catch anything. Tap to talk.'); } }, 15000);
      if (isMac() && window.__rbLive && window.__rbLive.native({ dictate: true })) {
        this.lastVal = '';
        return;
      }
      if (!SR) { this.on = false; setState('paused', 'Speech recognition isn’t available here.'); return; }
      const rec = new SR();
      rec.continuous = false; rec.interimResults = true; rec.lang = navigator.language || 'en-US';
      let heard = '';
      rec.onresult = (e) => {
        let txt = '';
        for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
        heard = txt; ta.value = txt; ta.dispatchEvent(new Event('input')); setHeard(txt);
      };
      rec.onerror = (e) => { if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { toast('Voice needs the microphone: allow it for this page.', 'error'); stopVoice(); } };
      rec.onend = () => { const f = this.onHeard; const was = this.on; this.rec = null; if (was) { this.on = false; clearTimeout(this.giveUpT); if (f) f(heard.trim()); } };
      this.rec = rec;
      try { rec.start(); } catch { this.on = false; }
    },
    /** macOS dictation types into the question: a pause after some words means you're done */
    typed() {
      if (!this.on || !isMac()) return;
      const v = $('#chat-input').value;
      if (v === this.lastVal) return;
      this.lastVal = v; setHeard(v);
      clearTimeout(this.quietT);
      if (v.trim()) this.quietT = setTimeout(() => this.finish(), 1500);
    },
    finish() {
      if (!this.on) return;
      const v = $('#chat-input').value.trim();
      const f = this.onHeard;
      this.stop();
      if (f) f(v);
    },
    stop() {
      clearTimeout(this.quietT); clearTimeout(this.giveUpT);
      if (this.on && isMac() && window.__rbLive) window.__rbLive.native({ dictate: false });
      if (this.rec) { const r = this.rec; this.rec = null; this.on = false; try { r.abort(); } catch { /* ended */ } }
      this.on = false;
    },
  };

  // ====================================================================== voice mode
  let active = false, state = 'idle', feed = null;
  const bar = h('div', { class: 'voice-bar', hidden: true, role: 'region', 'aria-label': 'Voice mode' });
  const orb = h('button', { type: 'button', class: 'vb-orb', 'aria-label': 'Talk', title: 'Tap to interrupt or to talk' }, h('span', { class: 'vb-ring' }), h('span', { class: 'vb-core' }));
  const status = h('div', { class: 'vb-status', 'aria-live': 'polite' });
  const heardEl = h('div', { class: 'vb-heard' });
  const endBtn = h('button', { type: 'button', class: 'btn btn-sm', text: 'End', title: 'Leave voice mode (Esc)' });
  bar.append(orb, h('div', { class: 'vb-text' }, status, heardEl), endBtn);
  const LABEL = { listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking', paused: 'Paused' };
  function setState(st, note) {
    state = st;
    bar.dataset.state = st;
    status.textContent = note || LABEL[st] || '';
    orb.setAttribute('aria-label', st === 'speaking' ? 'Interrupt' : st === 'listening' ? 'Stop listening' : 'Talk');
  }
  function setHeard(t) { heardEl.textContent = t ? `“${t}”` : ''; }

  function listenNow() {
    if (!active) return;
    setHeard('');
    setState('listening');
    listen.start((q) => {
      if (!active) return;
      if (!q) { setState('paused', 'Didn’t catch anything. Tap to talk.'); return; }
      ask(q);
    });
  }
  function ask(q) {
    setState('thinking');
    setHeard(q);
    const ta = $('#chat-input'); ta.value = ''; ta.dispatchEvent(new Event('input'));
    feed = sentenceFeeder();
    speech.stop();
    speech.onIdle = () => { if (active && state === 'speaking' && !S.chatBusy) listenNow(); };
    rb.sendChat(q);
  }
  // the chat tells us how its answer is going (static/app.js sendChat)
  S.voiceHook = {
    live(text) {
      if (!active || !feed) return;
      if (state === 'thinking') setState('speaking');
      feed(text, false);
    },
    done(msg) {
      if (!active || !feed) return;
      if (msg && msg.error) { feed = null; setState('paused', 'That didn’t work. Tap to try again.'); return; }
      if (msg && msg.content) feed(msg.content, true);
      feed = null;
      if (state === 'thinking') setState('speaking');
      if (!speech.speaking) listenNow();  // nothing (left) to say
    },
  };

  function startVoice() {
    if (rb.claudeBlocked && rb.claudeBlocked()) return;
    if (!SR && !isMac()) { toast('Voice needs speech recognition, which this browser doesn’t have. The Mac app has it.', 'info'); return; }
    rb.openChat();
    reading = null; speech.stop();
    active = true;
    const form = $('#chat-form');
    form.classList.add('voice-on');
    bar.hidden = false;
    listenNow();
  }
  function stopVoice(render = true) {
    if (!active) return;
    active = false; feed = null;
    listen.stop(); speech.stop(); speech.onIdle = null;
    bar.hidden = true;
    $('#chat-form').classList.remove('voice-on');
    setState('idle');
    if (render) $('#chat-input').focus();
  }
  orb.addEventListener('click', () => {
    if (state === 'speaking') { speech.stop(); listenNow(); }          // interrupt: it listens
    else if (state === 'listening') { listen.finish(); }                 // done talking: send what it has
    else if (state === 'paused' || state === 'idle') listenNow();
  });
  endBtn.addEventListener('click', () => stopVoice());

  // ---- wiring into the chat
  const form = $('#chat-form');
  if (form) {
    form.insertBefore(bar, form.firstChild);
    const row = $('.chat-row', form), send = $('#btn-chat-send');
    const vbtn = h('button', { id: 'btn-chat-voice', type: 'button', class: 'btn btn-icon chat-voice', title: 'Voice mode: talk, and hear the answer', 'aria-label': 'Voice mode' },
      h('span', { html: '<svg width="18" height="16" viewBox="0 0 18 16" aria-hidden="true"><path d="M2 6.5v3M5 4v8M8 1.5v13M11 4v8M14 6v4M16.5 7.2v1.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>' }));
    vbtn.addEventListener('click', () => (active ? stopVoice() : startVoice()));
    if (row && send) row.insertBefore(vbtn, send);
    const ta = $('#chat-input');
    ta.addEventListener('input', () => listen.typed());
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && active) { e.preventDefault(); e.stopPropagation(); stopVoice(); } }, true);
    // closing the chat ends voice mode
    new MutationObserver(() => { if ($('#chat').hidden && active) stopVoice(false); }).observe($('#chat'), { attributes: true, attributeFilter: ['hidden'] });
  }
  if (synth && synth.getVoices().length === 0 && synth.addEventListener) synth.addEventListener('voiceschanged', () => rb.renderChat());

  window.__rbVoice = { start: startVoice, stop: stopVoice, readAloud, isReading: (m) => reading === m, canSpeak: () => canSpeak() || !!synth,
    speakable, get state() { return active ? state : 'off'; }, _speech: speech, _feed: sentenceFeeder };
})();
