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
  /** Math read as speech: each letter its own symbol ("mc" is "m c"), Greek letters and functions by name, and
   *  powers, fractions and relations in words. */
  function plainMath(m) {
    const keep = [];
    const K = (w) => `\u0001${keep.push(w) - 1}\u0002`;  // words the reader adds itself: never split into letters
    const FUNCS = new Set(['sin', 'cos', 'tan', 'sinh', 'cosh', 'tanh', 'log', 'ln', 'exp', 'max', 'min', 'lim', 'det', 'arg', 'mod', 'sup', 'inf']);
    let t = String(m);
    t = t.replace(/\\(?:mathrm|text|textrm|mathbf|mathit|operatorname|mathsf)\{([^}]*)\}/g, (_, w) => ` ${K(w)} `);  // words and units
    t = t.replace(/\\(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega)(?![a-zA-Z])/g,
      (_, g) => ` ${K(g.replace(/^var/, ''))} `);
    t = t.replace(/\\(sin|cos|tan|sinh|cosh|tanh|log|ln|exp|max|min|lim|det|arg|sup|inf)(?![a-zA-Z])/g, (_, f) => ` ${K(f)} `);
    t = t.replace(/\\frac\{([^}]*)\}\{([^}]*)\}/g, `$1 ${K('over')} $2`).replace(/\\sqrt\{([^}]*)\}/g, `${K('root of')} $1`);
    t = t.replace(/\^\{?2\}?(?![0-9])/g, ` ${K('squared')} `).replace(/\^\{?3\}?(?![0-9])/g, ` ${K('cubed')} `)
      .replace(/\^\{?-1\}?/g, ` ${K('inverse')} `).replace(/\^\{([^}]*)\}|\^(\S)/g, (_, a, b2) => ` ${K('to the')} ${a || b2} `);
    // a subscript that's a word ("tot", "eff", "max") is read as the word; letters and numbers one by one
    t = t.replace(/_\{([^}]*)\}|_(\S)/g, (_, a, b2) => { const v = a || b2; return /^[A-Za-z]{3,}$/.test(v) ? ` ${K(v)} ` : ` ${v} `; });
    t = t.replace(/\\approx/g, ` ${K('approximately')} `).replace(/\\times/g, ` ${K('times')} `).replace(/\\cdot/g, ` ${K('dot')} `)
      .replace(/\\(le|leq)(?![a-z])/g, ` ${K('at most')} `).replace(/\\(ge|geq)(?![a-z])/g, ` ${K('at least')} `)
      .replace(/\\propto/g, ` ${K('proportional to')} `).replace(/\\sum/g, ` ${K('the sum of')} `).replace(/\\int/g, ` ${K('the integral of')} `)
      .replace(/\\nabla/g, ` ${K('del')} `).replace(/\\partial/g, ` ${K('partial')} `).replace(/\\infty/g, ` ${K('infinity')} `).replace(/\\pm/g, ` ${K('plus or minus')} `)
      .replace(/\\[a-zA-Z]+/g, ' ');
    t = t.replace(/[{}\\]/g, ' ').replace(/=/g, ` ${K('equals')} `).replace(/</g, ` ${K('less than')} `).replace(/>/g, ` ${K('greater than')} `)
      .replace(/\+/g, ` ${K('plus')} `).replace(/\//g, ` ${K('over')} `).replace(/(\s|^)-(?=\s|\w)/g, ` ${K('minus')} `);
    // what's left is symbols: a run of letters is a product of variables ("mc" is "m c"), unless it's a function
    t = t.replace(/[A-Za-z]{2,}/g, (w) => (FUNCS.has(w) ? w : w.split('').join(' ')))
      .replace(/(\d)([A-Za-z])/g, '$1 $2');
    t = t.replace(/\u0001(\d+)\u0002/g, (_, i) => keep[+i]);
    return t.replace(/\s+/g, ' ').trim();
  }
  // the computer's own voices, by name, the most natural first (macOS, Windows, Chrome's)
  const SYS = {
    female: ['Ava', 'Zoe', 'Samantha', 'Allison', 'Susan', 'Serena', 'Karen', 'Moira', 'Tessa', 'Microsoft Aria', 'Microsoft Jenny', 'Microsoft Zira', 'Google US English', 'Google UK English Female'],
    male: ['Evan', 'Nathan', 'Tom', 'Daniel', 'Alex', 'Aaron', 'Arthur', 'Oliver', 'Rishi', 'Fred', 'Microsoft Guy', 'Microsoft David', 'Microsoft Mark', 'Google UK English Male'],
  };
  const wantGender = () => { const v = neural.voice || ''; return v === 'system-male' || /^[ab]m_/.test(v) ? 'male' : 'female'; };
  function pickVoice() {
    if (!synth) return null;
    const vs = synth.getVoices();
    if (!vs.length) return null;
    const lang = (navigator.language || 'en-US').toLowerCase();
    const same = vs.filter((v) => v.lang && v.lang.toLowerCase().replace('_', '-').startsWith(lang.slice(0, 2)));
    const pool = same.length ? same : vs;
    const good = SYS[wantGender()];
    for (const g of good) { const v = pool.find((x) => x.name.startsWith(g) && /premium|enhanced|natural|neural/i.test(x.name)); if (v) return v; }
    for (const g of good) { const v = pool.find((x) => x.name.startsWith(g)); if (v) return v; }
    return pool.find((v) => v.localService) || pool[0];
  }
  if (synth && synth.addEventListener) synth.addEventListener('voiceschanged', () => {});
  let serverSay = null;  // the server can speak with macOS `say` (checked once)
  let neural = { state: 'missing' };  // the natural voice (app/voice.py): missing | installing | ready | error
  async function refreshVoice() {
    try { const v = await api('GET', '/api/voice'); serverSay = !!v.say; neural = v.neural || neural; }
    catch { if (serverSay === null) serverSay = false; }
    paintSetup();
    return neural;
  }
  async function canServerSay() { if (serverSay === null) await refreshVoice(); return serverSay; }
  refreshVoice();
  const useNeural = () => neural.state === 'ready' && !String(neural.voice || '').startsWith('system-');
  const useSynth = () => !!(synth && synth.getVoices().length);
  const canSpeak = () => useNeural() || useSynth() || serverSay === true;

  /** A queue of sentences, spoken one after another; onIdle runs when it has nothing left to say. */
  /** One sentence in the natural voice: fetched as soon as it's queued, so the next is ready while one plays. */
  function fetchNeural(text) {
    return fetch('/api/voice/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, voice: neural.voice || '' }) })
      .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
  }
  let audioEl = null;
  const speech = {
    queue: [], busy: false, epoch: 0, onIdle: null,
    say(text) {
      const t = String(text || '').trim();
      if (!t || !/[\p{L}\p{N}]/u.test(t)) return;
      // long sentences in two, so the voice starts sooner and keeps its breath
      const parts = t.length > 260 ? t.split(/(?<=[,;:])\s+(?=\S)/).reduce((a, x) => { if (a.length && (a[a.length - 1] + ' ' + x).length < 220) a[a.length - 1] += ' ' + x; else a.push(x); return a; }, []) : [t];
      for (const x of parts) this.queue.push({ text: x, audio: useNeural() ? fetchNeural(x) : null });
      if (!this.busy) this.next();
    },
    async next() {
      const item = this.queue.shift();
      if (item == null) { this.busy = false; const f = this.onIdle; if (f) f(); return; }
      const t = item.text;
      this.busy = true;
      const ep = this.epoch;
      const done = () => { if (ep === this.epoch) this.next(); };
      if (item.audio) {
        const blob = await item.audio;
        if (ep !== this.epoch) return;
        if (blob) {
          const url = URL.createObjectURL(blob);
          audioEl = new Audio(url);
          const end = () => { URL.revokeObjectURL(url); done(); };
          audioEl.onended = end; audioEl.onerror = end;
          audioEl.play().catch(end);
          return;
        }
      }
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
      if (audioEl) { try { audioEl.pause(); } catch { /* gone */ } audioEl = null; }
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
  let offeredOnce = false;
  function readAloud(m) {
    if (reading === m) { speech.stop(); reading = null; rb.renderChat(); return; }
    stopVoice(false);
    speech.stop();
    if (neural.state !== 'ready' && neural.state !== 'installing' && !offeredOnce && !offerOff()) {
      offeredOnce = true;
      toast('Want a natural, human-sounding voice? It’s an optional one-time download (about 480 MB) that stays on this computer.', 'info',
        { ms: 9000, action: { label: 'Download', fn: () => { installVoice(); toast('Setting up the natural voice. It’s used as soon as it’s ready.', 'info'); } } });
    }
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
  const pick = h('select', { class: 'vb-voice', 'aria-label': 'Voice', title: 'Which voice answers' });
  pick.addEventListener('change', async () => {
    if (pick.value === '__remove') {
      const ok = rb.confirmDialog ? await rb.confirmDialog('Remove the natural voice?', 'It frees about 480 MB. Answers are read in the computer’s built-in voice instead, and you can download it again any time.', 'Remove') : window.confirm('Remove the natural voice?');
      if (ok === true || ok === 'ok') {
        try { const st = await api('DELETE', '/api/voice'); neural = st; toast('Removed the natural voice.', 'ok'); } catch (e) { toast(e.message || 'Could not remove it', 'error'); }
      }
      paintSetup();
      return;
    }
    try { const st = await api('PUT', '/api/voice/settings', { voice: pick.value }); neural = { ...neural, ...st }; } catch { /* keep */ }
    speech.stop(); speech.say('Hi, this is how I sound.');
  });
  pick.hidden = false;
  // the natural voice isn't set up yet: offer it (one-time, on this Mac), with its progress
  const setup = h('div', { class: 'vb-setup', hidden: true });
  bar.append(orb, h('div', { class: 'vb-text' }, status, heardEl), pick, endBtn);
  const NOPE = 'rb.voiceOfferOff';
  const offerOff = () => { try { return localStorage.getItem(NOPE) === '1'; } catch { return false; } };
  function paintSetup() {
    const st = neural.state;
    // the voices: the natural ones (female, male) once downloaded; the computer's own, female or male, always
    const names = neural.voices || {}, sys = neural.system_voices || { 'system-female': 'Built-in female', 'system-male': 'Built-in male' };
    const groups = [];
    if (st === 'ready') {
      groups.push(h('optgroup', { label: 'Natural, female' }, (neural.female || []).map((k) => h('option', { value: k, text: names[k] }))));
      groups.push(h('optgroup', { label: 'Natural, male' }, (neural.male || []).map((k) => h('option', { value: k, text: names[k] }))));
    }
    groups.push(h('optgroup', { label: 'Built-in' }, Object.entries(sys).map(([k, v]) => h('option', { value: k, text: v }))));
    if (st === 'ready') groups.push(h('optgroup', { label: 'Natural voice' }, h('option', { value: '__remove', text: 'Remove the natural voice…' })));
    pick.replaceChildren(...groups);
    let v = neural.voice || 'af_heart';
    if (st !== 'ready' && !v.startsWith('system-')) v = /^[ab]m_/.test(v) ? 'system-male' : 'system-female';
    pick.value = v;
    if (st === 'ready' || !(active || setupWanted) || (offerOff() && st !== 'installing' && !setupWanted)) { setup.hidden = true; return; }
    setup.hidden = false;
    if (st === 'installing') {
      setup.replaceChildren(h('span', { text: `${neural.step || 'Setting up the natural voice'}… ${Math.round((neural.progress || 0) * 100)}%` }),
        h('span', { class: 'vb-bar' }, h('span', { style: `width:${Math.round((neural.progress || 0) * 100)}%` })));
    } else {
      setup.replaceChildren(
        h('span', { text: st === 'error' ? `The natural voice didn’t install: ${neural.error || 'something went wrong'}. ` : 'For a natural, human-sounding voice: ' }),
        h('button', { type: 'button', class: 'btn btn-text btn-sm', text: st === 'error' ? 'Try again' : `Download it (about ${neural.size_mb || 480} MB, stays on this computer)`, onclick: installVoice }),
        h('button', { type: 'button', class: 'btn btn-text btn-sm vb-nope', text: 'No thanks', title: 'Keep the built-in voice; you can still download it from the voice menu later',
          onclick: () => { try { localStorage.setItem(NOPE, '1'); } catch { /* ok */ } setup.hidden = true; } }));
    }
  }
  let setupWanted = false, pollT = 0;
  async function installVoice() {
    setupWanted = true;
    try { neural = await api('POST', '/api/voice/install', {}); } catch (e) { neural = { state: 'error', error: e.message }; }
    paintSetup();
    clearInterval(pollT);
    pollT = setInterval(async () => {
      await refreshVoice();
      if (neural.state !== 'installing') {
        clearInterval(pollT);
        if (neural.state === 'ready') { toast('The natural voice is ready.', 'ok'); api('POST', '/api/voice/warm', {}).catch(() => {}); rb.renderChat(); }
      }
    }, 1000);
  }
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
    if (useNeural()) api('POST', '/api/voice/warm', {}).catch(() => {});
    refreshVoice();
    listenNow();
  }
  function stopVoice(render = true) {
    if (!active) return;
    active = false; feed = null;
    listen.stop(); speech.stop(); speech.onIdle = null;
    bar.hidden = true;
    if (!setupWanted || neural.state !== 'installing') setup.hidden = true;
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
    form.insertBefore(setup, bar.nextSibling);
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

  window.__rbVoice = { start: startVoice, stop: stopVoice, readAloud, installVoice, get neural() { return neural; }, isReading: (m) => reading === m, canSpeak: () => canSpeak() || !!synth,
    speakable, get state() { return active ? state : 'off'; }, _speech: speech, _feed: sentenceFeeder };
})();
