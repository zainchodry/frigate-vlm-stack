import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.home_assistant import HomeAssistantConnector


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_home_assistant_event_is_ingested_with_source_timestamp(monkeypatch):
    captured = {}

    async def fake_ingest(session, envelope):
        captured["envelope"] = envelope

    monkeypatch.setattr("app.connectors.home_assistant.ingest_message", fake_ingest)
    connector = HomeAssistantConnector(
        session_factory=lambda: _SessionContext(),
        ha_url="http://homeassistant:8123",
        ha_token="secret-token",
        event_types=("state_changed",),
    )
    connector._write_checkpoint = AsyncMock()

    await connector._handle_event(
        {
            "event_type": "state_changed",
            "time_fired": "2026-10-28T10:00:00Z",
            "data": {"entity_id": "sensor.temperature", "new_state": {"state": "22"}},
        }
    )

    assert captured["envelope"].source == "home_assistant"
    assert captured["envelope"].source_event_at.isoformat() == "2026-10-28T10:00:00+00:00"
    connector._write_checkpoint.assert_awaited_once()


@pytest.mark.asyncio
async def test_home_assistant_seed_uses_fixture_states(monkeypatch):
    states = json.loads(
        (Path(__file__).parent / "fixtures" / "ha_states.json").read_text(encoding="utf-8")
    )
    captured = []

    async def fake_ingest(session, envelope):
        captured.append(envelope)

    monkeypatch.setattr("app.connectors.home_assistant.ingest_message", fake_ingest)
    connector = HomeAssistantConnector(
        session_factory=lambda: _SessionContext(),
        ha_url="http://homeassistant:8123",
        ha_token="secret-token",
        event_types=("state_changed",),
    )
    connector._request_json = AsyncMock(return_value=states)
    connector._write_checkpoint = AsyncMock()

    await connector._seed_states()

    assert len(captured) == 1
    assert captured[0].payload["data"]["entity_id"] == "sensor.temperature"
    assert captured[0].idempotency_key.startswith("home_assistant:state_seed:")
