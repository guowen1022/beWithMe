({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  content: '',
  style: {
    background: 'linear-gradient(180deg, #0f172a 0%, #0b1220 100%)',
    color: '#e2e8f0',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif',
    borderRadius: '14px',
    border: '1px solid rgba(255,255,255,0.06)',
    boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
    padding: '0',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
  },
  subscribes: ['__DOC_TOPIC__'],
  publishes: ['__SELECTION_TOPIC__'],
  run(root, bus, cleanup) {
    var userId = (typeof localStorage !== 'undefined' && localStorage.getItem('bewithme_user_id')) || '';
    var blockId = root.getAttribute('data-block-id') || '__BLOCK_ID__';

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
        'background:rgba(59,130,246,0.4);color:transparent;' +
      '}' +
      '[data-block-id="' + blockId + '"] ::-webkit-scrollbar{width:10px;height:10px;}' +
      '[data-block-id="' + blockId + '"] ::-webkit-scrollbar-thumb{' +
        'background:rgba(148,163,184,0.25);border-radius:5px;' +
      '}' +
      '[data-block-id="' + blockId + '"] ::-webkit-scrollbar-thumb:hover{' +
        'background:rgba(148,163,184,0.45);' +
      '}';
    document.head.appendChild(styleEl);
    cleanup(function () { if (styleEl.parentNode) styleEl.parentNode.removeChild(styleEl); });

    // Header bar
    var header = document.createElement('div');
    header.style.padding = '10px 14px';
    header.style.borderBottom = '1px solid rgba(255,255,255,0.06)';
    header.style.background = 'rgba(15,23,42,0.6)';
    header.style.backdropFilter = 'blur(8px)';
    header.style.display = 'flex';
    header.style.alignItems = 'center';
    header.style.gap = '10px';
    header.style.flexShrink = '0';

    var headerIcon = document.createElement('div');
    headerIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
    headerIcon.style.color = '#94a3b8';
    headerIcon.style.display = 'flex';

    var headerTitle = document.createElement('div');
    headerTitle.textContent = 'PDF reader';
    headerTitle.style.fontSize = '13px';
    headerTitle.style.fontWeight = '600';
    headerTitle.style.flex = '1';
    headerTitle.style.overflow = 'hidden';
    headerTitle.style.textOverflow = 'ellipsis';
    headerTitle.style.whiteSpace = 'nowrap';

    var headerMeta = document.createElement('div');
    headerMeta.style.fontSize = '11px';
    headerMeta.style.color = '#64748b';
    headerMeta.style.flexShrink = '0';
    headerMeta.textContent = 'waiting…';

    header.appendChild(headerIcon);
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
    empty.style.color = '#475569';
    empty.style.fontSize = '13px';
    empty.style.textAlign = 'center';
    empty.style.padding = '40px 20px';
    empty.innerHTML = '<div style="font-size:32px;opacity:0.4;margin-bottom:8px;">📄</div><div>Upload a PDF above to read it here.</div>';
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
        return;
      }
      headerMeta.textContent = 'loading…';
      headerTitle.textContent = title || 'PDF reader';
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

          var renderedPages = {}; // page number → promise (idempotent)
          var visiblePage = 1;

          function renderPageInto(pageNum, pageWrap, canvas, textLayerDiv) {
            if (renderedPages[pageNum]) return renderedPages[pageNum];
            renderedPages[pageNum] = pdf.getPage(pageNum).then(function (page) {
              var viewport = page.getViewport({ scale: scale });
              // Match the placeholder's size to the real viewport in case
              // page 1's aspect differed (covers mixed-page-size PDFs).
              pageWrap.style.width = viewport.width + 'px';
              pageWrap.style.height = viewport.height + 'px';
              pageWrap.style.setProperty('--scale-factor', String(scale));
              canvas.width = viewport.width;
              canvas.height = viewport.height;
              return page.render({ canvasContext: canvas.getContext('2d'), viewport: viewport })
                .promise
                .then(function () { return page.getTextContent(); })
                .then(function (textContent) {
                  if (typeof window.pdfjsLib.TextLayer === 'function') {
                    var tl = new window.pdfjsLib.TextLayer({
                      textContentSource: textContent,
                      container: textLayerDiv,
                      viewport: viewport,
                    });
                    return tl.render();
                  }
                  if (typeof window.pdfjsLib.renderTextLayer === 'function') {
                    return window.pdfjsLib.renderTextLayer({
                      textContentSource: textContent,
                      container: textLayerDiv,
                      viewport: viewport,
                      textDivs: [],
                    }).promise;
                  }
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
              pageWrap.style.borderRadius = '8px';
              pageWrap.style.overflow = 'hidden';
              pageWrap.style.boxShadow = '0 8px 24px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.3)';
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
          errBox.style.color = '#fca5a5';
          errBox.style.fontSize = '13px';
          errBox.style.padding = '20px';
          errBox.style.textAlign = 'center';
          errBox.textContent = 'Failed to render: ' + (err && err.message ? err.message : String(err));
          body.appendChild(errBox);
        });
    };

    var unsub = bus.subscribe('__DOC_TOPIC__', function (payload) {
      if (payload && payload.id) renderDoc(payload.id, payload.title);
    });
    cleanup(function () { unsub(); });
  },
})
