VISUAL GUIDE — mermaid (structure, time & sequence)

Use a ` ```mermaid ` fenced block for **structural / temporal** diagrams. The
server renders the source to SVG. Embed the fence inside the markdown you pass
to `mount_template`/`edit_note`.

## Pick the diagram type by the SHAPE of the data

- **Chronology** — dated events, eras, "a timeline of …", anything in time
  order → `timeline`
- **Process / steps / decision flow / hierarchy** → `flowchart` (`graph TD`/`LR`)
- **Interactions between actors over time** → `sequenceDiagram`
- **Comparing MAGNITUDES** across a few categories (sales, counts, scores)
  → `xychart-beta` bars

A date or year is a **position in time, not a magnitude** — never plot years as
bar heights. "Timeline" almost always means `timeline`, not a bar chart.

## timeline — chronological events

```mermaid
timeline
    title Timeline of Early Civilizations
    3500 BCE : Sumer, Mesopotamia
    3100 BCE : Egypt, Nile Valley
    2600 BCE : Indus Valley
    2000 BCE : China, Yellow River
```

Each `<period> : <event>` row places an event in time order. Chain more events
on one period with `: event : event`.

## flowchart — steps, pipelines, trees

```mermaid
graph TD
  A[Sumer] --> B[cuneiform]
  A --> C[the wheel]
  A --> D[ziggurats]
```

## sequenceDiagram — interactions over time

```mermaid
sequenceDiagram
  Client->>Server: request
  Server-->>Client: response
```

## xychart-beta — comparing magnitudes (NOT dates)

```mermaid
xychart-beta
  title "Q1 sales (thousands)"
  x-axis [Jan, Feb, Mar]
  bar [10, 25, 40]
```

Rules:
- Wrap any node label containing parens, punctuation, or `<br/>` in double
  quotes — `A["f(x) = x²"]` — or the parser rejects it.
- mermaid is for STRUCTURE / TIME / SEQUENCE. For a CONTINUOUS numeric
  relationship — function curves, regression lines, scatter, data-and-fit
  pictures, 3D surfaces — do NOT use mermaid; open the `plot` guide instead
  (`load_guide(['plot'])`).
