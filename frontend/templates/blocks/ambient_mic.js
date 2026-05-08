({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // The block's content is three controls — the teacher reading "MIC ON" via
  // read_media adds nothing. Skip the auto-snapshot reporter; we still call
  // helpers.reportState once on mount so the teacher knows the block exists.
  autosnapshot: false,
  style: {
    background: 'var(--bw-surface)',
    color: 'var(--bw-ink)',
    fontFamily: 'var(--bw-font-sans)',
    borderRadius: '0',
    border: '1px solid var(--bw-border)',
    padding: '0',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  publishes: ['ambient_mic.muted', 'ambient_mic.speak_to'],
  run: function (root, bus, cleanup, helpers) {
    var backend = helpers && helpers.backend ? helpers.backend : null;
    var audio = helpers && helpers.audio ? helpers.audio : null;
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};

    var LS_KEY = 'ambient_mic.speak_to';
    // Default to muted (closed). User opens via spacebar (hold = PTT,
    // double-tap = always-on) or by clicking the toggle button.
    var muted = true;
    var speakTo = 'teacher';

    // ── Keyboard mode state ─────────────────────────────
    // 'closed': mic off (default).
    // 'live':   always-on; VAD streams continuously.
    // 'ptt':    push-to-talk; mic open while space is held.
    var mode = 'closed';
    var TAP_MAX_MS = 250;
    var DOUBLE_TAP_WINDOW_MS = 400;
    var keyDownAt = null;
    var lastTapAt = 0;
    // VAD asset load (~40MB ONNX + wasm) takes a few seconds on a cold
    // cache. Track whether the engine is fully loaded so the UI can
    // show a clear "loading → ready" signal and we can refuse PTT/LIVE
    // attempts before the mic is actually usable.
    var vadReady = false;
    try {
      if (typeof localStorage !== 'undefined') {
        var stored = localStorage.getItem(LS_KEY);
        if (stored) speakTo = stored;
      }
    } catch (_) { /* localStorage may be denied; fall through */ }

    // ── Header strip ────────────────────────────────────
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'MIC';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var dot = document.createElement('span');
    dot.style.cssText =
      'width:8px; height:8px; border-radius:50%;' +
      'background:var(--bw-ink-faint); display:inline-block;' +
      'transition:background-color 0.15s ease, transform 0.2s ease;';

    var status = document.createElement('span');
    status.textContent = 'listening';
    status.style.cssText =
      'flex:1; font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-muted); text-transform:uppercase;' +
      'letter-spacing:.1em;';

    var counterEl = document.createElement('span');
    counterEl.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;';

    header.appendChild(idChip);
    header.appendChild(dot);
    header.appendChild(status);
    header.appendChild(counterEl);
    root.appendChild(header);

    // ── Controls row ────────────────────────────────────
    var controls = document.createElement('div');
    controls.style.cssText =
      'padding:10px 12px; display:flex; align-items:center; gap:10px;' +
      'border-bottom:1px solid var(--bw-border); flex-shrink:0;';
    root.appendChild(controls);

    var sel = document.createElement('select');
    sel.style.cssText =
      'flex:1; min-width:0; padding:6px 8px;' +
      'font-family:inherit; font-size:11.5px;' +
      'color:var(--bw-ink); background:var(--bw-surface-2);' +
      'border:1px solid var(--bw-border); border-radius:0;' +
      'cursor:pointer;';
    var opt = document.createElement('option');
    opt.value = 'teacher';
    opt.textContent = 'Speak to: Teacher';
    sel.appendChild(opt);
    sel.value = speakTo;
    controls.appendChild(sel);

    var muteBtn = document.createElement('button');
    muteBtn.type = 'button';
    muteBtn.textContent = 'Open mic';
    muteBtn.style.cssText =
      'padding:6px 10px; font-family:inherit; font-size:11px;' +
      'color:var(--bw-ink); background:var(--bw-surface-2);' +
      'border:1px solid var(--bw-border); border-radius:0;' +
      'cursor:pointer; flex-shrink:0;';
    controls.appendChild(muteBtn);

    // ── Caption row (last-heard transcript) ─────────────
    var heard = document.createElement('div');
    heard.style.cssText =
      'flex:1; padding:10px 12px;' +
      'font-family:var(--bw-font-mono); font-size:11px;' +
      'color:var(--bw-ink-muted); line-height:1.45;' +
      'white-space:pre-wrap; word-break:break-word;' +
      'overflow:auto;';
    heard.textContent = 'Waiting for mic to initialize…';
    root.appendChild(heard);

    // ── State helpers ───────────────────────────────────
    var startCount = 0;
    var phraseCount = 0;
    function refreshCounter() {
      counterEl.textContent =
        'speech:' + startCount + '  phrase:' + phraseCount;
    }
    refreshCounter();

    function setDot(state) {
      if (state === 'speaking') {
        dot.style.background = '#ef4444';
        dot.style.transform = 'scale(1.4)';
      } else if (state === 'muted' || state === 'paused') {
        dot.style.background = 'var(--bw-ink-faint)';
        dot.style.transform = 'scale(1)';
      } else {
        dot.style.background = 'var(--bw-accent)';
        dot.style.transform = 'scale(1)';
      }
    }
    setDot('paused');

    function setStatus(text) { status.textContent = text; }

    // Persona dropdown
    var onSelChange = function () {
      speakTo = sel.value;
      try { if (typeof localStorage !== 'undefined') localStorage.setItem(LS_KEY, speakTo); }
      catch (_) { /* noop */ }
      try { bus.publish('ambient_mic.speak_to', speakTo); } catch (_) { /* noop */ }
    };
    sel.addEventListener('change', onSelChange);
    cleanup(function () { sel.removeEventListener('change', onSelChange); });

    // Will be wired after audio.startVad resolves.
    var handleRef = { current: null };

    // applyMode — single entry point for state transitions. Drives the
    // VAD handle (pause/resume) and the visible UI in lockstep so the
    // keyboard handler, the click handler, and the initial mount paint
    // can't drift out of sync.
    function applyMode(next) {
      var h = handleRef.current;
      // Refuse PTT/LIVE before the VAD engine has finished loading —
      // the mic would silently no-op (no frames flow) and the user
      // would hold SPACE only to discover nothing was captured.
      // Show a clear "still loading" hint instead.
      if (next !== 'closed' && !vadReady) {
        heard.textContent = 'Mic engine still loading — wait for "ready" below.';
        return;
      }
      // Flush BEFORE mutating mode/muted: flush() invokes onPhrase
      // synchronously, and onPhrase short-circuits on `if (muted)`. If we
      // flipped muted=true first, the in-flight phrase would be encoded
      // and immediately dropped on the floor. Doing it here means the
      // synchronous onPhrase still sees the previous (open) state and
      // forwards the transcript to the backend before we tear down.
      if (next === 'closed' && mode !== 'closed') {
        if (h && h.flush) { try { h.flush(); } catch (e) { console.warn(e); } }
      }
      mode = next;
      muted = (mode === 'closed');
      if (mode === 'closed') {
        if (h && h.pause) { try { h.pause(); } catch (e) { console.warn(e); } }
        if (audio && audio.stopAll) { try { audio.stopAll(); } catch (e) { console.warn(e); } }
        setDot('muted');
        // Branch on vadReady so the user sees a real "loading → ready"
        // transition. `applyMode('closed')` is called both on initial
        // mount paint (when vadReady=false) and after startVad resolves
        // (vadReady=true) — same UI branch, different copy.
        if (vadReady) {
          setStatus('ready · mic off');
          heard.textContent = 'Hold SPACE to talk · double-tap SPACE for always-on.';
        } else {
          setStatus('loading mic…');
          heard.textContent = 'Loading mic engine — this takes a few seconds on first load.';
        }
        muteBtn.textContent = 'Open mic';
      } else if (mode === 'live') {
        if (h && h.resume) { try { h.resume(); } catch (e) { console.warn(e); } }
        setDot('idle');
        setStatus('always-on');
        heard.textContent = 'Always-on. Listening for speech…';
        muteBtn.textContent = 'Close mic';
      } else { // 'ptt'
        if (h && h.resume) { try { h.resume(); } catch (e) { console.warn(e); } }
        setDot('speaking');
        setStatus('PTT recording');
        muteBtn.textContent = 'Recording…';
      }
      try { bus.publish('ambient_mic.muted', muted); } catch (_) { /* noop */ }
    }

    // Click toggle: equivalent to a spacebar tap from the current mode.
    var onMuteClick = function () {
      applyMode(mode === 'closed' ? 'live' : 'closed');
    };
    muteBtn.addEventListener('click', onMuteClick);
    cleanup(function () { muteBtn.removeEventListener('click', onMuteClick); });

    // ── Spacebar control ───────────────────────────────
    // Hold SPACE → push-to-talk: mic opens on keydown, stays open while
    // held, closes on release. Phrases that fired during the hold are
    // already sent via onPhrase below.
    // Tap SPACE (CLOSED) once → no-op (closes the speculative PTT open).
    // Tap SPACE (CLOSED) twice within DOUBLE_TAP_WINDOW_MS → enter LIVE.
    // Tap SPACE (LIVE) once → exit LIVE, return to CLOSED.
    function isTypingTarget(el) {
      if (!el) return false;
      var t = (el.tagName || '').toLowerCase();
      if (t === 'input' || t === 'textarea' || t === 'select') return true;
      if (el.isContentEditable) return true;
      return false;
    }
    function onKeyDown(e) {
      if (e.code !== 'Space') return;
      if (e.repeat) return;
      if (isTypingTarget(document.activeElement)) return;
      e.preventDefault();
      keyDownAt = Date.now();
      // Speculatively open the mic on every keydown from CLOSED so
      // a HOLD captures audio from the first millisecond. If the user
      // releases quickly (TAP), keyup pauses again with no audio sent.
      if (mode === 'closed') applyMode('ptt');
    }
    function onKeyUp(e) {
      if (e.code !== 'Space') return;
      if (keyDownAt === null) return;
      if (isTypingTarget(document.activeElement)) {
        keyDownAt = null;
        return;
      }
      var duration = Date.now() - keyDownAt;
      keyDownAt = null;
      if (duration > TAP_MAX_MS) {
        // HOLD released — PTT done. applyMode('closed') flushes any
        // in-flight phrase (silero-vad won't fire onSpeechEnd until
        // 640ms of silence) before tearing down the mic.
        if (mode === 'ptt') applyMode('closed');
        // If we're already 'live', a hold is a no-op.
      } else {
        // TAP
        if (mode === 'live') {
          applyMode('closed');
          lastTapAt = 0;
        } else {
          // mode === 'ptt' (we set it on keydown). Pause and check for double-tap.
          var now = Date.now();
          applyMode('closed');
          if ((now - lastTapAt) < DOUBLE_TAP_WINDOW_MS) {
            lastTapAt = 0;
            applyMode('live');
          } else {
            lastTapAt = now;
          }
        }
      }
    }
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('keyup', onKeyUp);
    cleanup(function () {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('keyup', onKeyUp);
    });

    if (!audio || !audio.startVad || !backend || !backend.recordUtterance) {
      setStatus('helpers missing');
      setDot('paused');
      heard.textContent =
        'Block mounted but the audio/backend helpers are unavailable. ' +
        'Verify Block.tsx exposes helpers.audio.';
      report({
        kind: 'ambient_mic',
        content: 'Mic block mounted, but audio/backend helpers unavailable.',
        extra: { listening: false, speak_to: speakTo },
      });
      return;
    }

    report({
      kind: 'ambient_mic',
      content: 'Ambient mic listening — speaking to ' + speakTo + '.',
      extra: { listening: true, speak_to: speakTo },
    });

    var phraseInFlight = 0;

    function onSpeechStart() {
      startCount++;
      refreshCounter();
      console.log('[ambient_mic] onSpeechStart', startCount);
      if (!muted) {
        setDot('speaking');
        setStatus('hearing…');
      }
    }

    function onPhrase(wav, phraseId) {
      phraseCount++;
      refreshCounter();
      console.log('[ambient_mic] onPhrase id=', phraseId, 'bytes=', wav && wav.size);
      if (muted) return Promise.resolve();
      phraseInFlight++;
      setDot('speaking');
      setStatus('transcribing…');
      return audio.transcribe(wav, 'auto').then(function (out) {
        var text = (out && out.text ? out.text : '').trim();
        if (!text) {
          heard.textContent = '(no speech detected in phrase #' + phraseCount + ')';
          return null;
        }
        // Echo the transcript inline so the user can verify capture before
        // (or independently of) any teacher reaction. If the server
        // turns around and says it's an echo of our own TTS, we clear
        // it below — this brief flash is the cost of the round-trip.
        heard.textContent = '“' + text + '”';
        return backend.recordUtterance({
          text: text,
          language: out.language || null,
          audio_duration_s: out.duration_seconds || null,
          target_persona: speakTo,
        }).then(function (resp) {
          // Server echo dedup: when the perception endpoint identifies
          // this phrase as the teacher's own TTS bouncing back through
          // the speakers, it returns accepted=false reason=echo. Hide
          // the displayed transcript so the panel doesn't fill with
          // the teacher's own words (matches the server behavior of
          // suppressing the debug-panel emit). Anything else stays
          // visible — including legitimate interruptions ("stop",
          // "wait") that the dedup is designed to let through.
          if (resp && resp.accepted === false && resp.reason === 'echo') {
            // Only clear if the user hasn't already started a fresh phrase.
            if (heard.textContent.indexOf(text) !== -1) {
              heard.textContent = muted ? 'muted' : 'Listening for speech…';
            }
          }
          return resp;
        });
      }).catch(function (err) {
        console.warn('[ambient_mic] phrase failed', err);
        heard.textContent = 'transcribe error: ' + ((err && err.message) || err);
      }).then(function () {
        phraseInFlight = Math.max(0, phraseInFlight - 1);
        if (phraseInFlight === 0) {
          setDot(muted ? 'muted' : 'idle');
          setStatus(muted ? 'muted' : 'listening');
        }
      });
    }

    // Paint the closed UI immediately. The VAD's ONNX + wasm assets
    // (~40MB) take a few seconds to load on a cold cache, so we don't
    // want to block the UI on a misleading "Requesting mic permission…"
    // placeholder during that window. handleRef.current is null at
    // this point — applyMode tolerates that (the h.pause/h.resume calls
    // are guarded). The real pause hits the just-started VAD when
    // startVad resolves below. If the user already had permission
    // granted, no prompt fires; if not, only show the prompt note.
    applyMode('closed');
    try {
      if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: 'microphone' }).then(function (status) {
          // Only override the closed-UI hint if we actually need a
          // permission action from the user.
          if (status.state === 'prompt') {
            heard.textContent = 'Requesting mic permission…';
          } else if (status.state === 'denied') {
            heard.textContent = 'Mic blocked — enable it in browser settings.';
          }
        }).catch(function () { /* Permissions API not supported */ });
      }
    } catch (_) { /* noop */ }
    audio.startVad({
      onSpeechStart: onSpeechStart,
      onPhrase: onPhrase,
      onError: function (err) {
        console.warn('[ambient_mic] vad error', err);
        heard.textContent = 'vad error: ' + ((err && err.message) || err);
      },
    }).then(function (handle) {
      handleRef.current = handle;
      vadReady = true;
      console.log('[ambient_mic] mic ready');
      // The library auto-starts the VAD as part of its init
      // (startOnLoad=true is the default). Sync the just-started
      // VAD to the current mode and re-paint so the user sees the
      // transition from "loading mic…" to "ready · mic off".
      if (mode === 'closed') {
        try { handle.pause(); } catch (_) { /* noop */ }
        if (audio && audio.stopAll) { try { audio.stopAll(); } catch (_) { /* noop */ } }
        applyMode('closed');
      }
    }).catch(function (err) {
      console.warn('[ambient_mic] startVad failed', err);
      var msg = (err && err.message) || String(err);
      setStatus('mic unavailable');
      setDot('paused');
      heard.textContent = 'mic init failed: ' + msg;
    });

    cleanup(function () {
      var h = handleRef.current;
      handleRef.current = null;
      if (h && h.stop) {
        try { h.stop(); } catch (_) { /* noop */ }
      }
      // Belt-and-suspenders: nuke any leaked mic stream this module
      // opened in case a previous instance's cleanup didn't run after
      // an HMR swap.
      if (audio && audio.stopAll) {
        try { audio.stopAll(); } catch (_) { /* noop */ }
      }
    });
  },
})
