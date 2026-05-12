({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  content: '',
  style: {
    background: 'var(--bw-surface)',
    color: 'var(--bw-ink)',
    fontFamily: 'var(--bw-font-sans)',
    borderRadius: '0',
    border: '1px solid var(--bw-border)',
    padding: '14px 18px',
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
    overflow: 'hidden',
  },
  publishes: [],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};
    var backend = helpers && helpers.backend ? helpers.backend : null;
    var blockId = (helpers && helpers.blockId) || root.getAttribute('data-block-id') || '__BLOCK_ID__';
    var userId = (typeof localStorage !== 'undefined' && localStorage.getItem('bewithme_user_id')) || '';

    // Local UUID-ish (cryptographically random where available; falls back
    // to Math.random which is fine for an in-memory session id).
    var sessionId = (function () {
      try {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
      } catch (_) {}
      return 'sess-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    })();

    // Icon
    var icon = document.createElement('div');
    icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';
    icon.style.cssText =
      'flex-shrink:0; display:flex; align-items:center; justify-content:center;' +
      'width:34px; height:34px; border-radius:0;' +
      'color:var(--bw-accent);' +
      'background:var(--bw-accent-soft);' +
      'border:1px solid var(--bw-accent);';

    // Text column
    var textCol = document.createElement('div');
    textCol.style.cssText = 'display:flex; flex-direction:column; flex:1; min-width:0;';
    var title = document.createElement('div');
    title.textContent = 'Share screen';
    title.style.cssText =
      'font-size:13px; font-weight:600; color:var(--bw-ink);' +
      'letter-spacing:-0.005em;';
    var status = document.createElement('div');
    status.textContent = 'Idle';
    status.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); margin-top:3px;' +
      'text-transform:uppercase; letter-spacing:.08em;' +
      'overflow:hidden; text-overflow:ellipsis; white-space:nowrap;';
    textCol.appendChild(title);
    textCol.appendChild(status);

    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'START';
    button.style.cssText =
      'padding:8px 14px; border:none; border-radius:0;' +
      'font-family:var(--bw-font-mono); font-size:11px;' +
      'font-weight:500; letter-spacing:.1em;' +
      'color:#E8EEFA; background:var(--bw-accent);' +
      'cursor:pointer; flex-shrink:0;' +
      'transition:filter 0.15s ease;';

    var hover = function () { button.style.filter = 'brightness(1.1)'; };
    var unhover = function () { button.style.filter = 'none'; };
    button.addEventListener('mouseenter', hover);
    button.addEventListener('mouseleave', unhover);
    cleanup(function () {
      button.removeEventListener('mouseenter', hover);
      button.removeEventListener('mouseleave', unhover);
    });

    root.appendChild(icon);
    root.appendChild(textCol);
    root.appendChild(button);

    // Detect Electron screen-source bridge. In a plain browser the bridge
    // isn't present, in which case getDisplayMedia() works as the fallback
    // (the user gets the OS picker themselves).
    var bridge = (typeof window !== 'undefined' && window.beWithMeBridge && window.beWithMeBridge.screen) || null;

    var setColor = function (state) {
      if (state === 'active') {
        icon.style.color = '#7ED4A6';
        icon.style.background = 'rgba(126,212,166,0.12)';
        icon.style.border = '1px solid rgba(126,212,166,0.4)';
      } else if (state === 'error') {
        icon.style.color = '#E5837C';
        icon.style.background = 'rgba(229,131,124,0.12)';
        icon.style.border = '1px solid rgba(229,131,124,0.4)';
      } else {
        icon.style.color = 'var(--bw-accent)';
        icon.style.background = 'var(--bw-accent-soft)';
        icon.style.border = '1px solid var(--bw-accent)';
      }
    };

    var stream = null;
    var recorder = null;
    var sourceName = '';
    var audioNoteLive = '';      // set after stream is acquired; affects status text
    var chunksSent = 0;
    var chunksFailed = 0;
    var lastChunkBytes = 0;

    var authHeaders = userId ? { 'X-User-Id': userId } : {};

    var refreshStatusCounter = function () {
      var failTail = chunksFailed > 0 ? ', ' + chunksFailed + ' failed' : '';
      var bytesTail = lastChunkBytes > 0 ? ' · ' + Math.round(lastChunkBytes / 1024) + 'KB' : '';
      status.textContent = (sourceName || 'screen') + audioNoteLive +
        ' · ' + chunksSent + ' chunks' + failTail + bytesTail;
    };

    var postChunk = function (blob, startedAtMs) {
      lastChunkBytes = blob.size;
      var fd = new FormData();
      fd.append('file', blob, 'chunk.webm');
      fd.append('session_id', sessionId);
      fd.append('chunk_started_at_ms', String(startedAtMs));
      if (sourceName) fd.append('source_name', sourceName);
      fetch('/api/perception/screen_chunk', {
        method: 'POST', headers: authHeaders, body: fd,
      }).then(function (res) {
        if (res.ok) {
          chunksSent += 1;
        } else {
          chunksFailed += 1;
          // Surface the error body to the dev console so misrouted /
          // 4xx / 5xx is diagnosable without DevTools network tab.
          res.text().then(function (b) {
            console.warn('[screen_share] chunk POST ' + res.status + ': ' + b.slice(0, 300));
          }).catch(function () {});
        }
        refreshStatusCounter();
      }).catch(function (err) {
        chunksFailed += 1;
        console.warn('[screen_share] chunk POST network error:', err);
        refreshStatusCounter();
      });
    };

    var pickMime = function (mediaStream) {
      // Codec hint must match the stream shape. PROVEN by the next-dev
      // log on 2026-05-12: vp9+opus on a desktopCapturer stream causes
      // every ondataavailable to fire with size=0 — state stays
      // 'recording', isTypeSupported() returns true, but the muxer
      // silently drops all data. requestData() flush also returned 0
      // bytes. Same hardware works with vp8. Don't offer vp9 here.
      var hasAudio = mediaStream.getAudioTracks().length > 0;
      var candidates = hasAudio
        ? ['video/webm;codecs=vp8,opus', 'video/webm']
        : ['video/webm;codecs=vp8', 'video/webm'];
      if (typeof MediaRecorder === 'undefined') return candidates[candidates.length - 1];
      for (var i = 0; i < candidates.length; i++) {
        if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
      }
      return 'video/webm';
    };

    var sessionActive = false;
    var segmentTimer = null;
    var chosenMime = '';

    // Each MediaRecorder lifetime produces ONE self-contained webm with
    // its EBML header. If you `start(timeslice)`, every subsequent
    // ondataavailable fires a CONTINUATION segment (no header) and
    // ffprobe rejects it standalone — which is exactly what was
    // happening on the backend (21/22 chunks failed with "EBML header
    // parsing failed"). Pattern: start a fresh recorder, let it run
    // for ~3 s, stop it. onstop fires one final ondataavailable with a
    // complete webm, then we spin up the next recorder. ~50 ms gap
    // between chunks is acceptable at this cadence.
    var startSegment = function () {
      if (!sessionActive || !stream) return;
      try {
        recorder = new MediaRecorder(stream, { mimeType: chosenMime });
      } catch (err) {
        console.warn('[screen_share] MediaRecorder ctor failed:', err);
        title.textContent = 'Recorder failed';
        status.textContent = String(err && err.message ? err.message : err);
        setColor('error');
        sessionActive = false;
        return;
      }
      recorder.onerror = function (e) {
        var msg = (e && e.error && e.error.message) ? e.error.message : 'recorder error';
        console.warn('[screen_share] recorder.onerror:', e);
      };
      recorder.ondataavailable = function (e) {
        console.log('[screen_share] ondataavailable size=' + (e.data && e.data.size) + ' state=' + recorder.state);
        if (e.data && e.data.size > 0) {
          postChunk(e.data, Date.now());
        }
      };
      recorder.onstop = function () {
        // Either the segment timer fired (normal cadence) or the user
        // pressed STOP. In the active case, spin up the next segment
        // immediately. In the stop case, sessionActive is false.
        if (sessionActive) {
          startSegment();
        } else {
          finalizeStop();
        }
      };
      try {
        recorder.start();
      } catch (err) {
        console.warn('[screen_share] recorder.start failed:', err);
        sessionActive = false;
        return;
      }
      segmentTimer = setTimeout(function () {
        if (recorder && recorder.state === 'recording') {
          try { recorder.stop(); } catch (_) {}
        }
      }, 3000);
    };

    var finalizeStop = function () {
      if (segmentTimer) { clearTimeout(segmentTimer); segmentTimer = null; }
      if (stream) {
        stream.getTracks().forEach(function (t) { try { t.stop(); } catch (_) {} });
        stream = null;
      }
      title.textContent = 'Stopped';
      audioNoteLive = '';
      status.textContent = 'Idle';
      setColor('idle');
      button.textContent = 'START';
      var fd = new FormData();
      fd.append('session_id', sessionId);
      fetch('/api/perception/screen_chunk/stop', {
        method: 'POST', headers: authHeaders, body: fd,
      }).catch(function () {});
      report({
        kind: 'screen_share',
        content: 'Stopped',
        extra: { active: false, session_id: sessionId },
      });
    };

    var startRecorder = function (mediaStream) {
      stream = mediaStream;
      chosenMime = pickMime(mediaStream);
      console.log('[screen_share] starting recorder mime=' + chosenMime + ' audioTracks=' + mediaStream.getAudioTracks().length + ' videoTracks=' + mediaStream.getVideoTracks().length);
      sessionActive = true;
      startSegment();
      title.textContent = 'Sharing';
      audioNoteLive = hasSystemAudio ? ' + system audio' : ' (mic via ambient_mic block)';
      refreshStatusCounter();
      setColor('active');
      button.textContent = 'STOP';
      ensureAmbientMicMounted();
      report({
        kind: 'screen_share',
        content: 'Sharing ' + (sourceName || 'screen') + audioNoteLive,
        extra: {
          active: true,
          source_name: sourceName,
          session_id: sessionId,
          has_system_audio: hasSystemAudio,
        },
      });
    };

    var hasSystemAudio = false;

    var requestStream = async function () {
      // Prefer Electron desktopCapturer (no OS picker — silent start with
      // the first available screen source). Fall back to getDisplayMedia
      // when running in a plain browser.
      if (bridge && bridge.listSources) {
        var sources = await bridge.listSources();
        if (!sources || sources.length === 0) throw new Error('no screen sources');
        var pick = sources.find(function (s) { return s.kind === 'screen'; }) || sources[0];
        sourceName = pick.name || '';
        var videoConstraint = {
          mandatory: {
            chromeMediaSource: 'desktop',
            chromeMediaSourceId: pick.id,
            maxWidth: 1280,
            maxHeight: 720,
            maxFrameRate: 15,
          },
        };
        // Video-only on purpose. PROVEN by the next-dev log: when we
        // included `audio: {mandatory: {chromeMediaSource: 'desktop'}}`,
        // Chromium grabbed an audio device that conflicted with
        // ambient_mic's VAD getUserMedia({audio: true}) — every VAD
        // retry returned NotFoundError once screen_share had the audio
        // track open. Mic is the more important channel for the persona
        // (it gets a clean Whisper transcript via ambient_mic →
        // /perception/utterance), so we don't fight for the shared
        // audio device. System audio capture is deferred until we have
        // a separate audio-routing path that doesn't compete.
        hasSystemAudio = false;
        return await navigator.mediaDevices.getUserMedia({ video: videoConstraint });
      }
      // Browser fallback (non-Electron). getDisplayMedia is video-only
      // here for the same reason: don't compete with ambient_mic for the
      // audio device.
      sourceName = 'browser-shared';
      var ds = await navigator.mediaDevices.getDisplayMedia({
        video: { width: 1280, height: 720, frameRate: 15 },
      });
      hasSystemAudio = false;
      return ds;
    };

    // While sharing, the user typically also wants to talk. Mic capture
    // lives in the ambient_mic block (separate stream → separate
    // perception path → cleanly interleaved with screen segments by
    // wall-clock). If ambient_mic isn't already on canvas, mount it so
    // the user has a one-click path to "voice on" without leaving the
    // share. Best-effort: if the helper isn't available or the mount
    // fails, just skip — the user can still mount it manually.
    var ensureAmbientMicMounted = function () {
      if (!backend || !backend.mount_template) return;
      backend.mount_template({ template: 'ambient_mic' }).catch(function (err) {
        // If the block is already mounted the backend may noop or error;
        // either is fine. Log and move on.
        console.log('[screen_share] ambient_mic mount result:', err && err.message ? err.message : 'noop');
      });
    };

    var onClick = function () {
      if (sessionActive) {
        // User pressed STOP — flip the flag so onstop runs finalizeStop
        // instead of starting the next segment, then trigger the final
        // segment's emission by stopping the live recorder.
        sessionActive = false;
        if (segmentTimer) { clearTimeout(segmentTimer); segmentTimer = null; }
        if (recorder && recorder.state === 'recording') {
          try { recorder.stop(); } catch (_) {}
        } else {
          finalizeStop();
        }
        return;
      }
      title.textContent = 'Starting…';
      status.textContent = 'Requesting screen';
      button.disabled = true;
      requestStream().then(startRecorder).catch(function (err) {
        title.textContent = 'Permission denied';
        status.textContent = (err && err.message) ? err.message : String(err);
        setColor('error');
      }).finally(function () { button.disabled = false; });
    };
    button.addEventListener('click', onClick);
    cleanup(function () { button.removeEventListener('click', onClick); });

    cleanup(function () {
      sessionActive = false;
      if (segmentTimer) { clearTimeout(segmentTimer); segmentTimer = null; }
      if (recorder && recorder.state === 'recording') {
        try { recorder.stop(); } catch (_) {}
      } else if (stream) {
        stream.getTracks().forEach(function (t) { try { t.stop(); } catch (_) {} });
      }
    });

    report({
      kind: 'screen_share',
      content: 'Idle (click START to share)',
      extra: { active: false, session_id: sessionId },
    });
  },
})
