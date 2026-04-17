"use client";

import { useEffect, useRef, useMemo } from "react";
import * as d3 from "d3";
import dagre from "dagre";
import type { DAGData, DAGNode } from "@/lib/api";

const NODE_W = 180;
const NODE_H = 70;

const STATUS_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  goal:     { bg: "#1e3a5f", border: "#3b82f6", text: "#93c5fd" },
  pending:  { bg: "#1f2937", border: "#4b5563", text: "#d1d5db" },
  known:    { bg: "#14532d", border: "#22c55e", text: "#86efac" },
  unknown:  { bg: "#451a03", border: "#f97316", text: "#fdba74" },
  expanded: { bg: "#1e1b4b", border: "#818cf8", text: "#a5b4fc" },
};

function getColors(node: DAGNode) {
  if (node.type === "goal") return STATUS_COLORS.goal;
  return STATUS_COLORS[node.status] || STATUS_COLORS.pending;
}

interface Props {
  dag: DAGData;
  loading?: string | null; // node ID currently loading
  onExpand: (nodeId: string) => void;
  onKnow: (nodeId: string) => void;
  onUnknown: (nodeId: string) => void;
}

export default function GoalDAG({ dag, loading, onExpand, onKnow, onUnknown }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Compute layout with dagre
  const layout = useMemo(() => {
    if (!dag.nodes.length) return null;

    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", ranksep: 60, nodesep: 30, marginx: 30, marginy: 30 });
    g.setDefaultEdgeLabel(() => ({}));

    for (const node of dag.nodes) {
      g.setNode(node.id, { width: NODE_W, height: NODE_H });
    }
    for (const edge of dag.edges) {
      g.setEdge(edge.source, edge.target);
    }

    dagre.layout(g);

    const positions = new Map<string, { x: number; y: number }>();
    g.nodes().forEach((id) => {
      const n = g.node(id);
      if (n) positions.set(id, { x: n.x, y: n.y });
    });

    const edgePoints: { source: string; target: string; points: { x: number; y: number }[] }[] = [];
    g.edges().forEach((e) => {
      const edge = g.edge(e);
      if (edge?.points) {
        edgePoints.push({ source: e.v, target: e.w, points: edge.points });
      }
    });

    const graphInfo = g.graph();
    return {
      positions,
      edges: edgePoints,
      width: (graphInfo?.width ?? 600) + 60,
      height: (graphInfo?.height ?? 400) + 60,
    };
  }, [dag]);

  // Render with d3 for arrow markers
  useEffect(() => {
    if (!layout || !svgRef.current) return;
    const svg = d3.select(svgRef.current);

    // Clear and re-add arrow marker
    svg.selectAll("defs").remove();
    const defs = svg.append("defs");
    defs.append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "0 0 10 6")
      .attr("refX", 10)
      .attr("refY", 3)
      .attr("markerWidth", 8)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,0 L10,3 L0,6")
      .attr("fill", "#6b7280");
  }, [layout]);

  if (!layout || !dag.nodes.length) {
    return null;
  }

  const line = d3.line<{ x: number; y: number }>()
    .x((d) => d.x)
    .y((d) => d.y)
    .curve(d3.curveBasis);

  return (
    <div ref={containerRef} className="w-full h-full overflow-auto">
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        {/* SVG layer for edges */}
        <svg
          ref={svgRef}
          className="absolute inset-0 pointer-events-none"
          style={{ width: layout.width, height: layout.height }}
        >
          {layout.edges.map((e, i) => (
            <path
              key={i}
              d={line(e.points) || ""}
              fill="none"
              stroke="#4b5563"
              strokeWidth={1.5}
              markerEnd="url(#arrowhead)"
            />
          ))}
        </svg>

        {/* HTML layer for node cards */}
        {dag.nodes.map((node) => {
          const pos = layout.positions.get(node.id);
          if (!pos) return null;
          const colors = getColors(node);
          const isLoading = loading === node.id;
          const isGoal = node.type === "goal";
          const canAct = !isGoal && node.status === "pending";

          return (
            <div
              key={node.id}
              className="absolute rounded-lg border transition-all"
              style={{
                left: pos.x - NODE_W / 2,
                top: pos.y - NODE_H / 2,
                width: NODE_W,
                height: NODE_H,
                background: colors.bg,
                borderColor: colors.border,
                borderWidth: isGoal ? 2 : 1,
                opacity: isLoading ? 0.6 : 1,
              }}
            >
              <div className="px-2.5 py-1.5 h-full flex flex-col">
                {/* Label */}
                <p
                  className="text-[11px] leading-snug flex-1"
                  style={{ color: colors.text }}
                  title={node.label}
                >
                  {node.label.length > 50 ? node.label.slice(0, 48) + "..." : node.label}
                </p>

                {/* Action buttons — only for pending prerequisites */}
                {canAct && (
                  <div className="flex gap-1 mt-1">
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
                    <button
                      onClick={() => onExpand(node.id)}
                      disabled={isLoading}
                      className="flex-1 text-[9px] px-1.5 py-0.5 rounded bg-indigo-900/50 text-indigo-400 hover:bg-indigo-800/50 disabled:opacity-50"
                    >
                      Expand
                    </button>
                  </div>
                )}

                {/* Status indicator for non-pending nodes */}
                {!canAct && !isGoal && (
                  <div className="text-[9px] mt-1" style={{ color: colors.text, opacity: 0.7 }}>
                    {node.status === "known" && "✓ Known"}
                    {node.status === "unknown" && "⚠ To learn"}
                    {node.status === "expanded" && "↳ Expanded"}
                  </div>
                )}

                {isGoal && (
                  <div className="text-[9px] mt-1 text-blue-400 opacity-70">★ Goal</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
