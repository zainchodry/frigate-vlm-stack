import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.connectors.frigate_mqtt import FrigateMqttConnector


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_mqtt_message_is_acknowledged_only_after_ingestion(monkeypatch):
    captured = {}

    async def fake_ingest(session, envelope):
        captured["envelope"] = envelope

    monkeypatch.setattr("app.connectors.frigate_mqtt.ingest_message", fake_ingest)
    client = SimpleNamespace(ack=MagicMock())
    connector = FrigateMqttConnector(
        session_factory=lambda: _SessionContext(),
        mqtt_host="mqtt",
        mqtt_port=1883,
        mqtt_client_id="test-ingestor",
        mqtt_username=None,
        mqtt_password=None,
        mqtt_topics=("frigate/events",),
    )
    connector._client = client

    message = SimpleNamespace(
        topic="frigate/events",
        payload=json.dumps(
            {"type": "new", "after": {"id": "event-1", "frame_time": 1793181600}}
        ).encode(),
        mid=7,
        qos=1,
    )

    await connector._process_message(message)

    assert captured["envelope"].source == "frigate"
    assert captured["envelope"].idempotency_key.startswith("frigate:frigate/events:event-1:new:")
    client.ack.assert_called_once_with(7, 1)
