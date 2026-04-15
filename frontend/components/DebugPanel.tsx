"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getPreferences,
  distillPreferences,
  getConcepts,
  type Preferences,
  type Concept,
  type DebugEvent,
} from "@/lib/api";
import KnowledgeGraph from "./KnowledgeGraph";

const PREF_LABELS: Record<string, string> = {
  explanation_style: "Explanation Style",
  depth_preference: "Depth",
  analogy_affinity: "Analogy Use",
  math_comfort: "Math Comfort",
  pacing: "Pacing",
};

const STATE_COLORS: Record<string, string> = {
  mastered: "bg-green-500",
  understood: "bg-blue-500",
  learning: "bg-yellow-500",
  new: "bg-gray-400",
  dormant: "bg-gray-600",
};

export default function DebugPanel({
  open,
  onClose,
  lastDebug,
  promptVersion = "v1",
  onPromptVersionChange,
}: {
  open: boolean;
  onClose: () => void;
  lastDebug?: DebugEvent | null;
  promptVersion?: "v1" | "v2";
  onPromptVersionChange?: (v: "v1" | "v2") => void;
}) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [distilling, setDistilling] = useState(false);
  const [tab, setTab] = useState<"prefs" | "concepts" | "graph" | "llm">("prefs");
  const [graphKey, setGraphKey] = useState(0);

  const loadData = useCallback(() => {
    getPreferences().then(setPrefs).catch(() => setPrefs(null));
    getConcepts().then(setConcepts).catch(() => setConcepts([]));
  }, []);

  useEffect(() => {
    if (open) loadData();
  }, [open, loadData]);

  async function handleDistill() {
    setDistilling(true);
    try {
      const result = await distillPreferences();
      setPrefs(result);
    } catch {
      // ignore
    } finally {
      setDistilling(false);
    }
  }

  // Group concepts by state
  const byState: Record<string, Concept[]> = {};
  for (const c of concepts) {
    (byState[c.state] ||= []).push(c);
  }

  return (
    <div
      className={`fixed top-0 left-0 h-full bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 shadow-xl z-30 transition-all duration-300 ease-in-out ${
        tab === "graph" ? "w-[60vw]" : tab === "llm" ? "w-[45vw]" : "w-80"
      } ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 px-4 py-3">
        <div className="flex gap-1">
          <button
            onClick={() => setTab("prefs")}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-colors ${
              tab === "prefs"
                ? "bg-purple-100 dark:bg-purple-900 text-purple-700 dark:text-purple-300"
                : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            }`}
          >
            Preferences
          </button>
          <button
            onClick={() => { setTab("concepts"); loadData(); }}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-colors ${
              tab === "concepts"
                ? "bg-purple-100 dark:bg-purple-900 text-purple-700 dark:text-purple-300"
                : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            }`}
          >
            Concepts ({concepts.length})
          </button>
          <button
            onClick={() => { setTab("graph"); setGraphKey((k) => k + 1); }}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-colors ${
              tab === "graph"
                ? "bg-purple-100 dark:bg-purple-900 text-purple-700 dark:text-purple-300"
                : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            }`}
          >
            Graph
          </button>
          <button
            onClick={() => setTab("llm")}
            className={`px-3 py-1 text-xs font-medium rounded-full transition-colors ${
              tab === "llm"
                ? "bg-purple-100 dark:bg-purple-900 text-purple-700 dark:text-purple-300"
                : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            }`}
          >
            LLM
          </button>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Content */}
      <div className="overflow-y-auto h-[calc(100%-3rem)] px-4 py-3">
        {tab === "prefs" && (
          <div className="space-y-4">
            {/* Prompt version toggle */}
            {onPromptVersionChange && (
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
                  Prompt Version
                </p>
                <div className="flex gap-1">
                  {(["v1", "v2"] as const).map((v) => (
                    <button
                      key={v}
                      onClick={() => onPromptVersionChange(v)}
                      className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                        promptVersion === v
                          ? "bg-purple-600 text-white"
                          : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
                      }`}
                    >
                      {v.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <button
              onClick={handleDistill}
              disabled={distilling}
              className="w-full rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white hover:bg-purple-700 disabled:opacity-50 transition-colors"
            >
              {distilling ? "Distilling..." : "Distill Now"}
            </button>

            {prefs ? (
              <div className="space-y-3">
                {Object.entries(PREF_LABELS).map(([key, label]) => (
                  <div key={key}>
                    <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                      {label}
                    </p>
                    <p className="text-sm mt-0.5">
                      {(prefs as Record<string, unknown>)[key] as string}
                    </p>
                  </div>
                ))}
                {prefs.meta_notes && (
                  <div>
                    <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Notes</p>
                    <p className="text-sm mt-0.5 text-gray-600 dark:text-gray-300">{prefs.meta_notes}</p>
                  </div>
                )}
                <div className="border-t border-gray-100 dark:border-gray-800 pt-2">
                  <p className="text-xs text-gray-400">
                    Based on {prefs.interaction_count} interactions
                  </p>
                </div>
              </div>
            ) : (
              <p className="text-sm text-gray-400">No preferences yet.</p>
            )}
          </div>
        )}

        {tab === "concepts" && (
          <div className="space-y-3">
            {concepts.length === 0 ? (
              <p className="text-sm text-gray-400">No concepts extracted yet. Ask some questions first.</p>
            ) : (
              <>
                {/* Legend */}
                <div className="flex flex-wrap gap-2 text-xs">
                  {["mastered", "understood", "learning", "new"].map((s) => (
                    <span key={s} className="flex items-center gap-1">
                      <span className={`inline-block w-2 h-2 rounded-full ${STATE_COLORS[s]}`} />
                      {s} ({byState[s]?.length || 0})
                    </span>
                  ))}
                </div>

                {/* Concept list by state */}
                {["mastered", "understood", "learning", "new"].map((state) =>
                  byState[state]?.length ? (
                    <div key={state}>
                      <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1">
                        {state}
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {byState[state].map((c) => (
                          <span
                            key={c.id}
                            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs ${
                              state === "mastered"
                                ? "bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300"
                                : state === "understood"
                                  ? "bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300"
                                  : state === "learning"
                                    ? "bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300"
                                    : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400"
                            }`}
                            title={`Seen ${c.encounter_count} times`}
                          >
                            {c.name}
                            <span className="text-[10px] opacity-60">{c.encounter_count}</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null
                )}
              </>
            )}
          </div>
        )}

        {tab === "graph" && (
          <div className="h-full -mx-4 -my-3">
            <KnowledgeGraph refreshKey={graphKey} />
          </div>
        )}

        {tab === "llm" && <LlmTab debug={lastDebug ?? null} />}
      </div>
    </div>
  );
}

function LlmTab({ debug }: { debug: DebugEvent | null }) {
  if (!debug) {
    return (
      <p className="text-sm text-gray-400">
        Ask a question to see the exact prompt sent to the LLM and the token accounting.
      </p>
    );
  }

  const u = debug.usage;
  const totalInput = u.input_tokens + u.cache_creation_input_tokens + u.cache_read_input_tokens;
  const cacheHitPct = totalInput > 0 ? Math.round((u.cache_read_input_tokens / totalInput) * 100) : 0;
  // Rough 4 chars ≈ 1 token heuristic for UI only — provider usage is the source of truth.
  const approxTokens = (s: string) => Math.round(s.length / 4);

  return (
    <div className="space-y-4 text-sm">
      {/* Token usage summary */}
      <section className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">
          Token usage
        </h3>
        <div className="grid grid-cols-2 gap-y-1 text-xs">
          <span className="text-gray-500">Input (fresh)</span>
          <span className="font-mono text-right">{u.input_tokens}</span>
          <span className="text-gray-500">Cache write</span>
          <span className="font-mono text-right">{u.cache_creation_input_tokens}</span>
          <span className="text-gray-500">Cache read</span>
          <span className="font-mono text-right text-green-600 dark:text-green-400">
            {u.cache_read_input_tokens}
          </span>
          <span className="text-gray-500">Output</span>
          <span className="font-mono text-right">{u.output_tokens}</span>
          <span className="text-gray-500 border-t border-gray-200 dark:border-gray-700 pt-1 mt-1">
            Total input
          </span>
          <span className="font-mono text-right border-t border-gray-200 dark:border-gray-700 pt-1 mt-1">
            {totalInput}
          </span>
          <span className="text-gray-500">Cache hit rate</span>
          <span className="font-mono text-right">{cacheHitPct}%</span>
        </div>
        {u.cache_creation_input_tokens === 0 && u.cache_read_input_tokens === 0 && (
          <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">
            Provider reported 0 cached tokens — either the static prefix is too small to cache,
            or this endpoint doesn&apos;t honor <code>cache_control</code>.
          </p>
        )}
      </section>

      {/* Static system (cached) */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1 flex items-center gap-2">
          Static system
          <span className="rounded bg-green-100 dark:bg-green-900 px-1.5 py-0.5 text-[10px] font-normal text-green-700 dark:text-green-300">
            cached · ~{approxTokens(debug.static_system)}t · {debug.static_system.length} chars
          </span>
        </h3>
        <pre className="whitespace-pre-wrap break-words rounded bg-gray-50 dark:bg-gray-800 p-2 text-[11px] leading-snug font-mono max-h-60 overflow-y-auto">
          {debug.static_system || "(empty)"}
        </pre>
      </section>

      {/* Static user passage (cached) */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1 flex items-center gap-2">
          Static user (passage)
          <span className="rounded bg-green-100 dark:bg-green-900 px-1.5 py-0.5 text-[10px] font-normal text-green-700 dark:text-green-300">
            cached · ~{approxTokens(debug.static_user_passage)}t · {debug.static_user_passage.length} chars
          </span>
        </h3>
        <pre className="whitespace-pre-wrap break-words rounded bg-gray-50 dark:bg-gray-800 p-2 text-[11px] leading-snug font-mono max-h-40 overflow-y-auto">
          {debug.static_user_passage || "(no passage)"}
        </pre>
      </section>

      {/* Dynamic user (not cached) */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1 flex items-center gap-2">
          Dynamic user
          <span className="rounded bg-orange-100 dark:bg-orange-900 px-1.5 py-0.5 text-[10px] font-normal text-orange-700 dark:text-orange-300">
            fresh · ~{approxTokens(debug.dynamic_user)}t · {debug.dynamic_user.length} chars
          </span>
        </h3>
        <pre className="whitespace-pre-wrap break-words rounded bg-gray-50 dark:bg-gray-800 p-2 text-[11px] leading-snug font-mono max-h-96 overflow-y-auto">
          {debug.dynamic_user || "(empty)"}
        </pre>
      </section>
    </div>
  );
}
