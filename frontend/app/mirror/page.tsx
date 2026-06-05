"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  StreamEvent,
  getCurrentUserId,
  queryStream,
} from "@/lib/api";


const SOURCE_LABEL: Record<string, { label: string; color: string }> = {
  user: { label: "you", color: "bg-blue-100 text-blue-800" },
  agent: { label: "agent", color: "bg-emerald-100 text-emerald-800" },
  signal: { label: "signal", color: "bg-cyan-50 text-cyan-700" },
  maestro_long: { label: "maestro·long", color: "bg-violet-100 text-violet-800" },
  maestro_short: { label: "maestro·short", color: "bg-violet-50 text-violet-700" },
  system: { label: "system", color: "bg-gray-100 text-gray-700" },
  capture: { label: "capture", color: "bg-amber-100 text-amber-800" },
};


function SourceBadge({ source }: { source: string }) {
  const s = SOURCE_LABEL[source] || { label: source, color: "bg-gray-100 text-gray-700" };
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-mono ${s.color}`}>
      {s.label}
    </span>
  );
}


function EventRow({ event }: { event: StreamEvent }) {
  const ts = new Date(event.ts);
  const tsLabel = ts.toLocaleString();
  return (
    <li
      data-testid="mirror-event"
      data-event-id={event.event_id}
      data-kind={event.kind}
      data-source={event.source}
      className="border-l-2 border-gray-200 pl-3 py-1"
    >
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <span className="font-mono">{tsLabel}</span>
        <SourceBadge source={event.source} />
        <span className="font-mono text-gray-700">{event.kind}</span>
      </div>
      <pre className="text-xs text-gray-600 mt-1 whitespace-pre-wrap break-words font-mono">
        {JSON.stringify(event.body, null, 2)}
      </pre>
    </li>
  );
}


export default function MirrorPage() {
  const router = useRouter();
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await queryStream({ limit: 200, order: "desc" });
      setEvents(data);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === "unknown_user") {
        router.push("/");
        return;
      }
      setError(e instanceof Error ? e.message : "Failed to load mirror");
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

  const grouped = useMemo(() => {
    const byFamily: Record<string, StreamEvent[]> = {};
    for (const e of events) {
      // family = the source token before any dot in `kind`. e.g.
      // 'maestro_long.kickoff_decision' → 'maestro_long', 'user.*' → 'user'.
      const family = e.source;
      const arr = byFamily[family] || [];
      arr.push(e);
      byFamily[family] = arr;
    }
    return byFamily;
  }, [events]);

  const families = Object.keys(grouped).sort();

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Mirror</h1>
        <button
          onClick={reload}
          className="px-3 py-1.5 rounded border border-gray-300 text-sm hover:bg-gray-50"
        >
          Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Every event the system recorded for you, grouped by who emitted it.
        Read-only in Phase 0.
      </p>
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}
      {loading ? (
        <div className="text-center py-12 text-gray-500" data-testid="mirror-loading">
          Loading...
        </div>
      ) : events.length === 0 ? (
        <div className="text-center py-12 text-gray-500" data-testid="mirror-empty">
          No events yet. Start a turn or wait for the Maestro to act.
        </div>
      ) : (
        <div className="space-y-8" data-testid="mirror-content">
          {families.map((family) => (
            <section key={family} data-testid="mirror-family" data-family={family}>
              <h2 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
                <SourceBadge source={family} />
                <span className="text-gray-500 font-mono text-xs">
                  ({grouped[family].length})
                </span>
              </h2>
              <ul className="space-y-2">
                {grouped[family].map((e) => (
                  <EventRow key={e.event_id} event={e} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
