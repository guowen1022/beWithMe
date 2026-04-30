from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from infra.db import get_db
from infra.auth import parse_user_id as get_current_user_id
from persona.teacher.models.learning_goal import LearningGoal
from persona.teacher.goals.planner import plan_initial, plan_expand, plan_feedback

router = APIRouter()


class CreateGoalRequest(BaseModel):
    title: str


class ExpandRequest(BaseModel):
    node_id: str


class FeedbackRequest(BaseModel):
    node_id: str
    action: str  # "know" or "unknown"


@router.post("/goals")
async def create_goal(
    body: CreateGoalRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Create a new learning goal and generate initial prerequisite DAG."""
    result = await plan_initial(body.title)

    goal = LearningGoal(
        user_id=user_id,
        title=body.title,
        dag=result["dag"],
        transcript=[{"role": "assistant", "text": result["text"]}],
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)

    return {
        "id": str(goal.id),
        "title": goal.title,
        "dag": goal.dag,
        "transcript": goal.transcript,
        "status": goal.status,
    }


@router.post("/goals/{goal_id}/expand")
async def expand_node(
    goal_id: UUID,
    body: ExpandRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Expand a prerequisite node into sub-prerequisites."""
    result = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    node_label = next((n["label"] for n in goal.dag["nodes"] if n["id"] == body.node_id), body.node_id)
    plan_result = await plan_expand(body.node_id, goal.dag, goal.transcript)

    goal.dag = plan_result["dag"]
    transcript = list(goal.transcript)
    transcript.append({"role": "user", "text": f"Expand: {node_label}"})
    transcript.append({"role": "assistant", "text": plan_result["text"]})
    goal.transcript = transcript
    goal.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "id": str(goal.id),
        "dag": goal.dag,
        "transcript": goal.transcript,
        "text": plan_result["text"],
    }


@router.post("/goals/{goal_id}/feedback")
async def feedback_node(
    goal_id: UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Mark a node as known or unknown."""
    result = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    plan_result = await plan_feedback(body.node_id, body.action, goal.dag, goal.transcript)

    goal.dag = plan_result["dag"]
    transcript = list(goal.transcript)
    node_label = next((n["label"] for n in goal.dag["nodes"] if n["id"] == body.node_id), body.node_id)
    transcript.append({"role": "user", "text": f"{body.action.capitalize()}: {node_label}"})
    transcript.append({"role": "assistant", "text": plan_result["text"]})
    goal.transcript = transcript
    goal.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "id": str(goal.id),
        "dag": goal.dag,
        "transcript": goal.transcript,
        "text": plan_result["text"],
    }


@router.post("/goals/{goal_id}/finalize")
async def finalize_goal(
    goal_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Mark goal as finalized."""
    result = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    goal.status = "finalized"
    goal.updated_at = datetime.utcnow()
    await db.commit()

    return {"id": str(goal.id), "status": "finalized"}


@router.get("/goals")
async def list_goals(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """List all goals for the user."""
    result = await db.execute(
        select(LearningGoal)
        .where(LearningGoal.user_id == user_id)
        .order_by(LearningGoal.created_at.desc())
    )
    goals = result.scalars().all()
    return [
        {
            "id": str(g.id),
            "title": g.title,
            "status": g.status,
            "node_count": len(g.dag.get("nodes", [])),
            "created_at": g.created_at.isoformat() if g.created_at else "",
        }
        for g in goals
    ]


@router.get("/goals/{goal_id}")
async def get_goal(
    goal_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get a goal with its full DAG."""
    result = await db.execute(
        select(LearningGoal).where(
            LearningGoal.id == goal_id, LearningGoal.user_id == user_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    return {
        "id": str(goal.id),
        "title": goal.title,
        "dag": goal.dag,
        "transcript": goal.transcript,
        "status": goal.status,
        "created_at": goal.created_at.isoformat() if goal.created_at else "",
    }
