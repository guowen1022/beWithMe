"use client";

import { useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { QuestionNode } from "./Reader";
import { parseMarkdownBlocks } from "@/lib/markdownBlocks";
import { useRegisterBlock } from "@/lib/blockRegistry";

function RegisteredParentBlock({
  registryId,
  active,
  children,
}: {
  registryId: string;
  active: boolean;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useRegisterBlock({ id: registryId, kind: "parent", ref });
  return (
    <div
      ref={ref}
      className={
        active
          ? "rounded-lg border-2 border-blue-500/50 dark:border-blue-400/40 bg-white dark:bg-gray-900 px-4 py-3 shadow-sm"
          : "rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2.5 text-sm text-gray-500 dark:text-gray-400 hover:border-gray-300 dark:hover:border-gray-600 transition-colors"
      }
    >
      {children}
    </div>
  );
}

/**
 * The parent of the active question, shown in the center.
 *
 * Shows the parent answer as blocks:
 * - The block that the child question targets: fully expanded
 * - Other blocks: collapsed to bold summary headers
 * - The child's question shown below the expanded block
 */
export default function ParentCard({
  node,
  childNode,
  onPop,
}: {
  node: QuestionNode;
  childNode: QuestionNode | null;
  onPop: () => void;
}) {
  const blocks = useMemo(
    () => parseMarkdownBlocks(node.displayedText),
    [node.displayedText],
  );

  // Find which block the child question drilled into (by matching selectedText)
  const activeBlockId = useMemo(() => {
    if (!childNode?.selectedText || blocks.length === 0) return null;
    const sel = childNode.selectedText.trim();
    // Find the block whose markdown matches the child's selectedText
    const match = blocks.find((b) => b.markdown.trim() === sel);
    if (match) return match.id;
    // Fuzzy: find block that contains the selected text
    const fuzzy = blocks.find((b) => b.markdown.includes(sel.slice(0, 100)));
    return fuzzy?.id ?? null;
  }, [blocks, childNode?.selectedText]);

  function handleClick() {
    const sel = window.getSelection();
    if (sel && sel.toString().trim().length > 0) return;
    onPop();
  }

  return (
    <div
      onClick={handleClick}
      className="max-w-3xl mx-auto px-6 py-8 sm:px-12 cursor-pointer"
      title="Click to return to this question"
    >
      {/* Parent title */}
      {node.title && (
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-4">
          {node.title}
        </h2>
      )}

      {/* Blocks view */}
      <div className="space-y-1.5">
        {blocks.map((block) => {
          const isActive = block.id === activeBlockId;

          const registryId = `parent:${node.localId}:${block.id}`;

          if (isActive) {
            // Expanded block + child question underneath
            return (
              <div key={block.id}>
                <RegisteredParentBlock registryId={registryId} active>
                  <article className="prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:mt-2 prose-headings:mb-1">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[rehypeKatex]}
                    >
                      {block.markdown}
                    </ReactMarkdown>
                  </article>
                </RegisteredParentBlock>
                {/* Child question badge */}
                {childNode && (
                  <div className="ml-4 mt-1.5 mb-1 flex items-start gap-2">
                    <div className="w-0.5 h-full bg-purple-400/50 dark:bg-purple-500/40 shrink-0" />
                    <div className="rounded-lg bg-purple-50 dark:bg-purple-900/30 border border-purple-200 dark:border-purple-700/50 px-3 py-2 text-sm text-purple-800 dark:text-purple-300">
                      <span className="text-[10px] font-semibold uppercase tracking-wider text-purple-500 dark:text-purple-400 block mb-0.5">
                        Question
                      </span>
                      {childNode.question}
                    </div>
                  </div>
                )}
              </div>
            );
          }

          // Collapsed block — summary with border
          return (
            <RegisteredParentBlock key={block.id} registryId={registryId} active={false}>
              {block.summary}
            </RegisteredParentBlock>
          );
        })}
      </div>
    </div>
  );
}
