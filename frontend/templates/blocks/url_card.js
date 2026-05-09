({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // We publish a structured 'url_card' report ourselves; skip the
  // generic auto-snapshot so read_media gets a clean entry instead of
  // a raw DOM dump.
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

    // Initial values are substituted at mount time. JSON-encoded to
    // become valid JS literals; default empty string so the block stays
    // valid when params are missing.
    var initialUrl = __URL_CARD_URL__;
    var initialTitle = __URL_CARD_TITLE__;
    var initialExcerpt = __URL_CARD_EXCERPT__;

    function hostOf(u) {
      try { return new URL(u).host || u; } catch (_) { return u || ''; }
    }

    // ---- Header strip --------------------------------------------------
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'READ';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var titleEl = document.createElement('a');
    titleEl.style.cssText =
      'flex:1; font-size:12px; font-weight:600;' +
      'color:var(--bw-ink); text-decoration:none;' +
      'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;' +
      'min-width:0;';
    // External link in a real browser tab — preserves the existing
    // user behaviour for "I want to actually open this" without going
    // through the BrowserView pane.
    titleEl.target = '_blank';
    titleEl.rel = 'noreferrer';

    var hostEl = document.createElement('span');
    hostEl.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;' +
      'text-transform:lowercase; max-width:40%;' +
      'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;';

    header.appendChild(idChip);
    header.appendChild(titleEl);
    header.appendChild(hostEl);
    root.appendChild(header);

    // ---- Body: one-line excerpt ----------------------------------------
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; padding:10px 14px;' +
      'font-size:12px; line-height:1.45;' +
      'color:var(--bw-ink-soft);' +
      'overflow:hidden; display:-webkit-box;' +
      '-webkit-line-clamp:2; -webkit-box-orient:vertical;';
    root.appendChild(body);

    // ---- Render + state report -----------------------------------------
    var currentUrl = '';
    var currentTitle = '';
    var currentExcerpt = '';

    function applyState(url, title, excerpt) {
      currentUrl = url || '';
      currentTitle = title || '';
      currentExcerpt = excerpt || '';

      titleEl.textContent = currentTitle || currentUrl || '(no title)';
      titleEl.href = currentUrl || '#';
      hostEl.textContent = hostOf(currentUrl);
      body.textContent = currentExcerpt;

      var head = currentTitle || currentUrl || '(empty)';
      var summary = currentExcerpt
        ? (head + ' · ' + currentExcerpt.slice(0, 160))
        : head;
      report({
        kind: 'url_card',
        content: summary,
        extra: {
          url: currentUrl,
          title: currentTitle,
          excerpt: currentExcerpt,
        },
      });
    }

    applyState(initialUrl, initialTitle, initialExcerpt);
  },
})
