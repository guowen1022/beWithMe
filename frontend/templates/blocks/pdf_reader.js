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
    padding: '0',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
  },
  subscribes: ['__DOC_TOPIC__'],
  publishes: ['__SELECTION_TOPIC__'],
  // Skip auto-snapshot — we emit structured reports below (page + total +
  // viewport text). The textLayer is `color: transparent` so innerText would
  // come out garbled anyway.
  autosnapshot: false,
  run(root, bus, cleanup, helpers) {
    var userId = (typeof localStorage !== 'undefined' && localStorage.getItem('bewithme_user_id')) || '';
    var blockId = root.getAttribute('data-block-id') || '__BLOCK_ID__';
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};

    // Per-doc state we report on every visible-page change.
    var currentDocId = null;
    var currentDocTitle = null;
    var totalPages = 0;
    var lastReportedPage = null;
    // Flips to true on the first reportVisiblePage after a doc loads,
    // attaching `completed: true` to that single report so the perception
    // cache fires a BlockCompletedEvent — which wakes the teacher's tool
    // loop. Reset to false on every renderDoc so a re-loaded doc fires
    // again. Page changes after that do NOT re-fire the completion edge
    // (they're just state updates, not milestones).
    var loadCompletionPending = false;

    function viewportText(pageNum) {
      // The text layer renders <span>s with `color: transparent` and absolute
      // positioning. innerText still concatenates them in DOM order, which
      // is good enough for a 200-char persona summary. Returns '' if the
      // page hasn't rendered its text layer yet.
      var wrap = body.querySelector('[data-page-num="' + pageNum + '"]');
      if (!wrap) return '';
      var tl = wrap.querySelector('[data-pdf-text-layer]');
      if (!tl) return '';
      var t = (tl.innerText || '').replace(/\s+/g, ' ').trim();
      return t.slice(0, 220);
    }

    function reportVisiblePage(pageNum) {
      if (!currentDocId) return;
      var snippet = viewportText(pageNum);
      var content = 'page ' + pageNum + ' of ' + totalPages;
      if (snippet) content += ': ' + snippet;
      var completed = loadCompletionPending;
      loadCompletionPending = false;  // one-shot edge per doc load
      report({
        kind: 'pdf',
        content: content,
        completed: completed,
        extra: {
          document_id: currentDocId,
          document_title: currentDocTitle,
          page: pageNum,
          total_pages: totalPages,
          viewport_text: snippet,
        },
      });
      lastReportedPage = pageNum;
    }

    // Inject text-layer CSS once per block. pdfjs's TextLayer class outputs
    // <span>s with inline `transform: matrix(...)` but relies on CSS class
    // rules for color/position/cursor. Without these, the spans render as
    // visible black text on top of the canvas. Scope to this block via
    // [data-block-id="..."] so other PDF readers don't collide.
    var styleEl = document.createElement('style');
    styleEl.setAttribute('data-pdf-style-for', blockId);
    styleEl.textContent =
      '[data-block-id="' + blockId + '"] [data-pdf-text-layer]{' +
        'position:absolute;inset:0;overflow:hidden;line-height:1;' +
        'text-align:initial;transform-origin:0 0;opacity:1;' +
      '}' +
      '[data-block-id="' + blockId + '"] [data-pdf-text-layer] span,' +
      '[data-block-id="' + blockId + '"] [data-pdf-text-layer] br{' +
        'color:transparent;position:absolute;white-space:pre;' +
        'cursor:text;transform-origin:0% 0%;' +
      '}' +
      '[data-block-id="' + blockId + '"] [data-pdf-text-layer] ::selection{' +
        'background:var(--bw-accent-soft);color:transparent;' +
      '}' +
      '[data-block-id="' + blockId + '"] ::-webkit-scrollbar{width:10px;height:10px;}' +
      '[data-block-id="' + blockId + '"] ::-webkit-scrollbar-thumb{' +
        'background:var(--bw-border-strong);' +
      '}' +
      '[data-block-id="' + blockId + '"] ::-webkit-scrollbar-thumb:hover{' +
        'background:var(--bw-ink-faint);' +
      '}';
    document.head.appendChild(styleEl);
    cleanup(function () { if (styleEl.parentNode) styleEl.parentNode.removeChild(styleEl); });

    // Header bar
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'PDF-READER';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'PDF reader';
    headerTitle.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink); white-space:nowrap;' +
      'overflow:hidden; text-overflow:ellipsis;';

    var headerMeta = document.createElement('div');
    headerMeta.textContent = 'waiting…';
    headerMeta.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;' +
      'text-transform:uppercase; letter-spacing:.08em;';

    header.appendChild(idChip);
    header.appendChild(headerTitle);
    header.appendChild(headerMeta);
    root.appendChild(header);

    // Scroll body. Pages stack vertically; the body itself scrolls. We
    // center the page column horizontally with `justifyContent` while
    // letting flex-direction:column lay pages out top-to-bottom.
    var body = document.createElement('div');
    body.style.flex = '1';
    body.style.overflow = 'auto';
    body.style.padding = '20px';
    body.style.display = 'flex';
    body.style.flexDirection = 'column';
    body.style.alignItems = 'center';
    body.style.gap = '16px';
    root.appendChild(body);

    // Empty-state placeholder
    var empty = document.createElement('div');
    empty.style.cssText =
      'color:var(--bw-ink-muted); font-size:12px;' +
      'text-align:center; padding:40px 20px;';
    empty.innerHTML =
      '<div style="font-size:28px;opacity:0.35;margin-bottom:10px;">📄</div>' +
      '<div>Upload a PDF above to read it here.</div>';
    body.appendChild(empty);

    // Selection capture
    var onMouseUp = function () {
      var sel = window.getSelection && window.getSelection();
      var text = sel ? sel.toString().trim() : '';
      if (text) bus.publish('__SELECTION_TOPIC__', text);
    };
    root.addEventListener('mouseup', onMouseUp);
    cleanup(function () { root.removeEventListener('mouseup', onMouseUp); });

    var renderDoc = function (id, title) {
      if (!window.pdfjsLib) {
        headerMeta.textContent = 'pdf.js not loaded';
        report({
          kind: 'pdf',
          content: 'pdf.js not loaded',
          extra: { document_id: id, document_title: title || null },
        });
        return;
      }
      headerMeta.textContent = 'loading…';
      headerTitle.textContent = title || 'PDF reader';
      currentDocId = id;
      currentDocTitle = title || null;
      // Arm the completion edge — the first reportVisiblePage after the
      // doc renders will carry `completed: true`, waking the teacher.
      loadCompletionPending = true;
      report({
        kind: 'pdf',
        content: 'loading: ' + (title || id),
        extra: { document_id: id, document_title: title || null },
      });
      fetch('/api/documents/' + encodeURIComponent(id) + '/pdf', {
        headers: userId ? { 'X-User-Id': userId } : {},
      })
        .then(function (res) {
          if (!res.ok) throw new Error('fetch pdf: ' + res.status);
          return res.arrayBuffer();
        })
        .then(function (buf) {
          return window.pdfjsLib.getDocument({ data: new Uint8Array(buf) }).promise;
        })
        .then(function (pdf) {
          // Strategy: probe page 1 to learn the viewport, build sized
          // placeholders for every page so the scroll height is correct
          // up-front, then render each page's canvas + text layer only
          // when it scrolls near the viewport. Per-page render logic
          // (canvas + TextLayer) is unchanged from the single-page path
          // — we just defer the work.
          var scale = 1.4;
          body.innerHTML = '';
          headerMeta.textContent = 'page 1 / ' + pdf.numPages;
          totalPages = pdf.numPages;

          var renderedPages = {}; // page number → promise (idempotent)
          var visiblePage = 1;

          // HiDPI fix: on Retina (devicePixelRatio = 2 or 3), the canvas's
          // pixel buffer needs to be scaled up so each CSS pixel maps to
          // multiple device pixels. Otherwise text + figures look blurry.
          // We keep the CSS size at viewport.width/height and inflate the
          // pixel buffer + render transform by `outputScale`.
          var outputScale = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;

          function renderPageInto(pageNum, pageWrap, canvas, textLayerDiv) {
            if (renderedPages[pageNum]) return renderedPages[pageNum];
            renderedPages[pageNum] = pdf.getPage(pageNum).then(function (page) {
              var viewport = page.getViewport({ scale: scale });
              // CSS size of both the wrap and the canvas drives layout.
              pageWrap.style.width = viewport.width + 'px';
              pageWrap.style.height = viewport.height + 'px';
              pageWrap.style.setProperty('--scale-factor', String(scale));
              canvas.style.width = viewport.width + 'px';
              canvas.style.height = viewport.height + 'px';
              // Pixel buffer is scaled up for HiDPI sharpness.
              canvas.width = Math.floor(viewport.width * outputScale);
              canvas.height = Math.floor(viewport.height * outputScale);
              var renderTransform = outputScale !== 1
                ? [outputScale, 0, 0, outputScale, 0, 0]
                : null;
              return page.render({
                canvasContext: canvas.getContext('2d'),
                viewport: viewport,
                transform: renderTransform,
              })
                .promise
                .then(function () { return page.getTextContent(); })
                .then(function (textContent) {
                  var renderP;
                  if (typeof window.pdfjsLib.TextLayer === 'function') {
                    var tl = new window.pdfjsLib.TextLayer({
                      textContentSource: textContent,
                      container: textLayerDiv,
                      viewport: viewport,
                    });
                    renderP = tl.render();
                  } else if (typeof window.pdfjsLib.renderTextLayer === 'function') {
                    renderP = window.pdfjsLib.renderTextLayer({
                      textContentSource: textContent,
                      container: textLayerDiv,
                      viewport: viewport,
                      textDivs: [],
                    }).promise;
                  } else {
                    renderP = Promise.resolve();
                  }
                  return renderP.then(function () {
                    // If this page is the currently-visible one, the
                    // earlier IntersectionObserver report carried no text
                    // (text layer wasn't rendered yet). Re-report now.
                    if (pageNum === visiblePage) reportVisiblePage(pageNum);
                  });
                });
            });
            return renderedPages[pageNum];
          }

          // Lazy renderer: IntersectionObserver fires when a placeholder
          // gets within rootMargin of the scroll viewport. rootMargin
          // pre-renders the next page so scrolling stays smooth.
          var io = new IntersectionObserver(function (entries) {
            for (var i = 0; i < entries.length; i++) {
              var entry = entries[i];
              if (!entry.isIntersecting) continue;
              var wrap = entry.target;
              var pageNum = parseInt(wrap.getAttribute('data-page-num'), 10);
              if (entry.intersectionRatio > 0.3) {
                visiblePage = pageNum;
                headerMeta.textContent = 'page ' + visiblePage + ' / ' + pdf.numPages;
                if (pageNum !== lastReportedPage) reportVisiblePage(pageNum);
              }
              var canvas = wrap.querySelector('canvas');
              var textLayerDiv = wrap.querySelector('[data-pdf-text-layer]');
              renderPageInto(pageNum, wrap, canvas, textLayerDiv).catch(function (e) {
                console.warn('[pdf_reader] page', pageNum, 'render failed', e);
              });
            }
          }, { root: body, rootMargin: '600px 0px', threshold: [0, 0.3, 1] });
          cleanup(function () { io.disconnect(); });

          // Probe page 1 to seed placeholder dimensions, then build the
          // full list. Mixed-size PDFs are tolerated — each page wrap is
          // resized when it actually renders (see renderPageInto).
          return pdf.getPage(1).then(function (firstPage) {
            var firstVp = firstPage.getViewport({ scale: scale });
            for (var n = 1; n <= pdf.numPages; n++) {
              var pageWrap = document.createElement('div');
              pageWrap.setAttribute('data-page-num', String(n));
              pageWrap.style.position = 'relative';
              pageWrap.style.width = firstVp.width + 'px';
              pageWrap.style.height = firstVp.height + 'px';
              pageWrap.style.borderRadius = '0';
              pageWrap.style.overflow = 'hidden';
              pageWrap.style.border = '1px solid var(--bw-border)';
              pageWrap.style.background = '#ffffff';
              pageWrap.style.flexShrink = '0';
              pageWrap.style.setProperty('--scale-factor', String(scale));

              var canvas = document.createElement('canvas');
              canvas.width = firstVp.width;
              canvas.height = firstVp.height;
              canvas.style.display = 'block';
              pageWrap.appendChild(canvas);

              var textLayerDiv = document.createElement('div');
              textLayerDiv.setAttribute('data-pdf-text-layer', '');
              pageWrap.appendChild(textLayerDiv);

              body.appendChild(pageWrap);
              io.observe(pageWrap);
            }
          });
        })
        .catch(function (err) {
          headerMeta.textContent = 'error';
          body.innerHTML = '';
          var errBox = document.createElement('div');
          errBox.style.cssText =
            'font-family:var(--bw-font-mono); font-size:11px;' +
            'color:var(--bw-accent); padding:20px; text-align:center;';
          errBox.textContent = 'Failed to render: ' + (err && err.message ? err.message : String(err));
          body.appendChild(errBox);
        });
    };

    var unsub = bus.subscribe('__DOC_TOPIC__', function (payload) {
      if (payload && payload.id) renderDoc(payload.id, payload.title);
    });
    cleanup(function () { unsub(); });

    // Mount-time report — but DEFERRED. If a document is already in
    // flight (sticky pub/sub replays the documents.uploaded topic the
    // moment subscribe() runs), `renderDoc` will set currentDocId
    // synchronously and we'll skip the idle report. Without this
    // deferral, a re-mounted pdf-reader briefly stamps the perception
    // cache with `idle / NO DOCUMENT LOADED` before the doc handler
    // catches up — and a teacher turn that lands in that window
    // wrongly concludes there's no PDF and re-mounts upload_file.
    //
    // 300ms is enough for sticky replay + renderDoc's first sync state
    // post; not so long that a genuinely-empty reader stays invisible.
    var idleReportTimer = setTimeout(function () {
      if (currentDocId) return;  // a doc loaded — no idle report needed
      report({
        kind: 'pdf',
        content: '(awaiting document)',
        extra: { document_id: null, document_title: null, page: null, total_pages: 0 },
      });
    }, 300);
    cleanup(function () { clearTimeout(idleReportTimer); });
  },
})
