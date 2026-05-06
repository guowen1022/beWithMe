# Block state kinds

Every block reports a `BlockState` with a `kind` field that classifies
what the block is currently showing. The persona reads this to decide
what's already on screen.

| `kind`     | Meaning                                                                                                  |
|------------|----------------------------------------------------------------------------------------------------------|
| `pdf`      | A rendered PDF. `extra` carries `document_id`, `document_title`, `page`, `total_pages`, `viewport_text`, `outline`. |
| `passage`  | A text panel. `extra.title` is the optional heading; `content` is the body.                              |
| `browser`  | An embedded browser view. `content` is the URL or page summary.                                          |
| `upload`   | The file-picker widget. `state.completed=true` means the file uploaded; the widget is about to swap to `pdf`. |
| `launcher` | The two-button starter (Upload PDF / Paste Passage). Auto-mounted on empty canvases.                     |
| `snapshot` | A static snapshot — usually a placeholder; safe to ignore in most reasoning.                             |
| `graph`    | A Mermaid diagram. `extra` carries `mermaid_kind`, `node_ids`, `selected_node_id`.                       |

Other fields on `BlockState`:

- `content` — one-line text summary the block chose to expose.
- `focus` — `active` (user attention is here), `visible`, or `background`.
- `grid` — current `(x, y, w, h)` position.
- `extra` — kind-specific structured data (see table above).
- `completed` — boolean; semantics depend on kind (e.g. upload finished).

Blocks that haven't reported yet have `state = None`. Treat that as
"mounted but loading" — never as "absent."
