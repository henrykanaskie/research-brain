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

  // ====================================================================== hearing you: the microphone
  // Like Claude's voice mode: the page listens all the time, notices when you start talking and when you've
  // finished (a pause of about 0.9 s), and hands the recording to Whisper on this computer (POST /api/voice/stt).
  // Echo cancellation keeps its own voice out; while it speaks, talking over it (clearly louder than the room)
  // interrupts it. The level of the room is learned as it listens, so a fan or a quiet hum doesn't count as you.
  const mic = {
    stream: null, ctx: null, node: null, src: null, floor: 0.006, mode: 'off',  // off | listen | speaking | hold
    inUtt: false, rec: [], pre: [], preMs: 0, voiced: 0, silent: 0, total: 0, rate: 48000,
    onStart: null, onUtter: null, level: 0,
    async open() {
      if (this.stream) return null;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return 'unsupported';
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      } catch (e) { this.stream = null; return e && e.name === 'NotAllowedError' ? 'denied' : 'failed'; }
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AC();
      this.rate = this.ctx.sampleRate;
      this.src = this.ctx.createMediaStreamSource(this.stream);
      this.node = this.ctx.createScriptProcessor(2048, 1, 1);
      this.node.onaudioprocess = (e) => this.frame(e.inputBuffer.getChannelData(0));
      this.src.connect(this.node);
      const mute = this.ctx.createGain(); mute.gain.value = 0;  // keeps the node running without playing anything
      this.node.connect(mute); mute.connect(this.ctx.destination);
      if (this.ctx.state === 'suspended') await this.ctx.resume().catch(() => {});
      return null;
    },
    close() {
      this.mode = 'off'; this.inUtt = false; this.rec = []; this.pre = [];
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.ctx) this.ctx.close().catch(() => {});
      this.stream = null; this.ctx = null; this.node = null; this.src = null;
    },
    frame(buf) {
      if (this.mode === 'off' || this.mode === 'hold') { this.level = 0; return; }
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length), ms = (buf.length / this.rate) * 1000;
      this.level = rms;
      const copy = new Float32Array(buf);
      const barge = this.mode === 'speaking';
      const start = Math.max(this.floor * (barge ? 6 : 3.2), barge ? 0.045 : 0.012);
      if (!this.inUtt) {
        if (rms < start) this.floor = this.floor * 0.97 + Math.min(rms, this.floor * 2.5 + 0.001) * 0.03;  // the room's own level
        this.pre.push(copy); this.preMs += ms;
        while (this.preMs > 350 && this.pre.length > 1) { this.preMs -= (this.pre.shift().length / this.rate) * 1000; }
        if (rms > start) this.voiced += ms; else this.voiced = Math.max(0, this.voiced - ms * 0.5);
        if (this.voiced >= (barge ? 260 : 110)) {
          this.inUtt = true; this.rec = this.pre.slice(); this.pre = []; this.preMs = 0;
          this.silent = 0; this.total = 0; this.voiced = 0;
          if (this.onStart) this.onStart(barge);
        }
        return;
      }
      this.rec.push(copy); this.total += ms;
      if (rms < Math.max(this.floor * 2.2, 0.008)) this.silent += ms; else this.silent = 0;
      if (this.silent >= 900 || this.total > 30000) {
        const spoken = this.total - this.silent;
        const rec = this.rec; this.rec = []; this.inUtt = false; this.silent = 0;
        if (spoken < 300) return;  // a cough or a click, not you talking
        if (this.onUtter) this.onUtter(toWav16k(rec, this.rate));
      }
    },
  };
  /** The recording as a WAV file: 16 kHz, mono, 16-bit (what Whisper wants). */
  function toWav16k(chunks, rate) {
    let n = 0; for (const c of chunks) n += c.length;
    const all = new Float32Array(n); let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
    const ratio = rate / 16000, m = Math.floor(n / ratio);
    const pcm = new Int16Array(m);
    for (let i = 0; i < m; i++) {
      const x = i * ratio, j = Math.floor(x), f = x - j;
      const v = (all[j] || 0) * (1 - f) + (all[j + 1] || 0) * f;
      pcm[i] = Math.max(-1, Math.min(1, v)) * 0x7fff;
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2), dv = new DataView(buf);
    const w = (p, str) => { for (let i = 0; i < str.length; i++) dv.setUint8(p + i, str.charCodeAt(i)); };
    w(0, 'RIFF'); dv.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
    dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true); dv.setUint32(24, 16000, true);
    dv.setUint32(28, 32000, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); w(36, 'data'); dv.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: 'audio/wav' });
  }
  async function transcribe(wav) {
    const r = await fetch('/api/voice/stt', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Couldn’t make out what was said');
    return ((await r.json()).text || '').trim();
  }
  const canHear = () => neural.state === 'ready' && neural.hear === true;
  /** a line in the app's log (the Mac app: app.log), so a voice problem can be traced */
  function vlog(msg) { try { const l = window.__rbLive; if (l && l.native) l.native({ log: 'voice: ' + msg }); } catch { /* not the Mac app */ } }
  let engine = 'mic';  // mic (the microphone and Whisper) | old (the browser's recognizer, or macOS dictation)

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
    const complete = st === 'ready' && neural.hear;
    if (complete || !(active || setupWanted) || (offerOff() && st !== 'installing' && !setupWanted)) { setup.hidden = true; return; }
    setup.hidden = false;
    if (st === 'installing') {
      setup.replaceChildren(h('span', { text: `${neural.step || 'Setting up the natural voice'}… ${Math.round((neural.progress || 0) * 100)}%` }),
        h('span', { class: 'vb-bar' }, h('span', { style: `width:${Math.round((neural.progress || 0) * 100)}%` })));
    } else {
      setup.replaceChildren(
        h('span', { text: st === 'error' ? `The natural voice didn’t install: ${neural.error || 'something went wrong'}. `
          : st === 'ready' ? 'To talk hands-free (it hears you and knows when you’ve finished): ' : 'For hands-free talking and a natural, human-sounding voice: ' }),
        h('button', { type: 'button', class: 'btn btn-text btn-sm', text: st === 'error' ? 'Try again'
          : st === 'ready' ? 'Add hearing (about 220 MB, stays on this computer)' : `Download it (about ${neural.size_mb || 700} MB, stays on this computer)`, onclick: installVoice }),
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
        if (neural.state === 'ready') {
          toast(neural.hear ? 'Voice mode can hear you now: talk, and it answers out loud.' : 'The natural voice is ready.', 'ok');
          api('POST', '/api/voice/warm', {}).catch(() => {}); rb.renderChat();
          if (active && neural.hear) { stopVoice(false); startVoice(); }  // straight into hands-free
        }
      }
    }, 1000);
  }
  const LABEL = { listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking', paused: 'Paused' };
  // the orb follows your voice while it listens
  (function level() {
    if (active && engine === 'mic' && state === 'listening') orb.style.setProperty('--lvl', String(Math.min(1, mic.level * 14)));
    else orb.style.removeProperty('--lvl');
    requestAnimationFrame(level);
  })();
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
    if (engine === 'mic') { mic.mode = 'listen'; return; }  // the microphone is already open; it hands over when you've spoken
    listen.start((q) => {
      if (!active) return;
      if (!q) { setState('paused', 'Didn’t catch anything. Tap to talk.'); return; }
      ask(q);
    });
  }
  function ask(q) {
    if (engine === 'mic') mic.mode = 'hold';  // not while it thinks; it listens for an interruption once it talks
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
      if (state === 'thinking') { setState('speaking'); if (engine === 'mic') mic.mode = 'speaking'; }
      feed(text, false);
    },
    done(msg) {
      if (!active || !feed) return;
      if (msg && msg.error) { feed = null; setState('paused', 'That didn’t work. Tap to try again.'); return; }
      if (msg && msg.content) feed(msg.content, true);
      feed = null;
      if (state === 'thinking') { setState('speaking'); if (engine === 'mic') mic.mode = 'speaking'; }
      if (!speech.speaking) listenNow();  // nothing (left) to say
    },
  };

  async function startVoice() {
    if (rb.claudeBlocked && rb.claudeBlocked()) return;
    rb.openChat();
    reading = null; speech.stop();
    active = true;
    const form = $('#chat-form');
    form.classList.add('voice-on');
    bar.hidden = false;
    await refreshVoice();
    if (!active) return;
    if (neural.state === 'ready') api('POST', '/api/voice/warm', {}).catch(() => {});
    // hands-free with the microphone and Whisper when they're set up; else the browser's own recognizer (or
    // macOS dictation in the Mac app) as before
    engine = canHear() ? 'mic' : 'old';
    if (engine === 'old' && !SR && !isMac()) {
      setupWanted = true; paintSetup();
      setState('paused', 'To hear you, voice mode needs its listening model: download it below.');
      return;
    }
    if (engine === 'mic') {
      setState('paused', 'Starting the microphone…');
      // macOS asks you the first time; if nothing answers in a while, fall back to dictation rather than hang
      const err = await Promise.race([mic.open(), new Promise((r) => setTimeout(() => r('timeout'), 12000))]);
      vlog(`microphone: ${err || 'on'} (mediaDevices: ${typeof navigator.mediaDevices})`);
      if (!active) { mic.close(); return; }
      if (err === 'denied') {
        setState('paused', 'The microphone is off for research brain. Allow it in System Settings › Privacy & Security › Microphone, then try again.');
        return;
      }
      if (err) {
        mic.close();
        engine = 'old';
        if (!SR && !isMac()) { setState('paused', 'Couldn’t start the microphone. Check that one is connected, then try again.'); return; }
        toast('The microphone didn’t start, so voice mode uses dictation instead (it won’t know when you’ve finished; tap the orb when you have).', 'info', { ms: 7000 });
        listenNow();
        return;
      }
      mic.onStart = (barge) => {
        if (!active) return;
        if (barge) { speech.stop(); feed = null; }  // you talked over it: it stops and listens
        setHeard(''); setState('listening', 'Listening…');
      };
      mic.onUtter = async (wav) => {
        if (!active) return;
        mic.mode = 'hold';
        setState('thinking', 'Hearing you…');
        let q = '';
        try { q = await transcribe(wav); } catch (e) { vlog('transcribe failed: ' + e.message); setState('paused', e.message); mic.mode = 'listen'; return; }
        if (!active) return;
        if (!q || q.replace(/[^\p{L}\p{N}]/gu, '').length < 2) { listenNow(); return; }  // nothing said: keep listening
        ask(q);
      };
    }
    listenNow();
  }
  function stopVoice(render = true) {
    if (!active) return;
    active = false; feed = null;
    listen.stop(); mic.close(); speech.stop(); speech.onIdle = null;
    bar.hidden = true;
    if (!setupWanted || neural.state !== 'installing') setup.hidden = true;
    $('#chat-form').classList.remove('voice-on');
    setState('idle');
    if (render) $('#chat-input').focus();
  }
  orb.addEventListener('click', () => {
    if (state === 'speaking') { speech.stop(); listenNow(); }          // interrupt: it listens
    else if (state === 'listening' && engine === 'mic') { if (mic.inUtt) { mic.silent = 9999; } }  // done talking
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
    speakable, get state() { return active ? state : 'off'; }, _speech: speech, _feed: sentenceFeeder, _mic: mic };
})();
