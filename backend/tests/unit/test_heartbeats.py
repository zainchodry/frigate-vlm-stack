from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.monitoring.heartbeats import write_heartbeat


@pytest.mark.asyncio
async def test_write_heartbeat_upserts_service_status():
    class FakeSession:
        def __init__(self):
            self.execute = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return self

    session = FakeSession()
    session_factory = lambda: session
    observed_at = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)

    await write_heartbeat(
        session_factory,
        "ingestor",
        "healthy",
        {"mqtt": "connected"},
        observed_at=observed_at,
    )

    session.execute.assert_awaited_once()
