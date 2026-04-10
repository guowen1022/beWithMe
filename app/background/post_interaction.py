import uuid
from sqlalchemy import update
from app.database import async_session
from app.services.embedding import embed_text
from app.distill import extract_concepts, upsert_concepts_hlr, should_auto_distill, distill_preferences
from app.models.interaction import Interaction


async def post_interaction_update(interaction_id: uuid.UUID):
    """Async background task: embed the interaction, extract concepts with HLR, and auto-distill."""
    print(f"[post_interaction] Starting for {interaction_id}", flush=True)
    async with async_session() as db:
        result = await db.get(Interaction, interaction_id)
        if not result:
            print(f"[post_interaction] Interaction {interaction_id} not found", flush=True)
            return

        # 1. Embed the interaction
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
            print(f"[post_interaction] Embedded OK", flush=True)
        except Exception as e:
            print(f"[post_interaction] Failed to embed: {e}", flush=True)

        # 2. Extract and upsert concepts with HLR
        try:
            concepts = await extract_concepts(
                passage=result.passage_text,
                question=result.question,
                answer=result.answer,
            )
            if concepts:
                nodes = await upsert_concepts_hlr(db, concepts)
                print(f"[post_interaction] Extracted concepts: {[n.name for n in nodes]}", flush=True)
            else:
                print(f"[post_interaction] No concepts extracted", flush=True)
        except Exception as e:
            print(f"[post_interaction] Failed to extract concepts: {e}", flush=True)
            import traceback
            traceback.print_exc()

        # 3. Auto-distill preferences if enough interactions accumulated
        try:
            if await should_auto_distill(db):
                print(f"[post_interaction] Triggering auto-distillation", flush=True)
                await distill_preferences(db)
        except Exception as e:
            print(f"[post_interaction] Failed to auto-distill: {e}", flush=True)

    print(f"[post_interaction] Done for {interaction_id}", flush=True)
