"use client";

import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { askStream, type AskRequest } from "@/lib/api";
import { logEvent } from "@/lib/eventLog";

// Bottom-fixed command bar for the dedicated /canvas page. Visual style
// mirrors block-canvas/components/CommandBar.tsx (the PoC). On submit,
// hits the existing /api/ask/stream — the teacher's intent router (or the
// frontend_engineer bypass) handles delegation. Block updates land via
// the SSE channel that DynamicSurface already owns.

type Addressee = NonNullable<AskRequest["addressee"]>;
type Sender = "user" | "teacher";

const ADDRESSEE_KEY = "bewithme_canvas_addressee";
// One-shot handoff: a recommendation's "Start Learning" stashes a seed
// question here, then routes to the Reader. Consumed (and cleared) on mount.
const SEED_KEY = "bewithme_canvas_seed";

// Only two valid pairs are reachable: user→teacher (normal flow, runs the
// LLM intent router) and teacher→frontend_engineer (test mode, bypasses
// the router). The two selects are linked — changing one snaps the other
// to its only valid partner.
const FROM_OPTIONS: { value: Sender; label: string }[] = [
  { value: "user", label: "user" },
  { value: "teacher", label: "teacher" },
];

const TO_OPTIONS: { value: Addressee; label: string }[] = [
  { value: "teacher", label: "teacher" },
  { value: "frontend_engineer", label: "frontend_engineer" },
];

function senderForAddressee(a: Addressee): Sender {
  return a === "teacher" ? "user" : "teacher";
}

function addresseeForSender(s: Sender): Addressee {
  return s === "user" ? "teacher" : "frontend_engineer";
}

function renderDebugLines(text: string) {
  const lines = text.split(/\r?\n/);
  return lines.map((line, i) => {
    const isPrompt = line.startsWith(">");
    return (
      <div key={i} style={{ color: isPrompt ? "var(--bw-accent)" : "var(--bw-ink)" }}>
        {line || " "}
      </div>
    );
  });
}

function selectStyle(isTestMode: boolean): CSSProperties {
  return {
    padding: "4px 6px",
    fontSize: 12,
    fontFamily: "inherit",
    background: "transparent",
    color: isTestMode ? "#E5C36F" : "var(--bw-ink-muted)",
    border: "1px solid var(--bw-border)",
    borderRadius: 0,
    cursor: "pointer",
    outline: "none",
  };
}

export default function CanvasCommandBar() {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addressee, setAddressee] = useState<Addressee>("teacher");
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugText, setDebugText] = useState("");
  const sessionIdRef = useRef<string>("");
  const debugScrollRef = useRef<HTMLDivElement>(null);

  // Hydrate addressee from localStorage on mount, persist on change.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(ADDRESSEE_KEY);
    if (stored === "teacher" || stored === "frontend_engineer") {
      setAddressee(stored);
    }
  }, []);
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(ADDRESSEE_KEY, addressee);
  }, [addressee]);

  // Consume a one-shot seed left by a recommendation's "Start Learning".
  // Prefill the bar so the user lands ready to ask; we do NOT auto-send —
  // they review and press Enter, keeping the user in control.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const seed = window.localStorage.getItem(SEED_KEY);
    if (seed) {
      window.localStorage.removeItem(SEED_KEY);
      setValue(seed);
    }
  }, []);

  // One session id per page mount.
  if (!sessionIdRef.current && typeof window !== "undefined") {
    sessionIdRef.current = crypto.randomUUID();
  }

  // Keep error transient.
  useEffect(() => {
    if (!error) return;
    const t = window.setTimeout(() => setError(null), 6000);
    return () => window.clearTimeout(t);
  }, [error]);

  // Pin the debug stream to the bottom as new tokens arrive.
  useEffect(() => {
    const el = debugScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [debugText, debugOpen]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const command = value.trim();
    if (!command || busy) return;
    setBusy(true);
    setError(null);
    setDebugText(`> ${addressee === "frontend_engineer" ? "engineer" : "teacher"} ← ${command}\n`);
    const submitT0 = performance.now();
    logEvent("ui.ask.submit", {
      modality: "text",
      surface: "canvas_command_bar",
      addressee,
      question_len: command.length,
      session_id: sessionIdRef.current,
    });
    try {
      await askStream(
        {
          question: command,
          passage_text: "",
          session_id: sessionIdRef.current,
          addressee,
        },
        // Surface the LLM's streaming output into the debug panel —
        // the canvas page has no AnswerDrawer, so this is the only
        // way to see what the model is "thinking". Block deliveries
        // still flow through DynamicSurface's separate SSE channel.
        (event) => {
          if (event.type === "status") {
            const detail = event.detail ? `: ${event.detail}` : "";
            setDebugText((prev) => `${prev}\n> ${event.status}${detail}\n`);
          } else if (event.type === "title") {
            setDebugText((prev) => `${prev}\n> title: ${event.title}\n`);
          } else if (event.type === "token") {
            setDebugText((prev) => prev + event.text);
          } else if (event.type === "answer") {
            setDebugText((prev) => `${prev}\n> done\n`);
            logEvent("ui.ask.answer", {
              surface: "canvas_command_bar",
              session_id: sessionIdRef.current,
              wall_ms: Math.round(performance.now() - submitT0),
              answer_len: event.answer?.length ?? 0,
              title: event.title ?? null,
            });
          }
        },
      );
      setValue("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDebugText((prev) => `${prev}\n> error: ${err instanceof Error ? err.message : String(err)}\n`);
      logEvent("ui.ask.error", {
        surface: "canvas_command_bar",
        session_id: sessionIdRef.current,
        error: err instanceof Error ? err.message : String(err),
        wall_ms: Math.round(performance.now() - submitT0),
      });
    } finally {
      setBusy(false);
    }
  }

  const isTestMode = addressee === "frontend_engineer";

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        justifyContent: "flex-end",
        padding: "0 16px 12px",
        // Form/debug stack pushes against the bottom of the cell. The
        // shell handles grid placement; here we only do internal layout.
      }}
    >
      {error && (
        <div
          style={{
            background: "rgba(229,131,124,0.18)",
            color: "#E5837C",
            border: "1px solid rgba(229,131,124,0.5)",
            padding: "8px 12px",
            marginBottom: 8,
            borderRadius: 0,
            fontFamily: "var(--bw-font-mono)",
            fontSize: 12,
            whiteSpace: "pre-wrap",
          }}
        >
          {error}
        </div>
      )}
      <div
        style={{
          height: debugOpen ? 280 : 0,
          opacity: debugOpen ? 1 : 0,
          marginBottom: debugOpen ? 8 : 0,
          pointerEvents: debugOpen ? "auto" : "none",
          transition: "height 180ms ease, opacity 180ms ease, margin 180ms ease",
          background: "var(--bw-surface)",
          border: "1px solid var(--bw-border)",
          borderRadius: 0,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          fontFamily: "var(--bw-font-mono)",
          fontSize: 12,
        }}
      >
        <div
          style={{
            padding: "8px 14px",
            borderBottom: "1px solid var(--bw-border)",
            background: "var(--bw-surface-2)",
            color: "var(--bw-ink-muted)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontSize: 10,
          }}
        >
          <span>
            llm thinking {busy ? "• streaming" : ""}
          </span>
          <span style={{ color: busy ? "var(--bw-accent)" : "var(--bw-ink-faint)" }}>
            {busy ? "●" : "○"}
          </span>
        </div>
        <div
          ref={debugScrollRef}
          style={{
            flex: 1,
            padding: "10px 14px",
            overflowY: "auto",
            color: "var(--bw-ink)",
            whiteSpace: "pre-wrap",
            lineHeight: 1.5,
          }}
        >
          {debugText ? (
            renderDebugLines(debugText)
          ) : (
            <span style={{ color: "var(--bw-ink-faint)" }}>
              (the LLM&apos;s tokens stream here when you run a command)
            </span>
          )}
        </div>
      </div>
      <form
        onSubmit={handleSubmit}
        style={{
          display: "flex",
          alignItems: "stretch",
          gap: 8,
          background: "var(--bw-surface)",
          borderRadius: 0,
          border: `1px solid ${isTestMode ? "#E5C36F" : "var(--bw-border)"}`,
          padding: 6,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            paddingLeft: 8,
            paddingRight: 4,
            fontSize: 11,
            color: isTestMode ? "#E5C36F" : "var(--bw-ink-muted)",
            fontFamily: "var(--bw-font-mono)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
          title="Routing override. user → teacher runs the LLM router. teacher → frontend_engineer bypasses it for testing."
        >
          <select
            aria-label="from"
            value={senderForAddressee(addressee)}
            onChange={(e) => setAddressee(addresseeForSender(e.target.value as Sender))}
            style={selectStyle(isTestMode)}
          >
            {FROM_OPTIONS.map((o) => (
              <option key={o.value} value={o.value} style={{ background: "#12121E" }}>
                {o.label}
              </option>
            ))}
          </select>
          <span aria-hidden="true">→</span>
          <select
            aria-label="to"
            value={addressee}
            onChange={(e) => setAddressee(e.target.value as Addressee)}
            style={selectStyle(isTestMode)}
          >
            {TO_OPTIONS.map((o) => (
              <option key={o.value} value={o.value} style={{ background: "#12121E" }}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={busy}
          placeholder={
            busy
              ? "Thinking…"
              : isTestMode
                ? "Test mode: command goes straight to frontend_engineer"
                : 'Ask the teacher (try: "upload a paper and read it")'
          }
          style={{
            flex: 1,
            padding: "10px 14px",
            fontSize: 14,
            fontFamily: "var(--bw-font-sans)",
            borderRadius: 0,
            border: "1px solid var(--bw-border)",
            background: "var(--bw-void-2)",
            color: "var(--bw-ink)",
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={() => setDebugOpen((o) => !o)}
          title={debugOpen ? "Hide LLM thinking panel" : "Show LLM thinking panel"}
          style={{
            width: 44,
            borderRadius: 0,
            border: "1px solid var(--bw-border)",
            background: debugOpen ? "var(--bw-accent-soft)" : "transparent",
            color: debugOpen ? "var(--bw-accent)" : "var(--bw-ink-muted)",
            fontFamily: "var(--bw-font-mono)",
            fontSize: 16,
            cursor: "pointer",
          }}
        >
          {">_"}
        </button>
      </form>
    </div>
  );
}
