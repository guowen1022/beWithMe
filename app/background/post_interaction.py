import uuid
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session
from app.services.embedding import embed_text
from app.models.interaction import Interaction


async def post_interaction_update(interaction_id: uuid.UUID):
    """Async background task: embed the interaction after it's stored."""
    async with async_session() as db:
        result = await db.get(Interaction, interaction_id)
        if not result:
            return

        text_to_embed = ""
        if result.passage_text:
            text_to_embed = result.passage_text + " " + result.question
        else:
            text_to_embed = result.question

        try:
            embedding = await embed_text(text_to_embed)
            stmt = (
                update(Interaction)
                .where(Interaction.id == interaction_id)
                .values(embedding=embedding)
            )
            await db.execute(stmt)
            await db.commit()
        except Exception as e:
            print(f"Failed to embed interaction {interaction_id}: {e}")
