"""interactive_graph — teacher's tool for a Mermaid-driven canvas diagram.

One canonical block (`interactive-graph`) renders Mermaid syntax. The
teacher publishes Mermaid strings on the `graph.mermaid` topic; the
block re-renders the SVG on every tick. For incremental teaching the
teacher emits a fuller diagram each turn (step 1, step 1 + step 2, …)
and the same block grows in place.

Mechanics:
  1. Ensure `blocks/interactive-graph.{js,md}` exists in the user's git
     workspace. The block code is a fixed template — no engineer LLM in
     the loop, so the diagram update lands in tens of milliseconds.
  2. Mount it on the user's canvas if it isn't already (UIUpdate event +
     canvas_layout row).
  3. Publish the Mermaid source on `graph.mermaid` (sticky pub/sub means
     a block mounted in the same SSE batch sees the value).
  4. Optionally publish a node id on `graph.highlight` to flash that
     node, or `True` on `graph.clear` to wipe the diagram.

Topics:
  Subscribed by the block:
    - `graph.mermaid`   string — full Mermaid source
    - `graph.highlight` {node_id, durationMs?}
    - `graph.clear`     truthy — wipe the SVG

  Published by the block:
    - `graph.selected`  {node_id, label} — when the user clicks a node
    - `graph.error`     {message} — Mermaid render/parse error
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.frontend_engineer import workspace as ws
from infra.contracts.ui import BlockMessage, BlockSource, UIUpdate
from infra.db import async_session
from infra.devices import registry as device_registry
from services.persona.routers.dynamic import enqueue_for_device, enqueue_for_user
from silicon_brain.models.canvas_layout import CanvasLayout


_GRAPH_BLOCK_ID = "interactive-graph"
_TOPIC_MERMAID = "graph.mermaid"
_TOPIC_HIGHLIGHT = "graph.highlight"
_TOPIC_CLEAR = "graph.clear"


_GRAPH_BLOCK_JS = """\
({
  id: 'interactive-graph',
  grid: { x: 30, y: 10, w: 100, h: 70 },
  // Block reports its own structured state below; skip the DOM-text fallback.
  autosnapshot: false,
  style: {
    background: '#0f172a',
    color: '#e5e7eb',
    border: '1px solid rgba(148,163,184,0.18)',
    borderRadius: '12px',
    boxShadow: '0 8px 28px rgba(0,0,0,0.35)',
    padding: '12px',
    overflow: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  subscribes: ['graph.mermaid', 'graph.highlight', 'graph.clear'],
  publishes: ['graph.selected', 'graph.error'],
  run(root, bus, cleanup, helpers) {
    var blockId = (helpers && helpers.blockId) || 'interactive-graph';
    var report = helpers && typeof helpers.reportState === 'function'
      ? helpers.reportState
      : function () {};

    var status = document.createElement('div');
    status.style.fontSize = '11px';
    status.style.opacity = '0.55';
    status.style.minHeight = '14px';
    status.textContent = 'loading mermaid…';
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
            try { bus.publish('graph.selected', { node_id: id, label: label }); } catch (e) {}
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
      status.textContent = '';
      pushState();
    }

    function whenReady() {
      if (typeof window === 'undefined') return Promise.reject(new Error('no window'));
      if (window.__mermaidReady) return window.__mermaidReady;
      if (window.mermaid) return Promise.resolve(window.mermaid);
      // Loader hasn't run yet — poll briefly.
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
      status.textContent = '';
      return mermaid;
    }).catch(function (err) {
      status.textContent = 'mermaid failed to load: ' + (err && err.message || err);
      try { bus.publish('graph.error', { message: String(err && err.message || err) }); } catch (e) {}
      throw err;
    });

    function dropMermaidSandbox(renderId) {
      // mermaid.render appends a 'd<renderId>' div to <body> for layout
      // measurement and is supposed to clean it up — but in practice it
      // leaks. Sweep any sandbox div whose id begins with our prefix.
      try {
        var sandboxes = document.body.querySelectorAll('[id^="dmermaid-' + blockId + '-"]');
        for (var i = 0; i < sandboxes.length; i++) sandboxes[i].remove();
        if (renderId) {
          var direct = document.getElementById('d' + renderId);
          if (direct) direct.remove();
        }
      } catch (e) {}
    }

    function fitSvg(svg) {
      // Make the SVG fill the container box on both axes while preserving
      // aspect ratio (no overflow, no off-screen content).
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.style.width = '100%';
      svg.style.height = '100%';
      svg.style.maxWidth = '100%';
      svg.style.maxHeight = '100%';
      svg.style.display = 'block';
    }

    function renderMermaid(source) {
      var seq = ++renderSeq;
      lastSource = String(source || '');
      lastKind = detectKind(lastSource);
      ready.then(function (mermaid) {
        if (seq !== renderSeq) return;  // a newer render superseded this one
        var renderId = 'mermaid-' + blockId + '-' + seq;
        Promise.resolve()
          .then(function () { return mermaid.render(renderId, lastSource); })
          .then(function (out) {
            dropMermaidSandbox(renderId);
            if (seq !== renderSeq) return;
            container.innerHTML = out && out.svg ? out.svg : '';
            status.textContent = '';
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
            // Selection survives a re-render only if the same node id is still present.
            if (selectedNode && nodeIds.indexOf(selectedNode) < 0) selectedNode = null;
            pushState();
          })
          .catch(function (err) {
            dropMermaidSandbox(renderId);
            if (seq !== renderSeq) return;
            var msg = err && err.message ? err.message : String(err);
            status.textContent = 'render error: ' + msg;
            container.innerHTML = '';
            nodeIds = [];
            try { bus.publish('graph.error', { message: msg }); } catch (e) {}
            pushState({ error: msg });
          });
      });
    }

    var unsubMermaid = bus.subscribe('graph.mermaid', function (value) {
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

    var unsubHighlight = bus.subscribe('graph.highlight', function (value) {
      if (!value) return;
      var nodeId = typeof value === 'string' ? value : value.node_id;
      var ms = (typeof value === 'object' && value) ? value.durationMs : null;
      if (!nodeId) return;
      var svg = container.querySelector('svg');
      flashNode(svg, nodeId, ms);
    });
    cleanup(unsubHighlight);

    var unsubClear = bus.subscribe('graph.clear', function (value) {
      if (!value) return;
      clearSvg();
    });
    cleanup(unsubClear);

    cleanup(function () { dropMermaidSandbox(null); });
    pushState();
  },
})
"""

_GRAPH_BLOCK_MD = (
    "# interactive-graph\n\n"
    "Canonical Mermaid diagram block owned by the teacher's "
    "`interactive_graph` tool. Subscribes to `graph.mermaid` (string: "
    "full Mermaid source), `graph.highlight` ({node_id, durationMs?}), "
    "and `graph.clear` (truthy: wipe). Publishes `graph.selected` "
    "({node_id, label}) on click and `graph.error` ({message}) on "
    "render failure. Re-renders on every `graph.mermaid` tick — for "
    "incremental teaching, the teacher emits a fuller diagram each "
    "turn and the same block grows in place.\n"
)


def _ensure_graph_block_in_workspace(user_id: UUID) -> BlockSource:
    """Write blocks/interactive-graph.{js,md} if missing or stale.

    Always returns the BlockSource so callers can ship it as a mount event.
    """
    snap = ws.read_snapshot(user_id)
    existing = snap.blocks.get(_GRAPH_BLOCK_ID)
    if existing is None or existing.js != _GRAPH_BLOCK_JS:
        ws.write_files(
            user_id,
            [
                ws.FileWrite(path=f"blocks/{_GRAPH_BLOCK_ID}.js", content=_GRAPH_BLOCK_JS),
                ws.FileWrite(path=f"blocks/{_GRAPH_BLOCK_ID}.md", content=_GRAPH_BLOCK_MD),
            ],
        )
        ws.regenerate_topics(user_id)
        ws.commit(user_id, "tools.interactive_graph: install interactive-graph block")
    return BlockSource(id=_GRAPH_BLOCK_ID, source=_GRAPH_BLOCK_JS, design_doc=_GRAPH_BLOCK_MD)


async def _online_device_ids(user_id: UUID) -> list[UUID]:
    devices = await device_registry.list_for_user(user_id)
    return [d.device_id for d in devices if d.online]


async def _record_mount(user_id: UUID, block_id: str, device_ids: list[UUID]) -> None:
    if not device_ids:
        return
    async with async_session() as session:
        for did in device_ids:
            stmt = (
                pg_insert(CanvasLayout)
                .values(user_id=user_id, device_id=did, block_id=block_id)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "device_id", "block_id"],
                )
            )
            await session.execute(stmt)
        await session.commit()


async def interactive_graph(
    *,
    user_id: UUID,
    mermaid: Optional[str] = None,
    highlight_node: Optional[str] = None,
    clear: bool = False,
    target_device_id: Optional[UUID] = None,
) -> dict:
    """Mount the interactive-graph block (idempotent) and update its state.

    At least one of `mermaid`, `highlight_node`, `clear` must be set.
    Order within the SSE batch: clear (if set) → mermaid (if set) → highlight.
    """
    if mermaid is None and highlight_node is None and not clear:
        return {"error": "no-op: pass mermaid, highlight_node, or clear=True"}

    block = _ensure_graph_block_in_workspace(user_id)
    mount_event = UIUpdate(action="mount", block=block)

    async def _send(event) -> int:
        if target_device_id is not None:
            return await enqueue_for_device(user_id, target_device_id, event)
        return await enqueue_for_user(user_id, event)

    if target_device_id is not None:
        mount_targets = [target_device_id]
    else:
        mount_targets = await _online_device_ids(user_id)

    delivered_mount = await _send(mount_event)
    await _record_mount(user_id, _GRAPH_BLOCK_ID, mount_targets)

    delivered_clear = 0
    delivered_mermaid = 0
    delivered_highlight = 0

    if clear:
        delivered_clear = await _send(
            BlockMessage(block_id=_GRAPH_BLOCK_ID, topic=_TOPIC_CLEAR, value=True)
        )

    if mermaid is not None:
        delivered_mermaid = await _send(
            BlockMessage(block_id=_GRAPH_BLOCK_ID, topic=_TOPIC_MERMAID, value=mermaid)
        )

    if highlight_node:
        delivered_highlight = await _send(
            BlockMessage(
                block_id=_GRAPH_BLOCK_ID,
                topic=_TOPIC_HIGHLIGHT,
                value={"node_id": highlight_node, "durationMs": 1600},
            )
        )

    return {
        "block_id": _GRAPH_BLOCK_ID,
        "delivered_mount": delivered_mount,
        "delivered_mermaid": delivered_mermaid,
        "delivered_highlight": delivered_highlight,
        "delivered_clear": delivered_clear,
        "mermaid_chars": len(mermaid) if mermaid else 0,
        "highlighted": bool(highlight_node),
        "cleared": bool(clear),
    }


__all__ = ["interactive_graph"]
