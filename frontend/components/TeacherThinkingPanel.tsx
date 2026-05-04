"use client";

// Floating debug panel that surfaces teacher auto-trigger activity.
// Subscribes to the dynamic SSE stream, picks out `teacher-thinking`
// events, and renders the latest few in a collapsible bottom-right
// pill. No-op when the array is empty so it doesn't clutter the canvas.

import { useEffect, useState } from "react";

import { subscribeToDynamicStream } from "@/lib/api";

interface ThinkingEntry {
  id: number;
  startedAt: number;
  trigger: string;
  summary: string;
  text?: string | null;
  toolCalls: { name?: string; arguments?: Record<string, unknown> }[];
  done: boolean;
}

const MAX_ENTRIES = 5;

export default function TeacherThinkingPanel() {
  const [entries, setEntries] = useState<ThinkingEntry[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    let nextId = 0;
    subscribeToDynamicStream((event) => {
      if (event.type !== "teacher-thinking") return;
      setEntries((prev) => {
        if (event.phase === "start") {
          const head: ThinkingEntry = {
            id: ++nextId,
            startedAt: Date.now(),
            trigger: event.trigger,
            summary: event.summary || "",
            toolCalls: [],
            done: false,
          };
          return [head, ...prev].slice(0, MAX_ENTRIES);
        }
        // phase === "end" — fold into the most recent matching start.
        const idx = prev.findIndex((e) => e.trigger === event.trigger && !e.done);
        if (idx === -1) {
          // Lost the start (e.g., we mounted mid-turn). Still surface it.
          const head: ThinkingEntry = {
            id: ++nextId,
            startedAt: Date.now(),
            trigger: event.trigger,
            summary: event.summary || "",
            text: event.text ?? null,
            toolCalls: event.tool_calls ?? [],
            done: true,
          };
          return [head, ...prev].slice(0, MAX_ENTRIES);
        }
        const merged = [...prev];
        merged[idx] = {
          ...merged[idx],
          text: event.text ?? null,
          toolCalls: event.tool_calls ?? [],
          done: true,
        };
        return merged;
      });
    }, ctrl.signal).catch((err) => {
      if (err?.name !== "AbortError") {
        console.warn("[teacher-thinking] stream ended", err);
      }
    });
    return () => ctrl.abort();
  }, []);

  if (entries.length === 0) return null;

  const latest = entries[0];
  const wrapStyle: React.CSSProperties = {
    position: "fixed",
    right: 16,
    bottom: 80,
    maxWidth: 360,
    fontFamily: "ui-monospace, SFMono-Regular, monospace",
    fontSize: 11,
    color: "rgba(229,231,235,0.92)",
    background: "rgba(15,23,42,0.92)",
    border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: 10,
    boxShadow: "0 8px 30px rgba(0,0,0,0.5)",
    zIndex: 60,
    overflow: "hidden",
  };
  const headerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 10px",
    background: "rgba(30,41,59,0.6)",
    borderBottom: collapsed ? "none" : "1px solid rgba(255,255,255,0.06)",
    cursor: "pointer",
    userSelect: "none",
  };
  const dotStyle: React.CSSProperties = {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: latest.done ? "#86efac" : "#fbbf24",
    boxShadow: latest.done ? "none" : "0 0 6px rgba(251,191,36,0.8)",
  };

  return (
    <div style={wrapStyle} data-teacher-thinking="">
      <div
        style={headerStyle}
        onClick={() => setCollapsed((c) => !c)}
        title="teacher auto-trigger activity"
      >
        <span style={dotStyle} />
        <span style={{ fontWeight: 600 }}>teacher thinking</span>
        <span style={{ opacity: 0.6 }}>
          {entries.length} event{entries.length === 1 ? "" : "s"}
        </span>
        <span style={{ marginLeft: "auto", opacity: 0.5 }}>{collapsed ? "▸" : "▾"}</span>
      </div>
      {!collapsed && (
        <div style={{ maxHeight: 280, overflow: "auto" }}>
          {entries.map((e) => (
            <div
              key={e.id}
              style={{
                padding: "8px 10px",
                borderBottom: "1px solid rgba(255,255,255,0.04)",
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ color: "#94a3b8" }}>{e.trigger}</span>
                <span style={{ marginLeft: "auto", opacity: 0.55, fontSize: 10 }}>
                  {e.done ? "done" : "running…"}
                </span>
              </div>
              {e.summary && (
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    margin: "4px 0 0",
                    color: "#cbd5e1",
                    fontSize: 10.5,
                    lineHeight: 1.4,
                  }}
                >
                  {e.summary}
                </pre>
              )}
              {e.toolCalls.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  {e.toolCalls.map((tc, i) => (
                    <div key={i} style={{ color: "#fbbf24", fontSize: 10.5 }}>
                      → {tc.name}
                      {tc.arguments && Object.keys(tc.arguments).length > 0 ? (
                        <span style={{ color: "#94a3b8" }}>
                          ({Object.keys(tc.arguments).join(", ")})
                        </span>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
              {e.text && (
                <div
                  style={{
                    marginTop: 6,
                    color: "#94a3b8",
                    fontStyle: "italic",
                    fontSize: 10.5,
                    lineHeight: 1.4,
                  }}
                >
                  {e.text.length > 200 ? e.text.slice(0, 200) + "…" : e.text}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
