"use client";

import { useState, useRef, useEffect } from "react";
import GoalDAG from "./GoalDAG";
import {
  createGoal,
  expandNode,
  feedbackNode,
  finalizeGoal,
  type DAGData,
} from "@/lib/api";

export default function GoalPlanner({ onBack }: { onBack: () => void }) {
  const [goalId, setGoalId] = useState<string | null>(null);
  const [goalTitle, setGoalTitle] = useState("");
  const [dag, setDag] = useState<DAGData | null>(null);
  const [transcript, setTranscript] = useState<{ role: string; text: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingNode, setLoadingNode] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [finalized, setFinalized] = useState(false);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  async function handleCreateGoal() {
    const title = inputValue.trim();
    if (!title) return;
    setLoading(true);
    setInputValue("");
    setGoalTitle(title);
    try {
      const result = await createGoal(title);
      setGoalId(result.id);
      setDag(result.dag);
      setTranscript(result.transcript);
    } catch (err) {
      console.error(err);
      setTranscript([{ role: "assistant", text: "Failed to create goal. Please try again." }]);
    }
    setLoading(false);
  }

  async function handleExpand(nodeId: string) {
    if (!goalId || loadingNode) return;
    setLoadingNode(nodeId);
    try {
      const result = await expandNode(goalId, nodeId);
      setDag(result.dag);
      setTranscript(result.transcript);
    } catch (err) {
      console.error(err);
    }
    setLoadingNode(null);
  }

  async function handleKnow(nodeId: string) {
    if (!goalId || loadingNode) return;
    setLoadingNode(nodeId);
    try {
      const result = await feedbackNode(goalId, nodeId, "know");
      setDag(result.dag);
      setTranscript(result.transcript);
    } catch (err) {
      console.error(err);
    }
    setLoadingNode(null);
  }

  async function handleUnknown(nodeId: string) {
    if (!goalId || loadingNode) return;
    setLoadingNode(nodeId);
    try {
      const result = await feedbackNode(goalId, nodeId, "unknown");
      setDag(result.dag);
      setTranscript(result.transcript);
    } catch (err) {
      console.error(err);
    }
    setLoadingNode(null);
  }

  async function handleFinalize() {
    if (!goalId) return;
    setLoading(true);
    try {
      await finalizeGoal(goalId);
      setFinalized(true);
      setTranscript((t) => [...t, { role: "assistant", text: "Goal plan finalized! You can now start learning." }]);
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  }

  // Initial state: show goal input
  if (!dag) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-950 px-4">
        <button
          onClick={onBack}
          className="absolute top-4 left-4 text-sm text-gray-500 hover:text-gray-300"
        >
          ← Back
        </button>
        <h1 className="text-2xl font-semibold text-gray-100 mb-2">Plan a Learning Goal</h1>
        <p className="text-sm text-gray-400 mb-8 text-center max-w-md">
          Tell me what you want to learn. I'll break it down into prerequisites
          and help you build a learning path.
        </p>
        <div className="w-full max-w-lg flex gap-2">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreateGoal()}
            placeholder="e.g., I want to learn web development"
            className="flex-1 rounded-lg border border-gray-700 bg-gray-900 px-4 py-3 text-sm text-gray-200 placeholder-gray-500 focus:border-blue-500 focus:outline-none"
            disabled={loading}
            autoFocus
          />
          <button
            onClick={handleCreateGoal}
            disabled={loading || !inputValue.trim()}
            className="rounded-lg bg-blue-600 px-6 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Planning..." : "Plan"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-gray-950">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-sm text-gray-500 hover:text-gray-300">
            ← Back
          </button>
          <h2 className="text-sm font-medium text-gray-200">{goalTitle}</h2>
        </div>
        <div className="flex items-center gap-2">
          {!finalized && (
            <button
              onClick={handleFinalize}
              disabled={loading}
              className="rounded-lg bg-green-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              Finalize Plan
            </button>
          )}
          {finalized && (
            <span className="text-xs text-green-400 font-medium">Plan Finalized</span>
          )}
        </div>
      </div>

      {/* DAG visualization — main area */}
      <div className="flex-1 overflow-hidden">
        <GoalDAG
          dag={dag}
          loading={loadingNode}
          onExpand={handleExpand}
          onKnow={handleKnow}
          onUnknown={handleUnknown}
        />
      </div>

      {/* Transcript panel — bottom */}
      <div className="h-48 border-t border-gray-800 overflow-y-auto px-4 py-3 shrink-0 bg-gray-900/50">
        <div className="space-y-2">
          {transcript.map((entry, i) => (
            <div key={i} className="text-xs">
              <span className={`font-medium ${entry.role === "user" ? "text-blue-400" : "text-gray-400"}`}>
                {entry.role === "user" ? "You" : "Planner"}:
              </span>
              <span className="text-gray-300 ml-1.5">{entry.text}</span>
            </div>
          ))}
          {(loading || loadingNode) && (
            <div className="text-xs text-gray-500">Thinking...</div>
          )}
          <div ref={transcriptEndRef} />
        </div>
      </div>
    </div>
  );
}
