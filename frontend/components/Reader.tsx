"use client";

import { useState, useCallback } from "react";
import ContentInput from "./ContentInput";
import ReadingPane from "./ReadingPane";
import QuestionBar from "./QuestionBar";
import AnswerDrawer from "./AnswerDrawer";
import DebugPanel from "./DebugPanel";
import { askStream, type AnswerEvent, type DebugEvent } from "@/lib/api";

export type AgentStatus = "idle" | "thinking" | "searching" | "done";

export default function Reader() {
  const [content, setContent] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [answer, setAnswer] = useState<AnswerEvent | null>(null);
  const [lastDebug, setLastDebug] = useState<DebugEvent | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [hasAnswer, setHasAnswer] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<AgentStatus>("idle");
  const [searchDetail, setSearchDetail] = useState<string | null>(null);
  const [recordTrigger, setRecordTrigger] = useState(0);
  const [sessionId] = useState(() => crypto.randomUUID());

  const handleSelection = useCallback((text: string) => {
    setSelectedText(text);
    setRecordTrigger((n) => n + 1);
  }, []);

  async function handleAsk(question: string) {
    if (!question.trim()) return;
    setLoading(true);
    setDrawerOpen(true);
    setAnswer(null);
    setStatus("thinking");
    setSearchDetail(null);

    try {
      await askStream(
        {
          passage_text: content,
          selected_text: selectedText || undefined,
          question: question.trim(),
          session_id: sessionId,
        },
        (event) => {
          if (event.type === "status") {
            setStatus(event.status as AgentStatus);
            if (event.status === "searching") {
              setSearchDetail(event.detail);
            }
          } else if (event.type === "answer") {
            setAnswer(event as AnswerEvent);
            setHasAnswer(true);
            setStatus("done");
          } else if (event.type === "debug") {
            setLastDebug(event as DebugEvent);
          }
        }
      );
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setStatus("idle");
    }
  }

  if (!content) {
    return <ContentInput onSubmit={setContent} />;
  }

  return (
    <div className="relative flex h-screen">
      {/* Debug panel (left) */}
      <DebugPanel open={debugOpen} onClose={() => setDebugOpen(false)} lastDebug={lastDebug} />

      {/* Toggle debug panel button */}
      {!debugOpen && (
        <button
          onClick={() => setDebugOpen(true)}
          className="fixed top-1/2 left-0 -translate-y-1/2 z-40 rounded-r-lg bg-purple-600 p-2.5 text-white shadow-lg hover:bg-purple-700 transition-colors"
          aria-label="Show learning profile"
          title="Learning profile (debug)"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 18l6-6-6-6" />
          </svg>
        </button>
      )}

      {/* Reading area — adjusts margins for both panels */}
      <div
        className={`flex-1 flex flex-col overflow-y-auto pb-20 transition-all duration-300 ${
          drawerOpen ? "mr-[28rem]" : "mr-0"
        } ${debugOpen ? "ml-80" : "ml-0"}`}
      >
        <ReadingPane content={content} onSelection={handleSelection} />
      </div>

      {/* Answer drawer (right) */}
      <AnswerDrawer
        open={drawerOpen}
        loading={loading}
        status={status}
        searchDetail={searchDetail}
        answer={answer}
        onClose={() => setDrawerOpen(false)}
      />

      {!drawerOpen && hasAnswer && (
        <button
          onClick={() => setDrawerOpen(true)}
          className="fixed top-1/2 right-0 -translate-y-1/2 z-40 rounded-l-lg bg-blue-600 p-2.5 text-white shadow-lg hover:bg-blue-700 transition-colors"
          aria-label="Show answer"
          title="Show last answer"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
      )}

      {/* Bottom question bar — adjusts for both panels */}
      <QuestionBar
        selectedText={selectedText}
        onAsk={handleAsk}
        loading={loading}
        onClearSelection={() => setSelectedText("")}
        drawerOpen={drawerOpen}
        debugOpen={debugOpen}
        recordTrigger={recordTrigger}
      />
    </div>
  );
}
