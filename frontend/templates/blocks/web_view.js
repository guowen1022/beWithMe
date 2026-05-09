({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // The block's DOM has no meaningful content — the BrowserView pane is
  // what the user actually looks at. Skip auto-snapshot; we publish a
  // structured `web_view` report ourselves below.
  autosnapshot: false,
  style: {
    background: 'var(--bw-surface)',
    color: 'var(--bw-ink)',
    fontFamily: 'var(--bw-font-sans)',
    borderRadius: '0',
    border: '1px solid var(--bw-border)',
    padding: '0',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
  },
  subscribes: [],
  publishes: [],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};
    var bridge = (typeof window !== 'undefined' && window.beWithMeBridge && window.beWithMeBridge.browser) || null;

    // ---- Header --------------------------------------------------------
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'WEB';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var titleEl = document.createElement('div');
    titleEl.textContent = 'Web view';
    titleEl.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;';

    var urlEl = document.createElement('div');
    urlEl.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:1; min-width:0;' +
      'overflow:hidden; text-overflow:ellipsis; white-space:nowrap;' +
      'max-width:50%;';

    header.appendChild(idChip);
    header.appendChild(titleEl);
    header.appendChild(urlEl);
    root.appendChild(header);

    // ---- Body ----------------------------------------------------------
    // The body is the slot the BrowserView covers. We don't put anything
    // inside it (the BrowserView would just hide our content). We DO
    // measure it on every layout change and forward those bounds to the
    // bridge so the BrowserView tracks the block's current rect.
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; position:relative; background:var(--bw-surface-2);' +
      'min-height:0;';
    root.appendChild(body);

    // ---- Non-Electron fallback ----------------------------------------
    if (!bridge) {
      var placeholder = document.createElement('div');
      placeholder.style.cssText =
        'position:absolute; inset:0;' +
        'display:flex; flex-direction:column; align-items:center; justify-content:center;' +
        'gap:8px; padding:24px; text-align:center;' +
        'color:var(--bw-ink-faint); font-size:13px;';
      var headline = document.createElement('div');
      headline.textContent = 'Web view requires the desktop app';
      headline.style.cssText = 'font-size:14px; color:var(--bw-ink); font-weight:600;';
      var sub = document.createElement('div');
      sub.textContent =
        'Open beWithMe in the desktop shell to view live web content.' +
        ' This block uses a real Chromium pane that only the desktop ships.';
      placeholder.appendChild(headline);
      placeholder.appendChild(sub);
      body.appendChild(placeholder);

      report({
        kind: 'web_view',
        content: '(web view unavailable — not running in desktop)',
        extra: { available: false },
      });
      return;
    }

    // ---- Bounds tracking ----------------------------------------------
    var lastSent = { x: -1, y: -1, width: -1, height: -1 };
    function syncBounds() {
      var r = body.getBoundingClientRect();
      var rect = {
        x: Math.round(r.left),
        y: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
      if (rect.width <= 1 || rect.height <= 1) return;
      if (
        rect.x === lastSent.x && rect.y === lastSent.y &&
        rect.width === lastSent.width && rect.height === lastSent.height
      ) return;
      lastSent = rect;
      try { bridge.setBounds(rect); } catch (_) { /* main not ready yet */ }
    }

    // Initial sync + cheap belt-and-suspenders listeners for events that
    // *do* fire on resize / scroll.
    syncBounds();
    var ro = (typeof ResizeObserver !== 'undefined') ? new ResizeObserver(syncBounds) : null;
    if (ro) ro.observe(body);
    window.addEventListener('resize', syncBounds);
    window.addEventListener('scroll', syncBounds, true);

    // Continuous requestAnimationFrame loop. react-grid drags the block
    // via CSS transform, which does NOT fire ResizeObserver (no size
    // delta) or scroll listeners — only `getBoundingClientRect()` reports
    // the new visual position. Polling every frame is the only way to
    // keep the BrowserView locked to the block while it's being dragged.
    //
    // Cost: getBoundingClientRect is microseconds; the lastSent dedup
    // skips bridge.setBounds when the rect is unchanged, so IPC traffic
    // is zero while stable. Browser throttles rAF when the tab is hidden,
    // so background blocks pay nothing.
    var rafHandle = 0;
    var stopped = false;
    function tick() {
      if (stopped) return;
      syncBounds();
      rafHandle = requestAnimationFrame(tick);
    }
    rafHandle = requestAnimationFrame(tick);

    cleanup(function () {
      stopped = true;
      if (rafHandle) cancelAnimationFrame(rafHandle);
      window.removeEventListener('resize', syncBounds);
      window.removeEventListener('scroll', syncBounds, true);
      if (ro) { try { ro.disconnect(); } catch (_) {} }
      // The page contents stay loaded in the WebContents; we just hide
      // the pane so the next mount can re-show without reloading.
      try { bridge.hide(); } catch (_) {}
    });

    // ---- State reporting ----------------------------------------------
    var currentUrl = '';
    var currentTitle = '';
    function publishState() {
      var head = currentTitle || currentUrl || '(no page)';
      urlEl.textContent = currentUrl;
      titleEl.textContent = currentTitle ? currentTitle : 'Web view';
      report({
        kind: 'web_view',
        content: head,
        extra: {
          url: currentUrl,
          title: currentTitle,
          available: true,
        },
      });
    }
    publishState();

    // The bridge already fans browser:url-changed events. Subscribe so
    // navigations driven by the persona's web_view(open, url) tool
    // surface in the block's header + perception report without us
    // having to poll.
    var unsubUrl = bridge.onUrlChange(function (p) {
      currentUrl = (p && p.url) || '';
      currentTitle = (p && p.title) || '';
      publishState();
    });
    cleanup(function () { try { unsubUrl(); } catch (_) {} });

    // Seed with whatever URL is currently loaded (if anything).
    bridge.getCurrentUrl().then(function (url) {
      if (url && !currentUrl) {
        currentUrl = url;
        publishState();
      }
    }).catch(function () { /* ignored */ });
  },
})
