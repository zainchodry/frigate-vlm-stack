from app.models import Base


def test_phase_one_schema_contains_ingestion_and_future_media_tables():
    expected = {
        "ingest_messages",
        "frigate_events",
        "frigate_reviews",
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
