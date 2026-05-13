// Types only in Phase 1. No call sites until Phase 3.

export interface Interaction {
  id: string;
  session_id: string;
  passage_text: string | null;
  question: string;
  answer: string;
  source_document: string | null;
  created_at: string;
}
