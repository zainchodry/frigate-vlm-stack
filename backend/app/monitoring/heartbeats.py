from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy.dialects.postgresql import insert

from app.models import ServiceHeartbeat


async def write_heartbeat(
    session_factory: Callable[[], Any],
    name: str,
    status: str,
    details: dict[str, Any] | None = None,
    *,
    observed_at: datetime | None = None,
) -> None:
    """Persist one service heartbeat without creating duplicate rows."""
    heartbeat_at = observed_at or datetime.now(UTC)
    statement = insert(ServiceHeartbeat).values(
        name=name,
        last_heartbeat=heartbeat_at,
        status=status,
        details=details,
        updated_at=heartbeat_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[ServiceHeartbeat.name],
        set_={
            "last_heartbeat": statement.excluded.last_heartbeat,
            "status": statement.excluded.status,
            "details": statement.excluded.details,
            "updated_at": statement.excluded.updated_at,
        },
    )
    async with session_factory() as session:
        async with session.begin():
            await session.execute(statement)

