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
  model?: string | null;
  provider?: string | null;
  promptTokens?: number | null;
  completionTokens?: number | null;
  latencyMs?: number | null;
}

const MAX_ENTRIES = 12;

// Color map per scenario — keeps the panel scannable when many call sites
// fire in quick succession.
const TRIGGER_COLOR: Record<string, string> = {
  answer: "var(--bw-accent)",
  reflect: "var(--bw-ink-muted)",
  "block-completed": "var(--bw-accent)",
  "canvas-changed": "var(--bw-ink-muted)",
  voice: "var(--bw-ink-muted)",
  "ambient-mic": "#7AA2F7",
  "user-speech": "#7AA2F7",
  router: "#7AA2F7",
  recommender: "#9D7CD8",
  distiller: "#9D7CD8",
  "goal-planner": "#7AA2F7",
  "session-summarizer": "#9D7CD8",
  "delegate-engineer": "#E0AF68",
};

function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k tok`;
  return `${n} tok`;
}

function fmtLatency(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

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
            model: event.model ?? null,
            provider: event.provider ?? null,
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
            model: event.model ?? null,
            provider: event.provider ?? null,
            promptTokens: event.prompt_tokens ?? null,
            completionTokens: event.completion_tokens ?? null,
            latencyMs: event.latency_ms ?? null,
          };
          return [head, ...prev].slice(0, MAX_ENTRIES);
        }
        const merged = [...prev];
        merged[idx] = {
          ...merged[idx],
          text: event.text ?? null,
          toolCalls: event.tool_calls ?? [],
          done: true,
          model: event.model ?? merged[idx].model ?? null,
          provider: event.provider ?? merged[idx].provider ?? null,
          promptTokens: event.prompt_tokens ?? null,
          completionTokens: event.completion_tokens ?? null,
          latencyMs: event.latency_ms ?? null,
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
    fontFamily: "var(--bw-font-mono)",
    fontSize: 11,
    color: "var(--bw-ink)",
    background: "var(--bw-surface)",
    border: "1px solid var(--bw-border)",
    borderRadius: 0,
    zIndex: 60,
    overflow: "hidden",
  };
  const headerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 10px",
    background: "var(--bw-surface-2)",
    borderBottom: collapsed ? "none" : "1px solid var(--bw-border)",
    cursor: "pointer",
    userSelect: "none",
  };
  const dotStyle: React.CSSProperties = {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: latest.done ? "#7ED4A6" : "var(--bw-accent)",
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
                borderBottom: "1px solid var(--bw-border)",
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{
                  color: TRIGGER_COLOR[e.trigger] || "var(--bw-ink-muted)",
                  fontWeight: 600,
                }}>{e.trigger}</span>
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
                    color: "var(--bw-ink-muted)",
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
                    <div key={i} style={{ color: "var(--bw-accent)", fontSize: 10.5 }}>
                      → {tc.name}
                      {tc.arguments && Object.keys(tc.arguments).length > 0 ? (
                        <span style={{ color: "var(--bw-ink-faint)" }}>
                          ({Object.keys(tc.arguments).join(", ")})
                        </span>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
              {e.text ? (
                <div
                  style={{
                    marginTop: 6,
                    color: "var(--bw-ink-muted)",
                    fontFamily: "var(--bw-font-serif)",
                    fontStyle: "italic",
                    fontSize: 11,
                    lineHeight: 1.5,
                  }}
                >
                  {e.text.length > 200 ? e.text.slice(0, 200) + "…" : e.text}
                </div>
              ) : (e.done && e.toolCalls.length === 0) ? (
                // Reception confirmed, no text, no tool calls — the persona
                // ran the turn and chose to stay silent (the default for
                // user_speech per skills/respond_to_speech.md). Render an
                // explicit marker so the user can read this as a deliberate
                // decision rather than an empty/broken entry.
                <div
                  style={{
                    marginTop: 6,
                    color: "var(--bw-ink-faint)",
                    fontFamily: "var(--bw-font-serif)",
                    fontStyle: "italic",
                    fontSize: 11,
                    lineHeight: 1.5,
                  }}
                >
                  (silent — no response)
                </div>
              ) : null}
              {(e.model || e.promptTokens != null || e.latencyMs != null) && (
                <div style={{
                  marginTop: 6,
                  color: "var(--bw-ink-faint)",
                  fontSize: 10,
                  display: "flex",
                  gap: 8,
                  flexWrap: "wrap",
                }}>
                  {e.model && <span>{e.provider}/{e.model}</span>}
                  {e.promptTokens != null && <span>· {fmtTokens(e.promptTokens)} in</span>}
                  {e.completionTokens != null && <span>· {fmtTokens(e.completionTokens)} out</span>}
                  {e.latencyMs != null && <span>· {fmtLatency(e.latencyMs)}</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
