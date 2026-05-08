({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip auto-snapshot — we publish a structured `text` report from the
  // run() body so read_media surfaces a proper kind:'text' entry instead
  // of a raw DOM dump.
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
    // The content placeholder below is replaced by mount_template with a
    // JSON-encoded string literal, so this is a complete JS expression
    // after rendering (default "" when no params.content is passed).
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
    idChip.textContent = 'NOTE';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'Note';
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
    // The `bw-prose` class owns typography (headings, tables, code, em→serif,
    // accent links, list markers) — see frontend/app/globals.css. Inline
    // styles here only set layout (padding, scroll, box model); typography
    // is delegated so every markdown surface looks identical project-wide.
    var body = document.createElement('div');
    body.className = 'bw-prose';
    body.style.cssText =
      'flex:1; padding:18px 22px;' +
      'overflow-y:auto; box-sizing:border-box;';
    root.appendChild(body);

    // ---- Markdown rendering --------------------------------------------
    // Persona prose arrives as GFM markdown (tables, headings, lists,
    // fenced code, blockquotes, etc.). We delegate to helpers.markdown
    // which is backed by the host's `marked` instance — so every block
    // gets the same parser and we don't reinvent it per template.
    var renderMarkdown = (helpers && typeof helpers.markdown === 'function')
      ? helpers.markdown
      : function (s) {
          // Fallback for an older host that doesn't expose helpers.markdown:
          // just escape and show plain text. Better than crashing.
          return String(s || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        };

    // Last full text and last user-highlighted slice. Both go into every
    // state report so the teacher's read_media sees both the prose and
    // what the user just selected ("what's this?" anchors on extra.selection).
    var currentText = '';
    var currentSelection = '';

    function publishState() {
      var trimmed = currentText.trim();
      headerMeta.textContent = trimmed.length
        ? (trimmed.length + ' chars' + (currentSelection ? ' · selected ' + currentSelection.length : ''))
        : 'empty';
      var head = trimmed.slice(0, 200);
      var content = head + (trimmed.length > 200 ? '…' : '');
      report({
        kind: 'text',
        content: content,
        extra: {
          char_count: trimmed.length,
          selection: currentSelection || null,
        },
      });
    }

    function setText(text) {
      currentText = (typeof text === 'string') ? text : '';
      body.innerHTML = renderMarkdown(currentText);
      // Replacing innerHTML invalidates any prior selection.
      currentSelection = '';
      publishState();
    }

    function readSelection() {
      try {
        var sel = window.getSelection && window.getSelection();
        if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return '';
        // Only honour selections anchored inside this block's body —
        // otherwise we'd echo selections made in other blocks.
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
        // Mirror passage_reader: emit on the global selection topic so
        // other blocks (and the teacher's selection-aware tooling) see it.
        try { bus.publish('__SELECTION_TOPIC__', sel); } catch (_) {}
      }
      publishState();
    }

    body.addEventListener('mouseup', onSelectMaybe);
    body.addEventListener('keyup', onSelectMaybe);
    // selectionchange fires on the document; cheap enough to listen
    // globally and filter by anchor in readSelection().
    document.addEventListener('selectionchange', onSelectMaybe);
    cleanup(function () {
      body.removeEventListener('mouseup', onSelectMaybe);
      body.removeEventListener('keyup', onSelectMaybe);
      document.removeEventListener('selectionchange', onSelectMaybe);
    });

    setText(initial);

    // Subscribe so the persona can replace the prose later via a
    // push_block_content call on this block's content topic. Accepts
    // either a raw string payload or {content: string}.
    var unsub = bus.subscribe('__CONTENT_TOPIC__', function (payload) {
      if (typeof payload === 'string') {
        setText(payload);
      } else if (payload && typeof payload.content === 'string') {
        setText(payload.content);
      }
    });
    cleanup(function () { unsub(); });
  },
})
