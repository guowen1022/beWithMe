// coordinate-plot skill — renders 2D line charts and 3D surface plots via Plotly.js.
// Invoked by note.js dispatchSkills() when it finds <div data-skill="coordinate-plot">.
//
// Config shape (JSON from the ```plot fence):
//   mode:        "2d" | "3d_surface"
//   title:       string
//   x_label:     string  (default "x")
//   y_label:     string  (default "y")
//   z_label:     string  (default "z")  — 3d_surface only
//   expression:  math string evaluated as f(x) [2d] or f(x,y) [3d_surface]
//                e.g. "x*x" or "x*x + y*y"
//   x_range:     [min, max]  (default [-3, 3])
//   y_range:     [min, max]  (default [-3, 3])  — 3d_surface only
//   resolution:  int grid points per axis (default 50)
//   path:        [{x, y}] gradient descent trail — 3d_surface only
//   annotations: [{x, y, text}] — 2d only

(function (element, config) {
  var PLOTLY_CDN = '/plotly-2.32.0.min.js';

  // ── helpers ──────────────────────────────────────────────────────────────

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  function showStatus(msg) {
    // Use a dedicated child div so render() can find and remove it precisely,
    // rather than wiping all children via textContent which can race with Plotly.
    var existing = element.querySelector('[data-skill-status]');
    if (existing) { existing.textContent = msg; return; }
    var div = document.createElement('div');
    div.setAttribute('data-skill-status', '1');
    div.style.cssText = [
      'display:flex', 'align-items:center', 'justify-content:center',
      'color:' + cssVar('--bw-ink-muted', '#888'),
      'font-size:13px', 'font-family:' + cssVar('--bw-font-sans', 'sans-serif'),
      'height:360px', 'width:100%',
    ].join(';');
    div.textContent = msg;
    element.appendChild(div);
  }

  function clearStatus() {
    var existing = element.querySelector('[data-skill-status]');
    if (existing) element.removeChild(existing);
  }

  function buildFn(expr, params) {
    // Very conservative: only allow math chars + named params.
    // The expression comes from the teacher LLM, not user input, but
    // we still restrict to math operators to avoid surprises.
    if (!expr || typeof expr !== 'string') return null;
    var safe = /^[\sx\+\-\*\/\^\(\)\.0-9Math\.sincoqrtabspitanlogflrceilu]+$/.test(expr.replace(/\s/g, ''));
    if (!safe) return null;
    try {
      var args = params.concat(['return (' + expr + ')']);
      return Function.apply(null, args);
    } catch (_) { return null; }
  }

  function linspace(lo, hi, n) {
    var arr = [], step = (hi - lo) / (n - 1);
    for (var i = 0; i < n; i++) arr.push(lo + i * step);
    return arr;
  }

  // ── dark theme layout ────────────────────────────────────────────────────

  function baseLayout(title) {
    var bg   = cssVar('--bw-surface',     '#0d0d0d');
    var ink  = cssVar('--bw-ink',         '#e8e8e8');
    var muted= cssVar('--bw-ink-muted',   '#666');
    var grid = cssVar('--bw-border',      '#222');
    return {
      title: { text: title || '', font: { color: ink, size: 15 } },
      paper_bgcolor: bg,
      plot_bgcolor:  bg,
      font: { color: ink, family: cssVar('--bw-font-sans', 'sans-serif'), size: 12 },
      margin: { l: 50, r: 20, t: title ? 48 : 20, b: 50 },
      _muted: muted,
      _grid:  grid,
    };
  }

  // ── 3D surface ───────────────────────────────────────────────────────────

  function render3d(Plotly) {
    var expr  = config.expression || 'x*x + y*y';
    var xlo   = (config.x_range || [-3, 3])[0];
    var xhi   = (config.x_range || [-3, 3])[1];
    var ylo   = (config.y_range || [-3, 3])[0];
    var yhi   = (config.y_range || [-3, 3])[1];
    var res   = Math.min(Math.max(config.resolution || 50, 10), 120);
    var xs    = linspace(xlo, xhi, res);
    var ys    = linspace(ylo, yhi, res);

    var fn = buildFn(expr, ['x', 'y']);
    if (!fn) { showStatus('[coordinate-plot] invalid expression'); return; }

    var z = [];
    for (var i = 0; i < ys.length; i++) {
      var row = [];
      for (var j = 0; j < xs.length; j++) {
        try { row.push(fn(xs[j], ys[i])); }
        catch (_) { row.push(0); }
      }
      z.push(row);
    }

    var traces = [{
      type: 'surface',
      x: xs, y: ys, z: z,
      colorscale: 'Viridis',
      showscale: false,
      opacity: 0.85,
      contours: {
        z: { show: true, usecolormap: true, highlightcolor: '#fff', project: { z: false } },
      },
    }];

    // Optional gradient descent path overlay
    if (Array.isArray(config.path) && config.path.length) {
      var px = [], py = [], pz = [];
      config.path.forEach(function(pt) {
        if (pt && typeof pt.x === 'number' && typeof pt.y === 'number') {
          px.push(pt.x); py.push(pt.y);
          try { pz.push(fn(pt.x, pt.y)); } catch(_) { pz.push(0); }
        }
      });
      if (px.length) {
        traces.push({
          type: 'scatter3d',
          x: px, y: py, z: pz,
          mode: 'lines+markers',
          line: { color: '#ff4444', width: 4 },
          marker: { color: '#ff4444', size: 4 },
          name: 'descent path',
        });
      }
    }

    var layout = baseLayout(config.title);
    layout.scene = {
      xaxis: { title: config.x_label || 'x',
               gridcolor: layout._grid, zerolinecolor: layout._muted, backgroundcolor: layout.paper_bgcolor },
      yaxis: { title: config.y_label || 'y',
               gridcolor: layout._grid, zerolinecolor: layout._muted, backgroundcolor: layout.paper_bgcolor },
      zaxis: { title: config.z_label || 'z',
               gridcolor: layout._grid, zerolinecolor: layout._muted, backgroundcolor: layout.paper_bgcolor },
      bgcolor: layout.paper_bgcolor,
    };

    element.style.cssText = 'width:100%;height:420px;';
    Plotly.newPlot(element, traces, layout, { responsive: true, displayModeBar: false });
  }

  // ── 2D line ──────────────────────────────────────────────────────────────

  function render2d(Plotly) {
    var expr = config.expression || 'x*x';
    var xlo  = (config.x_range || [-3, 3])[0];
    var xhi  = (config.x_range || [-3, 3])[1];
    var res  = Math.min(Math.max(config.resolution || 200, 50), 500);
    var xs   = linspace(xlo, xhi, res);

    var fn = buildFn(expr, ['x']);
    if (!fn) { showStatus('[coordinate-plot] invalid expression'); return; }

    var ys = xs.map(function(x) {
      try { return fn(x); } catch(_) { return null; }
    });

    var layout = baseLayout(config.title);
    layout.xaxis = {
      title: config.x_label || 'x',
      gridcolor: layout._grid, zerolinecolor: layout._muted,
    };
    layout.yaxis = {
      title: config.y_label || 'y',
      gridcolor: layout._grid, zerolinecolor: layout._muted,
    };

    var traces = [{
      type: 'scatter', mode: 'lines',
      x: xs, y: ys,
      name: config.y_label || 'y',
      line: { color: cssVar('--bw-accent', '#7c6ff7'), width: 2.5 },
    }];

    // Optional annotation markers
    if (Array.isArray(config.annotations)) {
      var annPx = [], annPy = [], annTexts = [];
      config.annotations.forEach(function(a) {
        if (a && typeof a.x === 'number') {
          var y = typeof a.y === 'number' ? a.y : (function(){ try{return fn(a.x);}catch(_){return 0;}}());
          annPx.push(a.x); annPy.push(y); annTexts.push(a.text || '');
        }
      });
      if (annPx.length) {
        traces.push({
          type: 'scatter', mode: 'markers+text',
          x: annPx, y: annPy, text: annTexts,
          name: 'points',
          textposition: 'top center',
          marker: { color: '#ff4444', size: 8 },
          textfont: { color: cssVar('--bw-ink', '#e8e8e8'), size: 12 },
          showlegend: false,
        });
      }
    }

    layout.showlegend = (traces.length > 1);
    element.style.cssText = 'width:100%;height:360px;';
    Plotly.newPlot(element, traces, layout, { responsive: true, displayModeBar: false });
  }

  // ── entry point ──────────────────────────────────────────────────────────

  function render(Plotly) {
    clearStatus();
    if (config.mode === '2d') render2d(Plotly);
    else render3d(Plotly);
  }

  showStatus('Loading chart…');

  if (window.Plotly) {
    render(window.Plotly);
    return;
  }

  // Load Plotly once — reuse across multiple skill instances on the same page
  if (window.__plotlyLoading) {
    window.__plotlyLoading.then(function() { render(window.Plotly); });
    return;
  }

  window.__plotlyLoading = new Promise(function(resolve, reject) {
    var s = document.createElement('script');
    s.src = PLOTLY_CDN;
    s.onload = function() { resolve(); };
    s.onerror = function() { reject(new Error('Plotly CDN failed')); };
    document.head.appendChild(s);
  });

  window.__plotlyLoading
    .then(function() { render(window.Plotly); })
    .catch(function() {
      showStatus('[coordinate-plot] failed to load Plotly (offline?)');
    });
})(element, config);
