"""Router for analytics endpoints.

Each endpoint performs SQL aggregation queries on the interaction data
populated by the ETL pipeline. All endpoints require a `lab` query
parameter to filter results by lab (e.g., "lab-01").
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, distinct
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models.interaction import InteractionLog
from app.models.item import ItemRecord
from app.models.learner import Learner

router = APIRouter()


async def _get_lab_and_task_ids(session: AsyncSession, lab: str) -> tuple[int | None, list[int]]:
    """Find lab item and its child task items by lab identifier.

    Transforms 'lab-04' → title contains 'Lab 04'.
    Returns (lab_id, list of task_ids).
    """
    # Transform lab-04 → Lab 04 for title matching
    lab_title_part = lab.replace("lab-", "Lab ").replace("LAB-", "Lab ")

    # Find the lab item
    lab_item = (await session.exec(
        select(ItemRecord).where(
            ItemRecord.type == "lab",
            ItemRecord.title.contains(lab_title_part)  # type: ignore[attr-defined]
        )
    )).first()

    if lab_item is None:
        return None, []

    # Find all tasks that belong to this lab
    tasks = (await session.exec(
        select(ItemRecord).where(
            ItemRecord.type == "task",
            ItemRecord.parent_id == lab_item.id
        )
    )).all()

    task_ids = [t.id for t in tasks]  # type: ignore[misc]
    return lab_item.id, task_ids  # type: ignore[return-value]


@router.get("/scores")
async def get_scores(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Score distribution histogram for a given lab.

    Returns a JSON array with 4 buckets:
    [{"bucket": "0-25", "count": 12}, {"bucket": "26-50", "count": 8}, ...]
    Always returns all four buckets, even if count is 0.
    """
    lab_id, task_ids = await _get_lab_and_task_ids(session, lab)

    if lab_id is None or not task_ids:
        return [
            {"bucket": "0-25", "count": 0},
            {"bucket": "26-50", "count": 0},
            {"bucket": "51-75", "count": 0},
            {"bucket": "76-100", "count": 0},
        ]

    # Query interactions for these items that have a score
    # Group scores into buckets using CASE WHEN
    bucket_0_25 = (await session.exec(
        select(func.count(InteractionLog.id)).where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
            InteractionLog.score.isnot(None),
            InteractionLog.score >= 0,
            InteractionLog.score <= 25,
        )
    )).one()

    bucket_26_50 = (await session.exec(
        select(func.count(InteractionLog.id)).where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
            InteractionLog.score.isnot(None),
            InteractionLog.score >= 26,
            InteractionLog.score <= 50,
        )
    )).one()

    bucket_51_75 = (await session.exec(
        select(func.count(InteractionLog.id)).where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
            InteractionLog.score.isnot(None),
            InteractionLog.score >= 51,
            InteractionLog.score <= 75,
        )
    )).one()

    bucket_76_100 = (await session.exec(
        select(func.count(InteractionLog.id)).where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
            InteractionLog.score.isnot(None),
            InteractionLog.score >= 76,
            InteractionLog.score <= 100,
        )
    )).one()

    return [
        {"bucket": "0-25", "count": bucket_0_25 or 0},
        {"bucket": "26-50", "count": bucket_26_50 or 0},
        {"bucket": "51-75", "count": bucket_51_75 or 0},
        {"bucket": "76-100", "count": bucket_76_100 or 0},
    ]


@router.get("/pass-rates")
async def get_pass_rates(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Per-task pass rates for a given lab.

    Returns a JSON array:
    [{"task": "Repository Setup", "avg_score": 92.3, "attempts": 150}, ...]
    Ordered by task title.
    """
    lab_id, task_ids = await _get_lab_and_task_ids(session, lab)

    if lab_id is None or not task_ids:
        return []

    # Get task titles
    tasks = (await session.exec(
        select(ItemRecord).where(ItemRecord.id.in_(task_ids))  # type: ignore[attr-defined]
    )).all()
    task_id_to_title = {t.id: t.title for t in tasks}

    # Query avg_score and attempts per task
    results = (await session.exec(
        select(
            InteractionLog.item_id,
            func.round(func.avg(InteractionLog.score), 1).label("avg_score"),
            func.count(InteractionLog.id).label("attempts"),
        )
        .where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
        )
        .group_by(InteractionLog.item_id)
    )).all()

    response: list[dict[str, Any]] = []
    for item_id, avg_score, attempts in results:
        response.append({
            "task": task_id_to_title.get(item_id, "Unknown"),
            "avg_score": float(avg_score) if avg_score is not None else 0.0,
            "attempts": attempts,
        })

    # Order by task title
    response.sort(key=lambda x: x["task"])
    return response


@router.get("/timeline")
async def get_timeline(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Submissions per day for a given lab.

    Returns a JSON array:
    [{"date": "2026-02-28", "submissions": 45}, ...]
    Ordered by date ascending.
    """
    lab_id, task_ids = await _get_lab_and_task_ids(session, lab)

    if lab_id is None or not task_ids:
        return []

    # Group interactions by date
    results = (await session.exec(
        select(
            func.date(InteractionLog.created_at).label("date"),
            func.count(InteractionLog.id).label("submissions"),
        )
        .where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
        )
        .group_by(func.date(InteractionLog.created_at))
        .order_by(func.date(InteractionLog.created_at))
    )).all()

    return [
        {"date": str(date), "submissions": count}
        for date, count in results
    ]


@router.get("/groups")
async def get_groups(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Per-group performance for a given lab.

    Returns a JSON array:
    [{"group": "B23-CS-01", "avg_score": 78.5, "students": 25}, ...]
    Ordered by group name.
    """
    lab_id, task_ids = await _get_lab_and_task_ids(session, lab)

    if lab_id is None or not task_ids:
        return []

    # Join interactions with learners to get student_group
    results = (await session.exec(
        select(
            Learner.student_group.label("group"),
            func.round(func.avg(InteractionLog.score), 1).label("avg_score"),
            func.count(distinct(Learner.id)).label("students"),
        )
        .join(InteractionLog, InteractionLog.learner_id == Learner.id)
        .where(
            InteractionLog.item_id.in_(task_ids),  # type: ignore[attr-defined]
        )
        .group_by(Learner.student_group)
        .order_by(Learner.student_group)
    )).all()

    return [
        {
            "group": group,
            "avg_score": float(avg_score) if avg_score is not None else 0.0,
            "students": students,
        }
        for group, avg_score, students in results
    ]