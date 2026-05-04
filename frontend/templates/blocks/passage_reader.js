({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip auto-snapshot — the helpers.reportState calls below carry the
  // structured passage state, which is what the persona actually wants.
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
  publishes: ['__SELECTION_TOPIC__'],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};
    var blockId = (helpers && helpers.blockId) || '__BLOCK_ID__';

    // ---- Header ---------------------------------------------------------
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'PASSAGE';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'Passage';
    headerTitle.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;';

    var headerMeta = document.createElement('div');
    headerMeta.textContent = 'empty';
    headerMeta.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;' +
      'text-transform:uppercase; letter-spacing:.08em;';

    header.appendChild(idChip);
    header.appendChild(headerTitle);
    header.appendChild(headerMeta);
    root.appendChild(header);

    // ---- Textarea -------------------------------------------------------
    var textarea = document.createElement('textarea');
    textarea.placeholder = 'Paste or type a passage you want to read…';
    textarea.spellcheck = false;
    textarea.style.cssText =
      'flex:1; width:100%; padding:14px 16px;' +
      'font-family:inherit; font-size:13px; line-height:1.6;' +
      'color:var(--bw-ink); background:transparent;' +
      'border:none; outline:none; resize:none; box-sizing:border-box;';
    root.appendChild(textarea);

    // ---- Reporting (debounced 200ms) ------------------------------------
    var reportTimer = null;

    function summarize(text, selection) {
      var trimmed = text.trim();
      var head = trimmed.slice(0, 200);
      var content = head;
      if (trimmed.length > 200) content += '…';
      return {
        kind: 'passage',
        content: content,
        extra: {
          char_count: trimmed.length,
          selection: selection || null,
        },
      };
    }

    function getSelection() {
      var s = textarea.selectionStart;
      var e = textarea.selectionEnd;
      if (s === e) return '';
      return textarea.value.substring(s, e).trim();
    }

    function scheduleReport() {
      if (reportTimer) clearTimeout(reportTimer);
      reportTimer = setTimeout(function () {
        var sel = getSelection();
        var trimmed = textarea.value.trim();
        headerMeta.textContent = trimmed.length
          ? (trimmed.length + ' chars' + (sel ? ' · selected ' + sel.length : ''))
          : 'empty';
        report(summarize(textarea.value, sel));
      }, 200);
    }

    var onInput = function () { scheduleReport(); };
    var onSelect = function () {
      var sel = getSelection();
      if (sel) bus.publish('__SELECTION_TOPIC__', sel);
      scheduleReport();
    };
    textarea.addEventListener('input', onInput);
    textarea.addEventListener('select', onSelect);
    textarea.addEventListener('mouseup', onSelect);
    textarea.addEventListener('keyup', onSelect);
    cleanup(function () {
      textarea.removeEventListener('input', onInput);
      textarea.removeEventListener('select', onSelect);
      textarea.removeEventListener('mouseup', onSelect);
      textarea.removeEventListener('keyup', onSelect);
      if (reportTimer) clearTimeout(reportTimer);
    });

    // Initial empty report so read_media surfaces the block right away
    // (rather than as a "no-report-yet" entry).
    report(summarize('', ''));

    // Try to focus once the surface is laid out so the user can start
    // typing immediately.
    setTimeout(function () { try { textarea.focus(); } catch (_) {} }, 50);
  },
})
