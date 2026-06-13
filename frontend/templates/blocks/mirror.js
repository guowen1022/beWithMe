({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  // Self-reports a structured 'mirror' state; skip the generic auto-snapshot
  // so read_media gets a clean entry instead of the raw innerText dump.
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
  subscribes: [],
  publishes: [],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};
    var backend = helpers && helpers.backend ? helpers.backend : null;

    // Dark-friendly per-source palette. Mirrors the old /mirror page's badge
    // families (user, agent, signal, maestro_*, system, capture).
    var SOURCE_COLORS = {
      user: '#5C8CE6',
      agent: '#5BD6A0',
      signal: '#5BC8D6',
      maestro_long: '#A78BFA',
      maestro_short: '#9B86E0',
      system: '#9090A8',
      capture: '#E5C36F',
    };
    function colorFor(src) { return SOURCE_COLORS[src] || 'var(--bw-ink-muted)'; }

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
    chip.textContent = 'MIRROR';

    var subtitle = document.createElement('span');
    subtitle.style.cssText =
      'flex:1; font-size:12px; font-style:italic;' +
      'color:var(--bw-ink-muted);' +
      'white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0;';
    subtitle.textContent = 'every event the system recorded for you';

    var counter = document.createElement('span');
    counter.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); flex-shrink:0; letter-spacing:.05em;';

    var refresh = document.createElement('button');
    refresh.style.cssText =
      'background:none; border:1px solid var(--bw-border);' +
      'color:var(--bw-ink-faint);' +
      'font-family:var(--bw-font-mono); font-size:9.5px;' +
      'padding:2px 8px; cursor:pointer;' +
      'letter-spacing:.06em; text-transform:uppercase;';
    refresh.textContent = 'refresh';

    header.appendChild(chip);
    header.appendChild(subtitle);
    header.appendChild(counter);
    header.appendChild(refresh);
    root.appendChild(header);

    // ---- Body: event families ------------------------------------------
    var body = document.createElement('div');
    body.style.cssText =
      'flex:1; overflow-y:auto; padding:10px 12px;' +
      'display:flex; flex-direction:column; gap:14px;' +
      'box-sizing:border-box;';
    root.appendChild(body);

    function badge(src) {
      var c = colorFor(src);
      var b = document.createElement('span');
      b.textContent = src;
      b.style.cssText =
        'font-family:var(--bw-font-mono); font-size:9.5px;' +
        'padding:2px 7px; letter-spacing:.04em; flex-shrink:0;' +
        'color:' + c + ';' +
        'background:color-mix(in oklab, ' + c + ' 15%, transparent);' +
        'border:1px solid color-mix(in oklab, ' + c + ' 30%, transparent);';
      return b;
    }

    function eventRow(ev) {
      var row = document.createElement('div');
      row.style.cssText =
        'border-left:2px solid color-mix(in oklab, ' + colorFor(ev.source) + ' 50%, transparent);' +
        'padding:3px 0 3px 10px; display:flex; flex-direction:column; gap:3px;';

      var meta = document.createElement('div');
      meta.style.cssText =
        'display:flex; align-items:center; gap:8px; flex-wrap:wrap;' +
        'font-family:var(--bw-font-mono); font-size:10px; color:var(--bw-ink-faint);';

      var ts = document.createElement('span');
      var when = ev.ts ? new Date(ev.ts) : null;
      ts.textContent = when ? when.toLocaleString() : '';
      meta.appendChild(ts);
      meta.appendChild(badge(ev.source || 'system'));

      var kind = document.createElement('span');
      kind.textContent = ev.kind || '';
      kind.style.cssText = 'color:var(--bw-ink-muted);';
      meta.appendChild(kind);
      row.appendChild(meta);

      var pre = document.createElement('pre');
      pre.textContent = JSON.stringify(ev.body || {}, null, 2);
      pre.style.cssText =
        'margin:0; font-family:var(--bw-font-mono); font-size:10.5px;' +
        'line-height:1.45; color:var(--bw-ink-muted);' +
        'white-space:pre-wrap; word-break:break-word;';
      row.appendChild(pre);

      return row;
    }

    function setMessage(text) {
      while (body.firstChild) body.removeChild(body.firstChild);
      var m = document.createElement('div');
      m.textContent = text;
      m.style.cssText =
        'text-align:center; padding:32px 0;' +
        'color:var(--bw-ink-faint); font-size:12px; font-style:italic;';
      body.appendChild(m);
    }

    function renderEvents(events) {
      while (body.firstChild) body.removeChild(body.firstChild);
      counter.textContent = events.length + ' event' + (events.length === 1 ? '' : 's');

      if (!events.length) {
        setMessage('No events yet. Start a turn or wait for the Maestro to act.');
        report({ kind: 'mirror', content: '0 events', extra: { total: 0, families: [] } });
        return;
      }

      // Group by source (family), same as the old /mirror page.
      var byFamily = {};
      var order = [];
      for (var i = 0; i < events.length; i++) {
        var fam = events[i].source || 'system';
        if (!byFamily[fam]) { byFamily[fam] = []; order.push(fam); }
        byFamily[fam].push(events[i]);
      }
      order.sort();

      for (var f = 0; f < order.length; f++) {
        var fam = order[f];
        var section = document.createElement('section');
        section.style.cssText = 'display:flex; flex-direction:column; gap:6px;';

        var h = document.createElement('div');
        h.style.cssText = 'display:flex; align-items:center; gap:8px;';
        h.appendChild(badge(fam));
        var cnt = document.createElement('span');
        cnt.textContent = '(' + byFamily[fam].length + ')';
        cnt.style.cssText =
          'font-family:var(--bw-font-mono); font-size:10px; color:var(--bw-ink-faint);';
        h.appendChild(cnt);
        section.appendChild(h);

        var list = document.createElement('div');
        list.style.cssText = 'display:flex; flex-direction:column; gap:6px;';
        for (var j = 0; j < byFamily[fam].length; j++) {
          list.appendChild(eventRow(byFamily[fam][j]));
        }
        section.appendChild(list);
        body.appendChild(section);
      }

      report({
        kind: 'mirror',
        content: events.length + ' events across ' + order.length + ' sources',
        extra: { total: events.length, families: order },
      });
    }

    var loading = false;
    function load() {
      if (loading) return;
      loading = true;
      refresh.disabled = true;
      var prevText = refresh.textContent;
      refresh.textContent = '…';
      if (!body.firstChild) setMessage('Loading…');

      if (!backend || !backend.query_stream) {
        setMessage('Mirror unavailable: backend.query_stream not wired.');
        loading = false;
        refresh.disabled = false;
        refresh.textContent = prevText;
        return;
      }

      backend.query_stream({ limit: 200, order: 'desc' })
        .then(function (res) {
          if (!res || !res.ok) {
            setMessage('Failed to load mirror' + (res ? ' (HTTP ' + res.status + ')' : ''));
            return;
          }
          renderEvents(Array.isArray(res.data) ? res.data : []);
        })
        .catch(function (err) {
          setMessage('Failed to load mirror: ' + (err && err.message ? err.message : String(err)));
        })
        .finally(function () {
          loading = false;
          refresh.disabled = false;
          refresh.textContent = prevText;
        });
    }

    refresh.addEventListener('click', load);
    load();
  },
})
