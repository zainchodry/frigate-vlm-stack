from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from app.domain.keys import frigate_idempotency_key
from app.domain.normalizers import normalize_frigate
from app.services.ingestion import IngestEnvelope, ingest_message


class FrigateMqttConnector:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        mqtt_host: str,
        mqtt_port: int,
        mqtt_client_id: str,
        mqtt_username: str | None,
        mqtt_password: str | None,
        mqtt_topics: tuple[str, ...],
        max_inflight: int = 100,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._mqtt_client_id = mqtt_client_id
        self._mqtt_username = mqtt_username
        self._mqtt_password = mqtt_password
        self._mqtt_topics = mqtt_topics
        self._logger = logger or logging.getLogger(__name__)
        self._semaphore = asyncio.Semaphore(max_inflight)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: mqtt.Client | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._mqtt_client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
            manual_ack=True,
        )
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        if self._mqtt_username:
            client.username_pw_set(self._mqtt_username, self._mqtt_password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        client.connect_async(self._mqtt_host, self._mqtt_port, keepalive=60)
        client.loop_start()
        self._logger.info(
            "Frigate MQTT connector started",
            extra={"mqtt_host": self._mqtt_host, "mqtt_port": self._mqtt_port},
        )

    async def stop(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        client.disconnect()
        client.loop_stop()
        self._logger.info("Frigate MQTT connector stopped")

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code != 0:
            self._logger.error("Frigate MQTT connection failed", extra={"reason_code": str(reason_code)})
            return
        subscriptions = [(topic, 1) for topic in self._mqtt_topics]
        result, _mid = client.subscribe(subscriptions)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._logger.error("Frigate MQTT subscription failed", extra={"result": result})
            return
        self._logger.info("Frigate MQTT subscriptions active", extra={"topics": list(self._mqtt_topics)})

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        self._logger.warning("Frigate MQTT disconnected", extra={"reason_code": str(reason_code)})

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        if self._loop is None:
            self._logger.error("Frigate MQTT message received before event loop was ready")
            return
        future = asyncio.run_coroutine_threadsafe(self._process_message(message), self._loop)
        future.add_done_callback(self._log_processing_failure)

    def _log_processing_failure(self, future: Any) -> None:
        try:
            future.result()
        except Exception:
            self._logger.exception("Frigate MQTT message processing failed; broker redelivery remains enabled")

    async def _process_message(self, message: mqtt.MQTTMessage) -> None:
        async with self._semaphore:
            payload = self._decode_payload(message.payload)
            normalized = normalize_frigate(message.topic, payload)
            envelope = IngestEnvelope(
                source="frigate",
                topic=message.topic,
                idempotency_key=frigate_idempotency_key(message.topic, payload),
                source_event_at=normalized.source_event_at,
                payload=payload,
            )
            async with self._session_factory() as session:
                await ingest_message(session, envelope)

            client = self._client
            if client is not None:
                # manual_ack is intentional: acknowledge only after the
                # transaction has committed successfully.
                client.ack(message.mid, message.qos)

    @staticmethod
    def _decode_payload(raw_payload: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"_invalid_payload": raw_payload.decode("utf-8", errors="replace"), "_parse_error": str(exc)}
        if isinstance(payload, dict):
            return payload
        return {"_non_object_payload": payload}
