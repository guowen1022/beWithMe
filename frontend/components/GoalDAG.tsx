"use client";

import { useMemo } from "react";
import dagre from "dagre";
import type { DAGData, DAGNode } from "@/lib/api";

const NODE_W = 220;
const NODE_PAD = 12;
const CHAR_PER_LINE = 30;
const LINE_H = 15;
const BUTTON_H = 24;
const MIN_NODE_H = 60;

const STATUS_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  goal:     { bg: "#1e3a5f", border: "#3b82f6", text: "#93c5fd" },
  pending:  { bg: "#1f2937", border: "#4b5563", text: "#d1d5db" },
  known:    { bg: "#14532d", border: "#22c55e", text: "#86efac" },
  unknown:  { bg: "#451a03", border: "#f97316", text: "#fdba74" },
  expanded: { bg: "#1e1b4b", border: "#818cf8", text: "#a5b4fc" },
  atomic:   { bg: "#1a1a2e", border: "#6b7280", text: "#9ca3af" },
};

function getColors(node: DAGNode) {
  if (node.type === "goal") return STATUS_COLORS.goal;
  return STATUS_COLORS[node.status] || STATUS_COLORS.pending;
}

/** Estimate card height based on label length + buttons */
function estimateHeight(node: DAGNode): number {
  const lines = Math.ceil(node.label.length / CHAR_PER_LINE);
  const textH = lines * LINE_H;
  const hasButtons = node.type !== "goal" && (node.status === "pending" || node.status === "atomic");
  const statusH = node.type === "goal" ? 16 : (hasButtons ? BUTTON_H : 16);
  return Math.max(MIN_NODE_H, textH + statusH + NODE_PAD * 2);
}

/** Build orthogonal (right-angle) path between two points */
function orthogonalPath(
  sx: number, sy: number, sw: number,
  tx: number, ty: number, tw: number,
): string {
  const x1 = sx + sw / 2; // right edge of source
  const y1 = sy;
  const x2 = tx - tw / 2; // left edge of target
  const y2 = ty;
  const midX = (x1 + x2) / 2;
  return `M ${x1} ${y1} H ${midX} V ${y2} H ${x2}`;
}

interface Props {
  dag: DAGData;
  loading?: string | null;
  onExpand: (nodeId: string) => void;
  onKnow: (nodeId: string) => void;
  onUnknown: (nodeId: string) => void;
}

export default function GoalDAG({ dag, loading, onExpand, onKnow, onUnknown }: Props) {
  const layout = useMemo(() => {
    if (!dag.nodes.length) return null;

    const g = new dagre.graphlib.Graph();
    g.setGraph({
      rankdir: "LR",
      ranksep: 100,
      nodesep: 24,
      marginx: 40,
      marginy: 40,
      edgesep: 20,
    });
    g.setDefaultEdgeLabel(() => ({}));

    const heightMap = new Map<string, number>();
    for (const node of dag.nodes) {
      const h = estimateHeight(node);
      heightMap.set(node.id, h);
      g.setNode(node.id, { width: NODE_W, height: h });
    }
    for (const edge of dag.edges) {
      g.setEdge(edge.source, edge.target);
    }

    dagre.layout(g);

    const positions = new Map<string, { x: number; y: number; w: number; h: number }>();
    g.nodes().forEach((id) => {
      const n = g.node(id);
      if (n) positions.set(id, { x: n.x, y: n.y, w: NODE_W, h: heightMap.get(id) || MIN_NODE_H });
    });

    const edges: { source: string; target: string }[] = [];
    g.edges().forEach((e) => {
      edges.push({ source: e.v, target: e.w });
    });

    const graphInfo = g.graph();
    return {
      positions,
      edges,
      width: (graphInfo?.width ?? 600) + 80,
      height: (graphInfo?.height ?? 400) + 80,
    };
  }, [dag]);

  if (!layout || !dag.nodes.length) return null;

  // Build edge paths
  const edgePaths = layout.edges.map((e) => {
    const s = layout.positions.get(e.source);
    const t = layout.positions.get(e.target);
    if (!s || !t) return null;
    return {
      key: `${e.source}-${e.target}`,
      d: orthogonalPath(s.x, s.y, s.w, t.x, t.y, t.w),
    };
  }).filter(Boolean) as { key: string; d: string }[];

  return (
    <div className="w-full h-full overflow-auto">
      <div className="relative" style={{ minWidth: layout.width, minHeight: layout.height }}>
        {/* SVG for edges */}
        <svg
          className="absolute inset-0 pointer-events-none"
          style={{ width: layout.width, height: layout.height }}
        >
          <defs>
            <marker
              id="arrowhead"
              viewBox="0 0 10 6"
              refX={10}
              refY={3}
              markerWidth={8}
              markerHeight={6}
              orient="auto"
            >
              <path d="M0,0 L10,3 L0,6" fill="#6b7280" />
            </marker>
          </defs>
          {edgePaths.map((e) => (
            <path
              key={e.key}
              d={e.d}
              fill="none"
              stroke="#4b5563"
              strokeWidth={1.5}
              markerEnd="url(#arrowhead)"
            />
          ))}
        </svg>

        {/* Node cards */}
        {dag.nodes.map((node) => {
          const pos = layout.positions.get(node.id);
          if (!pos) return null;
          const colors = getColors(node);
          const isLoading = loading === node.id;
          const isGoal = node.type === "goal";
          const canTriage = !isGoal && (node.status === "pending" || node.status === "atomic");
          const canExpand = !isGoal && node.status === "pending";

          return (
            <div
              key={node.id}
              className="absolute rounded-lg border transition-all"
              style={{
                left: pos.x - pos.w / 2,
                top: pos.y - pos.h / 2,
                width: pos.w,
                minHeight: pos.h,
                background: colors.bg,
                borderColor: colors.border,
                borderWidth: isGoal ? 2 : 1,
                opacity: isLoading ? 0.6 : 1,
              }}
            >
              <div className="px-3 py-2 flex flex-col">
                {/* Full label text */}
                <p
                  className="text-[11px] leading-snug"
                  style={{ color: colors.text }}
                >
                  {node.label}
                </p>

                {/* Action buttons */}
                {canTriage && (
                  <div className="flex gap-1 mt-2">
                    <button
                      onClick={() => onKnow(node.id)}
                      disabled={isLoading}
                      className="flex-1 text-[9px] px-1.5 py-0.5 rounded bg-green-900/50 text-green-400 hover:bg-green-800/50 disabled:opacity-50"
                    >
                      Know
                    </button>
                    <button
                      onClick={() => onUnknown(node.id)}
                      disabled={isLoading}
                      className="flex-1 text-[9px] px-1.5 py-0.5 rounded bg-orange-900/50 text-orange-400 hover:bg-orange-800/50 disabled:opacity-50"
                    >
                      Don't know
                    </button>
                    {canExpand && (
                      <button
                        onClick={() => onExpand(node.id)}
                        disabled={isLoading}
                        className="flex-1 text-[9px] px-1.5 py-0.5 rounded bg-indigo-900/50 text-indigo-400 hover:bg-indigo-800/50 disabled:opacity-50"
                      >
                        Expand
                      </button>
                    )}
                  </div>
                )}

                {/* Status for resolved nodes */}
                {!canTriage && !isGoal && (
                  <div className="text-[9px] mt-1.5" style={{ color: colors.text, opacity: 0.7 }}>
                    {node.status === "known" && "✓ Known"}
                    {node.status === "unknown" && "⚠ To learn"}
                    {node.status === "expanded" && "↳ Expanded"}
                  </div>
                )}

                {isGoal && (
                  <div className="text-[9px] mt-1.5 text-blue-400 opacity-70">★ Goal</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
