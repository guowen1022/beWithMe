"use client";

import { useState, useRef, useCallback, useEffect } from "react";

export default function QuestionBar({
  selectedText,
  onAsk,
  loading,
  onClearSelection,
  drawerOpen,
  debugOpen,
  recordTrigger,
}: {
  selectedText: string;
  onAsk: (question: string) => void;
  loading: boolean;
  onClearSelection: () => void;
  drawerOpen: boolean;
  debugOpen: boolean;
  recordTrigger: number;
}) {
  const [question, setQuestion] = useState("");
  const [listening, setListening] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(false);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const questionRef = useRef(question);
  const listeningRef = useRef(listening);

  // Keep refs in sync
  useEffect(() => { questionRef.current = question; }, [question]);
  useEffect(() => { listeningRef.current = listening; }, [listening]);

  // Check for Web Speech API support
  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    setSpeechSupported(!!SpeechRecognition);
  }, []);

  // Set recording cursor on body while listening
  useEffect(() => {
    if (listening) {
      document.body.style.cursor =
        'url("data:image/svg+xml,<svg xmlns=%27http://www.w3.org/2000/svg%27 width=%2732%27 height=%2732%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27%23ef4444%27 stroke-width=%272%27><circle cx=%2712%27 cy=%2712%27 r=%2710%27 fill=%27%23fca5a5%27/><circle cx=%2712%27 cy=%2712%27 r=%275%27 fill=%27%23ef4444%27/></svg>") 16 16, pointer';
    } else {
      document.body.style.cursor = "";
    }
    return () => { document.body.style.cursor = ""; };
  }, [listening]);

  const startListening = useCallback(() => {
    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = Array.from(event.results)
        .map((r) => r[0].transcript)
        .join("");
      setQuestion(transcript);
    };

    recognition.onerror = () => setListening(false);
    recognition.onend = () => setListening(false);

    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
  }, []);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  const sendQuestion = useCallback(() => {
    const q = questionRef.current.trim();
    if (!q || loading) return;
    if (listeningRef.current) {
      recognitionRef.current?.stop();
      setListening(false);
    }
    onAsk(q);
    setQuestion("");
  }, [loading, onAsk]);

  const cancelRecording = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
    setQuestion("");
  }, []);

  // Auto-start recording when text is selected
  useEffect(() => {
    if (recordTrigger > 0 && speechSupported && !listening && !loading) {
      setQuestion("");
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
        setQuestion("");
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [listening, cancelRecording]);

  // Left click anywhere to send while recording
  useEffect(() => {
    if (!listening) return;

    let removeListener: (() => void) | null = null;

    // Small delay to avoid the mouseup from text selection triggering send
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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    sendQuestion();
  }

  return (
    <div
      className={`fixed bottom-0 border-t border-gray-200 dark:border-gray-700 bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm transition-all duration-300 z-20 ${
        drawerOpen ? "right-[28rem]" : "right-0"
      } ${debugOpen ? "left-80" : "left-0"}`}
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

      {/* Recording indicator */}
      {listening && (
        <div className="px-4 pt-2 flex items-center gap-2">
          <span className="inline-block w-2 h-2 bg-red-500 rounded-full animate-pulse" />
          <span className="text-xs text-red-500 font-medium">
            Recording — click anywhere to send &middot; Esc to cancel &middot; Backspace to clear
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
            onClick={listening ? cancelRecording : startListening}
            className={`rounded-full p-2.5 transition-colors shrink-0 ${
              listening
                ? "bg-red-100 dark:bg-red-900 text-red-600 dark:text-red-400 animate-pulse"
                : "text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
            aria-label={listening ? "Cancel recording" : "Voice input"}
            title={listening ? "Cancel recording" : "Speak your question"}
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

        {/* Text input — shows live transcript while recording */}
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          data-no-send
          placeholder={
            listening
              ? "Speak now..."
              : selectedText
                ? "Ask about the selected text..."
                : "Select text above, or just ask a question..."
          }
          className={`flex-1 rounded-full border px-5 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 ${
            listening
              ? "border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-950"
              : "border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-800"
          }`}
        />

        {/* Send button */}
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="rounded-full bg-blue-600 p-2.5 text-white hover:bg-blue-700 disabled:opacity-40 transition-colors shrink-0"
          aria-label="Send"
        >
          {loading ? (
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
