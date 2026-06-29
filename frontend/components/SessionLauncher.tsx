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
//
// Content is prepared OFFLINE (session-end webhook + Maestro scheduler), so the
// open path is a pure cache read: getFeed() returns instantly and never blocks
// on the LLM. When nothing is cached yet we show instant, non-LLM starter cards
// and quietly re-fetch (on focus) as background-produced cards land.

// One-shot handoff into the Reader: CanvasCommandBar consumes these on mount.
// SEED_KEY carries the first-turn text; SEED_AUTOSEND_KEY ("1") tells the bar
// to fire it immediately (Begin) rather than just prefill it (start-from-scratch).
const SEED_KEY = "bewithme_canvas_seed";
const SEED_AUTOSEND_KEY = "bewithme_canvas_seed_autosend";

// After a manual "Prepare new options", the server regenerates in the
// background. Keep the current cards visible and silently re-list a few times
// until the fresh batch lands (or we hit the ceiling). NOT used on normal open.
const REFRESH_POLL_MS = 5000;
const MAX_REFRESH_POLLS = 8;

function personaLabel(p: string): string {
  return p.charAt(0).toUpperCase() + p.slice(1);
}

function tagLabel(card: FeedCard): string {
  // Teacher cards carry a category (review/explore/deepen); fall back to posture.
  const raw = card.category || card.posture || "";
  return raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : "";
}

// The card shows a short one-line hook, not the full `opening` (that long
// paragraph is the session-seed framing, delivered when the learner taps Begin).
// Fall back to `opening` for older cards minted before hooks existed — the
// cardBody clamp keeps even that from running long.
function cardHook(card: FeedCard): string {
  const h = card.body && typeof card.body.hook === "string" ? (card.body.hook as string).trim() : "";
  return h || card.opening;
}

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
  gap: 16,
};

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

const cardShell: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  background: "var(--bw-surface)",
  border: "1px solid var(--bw-border)",
  borderTop: "2px solid var(--bw-accent)",
  padding: "18px 18px 16px",
  minHeight: 190,
};

const tagChip: CSSProperties = {
  fontFamily: "var(--bw-font-mono)",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.12em",
  color: "var(--bw-accent)",
  background: "var(--bw-accent-soft)",
  border: "1px solid color-mix(in oklab, var(--bw-accent) 22%, transparent)",
  padding: "2px 8px",
};

const cardTitle: CSSProperties = {
  fontFamily: "var(--bw-font-sans)",
  fontSize: 17,
  fontWeight: 700,
  letterSpacing: "-0.02em",
  color: "var(--bw-ink)",
  lineHeight: 1.25,
  margin: 0,
};

const cardBody: CSSProperties = {
  fontFamily: "var(--bw-font-sans)",
  fontSize: 13.5,
  lineHeight: 1.6,
  color: "var(--bw-ink-muted)",
  margin: "10px 0 0",
  flex: 1,
  // Hard cap at 3 lines so a long hook (or a fallback `opening`) can't turn the
  // card back into a wall of text.
  display: "-webkit-box",
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: 3,
  overflow: "hidden",
};

const primaryButton: CSSProperties = {
  fontFamily: "var(--bw-font-sans)",
  fontSize: 13,
  fontWeight: 600,
  color: "#fff",
  background: "var(--bw-accent)",
  border: "1px solid var(--bw-accent)",
  borderRadius: 0,
  padding: "8px 18px",
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
      style={cardShell}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 8 }}>
        {tag && <span style={tagChip}>{tag}</span>}
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

      <h3 style={cardTitle}>{card.title}</h3>
      <p style={cardBody}>{cardHook(card)}</p>

      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button
          data-testid="feed-card-begin"
          disabled={busy}
          onClick={() => onBegin(card)}
          style={{ ...primaryButton, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}
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

// Instant, non-LLM cold-start cards. Shown only when nothing is cached yet;
// real personalized cards replace them once the offline producer lands a batch.
function StarterCard({
  tag,
  title,
  body,
  cta,
  onAct,
}: {
  tag: string;
  title: string;
  body: string;
  cta: string;
  onAct: () => void;
}) {
  return (
    <div data-testid="feed-starter-card" data-starter={tag.toLowerCase()} style={cardShell}>
      <div style={{ display: "flex", marginBottom: 12 }}>
        <span style={tagChip}>{tag}</span>
      </div>
      <h3 style={cardTitle}>{title}</h3>
      <p style={cardBody}>{body}</p>
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button data-testid="feed-starter-begin" onClick={onAct} style={primaryButton}>
          {cta}
        </button>
      </div>
    </div>
  );
}

export default function SessionLauncher({
  onEnterReader,
  onViewPath,
  onStartProject,
}: {
  // `autostart` true means the Reader should fire the seeded turn immediately
  // (Begin) and skip the welcome card; false/omitted is the manual entry.
  onEnterReader: (autostart?: boolean) => void;
  // Open the learning-path view (the learner's journey). Optional so the
  // launcher still renders without it.
  onViewPath?: () => void;
  // Start the goal-anchored project demo (Phase A: run Brightwell's books).
  onStartProject?: () => void;
}) {
  const [cards, setCards] = useState<FeedCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasResumable, setHasResumable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const startedRef = useRef(false);
  const refreshStartRef = useRef(0);

  // Pure cache read — fast, no LLM. Does NOT flip `loading` true on re-fetch, so
  // focus/refresh re-lists update the grid in place without a blank flash.
  const load = useCallback(async () => {
    try {
      setError(null);
      const resp = await getFeed();
      setCards(resp.cards);
      setHasResumable(Boolean(resp.has_resumable));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load your feed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    load();
  }, [load]);

  // Surface background-prepared cards: re-list whenever the window regains
  // focus (content was likely prepared offline since we last looked).
  useEffect(() => {
    const onVisible = () => {
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        load();
      }
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  // While a manual refresh is in flight, silently re-list until the fresh batch
  // lands (a card newer than when we asked) or we hit the ceiling. Current cards
  // stay visible the whole time — no blank spinner.
  useEffect(() => {
    if (!refreshing) return;
    let tries = 0;
    const id = window.setInterval(async () => {
      tries += 1;
      try {
        const resp = await getFeed();
        setCards(resp.cards);
        setHasResumable(Boolean(resp.has_resumable));
        const landed = resp.cards.some(
          (c) => c.created_at && new Date(c.created_at).getTime() >= refreshStartRef.current,
        );
        if (landed) setRefreshing(false);
      } catch {
        // Keep trying until the ceiling; transient failures are non-fatal.
      }
      if (tries >= MAX_REFRESH_POLLS) setRefreshing(false);
    }, REFRESH_POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshing]);

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
    if (busy || refreshing) return;
    setRefreshing(true);
    refreshStartRef.current = Date.now();
    try {
      await refreshFeed();
    } catch {
      // Non-fatal — the silent poll / focus refetch still surfaces updates.
    }
  };

  const startFromScratch = () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(SEED_KEY);
      window.localStorage.removeItem(SEED_AUTOSEND_KEY);
    }
    onEnterReader(false);
  };

  const handleContinue = () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(SEED_KEY, "Let's continue where we left off.");
      window.localStorage.setItem(SEED_AUTOSEND_KEY, "1");
    }
    onEnterReader(true);
  };

  const starters: {
    key: string;
    tag: string;
    title: string;
    body: string;
    cta: string;
    onAct: () => void;
  }[] = [
    ...(hasResumable
      ? [{
          key: "continue",
          tag: "Continue",
          title: "Pick up where you left off",
          body: "Jump back into your last thread.",
          cta: "Continue",
          onAct: handleContinue,
        }]
      : []),
    {
      key: "explore",
      tag: "Explore",
      title: "Browse a new topic",
      body: "Start a fresh thread on anything you're curious about.",
      cta: "Begin",
      onAct: startFromScratch,
    },
    {
      key: "paste",
      tag: "Paste",
      title: "Drop in something you're reading",
      body: "Paste a passage and we'll read it together.",
      cta: "Begin",
      onAct: startFromScratch,
    },
  ];

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
          {loading ? (
            <div
              data-testid="feed-loading"
              style={{
                textAlign: "center",
                color: "var(--bw-ink-muted)",
                fontFamily: "var(--bw-font-mono)",
                fontSize: 13,
                padding: "48px 0",
              }}
            >
              Loading…
            </div>
          ) : cards.length > 0 ? (
            <div data-testid="feed-grid" style={gridStyle}>
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
          ) : (
            <div data-testid="feed-starters">
              <div style={gridStyle}>
                {starters.map((s) => (
                  <StarterCard
                    key={s.key}
                    tag={s.tag}
                    title={s.title}
                    body={s.body}
                    cta={s.cta}
                    onAct={s.onAct}
                  />
                ))}
              </div>
              <p
                style={{
                  textAlign: "center",
                  color: "var(--bw-ink-faint)",
                  fontFamily: "var(--bw-font-mono)",
                  fontSize: 12,
                  margin: "20px 0 0",
                }}
              >
                · lining up personalized threads…
              </p>
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
            <button onClick={handlePrepareNew} disabled={busy || refreshing} style={ghostButton}>
              {refreshing ? "Preparing…" : "↻ Prepare new options"}
            </button>
            <button onClick={startFromScratch} style={ghostButton}>
              ✎ Start from scratch
            </button>
            {onStartProject && (
              <button data-testid="start-project" onClick={onStartProject} style={primaryButton}>
                ▣ Run a company&apos;s books
              </button>
            )}
            {onViewPath && (
              <button data-testid="view-path" onClick={onViewPath} style={ghostButton}>
                ◈ Your learning path
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
