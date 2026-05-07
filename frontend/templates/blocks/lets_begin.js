({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip the auto-snapshot reporter — the card's content is one CTA.
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
  run: function (root, bus, cleanup, helpers) {
    var blockId = (helpers && helpers.blockId) || '__BLOCK_ID__';
    var backend = helpers && helpers.backend ? helpers.backend : null;
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};

    // Tell the teacher what's on screen — even though we won't auto-
    // snapshot, the persona should know this card is the current
    // first-paint surface.
    report({
      kind: 'lets_begin',
      content: 'Welcome card — awaiting user click on "Let\'s Begin".',
      extra: { stage: 'idle' },
    });

    // ── Header strip ────────────────────────────────────
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var idChip = document.createElement('span');
    idChip.textContent = 'BEGIN';
    idChip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';

    var headerTitle = document.createElement('span');
    headerTitle.textContent = 'Welcome';
    headerTitle.style.cssText =
      'flex:1; font-size:11.5px; font-weight:600;' +
      'color:var(--bw-ink);';

    header.appendChild(idChip);
    header.appendChild(headerTitle);
    root.appendChild(header);

    // ── Body ────────────────────────────────────────────
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; padding:32px 24px;' +
      'display:flex; flex-direction:column; align-items:center;' +
      'justify-content:center; gap:18px;';
    root.appendChild(body);

    var title = document.createElement('div');
    title.textContent = 'Ready when you are';
    title.style.cssText =
      'font-size:20px; font-weight:600; letter-spacing:-0.01em;' +
      'color:var(--bw-ink); text-align:center;';
    body.appendChild(title);

    var subtitle = document.createElement('div');
    subtitle.textContent = 'Click below to start the session.';
    subtitle.style.cssText =
      'font-size:12.5px; color:var(--bw-ink-muted);' +
      'text-align:center;';
    body.appendChild(subtitle);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = "Let's Begin";
    btn.style.cssText =
      'padding:12px 28px; border-radius:0;' +
      'font-family:inherit; font-size:13.5px; font-weight:600;' +
      'letter-spacing:0.01em;' +
      'color:var(--bw-on-accent); background:var(--bw-accent);' +
      'border:1px solid var(--bw-accent);' +
      'cursor:pointer; transition:opacity 0.15s ease;';
    body.appendChild(btn);

    var status = document.createElement('div');
    status.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); min-height:12px;' +
      'text-transform:uppercase; letter-spacing:.1em;';
    body.appendChild(status);

    var hover = function () { btn.style.opacity = '0.9'; };
    var unhover = function () { btn.style.opacity = '1'; };
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
      status.textContent = 'starting…';
      backend.mount_template({
        template: 'ambient_mic',
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
  },
})
