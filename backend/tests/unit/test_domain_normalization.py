from datetime import UTC, datetime

from app.domain.keys import (
    canonical_sha256,
    frigate_idempotency_key,
    home_assistant_idempotency_key,
)
from app.domain.timestamps import parse_timestamp


def test_parse_timestamp_normalizes_iso_and_epoch_values_to_utc():
    assert parse_timestamp("2026-10-28T10:00:00Z") == datetime(2026, 10, 28, 10, tzinfo=UTC)
    assert parse_timestamp("2026-10-28T15:00:00+05:00") == datetime(2026, 10, 28, 10, tzinfo=UTC)
    assert parse_timestamp(1793181600) == datetime(2026, 10, 28, 10, tzinfo=UTC)


def test_canonical_digest_is_order_independent():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_frigate_keys_dedupe_exact_redelivery_but_preserve_lifecycle_updates():
    first = {"type": "new", "after": {"id": "abc", "frame_time": 1793181600}}
    duplicate = {"after": {"frame_time": 1793181600, "id": "abc"}, "type": "new"}
    update = {"type": "end", "after": {"id": "abc", "end_time": 1793181605}}

    assert frigate_idempotency_key("frigate/events", first) == frigate_idempotency_key(
        "frigate/events", duplicate
    )
    assert frigate_idempotency_key("frigate/events", first) != frigate_idempotency_key(
        "frigate/events", update
    )


def test_home_assistant_key_uses_event_time_and_canonical_payload():
    event = {
        "event_type": "state_changed",
        "time_fired": "2026-10-28T10:00:00Z",
        "data": {"entity_id": "sensor.temperature", "new_state": {"state": "22"}},
    }
    reordered = {
        "data": {"new_state": {"state": "22"}, "entity_id": "sensor.temperature"},
        "time_fired": "2026-10-28T10:00:00Z",
        "event_type": "state_changed",
    }

    assert home_assistant_idempotency_key(event) == home_assistant_idempotency_key(reordered)
