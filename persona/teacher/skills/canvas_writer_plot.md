VISUAL GUIDE — plot (coordinate charts via Plotly)

For **coordinate** plots — scatter, curves, data-and-fit pictures, 3D
surfaces, anything with numeric axes — use a ` ```plot ` fenced block with a
JSON config body. Real x/y/z axes (not a flowchart). Embed the fence inside
the markdown you pass to `mount_template`/`edit_note`.

Common fields:
- `mode`: `"2d"` (line/scatter) or `"3d_surface"` (loss surface, manifold)
- `title`, `x_label`, `y_label`, `z_label` (3d only): axis labels
- `x_range`, `y_range` (3d only): `[min, max]`, default `[-3, 3]`

## Single analytic curve

- `expression`: math string — `f(x)` for 2d, `f(x,y)` for 3d_surface. Use `*`
  for multiply (NOT `^` for powers — write `x*x`). e.g. `"x*x"`,
  `"x*x + y*y"`, `"Math.sin(x) * y"`
- `annotations` (2d): `[{"x":…,"y":…,"text":"…"}]` — point markers
- `path` (3d): `[{"x":…,"y":…}, …]` — overlays a gradient-descent trail

**Example — 2D parabola:**

```plot
{"mode":"2d","title":"y = x²","expression":"x*x","x_label":"x","y_label":"f(x)","x_range":[-3,3]}
```

**Example — 3D loss surface with descent path:**

```plot
{"mode":"3d_surface","title":"Loss Surface","expression":"x*x + y*y","x_label":"Weight w₁","y_label":"Bias w₂","z_label":"Loss","x_range":[-3,3],"y_range":[-3,3],"path":[{"x":2.5,"y":2.5},{"x":1.8,"y":1.8},{"x":1.2,"y":1.2},{"x":0.6,"y":0.6},{"x":0.1,"y":0.1}]}
```

## Multiple overlaid traces — `series` (2d)

For **data-and-fit** pictures (the most common teaching plot) use `series`
instead of a single `expression`. Each item is one trace on the same axes:

- `{"kind":"scatter","points":[{"x":…,"y":…}, …],"name":"…","color":"…"}` — raw data points
- `{"kind":"curve","expression":"f(x)","name":"…","color":"…"}` — analytic curve
- `{"kind":"line","points":[{"x":…,"y":…}, …],"name":"…","color":"…"}` — explicit polyline

`color` is optional (a theme palette is applied automatically). The legend
shows whenever there is more than one trace.

**Example — overfitting vs underfitting.** Claim: *the overfit curve threads
nearly every training point while the underfit line misses the arch of the
data.* Scatter the noisy data, then draw both fits over the SAME points:

```plot
{"mode":"2d","title":"Overfitting vs Underfitting","x_label":"x","y_label":"y","x_range":[0,10],"series":[{"kind":"scatter","name":"training data","points":[{"x":1,"y":3.0},{"x":2,"y":4.2},{"x":3,"y":4.3},{"x":4,"y":4.7},{"x":5,"y":3.7},{"x":6,"y":3.3},{"x":7,"y":2.0},{"x":8,"y":1.1},{"x":9,"y":0.3}]},{"kind":"curve","name":"underfit (too simple)","expression":"3.6 - 0.1*x","color":"#4fd1c5"},{"kind":"curve","name":"overfit (memorizes noise)","expression":"2 + 2.5*Math.sin(0.45*x) + 0.5*Math.sin(2.5*x)","color":"#ff6b6b"}]}
```

Read at a glance: the straight line can't bend with the data; the wiggly
curve chases every point.

## Guidance

- State the claim first, then pick data + traces that make it obvious. Prefer
  `series` (data + fits) over a lone abstract curve when illustrating a
  *concept* about how a model behaves.
- Numbers must be honest: don't let a quantity that can't go negative dip
  below zero; if you say "U-shaped," use a genuinely U-shaped function (a
  quadratic with a labelled minimum), not a monotonic line; markers must sit
  where the data actually is.
- Prefer `plot` over mermaid for regression lines, loss surfaces, function
  graphs, scatter — anything with numeric coordinates.
