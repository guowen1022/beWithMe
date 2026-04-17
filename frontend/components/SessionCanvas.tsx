"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getSessionGraph, type SessionGraphData, type SessionNode } from "@/lib/api";

const CARD_W = 160;
const COL_GAP = 24;
const LANE_LABEL_W = 90;
const TIMELINE_H = 36;

const LANE_BG = [
  "rgba(74, 222, 128, 0.06)", "rgba(96, 165, 250, 0.06)",
  "rgba(250, 204, 21, 0.06)", "rgba(251, 146, 60, 0.06)",
  "rgba(192, 132, 252, 0.06)", "rgba(248, 113, 113, 0.06)",
  "rgba(45, 212, 191, 0.06)", "rgba(244, 114, 182, 0.06)",
];
const LANE_BORDER = [
  "rgba(74, 222, 128, 0.25)", "rgba(96, 165, 250, 0.25)",
  "rgba(250, 204, 21, 0.25)", "rgba(251, 146, 60, 0.25)",
  "rgba(192, 132, 252, 0.25)", "rgba(248, 113, 113, 0.25)",
  "rgba(45, 212, 191, 0.25)", "rgba(244, 114, 182, 0.25)",
];
const LANE_TEXT = [
  "#4ade80", "#60a5fa", "#facc15", "#fb923c",
  "#c084fc", "#f87171", "#2dd4bf", "#f472b6",
];

function dateKey(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function dateLabel(iso: string): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function SessionCanvas({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<SessionGraphData | null>(null);
  const [selected, setSelected] = useState<SessionNode | null>(null);
  const [filterLabel, setFilterLabel] = useState<string | null>(null);
  const prevCountRef = useRef(0);

  const loadData = useCallback(() => {
    getSessionGraph(filterLabel ?? undefined).then(setData).catch(console.error);
  }, [filterLabel]);

  useEffect(() => { loadData(); }, [loadData, refreshKey]);

  // Poll every 5s
  useEffect(() => {
    if (data) prevCountRef.current = data.nodes.length;
    const interval = setInterval(() => {
      getSessionGraph(filterLabel ?? undefined)
        .then((fresh) => {
          if (fresh.nodes.length !== prevCountRef.current) {
            setData(fresh);
            prevCountRef.current = fresh.nodes.length;
          }
        })
        .catch(() => {});
    }, 5000);
    return () => clearInterval(interval);
  }, [data, filterLabel]);

  // Collect all unique labels across all sessions for the filter bar
  const allLabels = new Set<string>();
  if (data) {
    for (const n of data.nodes) {
      for (const l of (n.labels || [])) allLabels.add(l);
    }
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="w-full h-full flex flex-col">
        {/* Label filter bar even when empty — so user can clear filter */}
        {filterLabel && (
          <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800 shrink-0">
            <button
              onClick={() => setFilterLabel(null)}
              className="text-xs px-2 py-1 rounded bg-blue-600 text-white"
            >
              {filterLabel} x
            </button>
          </div>
        )}
        <div className="flex-1 flex items-center justify-center text-sm text-gray-500">
          {filterLabel ? `No sessions with label "${filterLabel}".` : "No session summaries yet. End a session to generate one."}
        </div>
      </div>
    );
  }

  // Primary label = first label (used for lane grouping)
  const primaryLabel = (n: SessionNode) => (n.labels && n.labels[0]) || "General";

  // Unique dates (columns)
  const dateSet = new Set<string>();
  for (const n of data.nodes) dateSet.add(dateKey(n.created_at));
  const dates = [...dateSet].sort();

  // Unique lanes (by primary label)
  const laneOrder: string[] = [];
  const laneSet = new Set<string>();
  for (const n of data.nodes) {
    const pl = primaryLabel(n);
    if (!laneSet.has(pl)) { laneSet.add(pl); laneOrder.push(pl); }
  }

  // Grid: lane × date → sessions[]
  const grid = new Map<string, SessionNode[]>();
  for (const n of data.nodes) {
    const key = `${primaryLabel(n)}|${dateKey(n.created_at)}`;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key)!.push(n);
  }

  const colW = CARD_W + COL_GAP;
  const totalW = LANE_LABEL_W + dates.length * colW + 40;

  return (
    <div className="w-full h-full flex flex-col">
      {/* Label filter bar */}
      {allLabels.size > 0 && (
        <div className="flex items-center gap-1.5 px-4 py-2 border-b border-gray-800 shrink-0 overflow-x-auto">
          <span className="text-[10px] text-gray-500 mr-1 shrink-0">Labels:</span>
          {filterLabel && (
            <button
              onClick={() => setFilterLabel(null)}
              className="text-[10px] px-2 py-0.5 rounded-full bg-gray-700 text-gray-300 hover:bg-gray-600 shrink-0"
            >
              All
            </button>
          )}
          {[...allLabels].sort().map((l) => (
            <button
              key={l}
              onClick={() => setFilterLabel(filterLabel === l ? null : l)}
              className={`text-[10px] px-2 py-0.5 rounded-full shrink-0 transition-colors ${
                filterLabel === l
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {l}
            </button>
          ))}
        </div>
      )}

      {/* Scrollable canvas */}
      <div className="flex-1 overflow-auto">
        <div style={{ minWidth: totalW }}>
          {/* Timeline dots */}
          <div className="relative flex items-end" style={{ height: TIMELINE_H, paddingLeft: LANE_LABEL_W }}>
            {/* Connecting line */}
            <div
              className="absolute bg-gray-700"
              style={{
                height: 1,
                left: LANE_LABEL_W + colW / 2 - 4,
                right: colW / 2,
                top: TIMELINE_H - 6,
              }}
            />
            {dates.map((dk) => {
              const sample = data.nodes.find((n) => dateKey(n.created_at) === dk);
              return (
                <div key={dk} className="flex flex-col items-center relative" style={{ width: colW }}>
                  <span className="text-[10px] text-gray-400 mb-1">
                    {sample ? dateLabel(sample.created_at) : dk}
                  </span>
                  <div className="w-2 h-2 rounded-full bg-gray-400 z-10" />
                </div>
              );
            })}
          </div>

          {/* Domain lanes */}
          <div className="space-y-1 mt-2 px-1">
            {laneOrder.map((lane, laneIdx) => {
              const colorIdx = laneIdx % LANE_BG.length;
              return (
                <div
                  key={lane}
                  className="flex rounded-lg"
                  style={{
                    background: LANE_BG[colorIdx],
                    borderLeft: `3px solid ${LANE_BORDER[colorIdx]}`,
                  }}
                >
                  {/* Lane label */}
                  <div className="shrink-0 flex items-center justify-center" style={{ width: LANE_LABEL_W }}>
                    <span className="text-[11px] font-medium" style={{ color: LANE_TEXT[colorIdx] }}>
                      {lane}
                    </span>
                  </div>

                  {/* Grid cells */}
                  <div className="flex flex-1">
                    {dates.map((dk) => {
                      const sessions = grid.get(`${lane}|${dk}`) || [];
                      return (
                        <div key={dk} className="flex flex-col gap-1.5 py-2 px-1" style={{ width: colW }}>
                          {sessions.map((node) => (
                            <div
                              key={node.session_id}
                              className={`rounded-lg border cursor-pointer transition-all hover:border-blue-400 ${
                                selected?.session_id === node.session_id
                                  ? "border-blue-500 shadow-md shadow-blue-500/20"
                                  : "border-gray-700"
                              }`}
                              style={{ width: CARD_W, background: "#1f2937" }}
                              onClick={() => setSelected(node)}
                            >
                              <div className="px-2.5 py-2">
                                <p className="text-[11px] text-gray-200 leading-snug mb-1" title={node.title}>
                                  {node.title || `Session ${node.session_id.slice(0, 8)}`}
                                </p>
                                {/* Labels */}
                                {(node.labels || []).length > 1 && (
                                  <div className="flex flex-wrap gap-1 mb-1">
                                    {(node.labels || []).slice(1).map((l) => (
                                      <span key={l} className="text-[8px] px-1 py-0.5 rounded bg-gray-800 text-gray-500">
                                        {l}
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {/* Footer */}
                                <div className="flex items-center justify-between text-[9px] text-gray-500">
                                  <span>{dateLabel(node.created_at)}</span>
                                  <span>{node.duration_min ? `${node.duration_min} min` : ""}</span>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Expanded summary panel */}
      {selected && (
        <div className="absolute top-0 right-0 w-[28rem] h-full bg-gray-900 border-l border-gray-700 overflow-y-auto p-5 z-10">
          <div className="flex items-start justify-between gap-3 mb-3">
            <h2 className="text-base font-semibold text-gray-100 leading-snug">
              {selected.title || `Session ${selected.session_id.slice(0, 8)}`}
            </h2>
            <button
              onClick={() => setSelected(null)}
              className="text-gray-400 hover:text-gray-200 text-lg leading-none shrink-0 mt-0.5"
            >
              x
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            {(selected.labels || []).map((l) => (
              <span key={l} className="text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">
                {l}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-3 text-xs text-gray-500 mb-4">
            {selected.created_at && <span>{new Date(selected.created_at).toLocaleString()}</span>}
            {selected.duration_min > 0 && <span>{selected.duration_min} min</span>}
          </div>
          <div className="prose prose-sm prose-invert max-w-none text-gray-300 whitespace-pre-wrap text-sm leading-relaxed">
            {selected.summary}
          </div>
        </div>
      )}
    </div>
  );
}
