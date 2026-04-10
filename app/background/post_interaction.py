import re
import uuid
from sqlalchemy import update
from app.database import async_session
from app.services.embedding import embed_text
from app.knowledge import parse_concepts, upsert_concepts, link_concepts
from app.user_profile import should_auto_distill, distill_preferences, get_or_create_preferences
from app.user_profile.ema import ema_update, zero_embedding
from app.models.interaction import Interaction


async def post_interaction_update(interaction_id: uuid.UUID, user_id: uuid.UUID):
    """Async background task: embed, extract concepts, create edges, auto-distill."""
    print(f"[post_interaction] Starting for {interaction_id}", flush=True)
    async with async_session() as db:
        result = await db.get(Interaction, interaction_id)
        if not result:
            print(f"[post_interaction] Interaction {interaction_id} not found", flush=True)
            return

        # 1. Embed the interaction
        text_to_embed = (result.passage_text + " " + result.question) if result.passage_text else result.question
        try:
            embedding = await embed_text(text_to_embed)
            stmt = update(Interaction).where(Interaction.id == interaction_id).values(embedding=embedding)
            await db.execute(stmt)
            await db.commit()
            print(f"[post_interaction] Embedded OK", flush=True)

            # 1b. EMA update of preference embedding
            try:
                prefs = await get_or_create_preferences(db, user_id)
                current = [float(x) for x in prefs.preference_embedding] if prefs.preference_embedding is not None else zero_embedding()
                updated = ema_update(current, [float(x) for x in embedding])
                prefs.preference_embedding = [float(x) for x in updated]
                await db.commit()
            except Exception as e:
                print(f"[post_interaction] Failed to update preference embedding: {e}", flush=True)

        except Exception as e:
            print(f"[post_interaction] Failed to embed: {e}", flush=True)

        # 2. Parse concepts from the CONCEPTS: line in the answer
        try:
            concepts = parse_concepts(result.answer)
            if concepts:
                nodes = await upsert_concepts(db, user_id, concepts)
                print(f"[post_interaction] Concepts: {[n.name for n in nodes]}", flush=True)

                # 3. Create temporal edges between co-occurring concepts
                if len(concepts) >= 2:
                    edges = await link_concepts(db, user_id, concepts, context=result.question[:100])
                    print(f"[post_interaction] Edges: {len(edges)}", flush=True)
            else:
                print(f"[post_interaction] No CONCEPTS: line in answer", flush=True)
        except Exception as e:
            print(f"[post_interaction] Failed concepts/edges: {e}", flush=True)

        # 4. Auto-distill preferences
        try:
            if await should_auto_distill(db, user_id):
                print(f"[post_interaction] Auto-distilling", flush=True)
                await distill_preferences(db, user_id)
        except Exception as e:
            print(f"[post_interaction] Failed auto-distill: {e}", flush=True)

    print(f"[post_interaction] Done for {interaction_id}", flush=True)
