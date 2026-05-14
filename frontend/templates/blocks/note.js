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
    // the backend preprocessor (infra/render/note.process).
    var initial = __CONTENT__;
    // Whether to typewriter-reveal the initial content. True for fresh
    // authoring (LLM just wrote this); false for hydrate (we loaded a
    // stored note from disk — show it instantly).
    var animateInitial = __ANIMATE__;
    // Note timestamps for the header subtitle. Empty strings = unknown.
    var createdAtIso = __CREATED_AT__;
    var updatedAtIso = __UPDATED_AT__;

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

    var headerTitleWrap = document.createElement('div');
    headerTitleWrap.style.cssText =
      'flex:1; min-width:0; display:flex; flex-direction:column; gap:1px;';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'Explanation';
    headerTitle.style.cssText =
      'font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;';

    // Subtitle line: created / last-updated timestamps. Hidden when
    // both timestamps are unknown (legacy mounts without meta).
    var headerSubtitle = document.createElement('div');
    headerSubtitle.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-ink-faint); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;' +
      'letter-spacing:.04em;';

    headerTitleWrap.appendChild(headerTitle);
    headerTitleWrap.appendChild(headerSubtitle);

    var headerMeta = document.createElement('div');
    headerMeta.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;' +
      'text-transform:uppercase; letter-spacing:.08em;';

    header.appendChild(idChip);
    header.appendChild(headerTitleWrap);
    header.appendChild(headerMeta);
    root.appendChild(header);

    // Format the timestamps once and write to the subtitle. Re-fired
    // whenever an edit lands so "updated" reflects the latest write.
    function _formatShort(iso) {
      if (!iso) return '';
      var d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      var now = new Date();
      var sameDay = d.toDateString() === now.toDateString();
      var hh = String(d.getHours()).padStart(2, '0');
      var mm = String(d.getMinutes()).padStart(2, '0');
      if (sameDay) return hh + ':' + mm;
      var mo = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      return mo + ' ' + hh + ':' + mm;
    }
    function _relativeShort(iso) {
      if (!iso) return '';
      var d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      var seconds = Math.floor((Date.now() - d.getTime()) / 1000);
      if (seconds < 60) return 'just now';
      if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
      if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
      return _formatShort(iso);
    }
    function renderSubtitle() {
      var created = _formatShort(createdAtIso);
      var updated = _relativeShort(updatedAtIso);
      var parts = [];
      if (created) parts.push('created ' + created);
      if (updated && updatedAtIso !== createdAtIso) parts.push('updated ' + updated);
      headerSubtitle.textContent = parts.join(' · ');
      headerSubtitle.style.display = parts.length ? '' : 'none';
    }
    renderSubtitle();

    // ---- Body -----------------------------------------------------------
    // Backend has sanitized this HTML against the note grammar AND
    // inlined the Mermaid SVGs. We trust the bytes and set innerHTML
    // directly — no DOMPurify pass here. See infra/render/note.py
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

    // ---- Phase 2.6: block-by-block reveal at 2x speaking rate ---------
    // Reading is faster than listening. Instead of char-by-char (which
    // matches speech rate but drags for the eye), reveal one block at
    // a time — heading, paragraph, list item, diagram — with each
    // block appearing as a unit. Pacing budget = 2x Kokoro speed, so
    // a block of N chars schedules the next reveal at N / 44 seconds.
    // Mermaid SVG content stays intact (we never touch its text nodes;
    // the whole diagram is one block).
    var DISPLAY_CPS = 44;            // 2 × Kokoro speed=1.0
    var MIN_BLOCK_INTERVAL_MS = 150; // floor so tiny blocks don't stack
    var BLOCK_FADE_MS = 400;         // per-block fade-in duration
    var BLOCK_SELECTOR = 'h1, h2, h3, h4, p, li, blockquote, .bw-diagram, .bw-image';

    // Single in-flight reveal handle. New reveals cancel the prior one
    // (which snaps remaining blocks visible so the DOM never gets stuck).
    var activeReveal = null;

    function _collectBlocks(roots) {
      var out = [];
      var seen = new Set();
      for (var i = 0; i < roots.length; i++) {
        var r = roots[i];
        if (!r || r.nodeType !== 1) continue;
        // If the root itself matches, include it.
        if (r.matches && r.matches(BLOCK_SELECTOR) && !seen.has(r)) {
          seen.add(r); out.push(r);
        }
        if (r.querySelectorAll) {
          var matches = r.querySelectorAll(BLOCK_SELECTOR);
          for (var k = 0; k < matches.length; k++) {
            var m = matches[k];
            // Skip text nodes that live inside an svg — those belong
            // to mermaid diagrams. Our selector doesn't match those,
            // but a `<text>` inside an SVG would never match the
            // selector anyway. This is a belt-and-suspenders skip.
            if (m.closest && m.closest('svg')) continue;
            if (!seen.has(m)) { seen.add(m); out.push(m); }
          }
        }
      }
      // Sort by document order so the reveal walks top → bottom.
      out.sort(function (a, b) {
        if (a === b) return 0;
        var pos = a.compareDocumentPosition(b);
        if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
        return 1;
      });
      return out;
    }

    function _hideBlock(el) {
      // Inline transitions so we don't depend on any global stylesheet.
      el.style.transition =
        'opacity ' + BLOCK_FADE_MS + 'ms ease-out, transform ' +
        BLOCK_FADE_MS + 'ms ease-out';
      el.style.opacity = '0';
      el.style.transform = 'translateY(-6px)';
    }

    function _revealBlock(el) {
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    }

    // Reveal `roots` block-by-block. Schedule each block to appear
    // after a delay proportional to the PREVIOUS block's character
    // count (so paragraphs hang longer than headings). Each individual
    // block fades + slides in via inline transition.
    function blockReveal(roots) {
      var blocks = _collectBlocks(roots);
      if (!blocks.length) {
        return { promise: Promise.resolve(), cancel: function () {} };
      }

      // Hide everything first, in one pass.
      for (var i = 0; i < blocks.length; i++) _hideBlock(blocks[i]);

      var cancelled = false;
      var timers = [];
      var doneResolve;
      var promise = new Promise(function (r) { doneResolve = r; });

      function snapAllVisible() {
        for (var k = 0; k < blocks.length; k++) _revealBlock(blocks[k]);
      }

      // Schedule reveals. t accumulates over prior blocks.
      var t = 0;
      blocks.forEach(function (el, idx) {
        var chars = (el.textContent || '').replace(/\s+/g, ' ').trim().length;
        // Diagrams / images have ~no text but should still hang briefly.
        if (chars < 12) chars = 12;
        var thisDelay = t;
        var holdAfter = Math.max(MIN_BLOCK_INTERVAL_MS, (chars * 1000) / DISPLAY_CPS);
        timers.push(window.setTimeout(function () {
          if (cancelled) return;
          _revealBlock(el);
          if (idx === blocks.length - 1) {
            // Last block — resolve after its fade completes.
            window.setTimeout(function () {
              if (!cancelled) doneResolve();
              if (activeReveal && activeReveal._handle === handleRef) activeReveal = null;
            }, BLOCK_FADE_MS);
          }
        }, thisDelay));
        t += holdAfter;
      });

      var handleRef = {};
      var handle = {
        _handle: handleRef,
        promise: promise,
        cancel: function () {
          if (cancelled) return;
          cancelled = true;
          for (var k = 0; k < timers.length; k++) clearTimeout(timers[k]);
          snapAllVisible();
          doneResolve();
          if (activeReveal && activeReveal._handle === handleRef) activeReveal = null;
        },
      };
      return handle;
    }

    function startReveal(roots) {
      if (activeReveal) {
        try { activeReveal.cancel(); } catch (_) {}
      }
      var handle = blockReveal(roots);
      activeReveal = handle;
      return handle.promise;
    }

    cleanup(function () {
      if (activeReveal) {
        try { activeReveal.cancel(); } catch (_) {}
        activeReveal = null;
      }
    });

    function setHtml(html, opts) {
      currentHtml = (typeof html === 'string') ? html : '';
      body.innerHTML = currentHtml;
      currentSelection = '';
      if (opts && opts.animate && currentHtml) {
        startReveal([body]);
      }
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

    // Phase 2.6: animate the initial mount + any subsequent full-content
    // swap from push_block_content. Reveal types in at TTS rate so the
    // canvas pace matches the spoken pass. The `animateInitial` flag is
    // false when mount_template hydrated stored content from disk — the
    // user already had this note, so we show it instantly.
    setHtml(initial, { animate: animateInitial });

    // Subscribe so push_block_content can replace the card body in place.
    // Payload is the already-preprocessed HTML (workshop runs it through
    // infra.render.note.process before fan-out).
    var unsub = bus.subscribe('__CONTENT_TOPIC__', function (payload) {
      if (typeof payload === 'string') {
        setHtml(payload, { animate: true });
      } else if (payload && typeof payload.content === 'string') {
        setHtml(payload.content, { animate: true });
      }
    });
    cleanup(function () { unsub(); });

    // ---- Phase 2 voice-leads: animated edits --------------------------
    // The canvas writer's `edit_note` tool fans out one BlockMessage
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
      // Phase 2.6: structural ops now typewriter their new content in
      // place. The old "wait 700ms then reconcile via setHtml(newHtml)"
      // belt-and-suspenders pass is dropped — it would re-type text
      // we just finished typing. Op handlers leave the DOM matching
      // newHtml on their own.
      for (var i = 0; i < ops.length; i++) {
        var op = ops[i] || {};
        try {
          switch (op.op) {
            case 'append':           animateBoundary('end',   op.html || op.md); break;
            case 'prepend':          animateBoundary('start', op.html || op.md); break;
            case 'replace_section':  animateReplaceSection(op.anchor_text, op.html || op.md); break;
            case 'revise':           animateRevise(op.target_text, op.new_text); break;
            case 'highlight':        animateHighlight(op.target_text, op.duration_ms); break;
            case 'arrow_to_text':    animateArrow(op.target_text, op.label, op.direction); break;
            case 'annotate':         animateAnnotate(op.target_text, op.note); break;
          }
        } catch (e) {
          if (typeof console !== 'undefined' && console.warn) {
            console.warn('[note] edit op failed:', op, e);
          }
        }
      }
      publishState();
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
      // it to the live tree once we know where to put it.
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      var newNodes = [];
      while (tmp.firstChild) newNodes.push(tmp.firstChild), tmp.removeChild(tmp.firstChild);
      // Find the outermost .card container to insert inside; fall back
      // to body root if absent.
      var host = body.querySelector('.card') || body;
      for (var i = 0; i < newNodes.length; i++) {
        var n = newNodes[i];
        if (where === 'end') host.appendChild(n);
        else host.insertBefore(n, host.firstChild);
      }
      // Phase 2.6: block-by-block reveal. The reveal hides each
      // matching block via inline opacity then fades them in one at a
      // time at 2x speaking rate. No .bw-edit-enter class — its CSS
      // keyframe would fight the inline transition.
      startReveal(newNodes);
    }

    function animateReplaceSection(anchorText, html) {
      var target = findBlockContaining(anchorText);
      if (!target || typeof html !== 'string') return;
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      var nodes = [];
      while (tmp.firstChild) nodes.push(tmp.firstChild), tmp.removeChild(tmp.firstChild);
      // Fade old out.
      target.classList.add('bw-edit-exit');
      var parent = target.parentNode;
      window.setTimeout(function () {
        if (!parent || !target.parentNode) return;
        var anchor = target;
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          parent.insertBefore(n, anchor.nextSibling);
          anchor = n;
        }
        parent.removeChild(target);
        // Phase 2.6: block-by-block reveal of the new section.
        startReveal(nodes);
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
      // Phase 2.6: typewriter only the <ins> text. The <del> stays
      // intact during the flash so the diff is visible.
      startReveal([ins]);
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
      if (document.getElementById('bw-note-edit-style')) return;
      var s = document.createElement('style');
      s.id = 'bw-note-edit-style';
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
