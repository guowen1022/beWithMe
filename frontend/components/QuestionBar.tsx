"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { transcribeAudio } from "@/lib/api";
import { createMicVad, type MicVadHandle } from "@/lib/vad";
import * as micArbiter from "@/lib/micArbiter";
import { logEvent } from "@/lib/eventLog";

export default function QuestionBar({
  selectedText,
  onAsk,
  loading,
  onClearSelection,
  drawerOpen,
  debugOpen,
  treePanelOpen = false,
  recordTrigger,
}: {
  selectedText: string;
  onAsk: (question: string) => void;
  loading: boolean;
  onClearSelection: () => void;
  drawerOpen: boolean;
  debugOpen: boolean;
  treePanelOpen?: boolean;
  recordTrigger: number;
}) {
  const [question, setQuestion] = useState("");
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(false);

  const vadRef = useRef<MicVadHandle | null>(null);
  const pendingRef = useRef<Set<Promise<void>>>(new Set());
  // Committed phrases (already finalized by VAD end-of-speech).
  const committedRef = useRef("");
  // Live interim for the currently-open phrase. Replaced on every rolling
  // transcribe; cleared when the phrase commits.
  const interimRef = useRef("");
  const activePhraseRef = useRef(0);
  // Serialize interim POSTs — the backend whisper lock serializes anyway,
  // so there's no benefit to fanning out. One-at-a-time keeps latency tight.
  const interimInFlightRef = useRef(false);
  const autoSendRef = useRef(false);
  const onAskRef = useRef(onAsk);
  const questionRef = useRef(question);
  const listeningRef = useRef(listening);
  // Tracks whether the current value in the input box came from voice
  // (mic → whisper) so the submit event can tag modality correctly.
  // Set when listening starts; cleared when the user types or cancels.
  const wasVoiceRef = useRef(false);
  const recordStartTsRef = useRef<number | null>(null);

  const renderQuestion = useCallback(() => {
    const c = committedRef.current;
    const i = interimRef.current;
    if (c && i) return `${c} ${i}`;
    return c || i;
  }, []);

  useEffect(() => { onAskRef.current = onAsk; }, [onAsk]);
  useEffect(() => { questionRef.current = question; }, [question]);
  useEffect(() => { listeningRef.current = listening; }, [listening]);

  useEffect(() => {
    const ok =
      typeof window !== "undefined" &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof AudioWorkletNode !== "undefined";
    setSpeechSupported(ok);
  }, []);

  useEffect(() => {
    if (listening || transcribing) {
      document.body.style.cursor =
        'url("data:image/svg+xml,<svg xmlns=%27http://www.w3.org/2000/svg%27 width=%2732%27 height=%2732%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27%23ef4444%27 stroke-width=%272%27><circle cx=%2712%27 cy=%2712%27 r=%2710%27 fill=%27%23fca5a5%27/><circle cx=%2712%27 cy=%2712%27 r=%275%27 fill=%27%23ef4444%27/></svg>") 16 16, pointer';
    } else {
      document.body.style.cursor = "";
    }
    return () => { document.body.style.cursor = ""; };
  }, [listening, transcribing]);

  // Rolling interim during a phrase: show a best-guess transcript as the
  // user speaks. Dropped if the phrase commits before it lands.
  const handleInterim = useCallback((wav: Blob, phraseId: number) => {
    if (interimInFlightRef.current) return;
    if (phraseId !== activePhraseRef.current) return;
    interimInFlightRef.current = true;
    const prompt = committedRef.current.slice(-150);
    (async () => {
      try {
        const { text } = await transcribeAudio(wav, "auto", prompt);
        if (phraseId !== activePhraseRef.current) return;
        const clean = text.trim();
        interimRef.current = clean;
        setQuestion(renderQuestion());
      } catch (err) {
        console.warn("interim transcribe failed", err);
      } finally {
        interimInFlightRef.current = false;
      }
    })();
  }, [renderQuestion]);

  // Final clean transcribe on VAD end-of-speech; becomes the committed text
  // for that phrase and supersedes any interim.
  const handlePhrase = useCallback((wav: Blob, phraseId: number) => {
    const prompt = committedRef.current.slice(-150);
    const task = (async () => {
      try {
        const { text } = await transcribeAudio(wav, "auto", prompt);
        const clean = text.trim();
        // Only clear the interim for *this* phrase — a later phrase may
        // already be producing its own interim by the time we land.
        if (phraseId === activePhraseRef.current) {
          interimRef.current = "";
        }
        if (clean) {
          committedRef.current = committedRef.current
            ? `${committedRef.current} ${clean}`
            : clean;
        }
        setQuestion(renderQuestion());
      } catch (err) {
        console.warn("phrase transcribe failed", err);
      }
    })();
    pendingRef.current.add(task);
    task.finally(() => pendingRef.current.delete(task));
  }, [renderQuestion]);

  const teardownVad = useCallback(() => {
    const vad = vadRef.current;
    vadRef.current = null;
    if (vad) {
      try { vad.destroy(); } catch { /* noop */ }
    }
    // Hand the mic back so the ambient_mic block (if mounted) can resume.
    micArbiter.release("questionbar");
  }, []);

  const startListening = useCallback(async () => {
    if (!speechSupported || vadRef.current) return;
    try {
      // Preempt the ambient_mic block (if any) — push-to-talk is an
      // explicit user gesture and always wins.
      micArbiter.acquire("questionbar");

      committedRef.current = "";
      interimRef.current = "";
      activePhraseRef.current = 0;
      interimInFlightRef.current = false;
      pendingRef.current.clear();
      autoSendRef.current = false;
      setQuestion("");

      const vad = await createMicVad({
        onSpeechStart: (phraseId) => {
          activePhraseRef.current = phraseId;
        },
        onInterim: handleInterim,
        onPhrase: handlePhrase,
        onError: (err) => console.warn("vad error", err),
      });
      vadRef.current = vad;
      vad.start();
      setListening(true);
      wasVoiceRef.current = true;
      recordStartTsRef.current = performance.now();
      logEvent("ui.voice.record_start", { surface: "question_bar" });
    } catch (err) {
      console.error("mic/vad start failed", err);
      teardownVad();
      setListening(false);
      logEvent("ui.voice.record_error", {
        surface: "question_bar",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, [speechSupported, handleInterim, handlePhrase, teardownVad]);

  // Stop VAD and, once any in-flight phrase POSTs drain, auto-send if asked.
  const finalizeListening = useCallback(async (autoSend: boolean) => {
    if (!vadRef.current) return;
    teardownVad();
    setListening(false);

    const inflight = Array.from(pendingRef.current);
    if (inflight.length > 0) {
      setTranscribing(true);
      try { await Promise.allSettled(inflight); } finally { setTranscribing(false); }
    }

    // Bump phraseId so any stale interim response gets dropped instead of
    // overwriting the committed text.
    activePhraseRef.current += 1;
    interimRef.current = "";
    setQuestion(renderQuestion());

    const final = committedRef.current.trim();
    const recordMs = recordStartTsRef.current
      ? Math.round(performance.now() - recordStartTsRef.current)
      : null;
    recordStartTsRef.current = null;
    logEvent("ui.voice.record_end", {
      surface: "question_bar",
      auto_send: autoSend,
      committed_len: final.length,
      record_ms: recordMs,
      inflight_phrases: inflight.length,
    });
    if (autoSend && final) onAskRef.current(final);
  }, [teardownVad, renderQuestion]);

  const cancelRecording = useCallback(() => {
    teardownVad();
    pendingRef.current.clear();
    committedRef.current = "";
    interimRef.current = "";
    activePhraseRef.current += 1;
    interimInFlightRef.current = false;
    setQuestion("");
    setListening(false);
    const recordMs = recordStartTsRef.current
      ? Math.round(performance.now() - recordStartTsRef.current)
      : null;
    recordStartTsRef.current = null;
    wasVoiceRef.current = false;
    logEvent("ui.voice.record_cancel", {
      surface: "question_bar",
      record_ms: recordMs,
    });
    setTranscribing(false);
  }, [teardownVad]);

  const sendQuestion = useCallback(() => {
    if (loading) return;
    if (listeningRef.current) {
      autoSendRef.current = true;
      logEvent("ui.ask.submit", {
        modality: "voice",
        surface: "question_bar",
        via: "finalize_listening",
      });
      void finalizeListening(true);
      return;
    }
    const q = questionRef.current.trim();
    if (!q) return;
    logEvent("ui.ask.submit", {
      modality: wasVoiceRef.current ? "voice" : "text",
      surface: "question_bar",
      question_len: q.length,
    });
    wasVoiceRef.current = false;
    onAskRef.current(q);
    setQuestion("");
  }, [loading, finalizeListening]);

  // Auto-start recording when text is selected (driven by Reader's recordTrigger).
  useEffect(() => {
    if (recordTrigger > 0 && speechSupported && !listening && !transcribing && !loading) {
      startListening();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordTrigger]);

  // Keyboard shortcuts while recording: Esc = cancel, Backspace = clear
  useEffect(() => {
    if (!listening) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        cancelRecording();
      } else if (e.key === "Backspace") {
        e.preventDefault();
        committedRef.current = "";
        interimRef.current = "";
        setQuestion("");
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [listening, cancelRecording]);

  // Left-click anywhere to send while recording
  useEffect(() => {
    if (!listening) return;
    let removeListener: (() => void) | null = null;
    const id = setTimeout(() => {
      function handleClick(e: MouseEvent) {
        const target = e.target as HTMLElement;
        if (target.closest("[data-no-send]")) return;
        e.preventDefault();
        e.stopPropagation();
        sendQuestion();
      }
      window.addEventListener("click", handleClick, true);
      removeListener = () => window.removeEventListener("click", handleClick, true);
    }, 800);
    return () => {
      clearTimeout(id);
      removeListener?.();
    };
  }, [listening, sendQuestion]);

  // Stop VAD on unmount
  useEffect(() => {
    return () => {
      teardownVad();
      pendingRef.current.clear();
    };
  }, [teardownVad]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    sendQuestion();
  }

  return (
    <div
      className={`fixed bottom-0 border-t border-gray-200 dark:border-gray-700 bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm transition-all duration-300 z-20 ${
        drawerOpen ? "right-[28rem]" : "right-0"
      } ${treePanelOpen ? "left-96" : debugOpen ? "left-[28rem]" : "left-0"}`}
    >
      {/* Selected text chip */}
      {selectedText && (
        <div className="px-4 pt-2 flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 dark:bg-blue-900 px-3 py-1 text-xs text-blue-700 dark:text-blue-300 max-w-md truncate">
            <span className="truncate">&ldquo;{selectedText}&rdquo;</span>
            <button
              onClick={onClearSelection}
              data-no-send
              className="ml-1 hover:text-blue-900 dark:hover:text-blue-100 shrink-0"
              aria-label="Clear selection"
            >
              &times;
            </button>
          </span>
        </div>
      )}

      {/* Recording / transcribing indicator */}
      {(listening || transcribing) && (
        <div className="px-4 pt-2 flex items-center gap-2">
          <span className="inline-block w-2 h-2 bg-red-500 rounded-full animate-pulse" />
          <span className="text-xs text-red-500 font-medium">
            {listening
              ? "Recording — pause to transcribe · click anywhere to send · Esc to cancel"
              : "Finalizing transcript..."}
          </span>
        </div>
      )}

      {/* Input bar */}
      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-3 px-4 py-3"
      >
        {/* Mic button */}
        {speechSupported && (
          <button
            type="button"
            data-no-send
            onClick={listening ? cancelRecording : transcribing ? undefined : startListening}
            disabled={transcribing}
            className={`rounded-full p-2.5 transition-colors shrink-0 ${
              listening
                ? "bg-red-100 dark:bg-red-900 text-red-600 dark:text-red-400 animate-pulse"
                : transcribing
                  ? "text-gray-300 dark:text-gray-600"
                  : "text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
            aria-label={listening ? "Cancel recording" : "Voice input"}
            title={listening ? "Cancel recording" : transcribing ? "Transcribing..." : "Speak your question"}
          >
            {listening ? (
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="6" y="6" width="12" height="12" rx="1" />
              </svg>
            ) : (
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            )}
          </button>
        )}

        {/* Text input — populated with transcript after recording finishes */}
        <input
          type="text"
          value={question}
          onChange={(e) => {
            setQuestion(e.target.value);
            committedRef.current = e.target.value;
            interimRef.current = "";
            // Manual keystroke means the next submit is typed, not voice
            // — unless they re-trigger the mic.
            if (!listeningRef.current) wasVoiceRef.current = false;
          }}
          data-no-send
          placeholder={
            listening
              ? "Recording..."
              : transcribing
                ? "Transcribing..."
                : selectedText
                  ? "Ask about the selected text..."
                  : "Select text above, or just ask a question..."
          }
          disabled={transcribing}
          className={`flex-1 rounded-full border px-5 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 ${
            listening || transcribing
              ? "border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-950"
              : "border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-800"
          }`}
        />

        {/* Send button */}
        <button
          type="submit"
          disabled={loading || transcribing || (!listening && !question.trim())}
          className="rounded-full bg-blue-600 p-2.5 text-white hover:bg-blue-700 disabled:opacity-40 transition-colors shrink-0"
          aria-label="Send"
        >
          {loading || transcribing ? (
            <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 2L11 13" />
              <path d="M22 2L15 22L11 13L2 9L22 2Z" />
            </svg>
          )}
        </button>
      </form>
    </div>
  );
}
