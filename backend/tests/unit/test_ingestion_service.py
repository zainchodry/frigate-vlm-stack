from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ingestion import IngestEnvelope, ingest_message


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


def _session(results):
    session = SimpleNamespace()
    session.begin = MagicMock(return_value=_Transaction())
    session.execute = AsyncMock(side_effect=[_Result(value) for value in results])
    return session


@pytest.mark.asyncio
async def test_new_message_is_processed_inside_one_transaction(monkeypatch):
    message_id = uuid4()
    session = _session([message_id, message_id])
    apply_projection = AsyncMock()
    monkeypatch.setattr("app.services.ingestion.apply_projection", apply_projection)

    envelope = IngestEnvelope(
        source="home_assistant",
        topic="websocket",
        idempotency_key="ha:key",
        source_event_at=datetime(2026, 10, 28, 10, tzinfo=UTC),
        payload={"event_type": "call_service", "data": {}},
    )

    result = await ingest_message(session, envelope)

    assert result.inserted is True
    assert result.message_id == message_id
    session.begin.assert_called_once()
    assert session.execute.await_count == 2
    apply_projection.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_message_returns_existing_id_without_projection(monkeypatch):
    message_id = uuid4()
    session = _session([None, message_id])
    apply_projection = AsyncMock()
    monkeypatch.setattr("app.services.ingestion.apply_projection", apply_projection)

    envelope = IngestEnvelope(
        source="frigate",
        topic="frigate/events",
        idempotency_key="frigate:key",
        source_event_at=None,
        payload={"type": "new"},
    )

    result = await ingest_message(session, envelope)

    assert result.inserted is False
    assert result.duplicate is True
    assert result.message_id == message_id
    assert session.execute.await_count == 2
    apply_projection.assert_not_awaited()
