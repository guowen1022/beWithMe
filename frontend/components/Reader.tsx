"use client";

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import ContentInput from "./ContentInput";
import ReadingPane from "./ReadingPane";
import QuestionBar from "./QuestionBar";
import AnswerDrawer from "./AnswerDrawer";
import ParentCard from "./ParentCard";
import PinnedPassageCard from "./PinnedPassageCard";
import DebugPanel from "./DebugPanel";
import ExplorationTreePanel from "./ExplorationTreePanel";
import { askStream, type DebugEvent } from "@/lib/api";
import {
  type ExplorationTree,
  createTree,
  addNode as addTreeNode,
  updateTreeNode,
  toggleCollapsed,
  getPathToRoot,
  rebuildStack,
} from "@/lib/explorationTree";

export type AgentStatus = "idle" | "thinking" | "searching" | "done";

/**
 * One question in the recursive question tree the user navigates through.
 * The tree only exists in the UI for spatial navigation — the LLM sees a
 * flat chronological session via prior_messages on the server side.
 *
 * `localId` is generated client-side so we can target this node from
 * stream callbacks before the server has assigned an `interactionId`.
 */
export type QuestionNode = {
  localId: string;
  interactionId: string | null;
  parentInteractionId: string | null;
  title: string | null;
  question: string;
  selectedText: string | null;
  displayedText: string;
  status: AgentStatus;
  searchDetail: string | null;
  loading: boolean;
};

type SelectionSource = "passage" | "parent" | "active";

export default function Reader() {
  const [content, setContent] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [selectionSource, setSelectionSource] = useState<SelectionSource | null>(null);
  const [questionStack, setQuestionStack] = useState<QuestionNode[]>([]);
  const [explorationTree, setExplorationTree] = useState<ExplorationTree | null>(null);
  const [treePanelOpen, setTreePanelOpen] = useState(false);
  const [navigatedNodeId, setNavigatedNodeId] = useState<string | null>(null);
  const [lastDebug, setLastDebug] = useState<DebugEvent | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const [recordTrigger, setRecordTrigger] = useState(0);
  const [sessionId] = useState(() => crypto.randomUUID());

  const activeNode = questionStack.length > 0 ? questionStack[questionStack.length - 1] : null;
  const parentNode = questionStack.length >= 2 ? questionStack[questionStack.length - 2] : null;
  const drawerOpen = activeNode !== null;
  // Once the user has drilled into a sub-question, the parent's Q+A takes
  // the middle and the source passage moves to a small pinned card.
  const showPinnedPassage = parentNode !== null;

  const activePathIds = useMemo(
    () => new Set(questionStack.map((n) => n.localId)),
    [questionStack],
  );

  const treeRef = useRef(explorationTree);
  useEffect(() => { treeRef.current = explorationTree; }, [explorationTree]);

  const stackRef = useRef(questionStack);
  useEffect(() => { stackRef.current = questionStack; }, [questionStack]);

  // Global selection router. The previous design attached a mouseup
  // handler inside ReadingPane only; with three selectable surfaces
  // (passage, parent card, active drawer) we need one listener that
  // attributes the selection to whichever surface contains it.
  useEffect(() => {
    function handleMouseUp() {
      const selection = window.getSelection();
      const text = selection?.toString().trim() ?? "";
      if (!text) return;
      const anchor = selection?.anchorNode;
      if (!anchor) return;
      const el = anchor.nodeType === Node.ELEMENT_NODE
        ? (anchor as Element)
        : anchor.parentElement;
      if (!el) return;
      const surface = el.closest("[data-selection-source]") as HTMLElement | null;
      const source = surface?.dataset.selectionSource as SelectionSource | undefined;
      if (!source) return;
      setSelectedText(text);
      setSelectionSource(source);
      setRecordTrigger((n) => n + 1);
    }
    document.addEventListener("mouseup", handleMouseUp);
    return () => document.removeEventListener("mouseup", handleMouseUp);
  }, []);

  function makeNode(
    question: string,
    selText: string | null,
    parent_interaction_id: string | null,
  ): QuestionNode {
    return {
      localId: crypto.randomUUID(),
      interactionId: null,
      parentInteractionId: parent_interaction_id,
      title: null,
      question,
      selectedText: selText,
      displayedText: "",
      status: "thinking",
      searchDetail: null,
      loading: true,
    };
  }

  const popActive = useCallback(() => {
    setQuestionStack((stack) => (stack.length > 0 ? stack.slice(0, -1) : stack));
  }, []);

  const updateNode = useCallback(
    (localId: string, patch: (n: QuestionNode) => QuestionNode) => {
      setQuestionStack((stack) =>
        stack.map((n) => (n.localId === localId ? patch(n) : n)),
      );
      setExplorationTree((tree) => {
        if (!tree) return tree;
        return updateTreeNode(tree, localId, (tn) => {
          const patched = patch(tn);
          return { ...patched, parentLocalId: tn.parentLocalId, childIds: tn.childIds, collapsed: tn.collapsed };
        });
      });
    },
    [],
  );

  const navigateToNode = useCallback((localId: string) => {
    const tree = treeRef.current;
    if (!tree || !tree.nodes[localId]) return;
    setNavigatedNodeId(localId);
    setQuestionStack(rebuildStack(tree, localId));
  }, []);

  const handleToggleCollapse = useCallback((localId: string) => {
    setExplorationTree((tree) => tree ? toggleCollapsed(tree, localId) : tree);
  }, []);

  async function handleAsk(question: string) {
    if (!question.trim()) return;
    const source = selectionSource;
    const sel = selectedText;
    if (!source) return; // selection-required

    // Determine parent_interaction_id and how the new node fits into the stack.
    // - passage  → top-level question, resets the stack
    // - parent   → sibling at the parent's level, replaces the active node
    // - active   → drills deeper, pushes a new child onto the stack
    let parent_interaction_id: string | null = null;
    let parentLocalId: string | null = null;
    let nextStack: QuestionNode[];
    const currentStack = stackRef.current;

    if (source === "passage") {
      nextStack = [makeNode(question, sel, null)];
    } else if (source === "parent" && currentStack.length >= 2) {
      const parent = currentStack[currentStack.length - 2];
      parent_interaction_id = parent.interactionId;
      parentLocalId = parent.localId;
      nextStack = [
        ...currentStack.slice(0, -1),
        makeNode(question, sel, parent_interaction_id),
      ];
    } else if (source === "active" && currentStack.length >= 1) {
      const active = currentStack[currentStack.length - 1];
      parent_interaction_id = active.interactionId;
      parentLocalId = active.localId;
      nextStack = [...currentStack, makeNode(question, sel, parent_interaction_id)];
    } else {
      return;
    }

    const newNode = nextStack[nextStack.length - 1];
    setQuestionStack(nextStack);
    setSelectedText("");
    setSelectionSource(null);
    setNavigatedNodeId(null);

    // Add to exploration tree (create tree on first question)
    setExplorationTree((prev) => {
      const tree = prev ?? createTree(content);
      return addTreeNode(tree, newNode, parentLocalId);
    });
    if (!treePanelOpen) setTreePanelOpen(true);

    try {
      await askStream(
        {
          passage_text: content,
          selected_text: sel || undefined,
          question: question.trim(),
          session_id: sessionId,
          parent_interaction_id: parent_interaction_id ?? undefined,
        },
        (event) => {
          if (event.type === "status") {
            updateNode(newNode.localId, (n) => ({
              ...n,
              status: event.status as AgentStatus,
              searchDetail: event.status === "searching" ? event.detail : null,
            }));
          } else if (event.type === "title") {
            updateNode(newNode.localId, (n) => ({ ...n, title: event.title }));
          } else if (event.type === "token") {
            updateNode(newNode.localId, (n) => ({
              ...n,
              displayedText: n.displayedText + event.text,
              status: "done",
            }));
          } else if (event.type === "answer") {
            updateNode(newNode.localId, (n) => ({
              ...n,
              displayedText: event.answer,
              title: event.title ?? n.title,
              status: "done",
              loading: false,
            }));
          } else if (event.type === "interaction") {
            updateNode(newNode.localId, (n) => ({
              ...n,
              interactionId: event.interaction_id,
            }));
          } else if (event.type === "debug") {
            setLastDebug(event as DebugEvent);
          }
        },
      );
    } catch (err) {
      console.error(err);
      updateNode(newNode.localId, (n) => ({ ...n, loading: false, status: "idle" }));
    }
  }

  if (!content) {
    return (
      <div className="relative flex h-screen">
        <DebugPanel open={debugOpen} onClose={() => setDebugOpen(false)} lastDebug={lastDebug} />
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
        <div
          className={`flex-1 flex flex-col transition-all duration-300 ${
            debugOpen ? "ml-80" : "ml-0"
          }`}
        >
          <ContentInput onSubmit={setContent} />
        </div>
      </div>
    );
  }

  const leftMargin = treePanelOpen ? "ml-72" : debugOpen ? "ml-80" : "ml-0";

  return (
    <div className="relative flex h-screen">
      {/* Exploration tree panel (left) */}
      {explorationTree && (
        <ExplorationTreePanel
          tree={explorationTree}
          activeLocalId={activeNode?.localId ?? null}
          activePathIds={activePathIds}
          open={treePanelOpen}
          onClose={() => setTreePanelOpen(false)}
          onNavigate={navigateToNode}
          onToggleCollapse={handleToggleCollapse}
        />
      )}

      {/* Debug panel (left, behind tree panel) */}
      <DebugPanel open={debugOpen && !treePanelOpen} onClose={() => setDebugOpen(false)} lastDebug={lastDebug} />

      {/* Left toggle buttons */}
      <div className={`fixed top-1/2 -translate-y-1/2 z-40 flex flex-col gap-2 ${treePanelOpen ? "left-72" : "left-0"}`}>
        {explorationTree && !treePanelOpen && (
          <button
            onClick={() => setTreePanelOpen(true)}
            className="rounded-r-lg bg-gray-700 p-2.5 text-white shadow-lg hover:bg-gray-800 transition-colors"
            aria-label="Show exploration tree"
            title="Exploration tree"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 3v18h18" />
              <path d="M7 14l4-4 4 4 4-4" />
            </svg>
          </button>
        )}
        {!debugOpen && !treePanelOpen && (
          <button
            onClick={() => setDebugOpen(true)}
            className="rounded-r-lg bg-purple-600 p-2.5 text-white shadow-lg hover:bg-purple-700 transition-colors"
            aria-label="Show learning profile"
            title="Learning profile (debug)"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
        )}
      </div>

      {/* Pinned source passage when the user has drilled at least one level */}
      {showPinnedPassage && (
        <PinnedPassageCard content={content} offsetLeft={treePanelOpen || debugOpen} />
      )}

      {/* Middle surface — passage when no drilling, parent card otherwise */}
      <div
        className={`flex-1 flex flex-col overflow-y-auto pb-20 transition-all duration-300 ${
          drawerOpen ? "mr-[28rem]" : "mr-0"
        } ${leftMargin}`}
      >
        {parentNode ? (
          <ParentCard node={parentNode} onPop={popActive} />
        ) : (
          <ReadingPane
            content={content}
            onPlainClick={drawerOpen ? popActive : undefined}
          />
        )}
      </div>

      {/* Active question drawer (right). Re-mounted when the active node
          changes so the typewriter resets cleanly. */}
      {activeNode && (
        <AnswerDrawer key={activeNode.localId} node={activeNode} onClose={popActive} instant={navigatedNodeId === activeNode.localId} />
      )}

      {/* Bottom question bar — adjusts for both panels */}
      <QuestionBar
        selectedText={selectedText}
        onAsk={handleAsk}
        loading={activeNode?.loading ?? false}
        onClearSelection={() => {
          setSelectedText("");
          setSelectionSource(null);
        }}
        drawerOpen={drawerOpen}
        debugOpen={debugOpen}
        treePanelOpen={treePanelOpen}
        recordTrigger={recordTrigger}
      />
    </div>
  );
}
