from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IngestMessage(Base):
    __tablename__ = "ingest_messages"
    __table_args__ = (
        UniqueConstraint("source", "idempotency_key", name="uq_ingest_messages_source_key"),
        Index("ix_ingest_messages_source_event_at", "source", "source_event_at"),
        Index("ix_ingest_messages_processing_retry", "processing_status", "last_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_topic: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="processed")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FrigateEvent(Base):
    __tablename__ = "frigate_events"
    __table_args__ = (
        Index("ix_frigate_events_camera_start", "camera", "start_time"),
        Index("ix_frigate_events_label_start", "label", "start_time"),
        Index("ix_frigate_events_latest_source", "latest_source_at"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    camera: Mapped[str | None] = mapped_column(String(128))
    label: Mapped[str | None] = mapped_column(String(128))
    sub_label: Mapped[str | None] = mapped_column(String(256))
    top_score: Mapped[float | None] = mapped_column(Float)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frame_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str | None] = mapped_column(String(64))
    latest_source_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    zones: Mapped[list[Any] | None] = mapped_column(JSONB)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    recording_path: Mapped[str | None] = mapped_column(Text)
    raw_before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raw_after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ingest_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingest_messages.id"))


class FrigateReview(Base):
    __tablename__ = "frigate_reviews"
    __table_args__ = (
        Index("ix_frigate_reviews_camera_start", "camera", "start_time"),
        Index("ix_frigate_reviews_severity_start", "severity", "start_time"),
        Index("ix_frigate_reviews_latest_source", "latest_source_at"),
    )

    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    camera: Mapped[str | None] = mapped_column(String(128))
    severity: Mapped[str | None] = mapped_column(String(32))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_source_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str | None] = mapped_column(String(64))
    detections: Mapped[list[Any] | None] = mapped_column(JSONB)
    objects: Mapped[list[Any] | None] = mapped_column(JSONB)
    sub_labels: Mapped[list[Any] | None] = mapped_column(JSONB)
    zones: Mapped[list[Any] | None] = mapped_column(JSONB)
    audio: Mapped[list[Any] | None] = mapped_column(JSONB)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    raw_before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raw_after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ingest_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingest_messages.id"))


class HomeAssistantEntity(Base):
    __tablename__ = "home_assistant_entities"

    entity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    domain: Mapped[str | None] = mapped_column(String(64))
    friendly_name: Mapped[str | None] = mapped_column(String(255))
    device_id: Mapped[str | None] = mapped_column(String(255))
    area_id: Mapped[str | None] = mapped_column(String(255))
    current_state: Mapped[str | None] = mapped_column(String(255))
    current_attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_changed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HomeAssistantStateChange(Base):
    __tablename__ = "home_assistant_state_changes"
    __table_args__ = (
        UniqueConstraint("ingest_message_id", name="uq_ha_state_changes_ingest_message"),
        Index("ix_ha_state_changes_entity_time", "entity_id", "source_event_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingest_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingest_messages.id"), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    old_state: Mapped[str | None] = mapped_column(String(255))
    new_state: Mapped[str | None] = mapped_column(String(255))
    old_attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_changed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_id: Mapped[str | None] = mapped_column(String(255))
    parent_context_id: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(String(255))


class HomeAssistantEvent(Base):
    __tablename__ = "home_assistant_events"
    __table_args__ = (
        UniqueConstraint("ingest_message_id", name="uq_ha_events_ingest_message"),
        Index("ix_ha_events_type_time", "event_type", "source_event_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingest_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingest_messages.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_id: Mapped[str | None] = mapped_column(String(255))
    parent_context_id: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(String(255))


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_assets_event", "event_id", "media_type"),
        Index("ix_media_assets_retention", "availability_status", "retention_until"),
        Index("ix_media_assets_copy_status", "copy_status", "last_attempt_at"),
        UniqueConstraint("asset_key", name="uq_media_assets_asset_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("frigate_events.event_id"))
    review_id: Mapped[str | None] = mapped_column(ForeignKey("frigate_reviews.review_id"))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_path: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    archive_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    storage_backend: Mapped[str | None] = mapped_column(String(64))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="referenced")
    copy_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ConnectorCheckpoint(Base):
    __tablename__ = "connector_checkpoints"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_source_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="starting")
    recovery_requested_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_requested_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    gap_reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="starting")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
