import type { QuestionNode } from "@/components/Reader";

export type TreeNode = QuestionNode & {
  parentLocalId: string | null;
  childIds: string[];
  collapsed: boolean;
};

export type ExplorationTree = {
  passageSummary: string;
  nodes: Record<string, TreeNode>;
  rootIds: string[];
};

export function createTree(passageText: string): ExplorationTree {
  // Use first two sentences for a better summary, capped at 120 chars
  const sentences = passageText.match(/[^.!?]*[.!?]+/g);
  let passageSummary: string;
  if (sentences && sentences.length > 0) {
    const twoSentences = sentences.slice(0, 2).join("").trim();
    passageSummary = twoSentences.length > 120
      ? twoSentences.slice(0, 117).trim() + "\u2026"
      : twoSentences;
  } else {
    passageSummary = passageText.slice(0, 80).trim() + "\u2026";
  }
  return { passageSummary, nodes: {}, rootIds: [] };
}

export function addNode(
  tree: ExplorationTree,
  node: QuestionNode,
  parentLocalId: string | null,
): ExplorationTree {
  const treeNode: TreeNode = { ...node, parentLocalId, childIds: [], collapsed: false };
  const nodes = { ...tree.nodes, [node.localId]: treeNode };

  let rootIds = tree.rootIds;
  if (parentLocalId && nodes[parentLocalId]) {
    const parent = nodes[parentLocalId];
    nodes[parentLocalId] = { ...parent, childIds: [...parent.childIds, node.localId] };
  } else {
    rootIds = [...rootIds, node.localId];
  }

  return { ...tree, nodes, rootIds };
}

export function updateTreeNode(
  tree: ExplorationTree,
  localId: string,
  patch: (n: TreeNode) => TreeNode,
): ExplorationTree {
  const existing = tree.nodes[localId];
  if (!existing) return tree;
  return {
    ...tree,
    nodes: { ...tree.nodes, [localId]: patch(existing) },
  };
}

export function toggleCollapsed(tree: ExplorationTree, localId: string): ExplorationTree {
  return updateTreeNode(tree, localId, (n) => ({ ...n, collapsed: !n.collapsed }));
}

export function getPathToRoot(tree: ExplorationTree, localId: string): string[] {
  const path: string[] = [];
  let current: string | null = localId;
  while (current) {
    path.unshift(current);
    const node: TreeNode | undefined = tree.nodes[current];
    if (!node) break;
    current = node.parentLocalId;
  }
  return path;
}

export function rebuildStack(tree: ExplorationTree, targetLocalId: string): QuestionNode[] {
  const path = getPathToRoot(tree, targetLocalId);
  return path.map((id): QuestionNode => {
    const tn = tree.nodes[id];
    return {
      localId: tn.localId,
      interactionId: tn.interactionId,
      parentInteractionId: tn.parentInteractionId,
      title: tn.title,
      question: tn.question,
      selectedText: tn.selectedText,
      displayedText: tn.displayedText,
      status: tn.status,
      searchDetail: tn.searchDetail,
      loading: tn.loading,
    };
  });
}
