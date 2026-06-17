"""Per-device talk-channel preference.

Three structured fields — `talk_desktop`, `talk_tablet`, `talk_phone` —
each one of `voice` | `text` | `both`. Replaces the earlier free-text
field, which was too ambiguous for users to fill in usefully.

The teacher's system prompt renders these as a deterministic rule
("Desktop=both, Phone=text — pick the channel that matches the active
device class"), so the LLM doesn't have to interpret natural language.

This router lives on the `knowledge` (silicon_brain) sidecar and is
separate from `/api/preferences`, which operates on the teacher's
distilled view (`TeacherPreferenceModel`).
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import parse_user_id as get_current_user_id
from infra.db import get_db
from silicon_brain.models.user_preferences import UserPreferences

router = APIRouter()


TalkChannel = Literal["voice", "text", "both"]


class TalkPreferenceRead(BaseModel):
    desktop: TalkChannel
    tablet: TalkChannel
    phone: TalkChannel


class TalkPreferenceUpdate(BaseModel):
    desktop: TalkChannel
    tablet: TalkChannel
    phone: TalkChannel


async def _get_or_create_user_preferences(
    db: AsyncSession, user_id: UUID
) -> UserPreferences:
    result = await db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user_id)
    )
    prefs = result.scalar_one_or_none()
    if prefs is None:
        prefs = UserPreferences(user_id=user_id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


def _to_read(prefs: UserPreferences) -> TalkPreferenceRead:
    return TalkPreferenceRead(
        desktop=prefs.talk_desktop,  # type: ignore[arg-type]
        tablet=prefs.talk_tablet,    # type: ignore[arg-type]
        phone=prefs.talk_phone,      # type: ignore[arg-type]
    )


@router.get("/talk-preference", response_model=TalkPreferenceRead)
async def get_talk_preference(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    prefs = await _get_or_create_user_preferences(db, user_id)
    return _to_read(prefs)


@router.put("/talk-preference", response_model=TalkPreferenceRead)
async def update_talk_preference(
    body: TalkPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    prefs = await _get_or_create_user_preferences(db, user_id)
    prefs.talk_desktop = body.desktop
    prefs.talk_tablet = body.tablet
    prefs.talk_phone = body.phone
    await db.commit()
    await db.refresh(prefs)
    return _to_read(prefs)


# Voice/TTS output prefs live on the same user-stated UserPreferences row as
# talk-preference, so the `speak` tool reads them here (not via /api/preferences,
# which is the teacher's distilled TeacherPreferenceModel view).
class VoicePreferenceRead(BaseModel):
    voice_id: str
    voice_speed: float
    voice_lang: str


@router.get("/voice-preference", response_model=VoicePreferenceRead)
async def get_voice_preference(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Voice/TTS output prefs for the `speak` tool. Defaults (af_heart /
    1.0 / en-us) come from the UserPreferences model, applied on first create."""
    prefs = await _get_or_create_user_preferences(db, user_id)
    return VoicePreferenceRead(
        voice_id=prefs.voice_id,
        voice_speed=float(prefs.voice_speed),
        voice_lang=prefs.voice_lang,
    )
