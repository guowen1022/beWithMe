({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip auto-snapshot — we publish a structured `rich` report from run() so
  // read_media gets a clean text+counts entry instead of a raw DOM dump.
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
  subscribes: ['__CONTENT_TOPIC__'],
  publishes: ['__SELECTION_TOPIC__'],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};
    // mount_template replaces __CONTENT__ with a JSON-encoded string
    // literal at substitution time — already sanitized + SVG-inlined by
    // the backend preprocessor (infra/render/rich_card.process).
    var initial = __CONTENT__;

    // ---- Header ---------------------------------------------------------
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'CARD';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'Explanation';
    headerTitle.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;';

    var headerMeta = document.createElement('div');
    headerMeta.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;' +
      'text-transform:uppercase; letter-spacing:.08em;';

    header.appendChild(idChip);
    header.appendChild(headerTitle);
    header.appendChild(headerMeta);
    root.appendChild(header);

    // ---- Body -----------------------------------------------------------
    // Backend has sanitized this HTML against the rich_card grammar AND
    // inlined the Mermaid SVGs. We trust the bytes and set innerHTML
    // directly — no DOMPurify pass here. See infra/render/rich_card.py
    // for the sanitization contract.
    var body = document.createElement('div');
    body.className = 'bw-card';
    body.style.cssText = 'flex:1; padding:18px 22px; overflow-y:auto; box-sizing:border-box;';
    root.appendChild(body);

    var currentHtml = '';
    var currentSelection = '';

    function plaintextFallback() {
      // Grab text content for read_media's textual mirror. innerText
      // collapses inline whitespace the way a user sees it.
      var t = body.innerText || body.textContent || '';
      return t.replace(/\s+/g, ' ').trim();
    }

    function publishState() {
      var text = plaintextFallback();
      var diagCount = body.querySelectorAll('.bw-diagram').length;
      var imgCount  = body.querySelectorAll('.bw-image').length;
      headerMeta.textContent = text.length
        ? (text.length + ' chars' + (diagCount ? ' · ' + diagCount + ' diag' : '') + (imgCount ? ' · ' + imgCount + ' img' : ''))
        : 'empty';
      var head = text.slice(0, 200);
      report({
        kind: 'rich',
        content: head + (text.length > 200 ? '…' : ''),
        extra: {
          char_count: text.length,
          diagram_count: diagCount,
          image_count: imgCount,
          selection: currentSelection || null,
        },
      });
    }

    function setHtml(html) {
      currentHtml = (typeof html === 'string') ? html : '';
      body.innerHTML = currentHtml;
      currentSelection = '';
      publishState();
    }

    function readSelection() {
      try {
        var sel = window.getSelection && window.getSelection();
        if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return '';
        var anchor = sel.anchorNode;
        var focus = sel.focusNode;
        if (!anchor || !focus) return '';
        if (!body.contains(anchor) || !body.contains(focus)) return '';
        return String(sel.toString() || '').trim();
      } catch (_) {
        return '';
      }
    }

    function onSelectMaybe() {
      var sel = readSelection();
      if (sel === currentSelection) return;
      currentSelection = sel;
      if (sel) {
        try { bus.publish('__SELECTION_TOPIC__', sel); } catch (_) {}
      }
      publishState();
    }

    body.addEventListener('mouseup', onSelectMaybe);
    body.addEventListener('keyup', onSelectMaybe);
    document.addEventListener('selectionchange', onSelectMaybe);
    cleanup(function () {
      body.removeEventListener('mouseup', onSelectMaybe);
      body.removeEventListener('keyup', onSelectMaybe);
      document.removeEventListener('selectionchange', onSelectMaybe);
    });

    setHtml(initial);

    // Subscribe so push_block_content can replace the card body in place.
    // Payload is the already-preprocessed HTML (workshop runs it through
    // infra.render.rich_card.process before fan-out).
    var unsub = bus.subscribe('__CONTENT_TOPIC__', function (payload) {
      if (typeof payload === 'string') {
        setHtml(payload);
      } else if (payload && typeof payload.content === 'string') {
        setHtml(payload.content);
      }
    });
    cleanup(function () { unsub(); });
  },
})
