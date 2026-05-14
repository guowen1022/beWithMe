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
    // position:relative so absolutely-positioned overlays from edit
    // ops (arrow chips, future popovers) anchor to the card body
    // rather than the page.
    body.style.cssText = 'position:relative; flex:1; padding:18px 22px; overflow-y:auto; box-sizing:border-box;';
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

    // ---- Phase 2 voice-leads: animated edits --------------------------
    // The canvas writer's `edit_rich_card` tool fans out one BlockMessage
    // per call on `text.<block_id>.edits` with `{ops: [...], new_html}`.
    // We animate each op against the live DOM, then reconcile body to
    // new_html so future edits operate on the server's truth.
    ensureEditStyles();

    var unsubEdits = bus.subscribe('__EDITS_TOPIC__', function (payload) {
      if (!payload || !Array.isArray(payload.ops)) return;
      applyEdits(payload.ops, payload.new_html);
    });
    cleanup(function () { unsubEdits(); });

    function applyEdits(ops, newHtml) {
      var anyStructural = false;
      for (var i = 0; i < ops.length; i++) {
        var op = ops[i] || {};
        try {
          switch (op.op) {
            case 'append':           anyStructural = true; animateBoundary('end',   op.html); break;
            case 'prepend':          anyStructural = true; animateBoundary('start', op.html); break;
            case 'replace_section':  anyStructural = true; animateReplaceSection(op.anchor_text, op.html); break;
            case 'revise':           anyStructural = true; animateRevise(op.target_text, op.new_text); break;
            case 'highlight':        animateHighlight(op.target_text, op.duration_ms); break;
            case 'arrow_to_text':    animateArrow(op.target_text, op.label, op.direction); break;
            case 'annotate':         animateAnnotate(op.target_text, op.note); break;
          }
        } catch (e) {
          // Animation failure is best-effort. The reconcile below still
          // gives the user the final DOM.
          if (typeof console !== 'undefined' && console.warn) {
            console.warn('[rich_card] edit op failed:', op, e);
          }
        }
      }
      // Reconcile body to authoritative new_html AFTER animations have
      // had a moment to play. Skip when no structural ops fired so the
      // pulse/arrow/annotate overlays survive long enough to be seen.
      if (anyStructural && typeof newHtml === 'string' && newHtml.length) {
        window.setTimeout(function () { setHtml(newHtml); }, 700);
      } else {
        publishState();
      }
    }

    // ---- DOM-locator helpers ----
    // findTextNodes walks every text node under body and returns the
    // first one whose text contains `needle`. Used by all target_text ops.
    function findTextNode(needle) {
      if (!needle || !body) return null;
      var walker = document.createTreeWalker(
        body, NodeFilter.SHOW_TEXT, null
      );
      var node;
      while ((node = walker.nextNode())) {
        if (node.nodeValue && node.nodeValue.indexOf(needle) !== -1) {
          return node;
        }
      }
      return null;
    }

    function findBlockContaining(needle) {
      var text = findTextNode(needle);
      if (!text) return null;
      var el = text.parentElement;
      while (el && el !== body) {
        var tag = (el.tagName || '').toLowerCase();
        if (tag === 'p' || tag === 'h2' || tag === 'h3' || tag === 'h4'
            || tag === 'div' || tag === 'ul' || tag === 'ol' || tag === 'li') {
          return el;
        }
        el = el.parentElement;
      }
      return null;
    }

    // ---- Animations ----
    function animateBoundary(where, html) {
      if (typeof html !== 'string' || !html) return;
      // Render the new HTML into a detached container so we can attach
      // the enter class to its top-level children.
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      var newNodes = [];
      while (tmp.firstChild) newNodes.push(tmp.firstChild), tmp.removeChild(tmp.firstChild);
      // Find the outermost .card container to insert inside; fall back
      // to body root if absent.
      var host = body.querySelector('.card') || body;
      for (var i = 0; i < newNodes.length; i++) {
        var n = newNodes[i];
        if (n.nodeType === 1) n.classList.add('bw-edit-enter');
        if (where === 'end') host.appendChild(n);
        else host.insertBefore(n, host.firstChild);
      }
    }

    function animateReplaceSection(anchorText, html) {
      var target = findBlockContaining(anchorText);
      if (!target || typeof html !== 'string') return;
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      var nodes = [];
      while (tmp.firstChild) nodes.push(tmp.firstChild), tmp.removeChild(tmp.firstChild);
      // Fade old out, fade new in.
      target.classList.add('bw-edit-exit');
      var parent = target.parentNode;
      window.setTimeout(function () {
        if (!parent || !target.parentNode) return;
        var anchor = target;
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          if (n.nodeType === 1) n.classList.add('bw-edit-enter');
          parent.insertBefore(n, anchor.nextSibling);
          anchor = n;
        }
        parent.removeChild(target);
      }, 250);
    }

    function animateRevise(targetText, newText) {
      var node = findTextNode(targetText);
      if (!node) return;
      var parent = node.parentNode;
      var idx = node.nodeValue.indexOf(targetText);
      if (idx < 0) return;
      var before = node.nodeValue.slice(0, idx);
      var after = node.nodeValue.slice(idx + targetText.length);
      var span = document.createElement('span');
      span.className = 'revision-changed bw-edit-revise';
      var del = document.createElement('del');
      del.textContent = targetText;
      var ins = document.createElement('ins');
      ins.textContent = newText || '';
      span.appendChild(del);
      span.appendChild(ins);
      var afterNode = document.createTextNode(after);
      node.nodeValue = before;
      parent.insertBefore(span, node.nextSibling);
      parent.insertBefore(afterNode, span.nextSibling);
    }

    function animateHighlight(targetText, durationMs) {
      var node = findTextNode(targetText);
      if (!node) return;
      var parent = node.parentNode;
      var idx = node.nodeValue.indexOf(targetText);
      if (idx < 0) return;
      var before = node.nodeValue.slice(0, idx);
      var after = node.nodeValue.slice(idx + targetText.length);
      var span = document.createElement('span');
      span.className = 'bw-edit-pulse';
      span.textContent = targetText;
      var afterNode = document.createTextNode(after);
      node.nodeValue = before;
      parent.insertBefore(span, node.nextSibling);
      parent.insertBefore(afterNode, span.nextSibling);
      var dur = (typeof durationMs === 'number' && durationMs > 0) ? durationMs : 1500;
      window.setTimeout(function () {
        // Unwrap the pulse span so the DOM goes back to a clean text node
        // (next edit sees the same shape as before).
        if (!span.parentNode) return;
        span.parentNode.insertBefore(document.createTextNode(span.textContent || ''), span);
        span.parentNode.removeChild(span);
      }, dur + 200);
    }

    function animateArrow(targetText, label, direction) {
      var node = findTextNode(targetText);
      if (!node || !node.parentElement) return;
      var rect;
      try {
        var range = document.createRange();
        range.setStart(node, Math.max(0, node.nodeValue.indexOf(targetText)));
        range.setEnd(node, Math.min(node.nodeValue.length, node.nodeValue.indexOf(targetText) + targetText.length));
        rect = range.getBoundingClientRect();
      } catch (_) { return; }
      var bodyRect = body.getBoundingClientRect();
      var chip = document.createElement('div');
      chip.className = 'bw-arrow-chip';
      chip.textContent = (direction === 'left' ? '← ' : '→ ') + (label || 'this');
      chip.style.left = (rect.right - bodyRect.left + 8) + 'px';
      chip.style.top  = (rect.top - bodyRect.top - 2) + 'px';
      body.appendChild(chip);
      window.setTimeout(function () {
        chip.classList.add('bw-arrow-leave');
        window.setTimeout(function () {
          if (chip.parentNode) chip.parentNode.removeChild(chip);
        }, 500);
      }, 2800);
    }

    function animateAnnotate(targetText, note) {
      var node = findTextNode(targetText);
      if (!node || !node.parentElement) return;
      var chip = document.createElement('span');
      chip.className = 'bw-annotation-chip';
      chip.textContent = note || '';
      // Insert right after the target text's containing element.
      var host = node.parentElement;
      host.appendChild(chip);
    }

    // ---- One-shot style sheet ----
    function ensureEditStyles() {
      if (document.getElementById('bw-rich-card-edit-style')) return;
      var s = document.createElement('style');
      s.id = 'bw-rich-card-edit-style';
      s.textContent =
        '@keyframes bw-edit-enter { from { opacity:0; transform:translateY(-6px); } to { opacity:1; transform:translateY(0); } }' +
        '@keyframes bw-edit-exit  { from { opacity:1; } to { opacity:0; transform:translateY(6px); } }' +
        '@keyframes bw-edit-pulse { 0%,100% { background-color: transparent; } 50% { background-color: rgba(250,204,21,0.55); } }' +
        '@keyframes bw-arrow-fade { from { opacity:0; transform: translateX(-4px); } to { opacity:1; transform: translateX(0); } }' +
        '.bw-edit-enter { animation: bw-edit-enter 0.45s ease-out both; }' +
        '.bw-edit-exit  { animation: bw-edit-exit  0.25s ease-in  both; }' +
        '.bw-edit-pulse { animation: bw-edit-pulse 1.5s ease-in-out 2; border-radius: 3px; padding: 0 2px; }' +
        '.bw-edit-revise del { background: rgba(229,131,124,0.35); text-decoration: line-through; margin-right: 4px; padding: 0 3px; border-radius: 3px; }' +
        '.bw-edit-revise ins { background: rgba(120,200,140,0.4); text-decoration: none; padding: 0 3px; border-radius: 3px; }' +
        '.bw-arrow-chip { position: absolute; z-index: 4; font-family: var(--bw-font-mono,monospace); font-size: 11px; padding: 2px 8px; background: rgba(250,204,21,0.95); color: #111; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.25); pointer-events: none; animation: bw-arrow-fade 0.35s ease-out both; white-space: nowrap; }' +
        '.bw-arrow-chip.bw-arrow-leave { opacity: 0; transition: opacity 0.4s ease-in; }' +
        '.bw-annotation-chip { display: inline-block; margin-left: 6px; padding: 1px 6px; font-size: 10.5px; font-family: var(--bw-font-mono,monospace); background: rgba(255,255,255,0.08); color: var(--bw-ink-muted,#aaa); border: 1px solid var(--bw-border,#333); border-radius: 8px; vertical-align: 1px; }';
      document.head.appendChild(s);
    }
  },
})
