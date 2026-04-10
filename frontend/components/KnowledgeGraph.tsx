"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import * as d3 from "d3";
import { getGraphData, type GraphData, type GraphNode, type GraphEdge } from "@/lib/api";

const STATE_COLORS: Record<string, string> = {
  solid: "#22c55e",
  learning: "#3b82f6",
  rusty: "#eab308",
  faded: "#6b7280",
};

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  state: string;
  mastery: number;
  encounters: number;
  halfLife: number;
}

interface SimEdge extends d3.SimulationLinkDatum<SimNode> {
  weight: number;
  type: string;
}

export default function KnowledgeGraph({ refreshKey }: { refreshKey: number }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);

  const loadData = useCallback(() => {
    getGraphData().then(setData).catch(console.error);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData, refreshKey]);

  useEffect(() => {
    if (!data || !svgRef.current) return;
    if (data.nodes.length === 0) return;

    const svg = d3.select(svgRef.current);
    const width = svgRef.current.clientWidth;
    const height = svgRef.current.clientHeight;

    svg.selectAll("*").remove();

    const g = svg.append("g");

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    // Build simulation data
    const nodes: SimNode[] = data.nodes.map((n) => ({ ...n }));
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const edges: SimEdge[] = data.edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        source: e.source,
        target: e.target,
        weight: e.weight,
        type: e.type,
      }));

    // Force simulation
    const sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink<SimNode, SimEdge>(edges).id((d) => d.id).distance(80).strength((d) => Math.min(d.weight * 0.3, 1)))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide(30));

    // Edges
    const link = g.append("g")
      .selectAll("line")
      .data(edges)
      .join("line")
      .attr("stroke", "#555")
      .attr("stroke-opacity", 0.4)
      .attr("stroke-width", (d) => Math.max(1, Math.min(d.weight * 1.5, 6)));

    // Edge type indicator (dashed for non-temporal)
    link.attr("stroke-dasharray", (d) => d.type !== "temporal" ? "4,3" : null);

    // Nodes
    const node = g.append("g")
      .selectAll("g")
      .data(nodes)
      .join("g")
      .call(d3.drag<SVGGElement, SimNode>()
        .on("start", (event, d) => {
          if (!event.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event, d) => {
          if (!event.active) sim.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
      );

    // Node circles
    node.append("circle")
      .attr("r", (d) => 8 + Math.min(d.encounters * 2, 12))
      .attr("fill", (d) => STATE_COLORS[d.state] || "#6b7280")
      .attr("stroke", "#fff")
      .attr("stroke-width", 1.5)
      .attr("opacity", (d) => 0.4 + d.mastery * 0.6);

    // Node labels
    node.append("text")
      .text((d) => d.id)
      .attr("dy", (d) => -(12 + Math.min(d.encounters * 2, 12)))
      .attr("text-anchor", "middle")
      .attr("fill", "#d1d5db")
      .attr("font-size", "11px")
      .attr("pointer-events", "none");

    // Hover tooltip
    node.on("mouseenter", (event, d) => {
      setHoveredNode(d.id);
      d3.select(event.currentTarget).select("circle")
        .attr("stroke", STATE_COLORS[d.state] || "#fff")
        .attr("stroke-width", 3);
    }).on("mouseleave", (event) => {
      setHoveredNode(null);
      d3.select(event.currentTarget).select("circle")
        .attr("stroke", "#fff")
        .attr("stroke-width", 1.5);
    });

    // Tick
    sim.on("tick", () => {
      link
        .attr("x1", (d: any) => d.source.x)
        .attr("y1", (d: any) => d.source.y)
        .attr("x2", (d: any) => d.target.x)
        .attr("y2", (d: any) => d.target.y);
      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

    return () => { sim.stop(); };
  }, [data]);

  const hovered = data?.nodes.find((n) => n.id === hoveredNode);

  return (
    <div className="relative w-full h-full">
      {data && data.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-gray-500">
          No concepts yet. Ask some questions first.
        </div>
      )}
      <svg
        ref={svgRef}
        className="w-full h-full"
        style={{ background: "transparent" }}
      />
      {/* Legend */}
      <div className="absolute top-2 left-2 flex gap-3 text-[10px] text-gray-400">
        {Object.entries(STATE_COLORS).map(([state, color]) => (
          <span key={state} className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />
            {state}
          </span>
        ))}
      </div>
      {/* Hover info */}
      {hovered && (
        <div className="absolute bottom-2 left-2 bg-gray-800 text-gray-200 text-xs rounded-lg px-3 py-2 space-y-0.5">
          <p className="font-medium">{hovered.id}</p>
          <p>State: {hovered.state} ({Math.round(hovered.mastery * 100)}%)</p>
          <p>Encounters: {hovered.encounters}</p>
          <p>Half-life: {hovered.halfLife}h</p>
        </div>
      )}
    </div>
  );
}
