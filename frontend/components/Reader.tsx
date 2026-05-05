"use client";

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import QuestionBar from "./QuestionBar";
import AnswerDrawer from "./AnswerDrawer";
import ParentCard from "./ParentCard";
import DebugPanel from "./DebugPanel";
import ExplorationTreePanel from "./ExplorationTreePanel";
import { askStream, endSession, recordSignal, type DebugEvent } from "@/lib/api";
import { bus } from "@/lib/bus";
import type { Direction } from "./LogicBlock";
import type { OutlineSection } from "@/lib/passageOutline";
import DynamicSurface from "./DynamicSurface";
import { BlockRegistryProvider } from "@/lib/blockRegistry";
import {
  type ExplorationTree,
  createTree,
  addNode as addTreeNode,
  updateTreeNode,
  toggleCollapsed,
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

export default function Reader({ onGoalPlan: _onGoalPlan }: { onGoalPlan?: () => void }) {
  const [selectedText, setSelectedText] = useState("");
  const [selectionSource, setSelectionSource] = useState<SelectionSource | null>(null);
  const [questionStack, setQuestionStack] = useState<QuestionNode[]>([]);
  const [explorationTree, setExplorationTree] = useState<ExplorationTree | null>(null);
  const [treePanelOpen, setTreePanelOpen] = useState(false);
  const [navigatedNodeId, setNavigatedNodeId] = useState<string | null>(null);
  const [lastDebug, setLastDebug] = useState<DebugEvent | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const [promptVersion, setPromptVersion] = useState<"v1" | "v2">("v2");
  const [recordTrigger, setRecordTrigger] = useState(0);
  const [sessionId] = useState(() => crypto.randomUUID());
  const [endingSession, setEndingSession] = useState(false);
  const [debugInitialTab, setDebugInitialTab] = useState<"prefs" | "sessions" | undefined>(undefined);

  // Persist block collapse/review-later state across navigation
  // Key: nodeLocalId, Value: { collapsed: Set<blockId>, reviewLater: Set<blockId> }
  const blockStatesRef = useRef<Map<string, { collapsed: Set<string>; reviewLater: Set<string> }>>(new Map());

  // Dynamic readers (pdf_reader, passage_reader) publish text selections
  // on a shared topic. Subscribe so the QuestionBar gets `selectedText`
  // without us having to own the reading widgets ourselves.
  useEffect(() => {
    const unsub = bus.subscribe("reader.selection", (value) => {
      const text = typeof value === "string" ? value.trim() : "";
      if (!text) return;
      setSelectedText(text);
      setSelectionSource("passage");
      setRecordTrigger((n) => n + 1);
    });
    return () => unsub();
  }, []);

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

  // TODO: passage outline used to come from `content` set by the legacy
  // ContentInput. The exploration tree will move to a dynamic block in a
  // follow-up commit; for now no outline is produced.
  const outlineSections: OutlineSection[] = useMemo(() => [], []);

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

  // Global keyboard: [ and ] to switch focus between panels
  // Panels: left (exploration) → center (passage/parent) → right (answer drawer)
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Don't intercept if user is typing in an input/textarea
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      if (e.key === "]") {
        e.preventDefault();
        // Focus right: try answer drawer first, then center
        const answer = document.querySelector("[data-panel='answer']") as HTMLElement | null;
        if (answer) { answer.focus(); return; }
      }
      if (e.key === "[") {
        e.preventDefault();
        // Focus left: try exploration panel, or center reading area
        const center = document.querySelector("[data-panel='center']") as HTMLElement | null;
        if (center) { center.focus(); return; }
      }
      // Dev shortcut: Cmd/Ctrl+Shift+D toggles the debug panel. The visible
      // toggle button was removed so end users don't stumble into it.
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "D" || e.key === "d")) {
        e.preventDefault();
        setDebugOpen((v) => !v);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
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

  // TODO: section-click jumping into the PDF/passage used to drive
  // pdfScrollTarget on the legacy <PdfViewer>. With the dynamic
  // pdf_reader template, this would publish on a "scroll-to" topic the
  // template subscribes to. Wire up in the same commit that templatizes
  // the exploration tree.
  const handleSectionClick = useCallback((_section: OutlineSection) => {}, []);

  const navigateToNode = useCallback((localId: string) => {
    const tree = treeRef.current;
    if (!tree || !tree.nodes[localId]) return;
    setNavigatedNodeId(localId);
    setQuestionStack(rebuildStack(tree, localId));
  }, []);

  const handleToggleCollapse = useCallback((localId: string) => {
    setExplorationTree((tree) => tree ? toggleCollapsed(tree, localId) : tree);
  }, []);

  // Open the exploration tree on first session start so the user has a
  // panel to navigate questions in. Tree nodes are populated as the user
  // asks questions (no pre-existing passage to seed from).
  useEffect(() => {
    setExplorationTree((tree) => tree ?? createTree(""));
  }, []);

  /**
   * Core question-firing helper. Creates a QuestionNode, adds it to the
   * stack and exploration tree, and streams the answer from the backend.
   */
  async function fireQuestion(
    question: string,
    sel: string | null,
    parentInteractionId: string | null,
    parentLocalId: string | null,
    source: SelectionSource,
  ) {
    let nextStack: QuestionNode[];
    const currentStack = stackRef.current;

    if (source === "passage") {
      nextStack = [makeNode(question, sel, null)];
    } else if (source === "parent" && currentStack.length >= 2) {
      nextStack = [
        ...currentStack.slice(0, -1),
        makeNode(question, sel, parentInteractionId),
      ];
    } else if (source === "active" && currentStack.length >= 1) {
      nextStack = [...currentStack, makeNode(question, sel, parentInteractionId)];
    } else {
      return;
    }

    const newNode = nextStack[nextStack.length - 1];
    setQuestionStack(nextStack);
    setSelectedText("");
    setSelectionSource(null);
    setNavigatedNodeId(null);

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
          parent_interaction_id: parentInteractionId ?? undefined,
          prompt_version: promptVersion,
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

  async function handleAsk(question: string) {
    if (!question.trim()) return;
    // Slash-commands (e.g. /block hello) bypass the selection gate — they're
    // tool calls, not Q&A turns about a specific passage. Route them as a
    // top-level "passage"-source ask so the synthetic SSE answer renders.
    if (question.trim().startsWith("/")) {
      await fireQuestion(question, null, null, null, "passage");
      return;
    }
    const source = selectionSource;
    const sel = selectedText;
    if (!source) return;

    const currentStack = stackRef.current;
    let parentInteractionId: string | null = null;
    let parentLocalId: string | null = null;

    if (source === "parent" && currentStack.length >= 2) {
      const parent = currentStack[currentStack.length - 2];
      parentInteractionId = parent.interactionId;
      parentLocalId = parent.localId;
    } else if (source === "active" && currentStack.length >= 1) {
      const active = currentStack[currentStack.length - 1];
      parentInteractionId = active.interactionId;
      parentLocalId = active.localId;
    }

    await fireQuestion(question, sel, parentInteractionId, parentLocalId, source);
  }

  function handleBlockGesture(blockText: string, direction: Direction) {
    const active = stackRef.current.at(-1);
    if (!active?.interactionId) return;

    if (direction === "left") {
      // "Got it" — fire-and-forget signal
      recordSignal(sessionId, active.interactionId, blockText, "got_it");
      return;
    }
    if (direction === "up") {
      // "Too hard" — record signal, no LLM call
      recordSignal(sessionId, active.interactionId, blockText, "review_later");
      return;
    }
    if (direction === "down") {
      // "Explain more" — auto drill-down
      fireQuestion(
        "Explain this in more detail with examples",
        blockText,
        active.interactionId,
        active.localId,
        "active",
      );
      return;
    }
    if (direction === "right") {
      // Custom question — set selection context + trigger voice recording
      setSelectedText(blockText);
      setSelectionSource("active");
      setRecordTrigger((prev) => prev + 1);
    }
  }

  async function handleEndSession() {
    if (endingSession) return;
    setEndingSession(true);
    try {
      await endSession(sessionId);
    } catch (err) {
      console.error("Failed to end session:", err);
    }
    // Reset session state — the canvas itself stays whatever the user
    // has up; the dynamic templates (upload_file, pdf_reader, etc.)
    // own their own lifecycle.
    setSelectedText("");
    setSelectionSource(null);
    setQuestionStack([]);
    setExplorationTree(null);
    setTreePanelOpen(false);
    setNavigatedNodeId(null);
    setLastDebug(null);

    // Show debug panel on Sessions tab so user sees the result appear
    setDebugInitialTab("sessions");
    setDebugOpen(true);
    setEndingSession(false);
  }

  const leftMargin = treePanelOpen ? "ml-96" : debugOpen ? "ml-[28rem]" : "ml-0";

  return (
    <BlockRegistryProvider>
    <div className="relative flex h-screen">
      {/* Dynamic UI surface — mounts blocks pushed by frontend_engineer
          via SSE. Self-hides when no blocks are present. */}
      <DynamicSurface mode="fullscreen" />

      {/* End Session button (top-right) */}
      {explorationTree && (
        <button
          onClick={handleEndSession}
          disabled={endingSession}
          className="fixed top-4 right-4 z-50 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white shadow-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
        >
          {endingSession ? "Ending..." : "End Session"}
        </button>
      )}

      {/* Exploration tree panel (left) — shows outline even before questions */}
      {explorationTree && outlineSections.length > 0 && (
        <ExplorationTreePanel
          tree={explorationTree}
          activeLocalId={activeNode?.localId ?? null}
          activePathIds={activePathIds}
          open={treePanelOpen}
          onClose={() => setTreePanelOpen(false)}
          onNavigate={navigateToNode}
          onToggleCollapse={handleToggleCollapse}
          passageText={content}
          outlineSections={outlineSections}
          onSectionClick={handleSectionClick}
        />
      )}

      {/* Debug panel (left, behind tree panel) */}
      <DebugPanel open={debugOpen && !treePanelOpen} onClose={() => { setDebugOpen(false); setDebugInitialTab(undefined); }} lastDebug={lastDebug} initialTab={debugInitialTab} />

      {/* Left toggle buttons: exploration tree + debug panel */}
      <div className={`fixed top-1/2 -translate-y-1/2 z-40 flex flex-col gap-2 ${treePanelOpen ? "left-96" : "left-0"}`}>
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
        {!debugOpen && (
          <button
            onClick={() => { setDebugOpen(true); setTreePanelOpen(false); }}
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

      {/* Pinned source passage removed — parent blocks view replaces it */}

      {/* Middle surface — the dynamic canvas owns the actual reading
          UI now (upload_file, pdf_reader, passage_reader, future
          browser template). ParentCard is a thin React overlay until
          we templatize it (TODO). */}
      <div
        data-panel="center"
        tabIndex={0}
        className={`flex-1 flex flex-col overflow-y-auto pb-20 transition-all duration-300 outline-none ${
          drawerOpen ? "mr-[28rem]" : "mr-0"
        } ${leftMargin}`}
      >
        {parentNode && (
          <ParentCard node={parentNode} childNode={activeNode} onPop={popActive} />
        )}
      </div>

      {/* Active question drawer (right). Re-mounted when the active node
          changes so the typewriter resets cleanly. */}
      {activeNode && (
        <AnswerDrawer
          key={activeNode.localId}
          node={activeNode}
          onClose={popActive}
          onBlockGesture={handleBlockGesture}
          instant={!activeNode.loading}
          initialBlockStates={blockStatesRef.current.get(activeNode.localId)}
          onBlockStatesChange={(states) => {
            blockStatesRef.current.set(activeNode.localId, states);
          }}
        />
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
    </BlockRegistryProvider>
  );
}
