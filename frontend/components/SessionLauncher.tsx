"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  dismissCard,
  getFeed,
  refreshFeed,
  selectCard,
  type FeedCard,
} from "@/lib/api";

// The browseable landing feed. Personas contribute cards; the Maestro blends
// them (saturation-weighted) into one ranked list. The user browses and picks
// one to begin — selecting points the (always-running) engagement machinery
// at that content. Cinematic dark, single aurora accent, sharp corners — see
// app/globals.css design tokens.

// One-shot handoff into the Reader: CanvasCommandBar consumes these on mount.
// SEED_KEY carries the first-turn text; SEED_AUTOSEND_KEY ("1") tells the bar
// to fire it immediately (Begin) rather than just prefill it (start-from-scratch).
const SEED_KEY = "bewithme_canvas_seed";
const SEED_AUTOSEND_KEY = "bewithme_canvas_seed_autosend";
// How many times to re-poll while the feed is still being prepared.
const MAX_PREPARE_POLLS = 3;
const PREPARE_POLL_MS = 4000;

function personaLabel(p: string): string {
  return p.charAt(0).toUpperCase() + p.slice(1);
}

function tagLabel(card: FeedCard): string {
  // Teacher cards carry a category (review/explore/deepen); fall back to posture.
  const raw = card.category || card.posture || "";
  return raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : "";
}

const ghostButton: CSSProperties = {
  fontFamily: "var(--bw-font-mono)",
  fontSize: 12,
  letterSpacing: "0.04em",
  color: "var(--bw-ink-muted)",
  background: "transparent",
  border: "1px solid var(--bw-border)",
  borderRadius: 0,
  padding: "8px 14px",
  cursor: "pointer",
};

function Card({
  card,
  onBegin,
  onDismiss,
  busy,
}: {
  card: FeedCard;
  onBegin: (c: FeedCard) => void;
  onDismiss: (c: FeedCard) => void;
  busy: boolean;
}) {
  const tag = tagLabel(card);
  return (
    <div
      data-testid="feed-card"
      data-card-id={card.id}
      data-persona={card.source_persona}
      data-posture={card.posture}
      style={{
        display: "flex",
        flexDirection: "column",
        background: "var(--bw-surface)",
        border: "1px solid var(--bw-border)",
        borderTop: "2px solid var(--bw-accent)",
        padding: "18px 18px 16px",
        minHeight: 190,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 8 }}>
        {tag && (
          <span
            style={{
              fontFamily: "var(--bw-font-mono)",
              fontSize: 10,
              textTransform: "uppercase",
              letterSpacing: "0.12em",
              color: "var(--bw-accent)",
              background: "var(--bw-accent-soft)",
              border: "1px solid color-mix(in oklab, var(--bw-accent) 22%, transparent)",
              padding: "2px 8px",
            }}
          >
            {tag}
          </span>
        )}
        <span
          style={{
            fontFamily: "var(--bw-font-mono)",
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            color: "var(--bw-ink-faint)",
          }}
        >
          {personaLabel(card.source_persona)}
        </span>
      </div>

      <h3
        style={{
          fontFamily: "var(--bw-font-sans)",
          fontSize: 17,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: "var(--bw-ink)",
          lineHeight: 1.25,
          margin: 0,
        }}
      >
        {card.title}
      </h3>

      <p
        style={{
          fontFamily: "var(--bw-font-sans)",
          fontSize: 13.5,
          lineHeight: 1.6,
          color: "var(--bw-ink-muted)",
          margin: "10px 0 0",
          flex: 1,
        }}
      >
        {card.opening}
      </p>

      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button
          data-testid="feed-card-begin"
          disabled={busy}
          onClick={() => onBegin(card)}
          style={{
            fontFamily: "var(--bw-font-sans)",
            fontSize: 13,
            fontWeight: 600,
            color: "#fff",
            background: "var(--bw-accent)",
            border: "1px solid var(--bw-accent)",
            borderRadius: 0,
            padding: "8px 18px",
            cursor: busy ? "default" : "pointer",
            opacity: busy ? 0.6 : 1,
          }}
        >
          Begin
        </button>
        <button
          data-testid="feed-card-dismiss"
          disabled={busy}
          onClick={() => onDismiss(card)}
          style={{ ...ghostButton, fontSize: 12, padding: "8px 14px" }}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

export default function SessionLauncher({
  onEnterReader,
}: {
  // `autostart` true means the Reader should fire the seeded turn immediately
  // (Begin) and skip the welcome card; false/omitted is the manual entry.
  onEnterReader: (autostart?: boolean) => void;
}) {
  const [cards, setCards] = useState<FeedCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pollsRef = useRef(0);
  const startedRef = useRef(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const resp = await getFeed();
      setCards(resp.cards);
      // Empty + stale → the Maestro just kicked off async production; poll a
      // few times so freshly-produced cards appear without a manual refresh.
      if (resp.cards.length === 0 && resp.stale && pollsRef.current < MAX_PREPARE_POLLS) {
        pollsRef.current += 1;
        setPreparing(true);
        window.setTimeout(load, PREPARE_POLL_MS);
      } else {
        setPreparing(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load your feed");
      setPreparing(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    load();
  }, [load]);

  const handleBegin = async (card: FeedCard) => {
    if (busy) return;
    setBusy(true);
    try {
      // Marks the card selected and seeds the Maestro cache under the card's
      // purpose, so the teacher's first turn is framed by its posture/opening.
      await selectCard(card.id);
    } catch {
      // Non-fatal: still take the user into the Reader.
    }
    if (typeof window !== "undefined") {
      // Hand the first turn to the command bar AND tell it to fire immediately:
      // the thread starts on its own, framed by the posture seeded above.
      window.localStorage.setItem(SEED_KEY, `Let's get into: ${card.title}`);
      window.localStorage.setItem(SEED_AUTOSEND_KEY, "1");
    }
    onEnterReader(true);
  };

  const handleDismiss = async (card: FeedCard) => {
    setCards((prev) => prev.filter((c) => c.id !== card.id));
    try {
      await dismissCard(card.id);
    } catch {
      // Non-fatal: the card is already gone from the UI.
    }
  };

  const handlePrepareNew = async () => {
    if (busy) return;
    setBusy(true);
    setPreparing(true);
    pollsRef.current = 0;
    try {
      await refreshFeed();
    } catch {
      // Non-fatal — load() below surfaces whatever exists.
    }
    setBusy(false);
    await load();
  };

  const startFromScratch = () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(SEED_KEY);
      window.localStorage.removeItem(SEED_AUTOSEND_KEY);
    }
    onEnterReader(false);
  };

  const showEmpty = !loading && !preparing && cards.length === 0;

  return (
    <div
      style={{
        position: "relative",
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        background: "var(--bw-void)",
      }}
    >
      {/* Aurora glow — single accent, top-anchored. */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background:
            "radial-gradient(900px 380px at 50% -120px, var(--bw-accent-soft), transparent 70%)",
        }}
      />

      <div
        style={{
          position: "relative",
          maxWidth: 1040,
          margin: "0 auto",
          padding: "72px 24px 48px",
        }}
      >
        <h1
          style={{
            fontFamily: "var(--bw-font-sans)",
            fontSize: 38,
            fontWeight: 800,
            letterSpacing: "-0.035em",
            color: "var(--bw-ink)",
            margin: 0,
            textAlign: "center",
          }}
        >
          What do you want to get into?
        </h1>
        <p
          style={{
            fontFamily: "var(--bw-font-sans)",
            fontSize: 15,
            color: "var(--bw-ink-muted)",
            textAlign: "center",
            margin: "12px 0 0",
          }}
        >
          Pick a thread, or start from scratch.
        </p>

        {error && (
          <div
            style={{
              marginTop: 24,
              padding: "10px 14px",
              background: "rgba(229,131,124,0.14)",
              border: "1px solid rgba(229,131,124,0.4)",
              color: "#E5837C",
              fontFamily: "var(--bw-font-mono)",
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}

        <div style={{ marginTop: 40 }}>
          {loading || preparing ? (
            <div
              data-testid="feed-preparing"
              style={{
                textAlign: "center",
                color: "var(--bw-ink-muted)",
                fontFamily: "var(--bw-font-mono)",
                fontSize: 13,
                padding: "48px 0",
              }}
            >
              {preparing ? "Preparing your options…" : "Loading…"}
            </div>
          ) : showEmpty ? (
            <div
              data-testid="feed-empty"
              style={{ textAlign: "center", padding: "32px 0" }}
            >
              <p style={{ color: "var(--bw-ink-muted)", fontSize: 14, margin: "0 0 18px" }}>
                Nothing prepared yet — tell me what you want to learn and we&apos;ll start fresh.
              </p>
              <button
                onClick={startFromScratch}
                style={{
                  fontFamily: "var(--bw-font-sans)",
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#fff",
                  background: "var(--bw-accent)",
                  border: "1px solid var(--bw-accent)",
                  borderRadius: 0,
                  padding: "10px 22px",
                  cursor: "pointer",
                }}
              >
                Start from scratch
              </button>
            </div>
          ) : (
            <div
              data-testid="feed-grid"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                gap: 16,
              }}
            >
              {cards.map((card) => (
                <Card
                  key={card.id}
                  card={card}
                  onBegin={handleBegin}
                  onDismiss={handleDismiss}
                  busy={busy}
                />
              ))}
            </div>
          )}
        </div>

        {!loading && (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              gap: 12,
              marginTop: 36,
            }}
          >
            <button onClick={handlePrepareNew} disabled={busy || preparing} style={ghostButton}>
              ↻ Prepare new options
            </button>
            <button onClick={startFromScratch} style={ghostButton}>
              ✎ Start from scratch
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
