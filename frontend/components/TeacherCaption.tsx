"use client";

// TeacherCaption — fixed-position floating caption, like YouTube CC.
//
// Subscribes to the dynamic SSE stream and renders BlockMessage events on
// `topic === "teacher-speech.text"` as a borderless, always-on-top caption
// near the bottom of the viewport.
//
// Behaviour:
//   * Long utterances are chunked into ~one-line cues (sentence-aware).
//   * At most TWO cues are on screen at any moment: the previous cue at
//     the top (held, dimmed) and the current cue at the bottom revealing
//     left-to-right at reading speed.
//   * When the current cue finishes, the previous cue drops off, the
//     current cue shifts up to the previous slot, and the next queued
//     cue starts revealing in the bottom slot.
//   * Queue empty + dwell elapsed → the strip fades out.
//
// Mounted globally from `app/layout.tsx`, alongside SpeakerSink.

import { useEffect, useRef, useState } from "react";

import { subscribeToDynamicStream } from "@/lib/api";

// Comfortable English reading pace. Slightly above conversational speech
// so the caption doesn't drag behind a normal speak() audio track.
const READING_CHARS_PER_SEC = 22;
// Minimum reveal time so very short cues don't flash by.
const MIN_REVEAL_MS = 600;
// Hold the strip this long after the last cue finishes before fading.
const FINAL_DWELL_MS = 1500;
// Fade duration applied to the whole strip on drain end.
const FADE_OUT_MS = 350;
// Target chunk size — sized to fit on a single wrapped row at the
// caption's font size (see maxWidth + fontSize below). Picked
// conservatively so naturally-occurring chunks don't wrap.
const MAX_CHARS_PER_CUE = 60;

type Cue = { id: number; text: string; revealMs: number };

function chunkText(input: string, maxChars: number = MAX_CHARS_PER_CUE): string[] {
  const normalized = input.replace(/\s+/g, " ").trim();
  if (!normalized) return [];

  const out: string[] = [];
  // Sentence-first split. Keep punctuation by matching "[^.!?]+[.!?]+".
  const sentences = normalized.match(/[^.!?]+[.!?]+|\S[^.!?]*$/g) ?? [normalized];

  for (const raw of sentences) {
    const s = raw.trim();
    if (!s) continue;
    if (s.length <= maxChars) {
      out.push(s);
      continue;
    }
    // Long sentence — split at word boundaries up to maxChars.
    let buf = "";
    const words = s.split(/(\s+)/); // keeps separators so we can reassemble
    for (const w of words) {
      if ((buf + w).length > maxChars && buf.trim().length > 0) {
        out.push(buf.trim());
        buf = /^\s+$/.test(w) ? "" : w;
      } else {
        buf += w;
      }
    }
    if (buf.trim()) out.push(buf.trim());
  }
  return out;
}

function revealDurationMs(text: string): number {
  return Math.max(
    MIN_REVEAL_MS,
    Math.round((text.length / READING_CHARS_PER_SEC) * 1000),
  );
}

export default function TeacherCaption() {
  // Top slot: previous cue, fully revealed and dimmed.
  // Bottom slot: current cue, animating L→R.
  const [oldCue, setOldCue] = useState<Cue | null>(null);
  const [newCue, setNewCue] = useState<Cue | null>(null);
  const [visible, setVisible] = useState<boolean>(false);

  // Refs keep the pump/timeout closures fresh without re-binding handlers.
  const newCueRef = useRef<Cue | null>(null);
  const queueRef = useRef<string[]>([]);
  const idCounter = useRef<number>(0);
  const advanceTimerRef = useRef<number | null>(null);
  const dwellTimerRef = useRef<number | null>(null);
  const unmountTimerRef = useRef<number | null>(null);

  useEffect(() => {
    function clearDwellTimers() {
      if (dwellTimerRef.current != null) {
        window.clearTimeout(dwellTimerRef.current);
        dwellTimerRef.current = null;
      }
      if (unmountTimerRef.current != null) {
        window.clearTimeout(unmountTimerRef.current);
        unmountTimerRef.current = null;
      }
    }

    function pump() {
      // Re-entrant from two paths: (a) BlockMessage just landed; (b) the
      // current cue's reveal-timer just fired. If the current reveal is
      // still in flight, do nothing — its timer will call us back.
      if (advanceTimerRef.current != null) return;

      const next = queueRef.current.shift();
      if (!next) {
        // Queue drained. Hold the strip a beat, then fade.
        clearDwellTimers();
        dwellTimerRef.current = window.setTimeout(() => {
          setVisible(false);
          unmountTimerRef.current = window.setTimeout(() => {
            setOldCue(null);
            setNewCue(null);
            newCueRef.current = null;
          }, FADE_OUT_MS);
        }, FINAL_DWELL_MS);
        return;
      }

      // New content cancels any pending fade.
      clearDwellTimers();

      // Promote: previous current → top slot, new chunk → bottom slot.
      setOldCue(newCueRef.current);

      const cue: Cue = {
        id: ++idCounter.current,
        text: next,
        revealMs: revealDurationMs(next),
      };
      setNewCue(cue);
      newCueRef.current = cue;
      setVisible(true);

      advanceTimerRef.current = window.setTimeout(() => {
        advanceTimerRef.current = null;
        pump();
      }, cue.revealMs);
    }

    const ctrl = new AbortController();
    subscribeToDynamicStream((event) => {
      if (event.type !== "block-data") return;
      if (event.topic !== "teacher-speech.text") return;
      const value = event.value as unknown;
      let raw = "";
      if (typeof value === "string") {
        raw = value;
      } else if (
        value &&
        typeof value === "object" &&
        typeof (value as { text?: unknown }).text === "string"
      ) {
        raw = (value as { text: string }).text;
      }
      const chunks = chunkText(raw);
      if (chunks.length === 0) return;
      queueRef.current.push(...chunks);
      pump();
    }, ctrl.signal).catch((err) => {
      if (err?.name !== "AbortError") {
        console.warn("[teacher-caption] stream ended", err);
      }
    });

    return () => {
      ctrl.abort();
      if (advanceTimerRef.current != null) {
        window.clearTimeout(advanceTimerRef.current);
        advanceTimerRef.current = null;
      }
      clearDwellTimers();
    };
  }, []);

  if (!oldCue && !newCue) return null;

  return (
    <>
      <style jsx>{`
        @keyframes teacher-caption-reveal {
          from {
            clip-path: inset(0 100% 0 0);
          }
          to {
            clip-path: inset(0 0 0 0);
          }
        }
        @keyframes teacher-caption-old-in {
          from {
            opacity: 0;
            transform: translateY(4px);
          }
          to {
            opacity: 0.55;
            transform: translateY(0);
          }
        }
      `}</style>
      <div
        aria-live="polite"
        style={{
          position: "fixed",
          left: "50%",
          bottom: "7vh",
          transform: "translateX(-50%)",
          maxWidth: "min(70ch, 86vw)",
          padding: "10px 18px",
          background: "rgba(8, 10, 18, 0.78)",
          color: "#fff",
          fontFamily: "var(--font-onest), var(--bw-font-sans, system-ui)",
          fontWeight: 600,
          fontSize: "clamp(17px, 1.8vw, 22px)",
          lineHeight: 1.35,
          letterSpacing: "-0.005em",
          textAlign: "center",
          textShadow: "0 1px 2px rgba(0,0,0,0.6)",
          backdropFilter: "blur(6px)",
          WebkitBackdropFilter: "blur(6px)",
          pointerEvents: "none",
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: "2px",
          opacity: visible ? 1 : 0,
          transition: `opacity ${FADE_OUT_MS}ms ease-out`,
        }}
      >
        {oldCue && (
          <div
            key={`old-${oldCue.id}`}
            style={{
              opacity: 0.55,
              animation: `teacher-caption-old-in 220ms ease-out backwards`,
            }}
          >
            {oldCue.text}
          </div>
        )}
        {newCue && (
          <div
            key={`new-${newCue.id}`}
            style={{
              clipPath: "inset(0 100% 0 0)",
              animation: `teacher-caption-reveal ${newCue.revealMs}ms linear forwards`,
            }}
          >
            {newCue.text}
          </div>
        )}
      </div>
    </>
  );
}
