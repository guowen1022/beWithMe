"use client";

import type { ExplorationTree, TreeNode } from "@/lib/explorationTree";

function TreeNodeRow({
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
  const truncated = label.length > 60 ? label.slice(0, 57) + "\u2026" : label;

  return (
    <div>
      <div
        className={`group flex items-center gap-1.5 py-1 px-2 rounded-md cursor-pointer text-sm transition-colors ${
          isActive
            ? "bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 font-medium"
            : isOnPath
              ? "text-gray-800 dark:text-gray-200 font-medium hover:bg-gray-100 dark:hover:bg-gray-800"
              : "text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleCollapse(node.localId);
            }}
            className="shrink-0 w-4 h-4 flex items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            <svg
              className={`w-3 h-3 transition-transform ${node.collapsed ? "" : "rotate-90"}`}
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
            <span className={`w-1.5 h-1.5 rounded-full ${
              node.loading ? "bg-blue-400 animate-pulse" : "bg-gray-300 dark:bg-gray-600"
            }`} />
          </span>
        )}

        <span
          className="truncate flex-1"
          onClick={() => onNavigate(node.localId)}
          title={label}
        >
          {truncated}
        </span>

        {node.loading && (
          <span className="shrink-0 w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
        )}
      </div>

      {hasChildren && !node.collapsed && (
        <div>
          {node.childIds.map((childId) => {
            const child = tree.nodes[childId];
            if (!child) return null;
            return (
              <TreeNodeRow
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

export default function ExplorationTreePanel({
  tree,
  activeLocalId,
  activePathIds,
  open,
  onClose,
  onNavigate,
  onToggleCollapse,
}: {
  tree: ExplorationTree;
  activeLocalId: string | null;
  activePathIds: Set<string>;
  open: boolean;
  onClose: () => void;
  onNavigate: (localId: string) => void;
  onToggleCollapse: (localId: string) => void;
}) {
  if (!open) return null;

  const hasNodes = tree.rootIds.length > 0;

  return (
    <div className="fixed top-0 left-0 h-full w-72 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700 shadow-lg z-30 flex flex-col">
      <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Exploration
        </h2>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          aria-label="Close exploration panel"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {/* Passage root */}
        <div className="px-2 py-1.5 mb-1">
          <p className="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-0.5">
            Passage
          </p>
          <p className="text-xs text-gray-600 dark:text-gray-400 line-clamp-2">
            {tree.passageSummary}
          </p>
        </div>

        {hasNodes && (
          <div className="border-t border-gray-100 dark:border-gray-800 pt-1">
            {tree.rootIds.map((rootId) => {
              const node = tree.nodes[rootId];
              if (!node) return null;
              return (
                <TreeNodeRow
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

        {!hasNodes && (
          <p className="text-xs text-gray-400 dark:text-gray-500 px-2 py-4 text-center">
            Select text and ask a question to start exploring.
          </p>
        )}
      </div>
    </div>
  );
}
