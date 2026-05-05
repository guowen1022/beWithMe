"use client";

import { useState, useCallback, useEffect, useRef, useMemo, lazy, Suspense } from "react";
import ContentInput, { type ContentResult } from "./ContentInput";
import ReadingPane from "./ReadingPane";
import QuestionBar from "./QuestionBar";
import AnswerDrawer from "./AnswerDrawer";
import ParentCard from "./ParentCard";
import DebugPanel from "./DebugPanel";
import ExplorationTreePanel from "./ExplorationTreePanel";
import { askStream, endSession, recordSignal, type DebugEvent } from "@/lib/api";
import { postBlockState } from "@/lib/blockState";
import type { Direction } from "./LogicBlock";
import { parsePassageOutline, type OutlineSection } from "@/lib/passageOutline";
import BrowserSlot from "./BrowserSlot";
import DynamicSurface from "./DynamicSurface";
import { getBrowserBridge, isDesktop } from "@/lib/desktopBridge";
import { BlockRegistryProvider } from "@/lib/blockRegistry";
import {
  type ExplorationTree,
  createTree,
  addNode as addTreeNode,
  updateTreeNode,
  toggleCollapsed,
  getPathToRoot,
  rebuildStack,
} from "@/lib/explorationTree";

// Lazy-load the PDF viewer so pdf.js isn't in the initial bundle.
const PdfViewer = lazy(() => import("./PdfViewer"));

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

export default function Reader({ onGoalPlan }: { onGoalPlan?: () => void }) {
  const [content, setContent] = useState("");
  const [pdfFile, setPdfFile] = useState<File | null>(null);
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
  const [browserMode, setBrowserMode] = useState(false);
  const [browserUrl, setBrowserUrl] = useState("");
  const [debugInitialTab, setDebugInitialTab] = useState<"prefs" | "sessions" | undefined>(undefined);

  // Persist block collapse/review-later state across navigation
  // Key: nodeLocalId, Value: { collapsed: Set<blockId>, reviewLater: Set<blockId> }
  const blockStatesRef = useRef<Map<string, { collapsed: Set<string>; reviewLater: Set<string> }>>(new Map());
  const [pdfScrollTarget, setPdfScrollTarget] = useState<string | null>(null);

  // The legacy reading area (PDF / passage / browser) lives outside the
  // dynamic canvas, so the teacher can't see it via read_media unless we
  // explicitly report into the perception cache. We synthesize a single
  // "main-reader" block id whose state reflects whatever's currently up.
  useEffect(() => {
    if (pdfFile) {
      postBlockState("main-reader", {
        kind: "pdf",
        content: pdfFile.name,
        focus: "active",
        extra: { filename: pdfFile.name, size_bytes: pdfFile.size },
      });
    } else if (browserMode && browserUrl) {
      postBlockState("main-reader", {
        kind: "browser",
        content: browserUrl,
        focus: "active",
      });
    } else if (content) {
      const preview = content.slice(0, 200).replace(/\s+/g, " ").trim();
      postBlockState("main-reader", {
        kind: "passage",
        content: preview,
        focus: "active",
        extra: { length_chars: content.length },
      });
    } else {
      postBlockState("main-reader", {
        kind: "snapshot",
        content: "(empty — no PDF, passage, or page loaded)",
        focus: "background",
      });
    }
  }, [pdfFile, browserMode, browserUrl, content]);

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

  // Parse passage into structural outline for the exploration panel
  const outlineSections = useMemo(
    () => (content ? parsePassageOutline(content) : []),
    [content],
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

  // Callback-based selection for components that can't use the global
  // mouseup router (e.g. PdfViewer renders in a separate context).
  const handleSelection = useCallback((text: string) => {
    setSelectedText(text);
    setSelectionSource("passage");
    setRecordTrigger((n) => n + 1);
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

  const handleSectionClick = useCallback((section: OutlineSection) => {
    // For PDF mode: skip past the title heading and use the body text as anchor.
    // The title itself is often short and non-unique (e.g. "Attention" appears in abstract too).
    // The body text right after the heading is unique to that section.
    if (pdfFile) {
      const sectionContent = content.slice(section.textStart, section.textEnd);
      // Skip past the first line (the heading) to get body text
      const firstNewline = sectionContent.indexOf("\n");
      const bodyStart = firstNewline >= 0 ? firstNewline + 1 : 0;
      const bodyText = sectionContent.slice(bodyStart).trim();
      // Use at least 50 chars of body text for reliable matching
      const anchor = bodyText.slice(0, Math.max(100, Math.min(300, bodyText.length)));
      if (anchor.length >= 20) {
        setPdfScrollTarget(anchor + "|||" + Date.now());
      }
      return;
    }

    const pane = document.querySelector("[data-panel='center']");
    if (!pane) return;

    const sectionText = content.slice(section.textStart, section.textStart + 60);
    let best: Element | null = null;

    // Strategy 1: match by data-offset (ReadingPane paragraphs)
    const offsetEls = pane.querySelectorAll("p[data-offset]");
    for (const p of offsetEls) {
      const offset = parseInt(p.getAttribute("data-offset") || "-1", 10);
      if (offset >= section.textStart && offset < section.textEnd) {
        best = p;
        break;
      }
    }

    // Strategy 2: search by section title in all text elements (works for PDF text layers)
    if (!best) {
      const title = section.title;
      // Split title into key words for fuzzy matching
      const titleWords = title.toLowerCase().split(/\s+/).filter((w) => w.length > 2);
      const allEls = pane.querySelectorAll("p, span, div, .textLayer span");

      // First try: exact title substring match
      for (const el of allEls) {
        const text = el.textContent || "";
        if (text.toLowerCase().includes(title.toLowerCase().slice(0, 30))) {
          best = el;
          break;
        }
      }

      // Second try: word-level match on page containers
      if (!best && titleWords.length >= 2) {
        const pages = pane.querySelectorAll(".react-pdf__Page, article, section");
        for (const page of pages) {
          const text = (page.textContent || "").toLowerCase();
          const matches = titleWords.filter((w) => text.includes(w));
          if (matches.length >= Math.min(2, titleWords.length)) {
            best = page;
            break;
          }
        }
      }
    }

    if (best) {
      best.scrollIntoView({ behavior: "smooth", block: "start" });
      const el = best as HTMLElement;
      el.style.transition = "background-color 0.3s";
      el.style.backgroundColor = "rgba(250, 204, 21, 0.4)";
      setTimeout(() => { el.style.backgroundColor = ""; }, 2000);
    }
  }, [content, pdfFile]);

  const navigateToNode = useCallback((localId: string) => {
    const tree = treeRef.current;
    if (!tree || !tree.nodes[localId]) return;
    setNavigatedNodeId(localId);
    setQuestionStack(rebuildStack(tree, localId));
  }, []);

  const handleToggleCollapse = useCallback((localId: string) => {
    setExplorationTree((tree) => tree ? toggleCollapsed(tree, localId) : tree);
  }, []);

  function handleContentSubmit(result: ContentResult) {
    setContent(result.text);
    setBrowserMode(result.type === "browser");
    setBrowserUrl(result.url ?? "");
    // Create exploration tree and open outline panel only when we have
    // content to parse. Browser mode starts with empty text; opening the
    // panel anyway reserves ml-96 for a panel that never renders.
    setExplorationTree(createTree(result.text));
    setTreePanelOpen(result.text.length > 0);
    if (result.type === "pdf" && result.file) {
      setPdfFile(result.file);
    }
  }

  // Fetch the live URL's extracted text in the background so the outline
  // panel and any passage-reading flows have real content to work with.
  // The inline browser keeps rendering immediately; this only populates
  // `content` once trafilatura on the backend returns.
  useEffect(() => {
    if (!browserMode || !browserUrl) return;
    let cancelled = false;
    (async () => {
      try {
        const { uploadUrl } = await import("@/lib/api");
        const result = await uploadUrl(browserUrl);
        if (cancelled) return;
        if (result.text && result.text.trim().length > 0) {
          setContent(result.text);
          setExplorationTree(createTree(result.text));
          setTreePanelOpen(true);
        }
      } catch (err) {
        console.warn("uploadUrl for inline browser failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [browserMode, browserUrl]);

  // Browser selection: desktop uses an event subscription to the inline
  // WebContentsView; web falls back to polling the backend (legacy flow).
  useEffect(() => {
    if (!browserMode) return;
    if (isDesktop()) {
      const bridge = getBrowserBridge();
      if (!bridge) return;
      return bridge.onSelectionChange(({ text }) => {
        if (text) {
          setSelectedText(text);
          setSelectionSource("passage");
          setRecordTrigger((n) => n + 1);
        }
      });
    }
    const interval = setInterval(async () => {
      try {
        const { getBrowserSelection } = await import("@/lib/api");
        const { selection } = await getBrowserSelection();
        if (selection) {
          setSelectedText(selection);
          setSelectionSource("passage");
        }
      } catch {}
    }, 800);
    return () => clearInterval(interval);
  }, [browserMode]);

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
    // Reset reader state — return to content input
    setContent("");
    setPdfFile(null);
    setSelectedText("");
    setSelectionSource(null);
    setQuestionStack([]);
    setExplorationTree(null);
    setTreePanelOpen(false);
    setNavigatedNodeId(null);
    setLastDebug(null);
    setBrowserMode(false);
    setBrowserUrl("");

    // Show debug panel on Sessions tab so user sees the result appear
    setDebugInitialTab("sessions");
    setDebugOpen(true);
    setEndingSession(false);
  }

  if (!content && !browserMode) {
    return (
      <div className="relative flex h-screen">
        <DebugPanel open={debugOpen} onClose={() => { setDebugOpen(false); setDebugInitialTab(undefined); }} lastDebug={lastDebug} promptVersion={promptVersion} onPromptVersionChange={setPromptVersion} initialTab={debugInitialTab} />
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
            debugOpen ? "ml-[28rem]" : "ml-0"
          }`}
        >
          <ContentInput onSubmit={handleContentSubmit} onGoalPlan={onGoalPlan} />
        </div>
      </div>
    );
  }

  const leftMargin = treePanelOpen ? "ml-96" : debugOpen ? "ml-[28rem]" : "ml-0";

  return (
    <BlockRegistryProvider>
    <div className="relative flex h-screen">
      {/* Dynamic UI surface — mounts blocks pushed by frontend_engineer
          via SSE. Self-hides when no blocks are present. */}
      <DynamicSurface />

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

      {/* Middle surface — passage when no drilling, parent card otherwise */}
      <div
        data-panel="center"
        tabIndex={0}
        className={`flex-1 flex flex-col overflow-y-auto pb-20 transition-all duration-300 outline-none ${
          drawerOpen ? "mr-[28rem]" : "mr-0"
        } ${leftMargin}`}
      >
        {parentNode ? (
          <ParentCard node={parentNode} childNode={activeNode} onPop={popActive} />
        ) : browserMode && !parentNode ? (
          isDesktop() && browserUrl ? (
            <BrowserSlot url={browserUrl} />
          ) : (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="rounded-xl bg-gray-800/50 border border-gray-700 p-8 max-w-md">
                <h2 className="text-lg font-semibold text-gray-200 mb-2">Reading in Browser</h2>
                <p className="text-sm text-gray-400 mb-4">
                  Select text in the Chromium window, then ask questions below.
                </p>
                {selectedText && (
                  <div className="mt-3 p-3 rounded-lg bg-blue-900/30 border border-blue-700/50 text-left">
                    <p className="text-xs text-blue-400 mb-1 font-medium">Selected:</p>
                    <p className="text-sm text-gray-300 line-clamp-4">{selectedText}</p>
                  </div>
                )}
              </div>
            </div>
          )
        ) : pdfFile ? (
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-20 text-gray-400">
                Loading PDF viewer...
              </div>
            }
          >
            <PdfViewer file={pdfFile} onSelection={handleSelection} scrollToText={pdfScrollTarget} />
          </Suspense>
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
