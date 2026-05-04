({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip auto-snapshot — the helpers.reportState calls below carry the
  // structured passage state, which is what the persona actually wants.
  autosnapshot: false,
  style: {
    background: 'linear-gradient(180deg, #1f2937 0%, #111827 100%)',
    color: '#f9fafb',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif',
    borderRadius: '14px',
    border: '1px solid rgba(255,255,255,0.08)',
    boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
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
    header.style.padding = '10px 14px';
    header.style.borderBottom = '1px solid rgba(255,255,255,0.06)';
    header.style.background = 'rgba(15,23,42,0.6)';
    header.style.display = 'flex';
    header.style.alignItems = 'center';
    header.style.gap = '10px';
    header.style.flexShrink = '0';

    var headerIcon = document.createElement('div');
    headerIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
    headerIcon.style.color = '#94a3b8';
    headerIcon.style.display = 'flex';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'Passage';
    headerTitle.style.fontSize = '13px';
    headerTitle.style.fontWeight = '600';
    headerTitle.style.flex = '1';

    var headerMeta = document.createElement('div');
    headerMeta.style.fontSize = '11px';
    headerMeta.style.color = '#64748b';
    headerMeta.style.flexShrink = '0';
    headerMeta.textContent = 'empty';

    header.appendChild(headerIcon);
    header.appendChild(headerTitle);
    header.appendChild(headerMeta);
    root.appendChild(header);

    // ---- Textarea -------------------------------------------------------
    var textarea = document.createElement('textarea');
    textarea.placeholder = 'Paste or type a passage you want to read…';
    textarea.spellcheck = false;
    textarea.style.flex = '1';
    textarea.style.width = '100%';
    textarea.style.padding = '16px 18px';
    textarea.style.fontSize = '14px';
    textarea.style.lineHeight = '1.6';
    textarea.style.fontFamily = 'inherit';
    textarea.style.color = '#e2e8f0';
    textarea.style.background = 'transparent';
    textarea.style.border = 'none';
    textarea.style.outline = 'none';
    textarea.style.resize = 'none';
    textarea.style.boxSizing = 'border-box';
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
