from app.models import Base


def test_phase_one_schema_contains_ingestion_and_future_media_tables():
    expected = {
        "ingest_messages",
        "frigate_events",
        "frigate_reviews",
        "face_recognition_results",
        "home_assistant_entities",
        "home_assistant_state_changes",
        "home_assistant_events",
        "media_assets",
        "connector_checkpoints",
        "service_heartbeats",
    }

    assert set(Base.metadata.tables) == expected


def test_idempotency_constraints_are_declared():
    ingest = Base.metadata.tables["ingest_messages"]
    state_change = Base.metadata.tables["home_assistant_state_changes"]
    event = Base.metadata.tables["home_assistant_events"]

    assert any(
        constraint.name == "uq_ingest_messages_source_key"
        for constraint in ingest.constraints
    )
    assert any(
        constraint.name == "uq_ha_state_changes_ingest_message"
        for constraint in state_change.constraints
    )
    assert any(
        constraint.name == "uq_ha_events_ingest_message"
        for constraint in event.constraints
    )


def test_media_assets_have_archive_state_and_deduplication_fields():
    media = Base.metadata.tables["media_assets"]

    assert {
        "asset_key",
        "entity_id",
        "archive_path",
        "size_bytes",
        "mime_type",
        "copy_status",
        "attempt_count",
        "last_error",
        "last_attempt_at",
        "completed_at",
    } <= set(media.c.keys())
    assert any(
        constraint.name == "uq_media_assets_asset_key"
        for constraint in media.constraints
    )


def test_face_recognition_results_have_stable_source_identity():
    table = Base.metadata.tables["face_recognition_results"]

    assert {
        "event_id",
        "event_reference",
        "event_camera",
        "event_label",
        "event_start_time",
        "event_end_time",
        "event_type",
        "identity",
        "recognized_at",
        "camera",
        "match_score",
        "media_reference",
        "source_system",
        "source_key",
        "ingest_message_id",
    } <= set(table.c.keys())
    assert any(
        constraint.name == "uq_face_recognition_results_source_key"
        for constraint in table.constraints
    )
    event_fk = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if foreign_key.parent.name == "event_id"
    )
    assert event_fk.ondelete == "SET NULL"
