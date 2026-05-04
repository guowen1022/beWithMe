({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Skip the auto-snapshot reporter — the launcher's content (two buttons)
  // isn't useful for the persona to read. The chosen reader block reports
  // for itself.
  autosnapshot: false,
  style: {
    background: 'linear-gradient(180deg, #1f2937 0%, #111827 100%)',
    color: '#f9fafb',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif',
    borderRadius: '14px',
    border: '1px solid rgba(255,255,255,0.08)',
    boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
    padding: '28px',
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
    overflow: 'hidden',
  },
  publishes: [],
  run(root, bus, cleanup, helpers) {
    var blockId = (helpers && helpers.blockId) || '__BLOCK_ID__';
    var backend = helpers && helpers.backend ? helpers.backend : null;

    var prompt = document.createElement('div');
    prompt.textContent = 'What would you like to read?';
    prompt.style.fontSize = '15px';
    prompt.style.fontWeight = '600';
    prompt.style.opacity = '0.9';
    prompt.style.letterSpacing = '-0.01em';
    root.appendChild(prompt);

    var buttonRow = document.createElement('div');
    buttonRow.style.display = 'flex';
    buttonRow.style.gap = '12px';
    buttonRow.style.flexWrap = 'wrap';
    root.appendChild(buttonRow);

    var status = document.createElement('div');
    status.style.fontSize = '12px';
    status.style.opacity = '0.55';
    status.style.minHeight = '14px';
    root.appendChild(status);

    function makeButton(label, hint, templateName) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.style.flex = '1 1 200px';
      btn.style.minWidth = '180px';
      btn.style.padding = '18px 16px';
      btn.style.fontSize = '14px';
      btn.style.fontWeight = '600';
      btn.style.fontFamily = 'inherit';
      btn.style.color = '#f9fafb';
      btn.style.background = 'rgba(59,130,246,0.12)';
      btn.style.border = '1px solid rgba(147,197,253,0.3)';
      btn.style.borderRadius = '10px';
      btn.style.cursor = 'pointer';
      btn.style.textAlign = 'left';
      btn.style.display = 'flex';
      btn.style.flexDirection = 'column';
      btn.style.gap = '6px';
      btn.style.transition = 'background 0.15s ease, border-color 0.15s ease';

      var titleEl = document.createElement('div');
      titleEl.textContent = label;
      titleEl.style.fontSize = '14px';
      titleEl.style.fontWeight = '600';
      btn.appendChild(titleEl);

      var hintEl = document.createElement('div');
      hintEl.textContent = hint;
      hintEl.style.fontSize = '12px';
      hintEl.style.fontWeight = '400';
      hintEl.style.opacity = '0.7';
      btn.appendChild(hintEl);

      var hover = function () {
        btn.style.background = 'rgba(59,130,246,0.22)';
        btn.style.borderColor = 'rgba(147,197,253,0.5)';
      };
      var unhover = function () {
        btn.style.background = 'rgba(59,130,246,0.12)';
        btn.style.borderColor = 'rgba(147,197,253,0.3)';
      };
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
