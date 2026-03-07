"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime
from typing import Any

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from app.settings import settings


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict[str, Any]]:
    """Fetch the lab/task catalog from the autochecker API.

    Returns:
        List of dicts with keys: lab, task, title, type
    """
    url = f"{settings.autochecker_api_url}/api/items"
    auth = (settings.autochecker_email, settings.autochecker_password)

    async with httpx.AsyncClient() as client:
        response = await client.get(url, auth=auth)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]


async def fetch_logs(since: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch check results from the autochecker API.

    Args:
        since: Optional datetime to fetch logs after (for incremental sync).

    Returns:
        Combined list of all log dicts from all paginated responses.
    """
    url = f"{settings.autochecker_api_url}/api/logs"
    auth = (settings.autochecker_email, settings.autochecker_password)
    limit = 500

    all_logs: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        while True:
            params: dict[str, Any] = {"limit": limit}
            if since is not None:
                params["since"] = since.isoformat()

            response = await client.get(url, auth=auth, params=params)
            response.raise_for_status()
            data = response.json()

            logs = data.get("logs", [])
            all_logs.extend(logs)

            has_more = data.get("has_more", False)
            if not has_more or not logs:
                break

            # Use the last log's submitted_at as the new since value
            since = datetime.fromisoformat(logs[-1]["submitted_at"])

    return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(
    items: list[dict[str, Any]], session: AsyncSession
) -> int:
    """Load items (labs and tasks) into the database.

    Args:
        items: List of item dicts from the API.
        session: Database session.

    Returns:
        Number of newly created items.
    """
    from sqlmodel import select

    from app.models.item import ItemRecord

    # Import ItemRecord from app.models.item
    # Process labs first (items where type="lab"):
    #   - For each lab, check if an item with type="lab" and matching title
    #     already exists (SELECT)
    #   - If not, INSERT a new ItemRecord(type="lab", title=lab_title)
    #   - Build a dict mapping the lab's short ID (the "lab" field, e.g.
    #     "lab-01") to the lab's database record, so you can look up
    #     parent IDs when processing tasks
    # Then process tasks (items where type="task"):
    #   - Find the parent lab item using the task's "lab" field (e.g.
    #     "lab-01") as the key into the dict you built above
    #   - Check if a task with this title and parent_id already exists
    #   - If not, INSERT a new ItemRecord(type="task", title=task_title,
    #     parent_id=lab_item.id)
    # Commit after all inserts
    # Return the number of newly created items

    new_items_count = 0
    lab_short_id_to_record: dict[str, ItemRecord] = {}

    # First pass: process labs
    for item in items:
        if item.get("type") != "lab":
            continue

        title = item.get("title", "")
        lab_short_id = item.get("lab", "")

        # Check if lab already exists
        existing = await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab", ItemRecord.title == title
            )
        )
        lab_record = existing.first()

        if lab_record is None:
            # Create new lab record
            lab_record = ItemRecord(type="lab", title=title)
            session.add(lab_record)
            new_items_count += 1

        # Map short ID to record for task lookup
        if lab_short_id:
            lab_short_id_to_record[lab_short_id] = lab_record

    # Second pass: process tasks
    for item in items:
        if item.get("type") != "task":
            continue

        title = item.get("title", "")
        lab_short_id = item.get("lab", "")

        # Find parent lab
        parent_lab = lab_short_id_to_record.get(lab_short_id)
        if parent_lab is None:
            # Parent lab not found, skip this task
            continue

        # Check if task already exists
        existing = await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "task",
                ItemRecord.title == title,
                ItemRecord.parent_id == parent_lab.id,
            )
        )
        task_record = existing.first()

        if task_record is None:
            # Create new task record
            task_record = ItemRecord(
                type="task", title=title, parent_id=parent_lab.id
            )
            session.add(task_record)
            new_items_count += 1

    # Commit after all inserts
    await session.commit()

    return new_items_count


async def load_logs(
    logs: list[dict[str, Any]],
    items_catalog: list[dict[str, Any]],
    session: AsyncSession,
) -> int:
    """Load interaction logs into the database.

    Args:
        logs: Raw log dicts from the API (each has lab, task, student_id, etc.)
        items_catalog: Raw item dicts from fetch_items() — needed to map
            short IDs (e.g. "lab-01", "setup") to item titles stored in the DB.
        session: Database session.

    Returns:
        Number of newly created interactions.
    """
    from sqlmodel import select

    from app.models.interaction import InteractionLog
    from app.models.item import ItemRecord
    from app.models.learner import Learner

    # Build a lookup from (lab_short_id, task_short_id) to item title
    # For labs, the key is (lab, None). For tasks, the key is (lab, task).
    item_title_lookup: dict[tuple[str, str | None], str] = {}
    for item in items_catalog:
        lab_short_id = item.get("lab", "")
        task_short_id = item.get("task")  # Can be None for labs
        title = item.get("title", "")
        item_title_lookup[(lab_short_id, task_short_id)] = title

    new_interactions_count = 0

    for log in logs:
        # 1. Find or create a Learner by external_id (log["student_id"])
        student_id = log.get("student_id", "")
        student_group = log.get("group", "")

        learner = await session.exec(
            select(Learner).where(Learner.external_id == student_id)
        )
        learner_record = learner.first()

        if learner_record is None:
            learner_record = Learner(external_id=student_id, student_group=student_group)
            session.add(learner_record)
            await session.flush()  # Get the generated id

        # 2. Find the matching item in the database
        lab_short_id = log.get("lab", "")
        task_short_id = log.get("task")  # Can be None for lab logs
        item_title = item_title_lookup.get((lab_short_id, task_short_id))

        if item_title is None:
            # No matching item found, skip this log
            continue

        item_record = await session.exec(
            select(ItemRecord).where(ItemRecord.title == item_title)
        )
        item = item_record.first()

        if item is None:
            # Item not found in DB, skip this log
            continue

        # 3. Check if an InteractionLog with this external_id already exists
        log_external_id = log.get("id")
        existing_interaction = await session.exec(
            select(InteractionLog).where(
                InteractionLog.external_id == log_external_id
            )
        )
        if existing_interaction.first() is not None:
            # Already exists, skip for idempotent upsert
            continue

        # 4. Create InteractionLog
        # Assert IDs are present (they should be after flush/select)
        assert learner_record.id is not None
        assert item.id is not None

        interaction = InteractionLog(
            external_id=log_external_id,
            learner_id=learner_record.id,
            item_id=item.id,
            kind="attempt",
            score=log.get("score"),
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=datetime.fromisoformat(log["submitted_at"]),
        )
        session.add(interaction)
        new_interactions_count += 1

    # Commit after all inserts
    await session.commit()

    return new_interactions_count


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict[str, int]:
    """Run the full ETL pipeline.

    Returns:
        Dict with new_records (new interactions created) and
        total_records (total interactions in DB after sync).
    """
    from sqlmodel import func, select

    from app.models.interaction import InteractionLog

    # Step 1: Fetch items from the API and load them
    items = await fetch_items()
    await load_items(items, session)

    # Step 2: Determine the last synced timestamp
    # Query the most recent created_at from InteractionLog
    max_result = await session.exec(
        select(func.max(InteractionLog.created_at))
    )
    since = max_result.one_or_none()

    # Step 3: Fetch logs since that timestamp and load them
    logs = await fetch_logs(since=since)  # type: ignore[arg-type]
    new_records = await load_logs(logs, items, session)

    # Get total records count
    total_result = await session.exec(
        select(func.count(InteractionLog.id))  # type: ignore[arg-type]
    )
    total_records = total_result.one() or 0

    # Return summary
    return {"new_records": new_records, "total_records": total_records}
