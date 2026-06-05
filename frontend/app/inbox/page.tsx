"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  InboxProposal,
  dismissProposal,
  getCurrentUserId,
  listInbox,
  tapProposal,
} from "@/lib/api";


type Group = {
  kickoff_event_id: string;
  cards: InboxProposal[];
};


const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  pending: { label: "open", color: "bg-blue-100 text-blue-800" },
  tapped: { label: "tapped", color: "bg-emerald-100 text-emerald-800" },
  consumed: { label: "consumed", color: "bg-emerald-50 text-emerald-700" },
  dismissed: { label: "dismissed", color: "bg-gray-100 text-gray-600" },
  expired: { label: "expired", color: "bg-amber-50 text-amber-700" },
};


function StatusBadge({ status }: { status: string }) {
  const s = STATUS_LABEL[status] || { label: status, color: "bg-gray-100 text-gray-600" };
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-medium ${s.color}`}>
      {s.label}
    </span>
  );
}


function ProposalCard({
  prop,
  onTap,
  onDismiss,
}: {
  prop: InboxProposal;
  onTap: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const isActionable = prop.status === "pending";
  return (
    <div
      data-testid="inbox-card"
      data-proposal-id={prop.id}
      data-status={prop.status}
      data-posture={prop.posture}
      data-candidate-idx={prop.candidate_idx}
      className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm"
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <h3 className="font-semibold text-gray-900 text-sm leading-tight">
          {prop.title}
        </h3>
        <div className="flex gap-1 shrink-0">
          <span className="text-xs px-2 py-0.5 rounded font-mono bg-violet-100 text-violet-800">
            {prop.posture}
          </span>
          <StatusBadge status={prop.status} />
        </div>
      </div>
      <p className="text-sm text-gray-600 mb-2">{prop.opening}</p>
      <p className="text-xs text-gray-400 mb-3 font-mono">
        candidate #{prop.candidate_idx} · {prop.persona_purpose}
      </p>
      {isActionable && (
        <div className="flex gap-2">
          <button
            data-testid="inbox-card-tap"
            onClick={() => onTap(prop.id)}
            className="text-xs px-3 py-1.5 rounded bg-blue-600 text-white hover:bg-blue-700"
          >
            Tap
          </button>
          <button
            data-testid="inbox-card-dismiss"
            onClick={() => onDismiss(prop.id)}
            className="text-xs px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}


export default function InboxPage() {
  const router = useRouter();
  const [items, setItems] = useState<InboxProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listInbox();
      setItems(data);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === "unknown_user") {
        router.push("/");
        return;
      }
      setError(e instanceof Error ? e.message : "Failed to load inbox");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!getCurrentUserId()) {
      router.push("/");
      return;
    }
    reload();
  }, [reload, router]);

  const handleTap = async (id: string) => {
    try {
      await tapProposal(id);
      await reload();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDismiss = async (id: string) => {
    try {
      await dismissProposal(id);
      await reload();
    } catch (e) {
      console.error(e);
    }
  };

  // Group by kickoff_event_id so K candidates from one kickoff render
  // together (SPEC §6.1.1 — "A few directions:" stacked grouping).
  const groups = useMemo<Group[]>(() => {
    const byKickoff = new Map<string, InboxProposal[]>();
    for (const p of items) {
      const arr = byKickoff.get(p.kickoff_event_id) || [];
      arr.push(p);
      byKickoff.set(p.kickoff_event_id, arr);
    }
    // Sort cards inside each group by candidate_idx.
    const out: Group[] = [];
    for (const [k, cards] of byKickoff) {
      cards.sort((a, b) => a.candidate_idx - b.candidate_idx);
      out.push({ kickoff_event_id: k, cards });
    }
    // Newest groups first.
    out.sort((a, b) => {
      const aTs = a.cards[0]?.created_at || "";
      const bTs = b.cards[0]?.created_at || "";
      return bTs.localeCompare(aTs);
    });
    return out;
  }, [items]);

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Inbox</h1>
        <button
          onClick={reload}
          className="px-3 py-1.5 rounded border border-gray-300 text-sm hover:bg-gray-50"
        >
          Refresh
        </button>
      </div>
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}
      {loading ? (
        <div className="text-center py-12 text-gray-500" data-testid="inbox-loading">
          Loading inbox...
        </div>
      ) : groups.length === 0 ? (
        <div className="text-center py-12 text-gray-500" data-testid="inbox-empty">
          No proposals in your inbox yet. When the Maestro decides ACT, candidates land here.
        </div>
      ) : (
        <div className="space-y-6" data-testid="inbox-groups">
          {groups.map((g) => (
            <section
              key={g.kickoff_event_id}
              data-testid="inbox-group"
              data-kickoff-event-id={g.kickoff_event_id}
            >
              {g.cards.length > 1 && (
                <h2 className="text-sm font-medium text-gray-500 mb-2">
                  A few directions:
                </h2>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                {g.cards.map((p) => (
                  <ProposalCard
                    key={p.id}
                    prop={p}
                    onTap={handleTap}
                    onDismiss={handleDismiss}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
