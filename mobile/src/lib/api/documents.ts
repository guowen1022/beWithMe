// Types only in Phase 1. No call sites until Phase 3.

export interface Document {
  id: string;
  title: string;
  text: string;
  created_at: string;
}

export interface DocumentListItem {
  id: string;
  title: string;
  created_at: string;
}
