({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Self-published 'research_progress' state report; skip the generic
  // auto-snapshot so read_media gets a clean structured entry.
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
  publishes: [],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};

    var state = {
      goal: '',
      steps: [],
      finished: false,
      collapsed: false,
    };

    // ---- Header strip --------------------------------------------------
    var header = document.createElement('div');
    header.style.cssText =
      'display:flex; align-items:center; gap:10px;' +
      'padding:9px 12px;' +
      'background:var(--bw-surface-2);' +
      'border-bottom:1px solid var(--bw-border);' +
      'flex-shrink:0;';

    var chip = document.createElement('span');
    chip.style.cssText =
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'color:var(--bw-accent); background:var(--bw-accent-soft);' +
      'padding:3px 8px; letter-spacing:.08em; text-transform:uppercase;';
    chip.textContent = 'RESEARCHING';

    var goalEl = document.createElement('span');
    goalEl.style.cssText =
      'flex:1; font-size:12px; font-style:italic;' +
      'color:var(--bw-ink-soft);' +
      'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;' +
      'min-width:0;';

    var counter = document.createElement('span');
    counter.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0;' +
      'letter-spacing:.05em;';

    // Toggle: collapses on `finished=true`; user can click to re-expand.
    var toggle = document.createElement('button');
    toggle.style.cssText =
      'background:none; border:1px solid var(--bw-border);' +
      'color:var(--bw-ink-faint);' +
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'padding:2px 8px; cursor:pointer;' +
      'letter-spacing:.06em; text-transform:uppercase;';
    toggle.textContent = 'collapse';
    toggle.addEventListener('click', function () {
      state.collapsed = !state.collapsed;
      render();
    });

    header.appendChild(chip);
    header.appendChild(goalEl);
    header.appendChild(counter);
    header.appendChild(toggle);
    root.appendChild(header);

    // ---- Body: step list -----------------------------------------------
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; overflow-y:auto; padding:8px 12px;' +
      'display:flex; flex-direction:column; gap:6px;' +
      'box-sizing:border-box;';
    root.appendChild(body);

    function dotFor(status) {
      // Match common state-icon glyphs so the row is glanceable.
      if (status === 'done')  return { ch: '●', color: 'var(--bw-accent)' };
      if (status === 'doing') return { ch: '◐', color: 'var(--bw-accent)' };
      if (status === 'error') return { ch: '✕', color: 'var(--bw-error, #c46)' };
      return { ch: '◯', color: 'var(--bw-ink-faint)' };
    }

    function renderStepRow(step, index) {
      var row = document.createElement('div');
      row.style.cssText =
        'display:flex; flex-direction:column; gap:2px;' +
        'padding:3px 0;';

      var line = document.createElement('div');
      line.style.cssText =
        'display:flex; align-items:baseline; gap:8px;' +
        'font-size:12px; line-height:1.35;';

      var d = dotFor(step.status);
      var dot = document.createElement('span');
      dot.textContent = d.ch;
      dot.style.cssText =
        'color:' + d.color + ';' +
        'font-family:var(--bw-font-mono); font-size:11px;' +
        'flex-shrink:0; width:14px;';
      // Animate the in-flight dot so the user sees it's alive.
      if (step.status === 'doing') {
        dot.style.animation = 'bw-pulse 1.2s ease-in-out infinite';
      }

      var idx = document.createElement('span');
      idx.textContent = String(index + 1) + '.';
      idx.style.cssText =
        'font-family:var(--bw-font-mono); font-size:10px;' +
        'color:var(--bw-ink-faint); flex-shrink:0; width:18px;';

      var text = document.createElement('span');
      text.textContent = step.text || '(unnamed step)';
      text.style.cssText =
        'flex:1; color:' +
        (step.status === 'pending' ? 'var(--bw-ink-soft)' : 'var(--bw-ink)') +
        '; min-width:0;';

      line.appendChild(dot);
      line.appendChild(idx);
      line.appendChild(text);
      row.appendChild(line);

      if (step.note) {
        var noteEl = document.createElement('div');
        noteEl.textContent = step.note;
        noteEl.style.cssText =
          'margin-left:32px;' +
          'font-size:11px; line-height:1.4;' +
          'color:var(--bw-ink-faint);';
        row.appendChild(noteEl);
      }

      return row;
    }

    function render() {
      goalEl.textContent = state.goal || '(no goal)';
      var done = 0;
      for (var i = 0; i < state.steps.length; i++) {
        if (state.steps[i] && state.steps[i].status === 'done') done++;
      }
      counter.textContent = done + '/' + state.steps.length;

      // Collapse to a one-line chip when finished or user toggled.
      if (state.collapsed || (state.finished && state.collapsed === undefined)) {
        body.style.display = 'none';
        toggle.textContent = 'expand';
      } else {
        body.style.display = 'flex';
        toggle.textContent = state.finished ? 'collapse' : 'collapse';
      }

      while (body.firstChild) body.removeChild(body.firstChild);
      if (!state.steps.length) {
        var empty = document.createElement('div');
        empty.textContent = 'planning…';
        empty.style.cssText =
          'font-size:11px; color:var(--bw-ink-faint);' +
          'font-style:italic; padding:4px 0;';
        body.appendChild(empty);
      } else {
        for (var j = 0; j < state.steps.length; j++) {
          body.appendChild(renderStepRow(state.steps[j], j));
        }
      }

      var current = '';
      for (var k = 0; k < state.steps.length; k++) {
        if (state.steps[k] && state.steps[k].status === 'doing') {
          current = state.steps[k].text || '';
          break;
        }
      }
      var summary = (state.finished ? 'done' : (done + '/' + state.steps.length))
        + (current ? (' · ' + current) : '');
      report({
        kind: 'research_progress',
        content: summary,
        extra: {
          goal: state.goal,
          steps: state.steps,
          finished: state.finished,
        },
      });
    }

    // ---- Subscribe to content topic ------------------------------------
    var unsub = bus.subscribe('__CONTENT_TOPIC__', function (payload) {
      if (!payload || typeof payload !== 'object') return;
      if (typeof payload.goal === 'string') state.goal = payload.goal;
      if (Array.isArray(payload.steps)) state.steps = payload.steps;
      if (typeof payload.finished === 'boolean') {
        // Auto-collapse on first transition to finished — user can re-expand.
        if (payload.finished && !state.finished) state.collapsed = true;
        state.finished = payload.finished;
      }
      render();
    });
    cleanup(function () { unsub(); });

    // Inject the pulse keyframe once per page; cheap and idempotent.
    if (!document.getElementById('bw-research-progress-style')) {
      var style = document.createElement('style');
      style.id = 'bw-research-progress-style';
      style.textContent =
        '@keyframes bw-pulse { ' +
        '0%,100% { opacity: 1; } ' +
        '50% { opacity: .35; } ' +
        '}';
      document.head.appendChild(style);
    }

    render();
  },
})
