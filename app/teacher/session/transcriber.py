"""Session transcriber — saves a timestamped transcript of a learning session to disk."""

import os
import uuid
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.silicon_brain.models.interaction import Interaction

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "sessions"


def _format_timestamp(dt) -> str:
    """Format a datetime as [YYYY-MM-DD HH:MM]."""
    if dt is None:
        return "[unknown time]"
    return dt.strftime("[%Y-%m-%d %H:%M]")


def build_transcript(interactions: list[Interaction], passage_text: str = "") -> str:
    """Format session interactions into a timestamped transcript.

    Each exchange is formatted as:
        [2026-04-15 14:32] User: <question>
        [2026-04-15 14:33] Teacher: <answer>

    The passage text (what the user was reading) is included at the top
    for context.
    """
    lines: list[str] = []

    if passage_text:
        lines.append("## Reading Material\n")
        lines.append(passage_text.strip())
        lines.append("\n---\n")

    lines.append("## Transcript\n")

    for interaction in interactions:
        ts = _format_timestamp(interaction.created_at)

        # Show selected text if the user highlighted something
        if interaction.question:
            lines.append(f"{ts} **User**: {interaction.question}")

        if interaction.answer:
            lines.append(f"{ts} **Teacher**: {interaction.answer}")

        lines.append("")  # blank line between exchanges

    return "\n".join(lines)


def transcript_path(user_id: uuid.UUID, session_id: uuid.UUID) -> Path:
    """Return the file path for a session transcript."""
    return DATA_DIR / str(user_id) / str(session_id) / "transcript.md"


def summary_path(user_id: uuid.UUID, session_id: uuid.UUID) -> Path:
    """Return the file path for a session summary."""
    return DATA_DIR / str(user_id) / str(session_id) / "summary.md"


async def save_transcript(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> Path:
    """Query all interactions for a session and write the transcript to disk.

    Returns the path to the written file.
    """
    # Fetch interactions (own query, same pattern as fetch_session_history)
    stmt = (
        select(Interaction)
        .where(Interaction.user_id == user_id, Interaction.session_id == session_id)
        .order_by(Interaction.created_at.asc())
    )
    result = await db.execute(stmt)
    interactions = list(result.scalars().all())

    if not interactions:
        raise ValueError(f"No interactions found for session {session_id}")

    # Get passage text from the first interaction
    passage_text = interactions[0].passage_text or ""

    transcript = build_transcript(interactions, passage_text)

    # Write to disk
    path = transcript_path(user_id, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript, encoding="utf-8")

    print(
        f"[transcriber] Saved transcript for session {session_id} "
        f"({len(interactions)} interactions) to {path}",
        flush=True,
    )

    return path
