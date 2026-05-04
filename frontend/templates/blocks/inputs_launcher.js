({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip the auto-snapshot reporter — the launcher's content (two buttons)
  // isn't useful for the persona to read. The chosen reader block reports
  // for itself.
  autosnapshot: false,
  style: {
    background: 'var(--bw-surface)',
    color: 'var(--bw-ink)',
    fontFamily: 'var(--bw-font-sans)',
    borderRadius: '0',
    border: '1px solid var(--bw-border)',
    padding: '0',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  publishes: [],
  run(root, bus, cleanup, helpers) {
    var blockId = (helpers && helpers.blockId) || '__BLOCK_ID__';
    var backend = helpers && helpers.backend ? helpers.backend : null;

    // ── Header strip ────────────────────────────────────
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'LAUNCHER';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var headerTitle = document.createElement('span');
    headerTitle.textContent = 'Reader setup';
    headerTitle.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink);';

    header.appendChild(idChip);
    header.appendChild(headerTitle);
    root.appendChild(header);

    // ── Body ────────────────────────────────────────────
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; padding:24px; display:flex; flex-direction:column; gap:18px;';
    root.appendChild(body);

    var prompt = document.createElement('div');
    prompt.textContent = 'What would you like to read?';
    prompt.style.cssText =
      'font-size:14px; font-weight:500;' +
      'color:var(--bw-ink); letter-spacing:-0.005em;';
    body.appendChild(prompt);

    var buttonRow = document.createElement('div');
    buttonRow.style.cssText = 'display:flex; gap:10px; flex-wrap:wrap;';
    body.appendChild(buttonRow);

    var status = document.createElement('div');
    status.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); min-height:12px;' +
      'text-transform:uppercase; letter-spacing:.1em;';
    body.appendChild(status);

    function makeButton(label, hint, templateName) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.style.cssText =
        'flex:1 1 200px; min-width:180px;' +
        'padding:16px 14px; border-radius:0;' +
        'font-family:inherit; color:var(--bw-ink);' +
        'background:var(--bw-surface-2);' +
        'border:1px solid var(--bw-border);' +
        'cursor:pointer; text-align:left;' +
        'display:flex; flex-direction:column; gap:5px;' +
        'transition:border-color 0.15s ease;';

      var titleEl = document.createElement('div');
      titleEl.textContent = label;
      titleEl.style.cssText =
        'font-size:13px; font-weight:600; color:var(--bw-ink);' +
        'letter-spacing:-0.005em;';
      btn.appendChild(titleEl);

      var hintEl = document.createElement('div');
      hintEl.textContent = hint;
      hintEl.style.cssText =
        'font-size:11px; font-weight:400; color:var(--bw-ink-muted);';
      btn.appendChild(hintEl);

      var hover = function () { btn.style.borderColor = 'var(--bw-accent)'; };
      var unhover = function () { btn.style.borderColor = 'var(--bw-border)'; };
      btn.addEventListener('mouseenter', hover);
      btn.addEventListener('mouseleave', unhover);
      cleanup(function () {
        btn.removeEventListener('mouseenter', hover);
        btn.removeEventListener('mouseleave', unhover);
      });

      var click = function () {
        if (!backend || !backend.mount_template) {
          status.textContent = 'mount_template helper not available';
          return;
        }
        btn.disabled = true;
        btn.style.opacity = '0.6';
        btn.style.cursor = 'wait';
        status.textContent = 'mounting ' + templateName + '…';
        backend.mount_template({
          template: templateName,
          replace: [blockId],
        })
          .then(function (r) {
            if (!r.ok) throw new Error('mount failed: ' + r.status);
          })
          .catch(function (err) {
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.style.cursor = 'pointer';
            var msg = err && err.message ? err.message : String(err);
            status.textContent = msg;
          });
      };
      btn.addEventListener('click', click);
      cleanup(function () { btn.removeEventListener('click', click); });

      return btn;
    }

    buttonRow.appendChild(makeButton(
      'Upload PDF',
      'Pick a PDF from your computer.',
      'upload_file',
    ));
    buttonRow.appendChild(makeButton(
      'Paste Passage',
      'Paste or type text directly.',
      'passage_reader',
    ));
  },
})
