"""interactive_graph — teacher's tool for ephemeral Mermaid diagrams.

Diagrams the teacher draws to explain something are *ephemeral overlays*:
they appear over SSE, render in the browser, and disappear on next reload.
Nothing persists — no workspace file, no canvas_layout row. The user's git
workspace is reserved for surfaces they've actually customized for their
own workflow (upload bars, layouts, saved widgets); a teacher's in-the-
moment diagram is not that.

Multiple diagrams can be on screen at once, each addressed by a semantic
`name` the teacher chooses (default `"main"`). Block id is derived:
`interactive-graph` for `main`, `interactive-graph-<name>` otherwise.
Topics are namespaced by block id so instances don't trample each other.

Topics (per instance):
  Subscribed by the block:
    - `graph.<block_id>.mermaid`   string — full Mermaid source
    - `graph.<block_id>.highlight` {node_id, durationMs?}
    - `graph.<block_id>.clear`     truthy — wipe the SVG

  Published by the block:
    - `graph.<block_id>.selected`  {node_id, label} — when the user clicks
    - `graph.<block_id>.error`     {message} — Mermaid render/parse error
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import delete

from agents.frontend_engineer import workspace as ws
from infra.contracts.ui import BlockMessage, BlockSource, UIUpdate
from infra.db import async_session
from infra.devices.delivery import enqueue_for_device, enqueue_for_user
from infra.devices.canvas_layout import CanvasLayout
from infra.model.tools import ToolSpec


_DEFAULT_NAME = "main"
_BASE_BLOCK_ID = "interactive-graph"

# Sandbox validator: a Node script that runs mermaid.parse() against the
# source the teacher just produced. If parse fails, we return the error
# to the teacher as the tool result instead of mounting broken syntax on
# the user's canvas — the teacher's LLM then retries with corrected
# source in the same turn. ~150ms per call (Node cold start), worth it.
_VALIDATOR_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "mermaid-validate.mjs"
)
_VALIDATOR_TIMEOUT_S = 5.0


async def _validate_mermaid(source: str) -> Optional[str]:
    """Run the Node validator. Returns None on success, error string on
    failure. On infra failure (Node missing, validator script missing,
    timeout) we return None — better to let the user see a render error
    than to falsely block a valid diagram."""
    if not _VALIDATOR_SCRIPT.exists():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", str(_VALIDATOR_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError):
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(source.encode("utf-8")),
            timeout=_VALIDATOR_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None
    if proc.returncode == 0:
        return None
    msg = stderr.decode("utf-8", errors="replace").strip()
    if not msg:
        msg = stdout.decode("utf-8", errors="replace").strip() or (
            f"validator exited with code {proc.returncode}"
        )
    return msg

# `name` must match this — same kebab-case rule the workspace uses for block
# stems. We re-validate here even though the block isn't going to disk:
# `name` becomes part of the block id, which the browser uses as a DOM
# attribute and topic key, so we want clean characters.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _block_id_for(name: str) -> str:
    return _BASE_BLOCK_ID if name == _DEFAULT_NAME else f"{_BASE_BLOCK_ID}-{name}"


def _topic(block_id: str, suffix: str) -> str:
    return f"graph.{block_id}.{suffix}"


_GRAPH_BLOCK_JS_TEMPLATE = """\
({
  id: '__BLOCK_ID__',
  // Grid is in DESKTOP coords (12×9); the frontend rescales for tablet/phone.
  grid: { x: 2, y: 1, w: 8, h: 7 },
  // Block reports its own structured state below; skip the DOM-text fallback.
  autosnapshot: false,
  style: {
    background: 'var(--bw-surface)',
    color: 'var(--bw-ink)',
    fontFamily: 'var(--bw-font-sans)',
    border: '1px solid var(--bw-border)',
    borderRadius: '0',
    // Tight chrome — every wasted pixel makes the diagram look smaller
    // than the block size suggests.
    padding: '4px',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    gap: '0',
  },
  // Topics are namespaced per instance so multiple diagrams don't collide.
  subscribes: [
    'graph.__BLOCK_ID__.mermaid',
    'graph.__BLOCK_ID__.highlight',
    'graph.__BLOCK_ID__.clear',
  ],
  publishes: [
    'graph.__BLOCK_ID__.selected',
    'graph.__BLOCK_ID__.error',
  ],
  run(root, bus, cleanup, helpers) {
    var blockId = (helpers && helpers.blockId) || '__BLOCK_ID__';
    var T_MERMAID   = 'graph.' + blockId + '.mermaid';
    var T_HIGHLIGHT = 'graph.' + blockId + '.highlight';
    var T_CLEAR     = 'graph.' + blockId + '.clear';
    var T_SELECTED  = 'graph.' + blockId + '.selected';
    var T_ERROR     = 'graph.' + blockId + '.error';

    var report = helpers && typeof helpers.reportState === 'function'
      ? helpers.reportState
      : function () {};

    var status = document.createElement('div');
    status.style.fontSize = '11px';
    status.style.opacity = '0.55';
    // No reserved height — when status is empty (the common case after
    // first render) the diagram gets 100% of the block's vertical space.
    status.style.minHeight = '0';
    status.style.flexShrink = '0';
    function setStatus(text) {
      status.textContent = text || '';
      status.style.display = text ? 'block' : 'none';
      status.style.marginBottom = text ? '4px' : '0';
    }
    setStatus('loading mermaid…');
    root.appendChild(status);

    var container = document.createElement('div');
    container.style.flex = '1';
    container.style.minHeight = '0';
    container.style.minWidth = '0';
    container.style.display = 'flex';
    container.style.alignItems = 'center';
    container.style.justifyContent = 'center';
    container.style.overflow = 'hidden';
    root.appendChild(container);

    var renderSeq = 0;
    var lastSource = '';
    var lastKind = null;
    var nodeIds = [];
    var selectedNode = null;

    function detectKind(src) {
      if (!src) return null;
      var lines = String(src).split(/\\r?\\n/);
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line || line.startsWith('%%')) continue;
        var m = line.match(/^([a-zA-Z][\\w-]*)/);
        return m ? m[1] : null;
      }
      return null;
    }

    function summarize() {
      var nodeCount = nodeIds.length;
      var head = lastKind ? ('mermaid:' + lastKind) : 'mermaid';
      var bits = [head, nodeCount + ' nodes'];
      if (selectedNode) bits.push('selected: ' + selectedNode);
      return bits.join(', ');
    }

    function pushState(extra) {
      try {
        report({
          kind: 'graph',
          content: summarize(),
          extra: Object.assign({
            mermaid_kind: lastKind,
            node_ids: nodeIds.slice(0, 64),
            selected_node_id: selectedNode,
            source_chars: lastSource.length,
          }, extra || {}),
        });
      } catch (e) { /* report is best-effort */ }
    }

    function harvestNodeIds(svg) {
      var ids = [];
      try {
        var nodes = svg.querySelectorAll('g.node');
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          var id = n.getAttribute('data-id') || n.getAttribute('id') || '';
          if (id) ids.push(id);
        }
      } catch (e) {}
      return ids;
    }

    function attachClickHandlers(svg) {
      var nodes = svg.querySelectorAll('g.node');
      for (var i = 0; i < nodes.length; i++) {
        (function (g) {
          g.style.cursor = 'pointer';
          var onClick = function (ev) {
            ev.stopPropagation();
            var id = g.getAttribute('data-id') || g.getAttribute('id') || '';
            var labelEl = g.querySelector('foreignObject, .nodeLabel, text');
            var label = labelEl ? (labelEl.textContent || '').trim() : '';
            selectedNode = id || null;
            try { bus.publish(T_SELECTED, { node_id: id, label: label }); } catch (e) {}
            pushState();
          };
          g.addEventListener('click', onClick);
          cleanup(function () { g.removeEventListener('click', onClick); });
        })(nodes[i]);
      }
    }

    function findNodeElement(svg, nodeId) {
      if (!svg || !nodeId) return null;
      var direct = svg.querySelector('g.node[data-id="' + nodeId + '"]');
      if (direct) return direct;
      var byId = svg.querySelector('g.node[id="' + nodeId + '"]');
      if (byId) return byId;
      var groups = svg.querySelectorAll('g.node');
      for (var i = 0; i < groups.length; i++) {
        var g = groups[i];
        var id = g.getAttribute('data-id') || g.getAttribute('id') || '';
        if (id === nodeId) return g;
        if (id && id.indexOf(nodeId) >= 0) return g;
      }
      return null;
    }

    function flashNode(svg, nodeId, durationMs) {
      var el = findNodeElement(svg, nodeId);
      if (!el) return false;
      el.classList.remove('graph-flash');
      void el.getBoundingClientRect();
      el.classList.add('graph-flash');
      var t = window.setTimeout(function () {
        el.classList.remove('graph-flash');
      }, Math.max(200, Number(durationMs) || 1600));
      cleanup(function () { window.clearTimeout(t); });
      return true;
    }

    function clearSvg() {
      container.innerHTML = '';
      lastSource = '';
      lastKind = null;
      nodeIds = [];
      selectedNode = null;
      setStatus('');
      pushState();
    }

    function whenReady() {
      if (typeof window === 'undefined') return Promise.reject(new Error('no window'));
      if (window.__mermaidReady) return window.__mermaidReady;
      if (window.mermaid) return Promise.resolve(window.mermaid);
      return new Promise(function (resolve, reject) {
        var tries = 0;
        var iv = setInterval(function () {
          tries++;
          if (window.__mermaidReady) {
            clearInterval(iv);
            window.__mermaidReady.then(resolve, reject);
          } else if (window.mermaid) {
            clearInterval(iv);
            resolve(window.mermaid);
          } else if (tries > 50) {
            clearInterval(iv);
            reject(new Error('mermaid did not load'));
          }
        }, 100);
        cleanup(function () { clearInterval(iv); });
      });
    }

    var ready = whenReady().then(function (mermaid) {
      setStatus('');
      return mermaid;
    }).catch(function (err) {
      setStatus('mermaid failed to load: ' + (err && err.message || err));
      try { bus.publish(T_ERROR, { message: String(err && err.message || err) }); } catch (e) {}
      throw err;
    });

    function dropMermaidSandbox(renderId) {
      // mermaid.render appends a 'd<renderId>' div to <body> for layout
      // measurement and is supposed to clean it up — but in practice it
      // leaks. Per-render cleanup must touch ONLY this render's div: a
      // prefix-sweep here would wipe a concurrent render's sandbox while
      // mermaid is still using it (root.select(#dmermaid-foo-2).node()
      // → null → "Cannot read properties of null (reading 'firstChild')").
      // Block-teardown cleanup uses dropAllMermaidSandboxes() instead.
      if (!renderId) return;
      try {
        var direct = document.getElementById('d' + renderId);
        if (direct) direct.remove();
      } catch (e) {}
    }

    function dropAllMermaidSandboxes() {
      // Block is unmounting — safe to sweep everything we may have
      // leaked. Only call on teardown, never per-render.
      try {
        var sandboxes = document.body.querySelectorAll('[id^="dmermaid-' + blockId + '-"]');
        for (var i = 0; i < sandboxes.length; i++) sandboxes[i].remove();
      } catch (e) {}
    }

    function tightenViewBox(svg) {
      // mermaid emits a viewBox sized to its loose internal layout (with
      // its own diagramPadding). Replace with the actual content's
      // bounding box so the diagram fills the SVG box without internal
      // letterboxing — gives a noticeably bigger render at the same
      // block size. 4px breathing room so text and arrowheads aren't
      // flush against the SVG edge.
      try {
        if (typeof svg.getBBox !== 'function') return;
        var bbox = svg.getBBox();
        if (!(bbox && bbox.width > 0 && bbox.height > 0)) return;
        var pad = 4;
        svg.setAttribute('viewBox',
          (bbox.x - pad) + ' ' + (bbox.y - pad) + ' ' +
          (bbox.width + 2 * pad) + ' ' + (bbox.height + 2 * pad));
      } catch (e) { /* getBBox can throw if the SVG isn't laid out yet */ }
    }

    function fitSvg(svg) {
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.style.width = '100%';
      svg.style.height = '100%';
      svg.style.maxWidth = '100%';
      svg.style.maxHeight = '100%';
      svg.style.display = 'block';
      // getBBox needs the element in the layout tree; we're already
      // inside container.innerHTML = svg, so it's connected. Defer one
      // frame to be safe across browsers.
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(function () { tightenViewBox(svg); });
      } else {
        tightenViewBox(svg);
      }
    }

    // Serialised render queue. mermaid.render() mutates global state via
    // processAndSetConfigs(text), so two renders in flight at once corrupt
    // each other — one ends up with `root.select('#dmermaid-foo-N').node()`
    // returning null, throwing "Cannot read properties of null (reading
    // 'firstChild')". Chain every render onto pendingRender so only one is
    // ever active in mermaid; supersede stale ones via the seq guard.
    var pendingRender = Promise.resolve();

    function renderMermaid(source) {
      var seq = ++renderSeq;
      lastSource = String(source || '');
      lastKind = detectKind(lastSource);
      pendingRender = pendingRender.then(function () {
        if (seq !== renderSeq) return;   // a newer render superseded us
        return ready.then(function (mermaid) {
          if (seq !== renderSeq) return;
          var renderId = 'mermaid-' + blockId + '-' + seq;
          return mermaid.render(renderId, lastSource).then(function (out) {
            dropMermaidSandbox(renderId);
            if (seq !== renderSeq) return;
            container.innerHTML = out && out.svg ? out.svg : '';
            setStatus('');
            var svg = container.querySelector('svg');
            if (svg) {
              fitSvg(svg);
              nodeIds = harvestNodeIds(svg);
              attachClickHandlers(svg);
              if (typeof out.bindFunctions === 'function') {
                try { out.bindFunctions(container); } catch (e) {}
              }
            } else {
              nodeIds = [];
            }
            if (selectedNode && nodeIds.indexOf(selectedNode) < 0) selectedNode = null;
            pushState();
          }).catch(function (err) {
            dropMermaidSandbox(renderId);
            if (seq !== renderSeq) return;
            var msg = err && err.message ? err.message : String(err);
            setStatus('render error: ' + msg);
            container.innerHTML = '';
            nodeIds = [];
            try { bus.publish(T_ERROR, { message: msg }); } catch (e) {}
            pushState({ error: msg });
          });
        });
      }).catch(function () { /* swallow so the chain stays alive for the next render */ });
    }

    var unsubMermaid = bus.subscribe(T_MERMAID, function (value) {
      if (value === null || value === undefined) {
        clearSvg();
        return;
      }
      var src = typeof value === 'string' ? value : (value && value.source) || '';
      if (!src) {
        clearSvg();
        return;
      }
      renderMermaid(src);
    });
    cleanup(unsubMermaid);

    var unsubHighlight = bus.subscribe(T_HIGHLIGHT, function (value) {
      if (!value) return;
      var nodeId = typeof value === 'string' ? value : value.node_id;
      var ms = (typeof value === 'object' && value) ? value.durationMs : null;
      if (!nodeId) return;
      var svg = container.querySelector('svg');
      flashNode(svg, nodeId, ms);
    });
    cleanup(unsubHighlight);

    var unsubClear = bus.subscribe(T_CLEAR, function (value) {
      if (!value) return;
      clearSvg();
    });
    cleanup(unsubClear);

    cleanup(function () { dropAllMermaidSandboxes(); });
    pushState();
  },
})
"""


def _build_graph_block(name: str) -> BlockSource:
    """Build a fresh in-memory BlockSource for the named diagram instance.

    No disk I/O, no commit, no canvas_layout row. The browser evaluates the
    JS on every mount; on reload, the diagram is gone (nothing in workspace
    to hydrate from) — which is correct for an ephemeral overlay.
    """
    block_id = _block_id_for(name)
    js = _GRAPH_BLOCK_JS_TEMPLATE.replace("__BLOCK_ID__", block_id)
    return BlockSource(id=block_id, source=js, design_doc=None)


# Per-process guard so we don't redo the v1 cleanup migration on every call
# for the same user. The migration itself is idempotent — this just avoids
# the read_snapshot+commit overhead.
_MIGRATED_USERS: set[str] = set()


async def _migrate_v1_workspace_if_needed(user_id: UUID) -> list[str]:
    """v1 wrote `blocks/interactive-graph*.{js,md}` to git and recorded
    canvas_layout rows. v1.1 makes the diagram ephemeral. Sweep any leftovers.

    Returns the list of block ids that were cleaned up, so the caller can
    fan out unmount events for any browser still hydrated from old state.
    """
    key = str(user_id)
    if key in _MIGRATED_USERS:
        return []
    snap = ws.read_snapshot(user_id)
    stale_ids = [
        bid for bid in snap.blocks
        if bid == _BASE_BLOCK_ID or bid.startswith(_BASE_BLOCK_ID + "-")
    ]
    if not stale_ids:
        _MIGRATED_USERS.add(key)
        return []

    ws.delete_blocks(user_id, stale_ids)
    ws.regenerate_topics(user_id)
    ws.commit(user_id, "tools.interactive_graph: stop persisting ephemeral diagrams")

    async with async_session() as session:
        await session.execute(
            delete(CanvasLayout).where(
                CanvasLayout.user_id == user_id,
                CanvasLayout.block_id.in_(stale_ids),
            )
        )
        await session.commit()

    _MIGRATED_USERS.add(key)
    return stale_ids


async def interactive_graph(
    *,
    user_id: UUID,
    name: str = _DEFAULT_NAME,
    mermaid: Optional[str] = None,
    highlight_node: Optional[str] = None,
    clear: bool = False,
    target_device_id: Optional[UUID] = None,
) -> dict:
    """Mount or update an ephemeral diagram on the user's canvas.

    `name` selects which diagram instance. Pass the same `name` to update
    the same diagram; pass a different `name` to add a second diagram
    alongside. Default is `"main"`.

    At least one of `mermaid`, `highlight_node`, `clear` must be set.
    Order within the SSE batch: clear (if set) → mermaid (if set) → highlight.
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return {"error": "name must be kebab-case (lowercase letters, digits, dashes; start with a letter or digit)"}

    if mermaid is None and highlight_node is None and not clear:
        return {"error": "no-op: pass mermaid, highlight_node, or clear=True"}

    # Sandbox-validate the mermaid source before mounting. If it fails,
    # bail out with the parse error as the tool result so the teacher's
    # LLM retries in the same turn instead of the user seeing a broken
    # diagram on canvas.
    if mermaid is not None:
        validation_error = await _validate_mermaid(mermaid)
        if validation_error:
            return {
                "error": (
                    f"mermaid syntax error — fix and call again. "
                    f"Validator said: {validation_error}. "
                    "Common pitfall: parens, <br/>, or punctuation inside "
                    "node labels must be wrapped in double quotes — write "
                    "`A[\"Output (shifted)\"]` not `A[Output (shifted)]`."
                )
            }

    block = _build_graph_block(name)
    block_id = block.id

    async def _send(event) -> int:
        if target_device_id is not None:
            return await enqueue_for_device(user_id, target_device_id, event)
        return await enqueue_for_user(user_id, event)

    # One-time cleanup: if v1 left workspace files / layout rows for any
    # interactive-graph* block, remove them and unmount on connected browsers.
    stale_ids = await _migrate_v1_workspace_if_needed(user_id)
    for stale_id in stale_ids:
        if stale_id == block_id:
            # We're about to mount this id with fresh content anyway.
            continue
        await _send(UIUpdate(action="unmount", block=BlockSource(id=stale_id, source="")))

    delivered_mount = await _send(UIUpdate(action="mount", block=block))

    delivered_clear = 0
    delivered_mermaid = 0
    delivered_highlight = 0

    if clear:
        delivered_clear = await _send(
            BlockMessage(block_id=block_id, topic=_topic(block_id, "clear"), value=True)
        )

    if mermaid is not None:
        delivered_mermaid = await _send(
            BlockMessage(block_id=block_id, topic=_topic(block_id, "mermaid"), value=mermaid)
        )

    if highlight_node:
        delivered_highlight = await _send(
            BlockMessage(
                block_id=block_id,
                topic=_topic(block_id, "highlight"),
                value={"node_id": highlight_node, "durationMs": 1600},
            )
        )

    return {
        "name": name,
        "block_id": block_id,
        "delivered_mount": delivered_mount,
        "delivered_mermaid": delivered_mermaid,
        "delivered_highlight": delivered_highlight,
        "delivered_clear": delivered_clear,
        "mermaid_chars": len(mermaid) if mermaid else 0,
        "highlighted": bool(highlight_node),
        "cleared": bool(clear),
        "v1_cleaned_up": stale_ids,
    }


__all__ = ["interactive_graph", "build_spec"]

def _make_interactive_graph(user_id: UUID):
    async def executor(args: Dict[str, Any]) -> str:
        name_raw = args.get("name")
        if name_raw is not None and not isinstance(name_raw, str):
            return json.dumps({"error": "name must be a string"})
        name = (name_raw or "main").strip() or "main"

        mermaid_raw = args.get("mermaid")
        if mermaid_raw is not None and not isinstance(mermaid_raw, str):
            return json.dumps({"error": "mermaid must be a string"})
        mermaid = mermaid_raw.strip() if isinstance(mermaid_raw, str) else None
        if mermaid == "":
            mermaid = None

        highlight_raw = args.get("highlight_node")
        if highlight_raw is not None and not isinstance(highlight_raw, str):
            return json.dumps({"error": "highlight_node must be a string"})
        highlight_node = highlight_raw.strip() if isinstance(highlight_raw, str) else None
        if highlight_node == "":
            highlight_node = None

        clear = bool(args.get("clear") or False)

        if mermaid is None and highlight_node is None and not clear:
            return json.dumps({
                "error": "pass at least one of mermaid, highlight_node, or clear=true",
            })

        target_device_id = args.get("target_device_id")
        try:
            target_uuid = UUID(target_device_id) if target_device_id else None
        except (ValueError, TypeError):
            return json.dumps({"error": "invalid target_device_id"})

        result = await interactive_graph(
            user_id=user_id,
            name=name,
            mermaid=mermaid,
            highlight_node=highlight_node,
            clear=clear,
            target_device_id=target_uuid,
        )
        return json.dumps(result)
    return executor

def build_spec(user_id: UUID) -> ToolSpec:
    return ToolSpec(
        name="interactive_graph",
        description=(
            "Draw or update a diagram on the canvas — flowcharts, "
            "sequence diagrams, classes (UML), mindmaps, charts "
            "(bar / line / pie), gantt, sankey, timelines, ER, "
            "state machines, and more. Each diagram has a `name` you "
            "choose (e.g. \"steps\", \"protocol\"). Pass the SAME "
            "name to update an existing diagram in place; pass a "
            "DIFFERENT name to add a second diagram alongside. "
            "Diagrams are written in Mermaid syntax — concise text. "
            "The CURRENTLY ON CANVAS section in your prompt tells "
            "you which diagrams are already up. Diagrams are "
            "EPHEMERAL: they appear, illustrate the concept, and "
            "disappear when the user reloads. Don't worry about "
            "saving them. Pair with `speak` to narrate while the "
            "diagram grows; use `highlight_node` to flash a node "
            "while you're talking about it; use `clear=true` to "
            "wipe a diagram. For step-by-step explanation, send a "
            "fuller Mermaid each turn under the same name and the "
            "diagram grows with your words."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Semantic name for the diagram instance — "
                        "kebab-case (e.g. \"steps\", \"tls-handshake\", "
                        "\"krebs-cycle\"). Same name = update existing; "
                        "different name = add alongside. Defaults to "
                        "\"main\" if omitted."
                    ),
                },
                "mermaid": {
                    "type": "string",
                    "description": (
                        "Full Mermaid source. Replaces the prior content "
                        "of the named diagram. Examples: "
                        "'flowchart LR\\n  A[Step 1] --> B[Step 2]'; "
                        "'classDiagram\\nclass User { +String name }'; "
                        "'sequenceDiagram\\nAlice->>Bob: Hi'; "
                        "'xychart-beta\\ntitle \"Q1 sales\"\\nbar [10,20,30]'."
                    ),
                },
                "highlight_node": {
                    "type": "string",
                    "description": (
                        "Optional. Node id to flash for ~1.6s. Use the "
                        "same id you used in the Mermaid source "
                        "(e.g. 'A', 'Step1', or a class/actor name)."
                    ),
                },
                "clear": {
                    "type": "boolean",
                    "description": "If true, wipe the named diagram.",
                },
                "target_device_id": {
                    "type": "string",
                    "description": "Optional UUID; update on this device only.",
                },
            },
            "additionalProperties": False,
        },
        executor=_make_interactive_graph(user_id),
    )
