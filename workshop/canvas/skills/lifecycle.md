# Block lifecycle

Every block on the canvas moves through the same five-step lifecycle:

1. **Mount** — a block appears on the canvas. Either by `mount_template`
   (a known template like `upload_file`, `pdf_reader`, `passage_reader`)
   or by `request_new_block` (an engineer-authored novel widget). Mount
   is fan-out: the same block id appears on every connected device of
   the same class. A newly-mounted block is auto-raised to the front.

2. **State report** — once the block has rendered on a device, it
   self-reports its current state via the `record_block_state` API.
   The cached state is what perception readers (`read_media`) return
   on subsequent turns. Until the block reports, its state is `None`
   ("mounted, no state yet").

3. **Content push** — the block subscribes to one or more topics. The
   server can push values into a topic via `push_block_content` and the
   block updates in place — no remount, no reload. Use this for live
   data (counters, list rows, text updates).

4. **Action** — the server can trigger UI actions on a mounted block
   via `block_action`: `highlight` (flash glow), `focus` (keyboard
   focus), `scroll_to` (scroll into view), `raise` (front of stack),
   `set_grid` (reflow position).

5. **Unmount** — a block is removed by another mount call that names it
   in `replace: [...]`, or by an explicit unmount. The block tears down,
   cached state is cleared, and the canvas redraws.

A block id is unique per user. The same id on multiple devices is the
same logical block (same state, same topics) — just rendered on each
display.
