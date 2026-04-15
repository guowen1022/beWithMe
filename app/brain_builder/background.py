"""Background task: after each interaction, feed the brain builder."""

import uuid
from sqlalchemy import update
from app.database import async_session
from app.silicon_brain.models.interaction import Interaction
from app.brain_builder import AgentLearning, process_learning


async def post_interaction_update(interaction_id: uuid.UUID, user_id: uuid.UUID):
    """Async background task: feed the teacher's interaction into the brain builder."""
    print(f"[brain_builder] Starting for {interaction_id}", flush=True)
    async with async_session() as db:
        result = await db.get(Interaction, interaction_id)
        if not result:
            print(f"[brain_builder] Interaction {interaction_id} not found", flush=True)
            return

        text_to_embed = (
            (result.passage_text + " " + result.question)
            if result.passage_text
            else result.question
        )

        # Feed the brain builder
        learning = AgentLearning(
            source="teacher",
            user_id=user_id,
            session_id=result.session_id,
            text_to_embed=text_to_embed,
            answer_text=result.answer,
            context=result.question[:100],
        )

        outcome = await process_learning(db, learning)

        # Store the embedding on the interaction row itself (for retrieval)
        embedding = outcome.get("embedding")
        if embedding:
            stmt = (
                update(Interaction)
                .where(Interaction.id == interaction_id)
                .values(embedding=embedding)
            )
            await db.execute(stmt)
            await db.commit()
            print(f"[brain_builder] Stored interaction embedding", flush=True)

    print(f"[brain_builder] Done for {interaction_id}", flush=True)
