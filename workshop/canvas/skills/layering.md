# Block layering

Blocks may overlap on the grid. The visible stacking order is:

1. **Newly-mounted blocks auto-raise** to the front of the stack. After
   you mount a new diagram while a PDF is open, the diagram is on top
   automatically — no extra action needed.

2. **Clicking a block raises it.** When the user interacts with a block
   that's behind another, the click brings it to the front. The
   persona should not pre-empt this; it's user-driven.

3. **`block_action(action="raise")`** flips a previously-mounted block
   back to the front. Use when the user asks to see a surface that's
   now hidden behind something newer (e.g. "show me the PDF again"
   after a diagram was drawn over it).

4. **Reflowing with `layout_blocks` doesn't change z-order.** Resizing
   a block in place keeps its current stack position.

The stacking order is per-device — each connected canvas tracks its own
front-to-back ordering. Auto-raise on mount and click-to-raise both
apply per-device.
