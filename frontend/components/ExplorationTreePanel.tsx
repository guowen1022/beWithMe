"use client";

import { useState, useMemo } from "react";
import type { ExplorationTree, TreeNode } from "@/lib/explorationTree";
import type { OutlineSection } from "@/lib/passageOutline";
import { findSectionForText, flattenSections } from "@/lib/passageOutline";

function QuestionRow({
  node,
  depth,
  activeLocalId,
  activePathIds,
  tree,
  onNavigate,
  onToggleCollapse,
}: {
  node: TreeNode;
  depth: number;
  activeLocalId: string | null;
  activePathIds: Set<string>;
  tree: ExplorationTree;
  onNavigate: (localId: string) => void;
  onToggleCollapse: (localId: string) => void;
}) {
  const isActive = node.localId === activeLocalId;
  const isOnPath = activePathIds.has(node.localId);
  const hasChildren = node.childIds.length > 0;
  const label = node.title ?? node.question;
  const truncated = label.length > 50 ? label.slice(0, 47) + "\u2026" : label;

  return (
    <div>
      <div
        className={`group flex items-center gap-1.5 py-0.5 px-2 rounded cursor-pointer text-xs transition-colors ${
          isActive
            ? "bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-200 font-medium"
            : isOnPath
              ? "text-green-700 dark:text-green-400 font-medium hover:bg-gray-100 dark:hover:bg-gray-800"
              : "text-green-600 dark:text-green-500 hover:bg-gray-100 dark:hover:bg-gray-800"
        }`}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleCollapse(node.localId);
            }}
            className="shrink-0 w-3 h-3 flex items-center justify-center text-green-400"
          >
            <svg
              className={`w-2.5 h-2.5 transition-transform ${node.collapsed ? "" : "rotate-90"}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
            >
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
        ) : (
          <span className="shrink-0 w-3 h-3 flex items-center justify-center">
            <span className="w-1 h-1 rounded-full bg-green-400 dark:bg-green-600" />
          </span>
        )}

        <span
          className="truncate flex-1"
          onClick={() => onNavigate(node.localId)}
          title={label}
        >
          {truncated}
        </span>
      </div>

      {hasChildren && !node.collapsed && (
        <div>
          {node.childIds.map((childId) => {
            const child = tree.nodes[childId];
            if (!child) return null;
            return (
              <QuestionRow
                key={childId}
                node={child}
                depth={depth + 1}
                activeLocalId={activeLocalId}
                activePathIds={activePathIds}
                tree={tree}
                onNavigate={onNavigate}
                onToggleCollapse={onToggleCollapse}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Recursive outline section with fold/expand */
function SectionNode({
  section,
  questions,
  tree,
  activeLocalId,
  activePathIds,
  onNavigate,
  onToggleCollapse,
  onSectionClick,
  questionMap,
  depth,
}: {
  section: OutlineSection;
  questions: string[];
  tree: ExplorationTree;
  activeLocalId: string | null;
  activePathIds: Set<string>;
  onNavigate: (localId: string) => void;
  onToggleCollapse: (localId: string) => void;
  onSectionClick?: (section: OutlineSection) => void;
  questionMap: Record<string, string[]>;
  depth: number;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = section.children.length > 0;
  // Include questions from all descendants too
  const allQuestions = [...questions, ...(section.children.flatMap(c => questionMap[c.id] ?? []))];
  const hasQuestions = allQuestions.length > 0 || questions.length > 0;
  const isExpandable = hasChildren || questions.length > 0;

  return (
    <div>
      <div
        className="flex items-center gap-1 py-1 group"
        style={{ paddingLeft: `${depth * 14}px` }}
      >
        {/* Expand/collapse toggle */}
        {isExpandable ? (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 w-4 h-4 flex items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            <svg
              className={`w-3 h-3 transition-transform ${expanded ? "rotate-90" : ""}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
        ) : (
          <span className="shrink-0 w-4 h-4 flex items-center justify-center">
            <span className="w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-gray-600" />
          </span>
        )}

        {/* Section title */}
        <span
          className="text-xs text-gray-700 dark:text-gray-300 font-medium cursor-pointer hover:text-blue-600 dark:hover:text-blue-400 transition-colors truncate flex-1"
          onClick={() => onSectionClick?.(section)}
          title={section.title}
        >
          {section.title}
        </span>

        {/* Question count badge */}
        {hasQuestions && (
          <span className="shrink-0 text-[9px] bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 rounded-full px-1.5 py-0.5 font-medium">
            {questions.length}
          </span>
        )}
      </div>

      {expanded && (
        <div>
          {/* Child sections */}
          {section.children.map((child) => (
            <SectionNode
              key={child.id}
              section={child}
              questions={questionMap[child.id] ?? []}
              tree={tree}
              activeLocalId={activeLocalId}
              activePathIds={activePathIds}
              onNavigate={onNavigate}
              onToggleCollapse={onToggleCollapse}
              onSectionClick={onSectionClick}
              questionMap={questionMap}
              depth={depth + 1}
            />
          ))}

          {/* Questions under this section */}
          {questions.map((rootId) => {
            const node = tree.nodes[rootId];
            if (!node) return null;
            return (
              <QuestionRow
                key={rootId}
                node={node}
                depth={depth + 1}
                activeLocalId={activeLocalId}
                activePathIds={activePathIds}
                tree={tree}
                onNavigate={onNavigate}
                onToggleCollapse={onToggleCollapse}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function ExplorationTreePanel({
  tree,
  activeLocalId,
  activePathIds,
  open,
  onClose,
  onNavigate,
  onToggleCollapse,
  passageText,
  outlineSections,
  onSectionClick,
}: {
  tree: ExplorationTree;
  activeLocalId: string | null;
  activePathIds: Set<string>;
  open: boolean;
  onClose: () => void;
  onNavigate: (localId: string) => void;
  onToggleCollapse: (localId: string) => void;
  passageText: string;
  outlineSections: OutlineSection[];
  onSectionClick?: (section: OutlineSection) => void;
}) {
  if (!open) return null;

  // Map root questions to outline sections
  const sectionQuestions = useMemo(() => {
    const map: Record<string, string[]> = {};
    const unmapped: string[] = [];

    for (const rootId of tree.rootIds) {
      const node = tree.nodes[rootId];
      if (!node) continue;
      const textToMatch = node.selectedText || node.question;
      const sectionId = findSectionForText(outlineSections, passageText, textToMatch);
      if (sectionId) {
        (map[sectionId] ??= []).push(rootId);
      } else {
        unmapped.push(rootId);
      }
    }
    return { map, unmapped };
  }, [tree.rootIds, tree.nodes, outlineSections, passageText]);

  return (
    <div className="fixed top-0 left-0 h-full w-96 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 shadow-lg z-30 flex flex-col">
      <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Outline
        </h2>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          aria-label="Close panel"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-1 py-2">
        {outlineSections.length > 0 ? (
          <div>
            {outlineSections.map((section) => (
              <SectionNode
                key={section.id}
                section={section}
                questions={sectionQuestions.map[section.id] ?? []}
                tree={tree}
                activeLocalId={activeLocalId}
                activePathIds={activePathIds}
                onNavigate={onNavigate}
                onToggleCollapse={onToggleCollapse}
                onSectionClick={onSectionClick}
                questionMap={sectionQuestions.map}
                depth={0}
              />
            ))}

            {/* Unmapped questions */}
            {sectionQuestions.unmapped.length > 0 && (
              <div className="mt-2 pt-2 border-t border-gray-100 dark:border-gray-800">
                <div className="px-2 py-1 text-[10px] text-gray-400 uppercase tracking-wider font-medium">
                  General
                </div>
                {sectionQuestions.unmapped.map((rootId) => {
                  const node = tree.nodes[rootId];
                  if (!node) return null;
                  return (
                    <QuestionRow
                      key={rootId}
                      node={node}
                      depth={0}
                      activeLocalId={activeLocalId}
                      activePathIds={activePathIds}
                      tree={tree}
                      onNavigate={onNavigate}
                      onToggleCollapse={onToggleCollapse}
                    />
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <div className="px-3 py-2">
            <p className="text-xs text-gray-500 dark:text-gray-400 line-clamp-3">
              {tree.passageSummary}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
