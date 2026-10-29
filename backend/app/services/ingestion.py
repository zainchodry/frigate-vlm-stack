from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keys import canonical_sha256, frigate_idempotency_key, home_assistant_idempotency_key
from app.domain.normalizers import NormalizedMessage, normalize_frigate, normalize_home_assistant
from app.models import (
    FrigateEvent,
    FrigateReview,
    FaceRecognitionResult,
    HomeAssistantEntity,
    HomeAssistantEvent,
    HomeAssistantStateChange,
    IngestMessage,
)


@dataclass(frozen=True)
class IngestEnvelope:
    source: str
    topic: str
    idempotency_key: str
    source_event_at: datetime | None
    payload: dict[str, Any]
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class IngestResult:
    message_id: uuid.UUID
    inserted: bool
    duplicate: bool = False


def build_frigate_envelope(
    topic: str,
    payload: dict[str, Any],
    source_event_at: datetime | None,
) -> IngestEnvelope:
    return IngestEnvelope(
        source="frigate",
        topic=topic,
        idempotency_key=frigate_idempotency_key(topic, payload),
        source_event_at=source_event_at,
        payload=payload,
    )


def build_home_assistant_envelope(
    event: dict[str, Any],
    source_event_at: datetime | None,
    topic: str = "websocket",
) -> IngestEnvelope:
    return IngestEnvelope(
        source="home_assistant",
        topic=topic,
        idempotency_key=home_assistant_idempotency_key(event),
        source_event_at=source_event_at,
        payload=event,
    )


async def ingest_message(session: AsyncSession, envelope: IngestEnvelope) -> IngestResult:
    """Insert an envelope and its projection atomically.

    The unique source/key constraint makes redelivery a no-op. A projection
    is only written after the durable envelope insert succeeds, in the same
    transaction, so a failed projection rolls the envelope back for retry.
    """
    async with session.begin():
        insert_stmt = (
            insert(IngestMessage)
            .values(
                source=envelope.source,
                source_topic=envelope.topic,
                idempotency_key=envelope.idempotency_key,
                source_event_at=envelope.source_event_at,
                received_at=envelope.received_at,
                payload=envelope.payload,
                payload_sha256=canonical_sha256(envelope.payload),
                processing_status="processing",
                attempt_count=1,
                last_attempt_at=envelope.received_at,
            )
            .on_conflict_do_nothing(index_elements=["source", "idempotency_key"])
            .returning(IngestMessage.id)
        )
        result = await session.execute(insert_stmt)
        message_id = result.scalar_one_or_none()
        if message_id is None:
            existing = await session.execute(
                select(IngestMessage.id).where(
                    IngestMessage.source == envelope.source,
                    IngestMessage.idempotency_key == envelope.idempotency_key,
                )
            )
            return IngestResult(existing.scalar_one(), inserted=False, duplicate=True)

        if envelope.source in {"frigate", "frigate_sqlite"}:
            normalized = normalize_frigate(envelope.topic, envelope.payload)
        elif envelope.source == "home_assistant":
            normalized = normalize_home_assistant(envelope.payload)
        else:
            normalized = NormalizedMessage("unknown", envelope.idempotency_key, envelope.source_event_at, None, {})

        await apply_projection(
            session,
            message_id,
            normalized,
            envelope.received_at,
            source_system="frigate_mqtt" if envelope.source == "frigate" else envelope.source,
        )
        await session.execute(
            update(IngestMessage)
            .where(IngestMessage.id == message_id)
            .values(processing_status="processed", processed_at=func.now(), last_error=None)
        )

    return IngestResult(message_id, inserted=True)


async def apply_projection(
    session: AsyncSession,
    message_id: uuid.UUID,
    normalized: NormalizedMessage,
    received_at: datetime,
    source_system: str = "frigate_mqtt",
) -> None:
    if normalized.kind == "frigate_event":
        await _upsert_frigate_event(session, message_id, normalized)
        await _upsert_face_recognition_result(session, message_id, normalized, received_at, source_system)
    elif normalized.kind == "frigate_review":
        await _upsert_frigate_review(session, message_id, normalized)
    elif normalized.kind in {"home_assistant_state_change", "home_assistant_state_seed"}:
        await _insert_home_assistant_state(session, message_id, normalized, received_at)
    elif normalized.kind == "home_assistant_event":
        await _insert_home_assistant_event(session, message_id, normalized, received_at)


def _temporal_condition(model: Any, stmt: Any, source_event_at: datetime | None) -> Any:
    if source_event_at is None:
        return None
    return or_(
        model.latest_source_at.is_(None),
        stmt.excluded.latest_source_at.is_(None),
        model.latest_source_at <= stmt.excluded.latest_source_at,
    )


async def _upsert_frigate_event(
    session: AsyncSession,
    message_id: uuid.UUID,
    normalized: NormalizedMessage,
) -> None:
    event_values = {
        key: value
        for key, value in normalized.values.items()
        if key in FrigateEvent.__table__.columns
    }
    values = {
        "event_id": normalized.key,
        "ingest_message_id": message_id,
        **event_values,
    }
    stmt = insert(FrigateEvent).values(**values)
    update_values = {
        key: getattr(stmt.excluded, key)
        for key in event_values
        if key not in {"start_time", "frame_time"}
    }
    update_values["last_seen_at"] = func.now()
    stmt = stmt.on_conflict_do_update(
        index_elements=[FrigateEvent.event_id],
        set_=update_values,
        where=_temporal_condition(FrigateEvent, stmt, normalized.source_event_at),
    )
    await session.execute(stmt)


async def _upsert_frigate_review(
    session: AsyncSession,
    message_id: uuid.UUID,
    normalized: NormalizedMessage,
) -> None:
    values = {
        "review_id": normalized.key,
        "ingest_message_id": message_id,
        **normalized.values,
    }
    stmt = insert(FrigateReview).values(**values)
    update_values = {key: getattr(stmt.excluded, key) for key in normalized.values}
    update_values["last_seen_at"] = func.now()
    stmt = stmt.on_conflict_do_update(
        index_elements=[FrigateReview.review_id],
        set_=update_values,
        where=_temporal_condition(FrigateReview, stmt, normalized.source_event_at),
    )
    await session.execute(stmt)


async def _upsert_face_recognition_result(
    session: AsyncSession,
    message_id: uuid.UUID,
    normalized: NormalizedMessage,
    received_at: datetime,
    source_system: str,
) -> None:
    identity = normalized.values.get("face_identity")
    if not isinstance(identity, str) or not identity.strip():
        return

    identity = identity.strip()
    status = "unknown" if identity.casefold() in {"unknown", "person"} else "recognized"
    source_event_at = normalized.source_event_at or received_at
    values = {
        "id": uuid.uuid4(),
        "event_id": normalized.key,
        "event_reference": normalized.key,
        "event_camera": normalized.values.get("camera"),
        "event_label": normalized.values.get("label"),
        "event_start_time": normalized.values.get("start_time"),
        "event_end_time": normalized.values.get("end_time"),
        "event_type": normalized.event_type,
        "identity": identity,
        "result_status": status,
        "recognized_at": source_event_at,
        "camera": normalized.values.get("camera"),
        "match_score": normalized.values.get("face_score"),
        "media_reference": normalized.values.get("face_media_reference"),
        "source_system": source_system,
        "source_key": f"{source_system}:event:{normalized.key}:face",
        "result_metadata": {
            "event_type": normalized.event_type,
            "label": normalized.values.get("label"),
        },
        "ingest_message_id": message_id,
        "ingested_at": received_at,
    }
    stmt = insert(FaceRecognitionResult).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[FaceRecognitionResult.source_key],
        set_={
            "event_id": stmt.excluded.event_id,
            "event_reference": stmt.excluded.event_reference,
            "event_camera": stmt.excluded.event_camera,
            "event_label": stmt.excluded.event_label,
            "event_start_time": stmt.excluded.event_start_time,
            "event_end_time": stmt.excluded.event_end_time,
            "event_type": stmt.excluded.event_type,
            "identity": stmt.excluded.identity,
            "result_status": stmt.excluded.result_status,
            "recognized_at": stmt.excluded.recognized_at,
            "camera": stmt.excluded.camera,
            "match_score": stmt.excluded.match_score,
            "media_reference": stmt.excluded.media_reference,
            "result_metadata": stmt.excluded.result_metadata,
            "ingest_message_id": stmt.excluded.ingest_message_id,
            "ingested_at": stmt.excluded.ingested_at,
        },
    )
    await session.execute(stmt)


async def _insert_home_assistant_state(
    session: AsyncSession,
    message_id: uuid.UUID,
    normalized: NormalizedMessage,
    received_at: datetime,
) -> None:
    values = normalized.values
    source_event_at = normalized.source_event_at or received_at
    semantic_match = await session.execute(
        select(HomeAssistantStateChange.id).where(
            HomeAssistantStateChange.entity_id == values["entity_id"],
            HomeAssistantStateChange.source_event_at == source_event_at,
            HomeAssistantStateChange.new_state == values.get("new_state"),
            HomeAssistantStateChange.last_changed == values.get("last_changed"),
            HomeAssistantStateChange.last_updated == values.get("last_updated"),
        ).limit(1)
    )
    if semantic_match.scalar_one_or_none() is None:
        await session.execute(
            insert(HomeAssistantStateChange).values(
                id=uuid.uuid4(),
                ingest_message_id=message_id,
                entity_id=values["entity_id"],
                source_event_at=source_event_at,
                old_state=values.get("old_state"),
                new_state=values.get("new_state"),
                old_attributes=values.get("old_attributes"),
                new_attributes=values.get("new_attributes"),
                last_changed=values.get("last_changed"),
                last_updated=values.get("last_updated"),
                context_id=values.get("context_id"),
                parent_context_id=values.get("parent_context_id"),
                user_id=values.get("user_id"),
            )
        )

    entity_id = values["entity_id"]
    attributes = values.get("new_attributes") or {}
    entity_stmt = insert(HomeAssistantEntity).values(
        entity_id=entity_id,
        domain=entity_id.split(".", 1)[0] if "." in entity_id else None,
        friendly_name=attributes.get("friendly_name"),
        device_id=attributes.get("device_id"),
        area_id=attributes.get("area_id"),
        current_state=values.get("new_state"),
        current_attributes=attributes,
        last_changed=values.get("last_changed"),
        last_updated=values.get("last_updated"),
        last_seen_at=received_at,
    )
    entity_stmt = entity_stmt.on_conflict_do_update(
        index_elements=[HomeAssistantEntity.entity_id],
        set_={
            "friendly_name": entity_stmt.excluded.friendly_name,
            "device_id": entity_stmt.excluded.device_id,
            "area_id": entity_stmt.excluded.area_id,
            "current_state": entity_stmt.excluded.current_state,
            "current_attributes": entity_stmt.excluded.current_attributes,
            "last_changed": entity_stmt.excluded.last_changed,
            "last_updated": entity_stmt.excluded.last_updated,
            "last_seen_at": entity_stmt.excluded.last_seen_at,
        },
    )
    await session.execute(entity_stmt)


async def _insert_home_assistant_event(
    session: AsyncSession,
    message_id: uuid.UUID,
    normalized: NormalizedMessage,
    received_at: datetime,
) -> None:
    values = normalized.values
    await session.execute(
        insert(HomeAssistantEvent).values(
            id=uuid.uuid4(),
            ingest_message_id=message_id,
            event_type=normalized.event_type or "event",
            source_event_at=normalized.source_event_at or received_at,
            data=values.get("data") or {},
            context_id=values.get("context_id"),
            parent_context_id=values.get("parent_context_id"),
            user_id=values.get("user_id"),
        )
    )
