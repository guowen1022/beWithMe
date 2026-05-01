# Cross-block wiring (the bus)

Blocks are independent runtime objects. They coordinate over the
**BlockBus** — a sticky pub/sub on a single in-page `bus` instance.

## Bus shape

Inside `run(root, bus, cleanup)`:

```js
// publish a value (sticks: late subscribers get the latest value immediately)
bus.publish('uploaded_doc', { id, title, pages });

// subscribe; the handler fires with the current value if the topic
// already has one (sticky), then again on every subsequent publish.
const unsub = bus.subscribe('uploaded_doc', (payload) => { /* ... */ });
cleanup(() => unsub());
```

## Topic naming

Use kebab-case. Pick names that describe the **data**, not the source
block. Good: `uploaded_doc`, `selected_text`, `current_page`. Bad:
`upload_block_output`, `from_pdf_reader`.

For paired blocks, the suffix `_selection` is the convention for "user
selected text inside <topic>" — e.g. `uploaded_doc_selection`.

## Declaring topics

Every block MUST declare what it reads and writes via the `subscribes`
and `publishes` arrays in the metadata. The runtime uses these to
auto-regenerate `TOPICS.md` after every commit so the user (and you,
on the next turn) can see the current wiring.

```js
({
  id: 'pdf-reader',
  // ...
  subscribes: ['uploaded_doc'],
  publishes:  ['uploaded_doc_selection'],
  run(root, bus, cleanup) { /* ... */ },
})
```

## Composing existing blocks

Before writing a new block that needs data from another, check
`TOPICS.md` and the existing `blocks/<id>.md` files in this user's
workspace. If a block already publishes the topic you need, subscribe
to it instead of duplicating the source. If two blocks need to share a
topic, they MUST agree on the exact string.

## Common patterns

- **Producer / consumer**: one block writes data, another reads it
  (e.g., upload writes `uploaded_doc`, reader subscribes).
- **Selection broadcast**: a content block publishes `<topic>_selection`
  on user selection so other blocks (or the teacher) can react.
- **Mode flag**: a control block publishes a boolean / enum on a topic
  like `read_mode`; consumers swap behavior on subscribe.
