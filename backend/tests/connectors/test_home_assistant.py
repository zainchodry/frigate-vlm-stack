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


class _Websocket:
    def __init__(self):
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def recv(self):
        if not self.sent:
            return json.dumps({"type": "auth_required"})
        return json.dumps({"type": "auth_ok"})

    async def send(self, value):
        self.sent.append(value)

    async def close(self):
        return None


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
    assert captured[0].idempotency_key.startswith("home_assistant:state:")


@pytest.mark.asyncio
async def test_home_assistant_history_recovery_filters_seeded_entities():
    connector = HomeAssistantConnector(
        session_factory=lambda: _SessionContext(),
        ha_url="http://homeassistant:8123",
        ha_token="secret-token",
        event_types=("state_changed",),
    )
    connector._entity_ids = {"sensor.temperature", "binary_sensor.door"}
    connector._request_json = AsyncMock(return_value=[])
    connector._handle_event = AsyncMock()
    connector._write_checkpoint = AsyncMock()

    from datetime import UTC, datetime

    await connector._recover_gap(datetime(2026, 10, 28, 10, tzinfo=UTC), datetime(2026, 10, 28, 10, 1, tzinfo=UTC))

    params = connector._request_json.await_args.kwargs["params"]
    assert params["filter_entity_id"] == "binary_sensor.door,sensor.temperature"


@pytest.mark.asyncio
async def test_incomplete_recovery_checkpoint_is_not_overwritten_as_healthy(monkeypatch):
    connector = HomeAssistantConnector(
        session_factory=lambda: _SessionContext(),
        ha_url="http://homeassistant:8123",
        ha_token="secret-token",
        event_types=("state_changed",),
    )
    connector._recovery_needed = True
    from datetime import UTC, datetime

    connector._last_source_at = datetime(2026, 10, 28, 10, tzinfo=UTC)
    connector._stop_event.set()
    connector._recover_gap = AsyncMock()
    connector._write_checkpoint = AsyncMock()
    monkeypatch.setattr("app.connectors.home_assistant.connect", lambda *args, **kwargs: _Websocket())

    await connector._connect_and_consume()

    connector._recover_gap.assert_awaited_once()
    connector._write_checkpoint.assert_not_awaited()
    assert connector._recovery_needed is False
