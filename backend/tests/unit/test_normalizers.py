from datetime import UTC, datetime

from app.domain.normalizers import normalize_frigate, normalize_home_assistant


def test_normalize_frigate_event_preserves_lifecycle_and_source_times():
    normalized = normalize_frigate(
        "frigate/events",
        {
            "type": "end",
            "before": {"id": "event-1", "label": "person"},
            "after": {
                "id": "event-1",
                "camera": "front",
                "label": "person",
                "sub_label": "Zain",
                "data": {"sub_label_score": 0.91},
                "start_time": 1793181600,
                "end_time": 1793181605,
                "frame_time": 1793181604,
            },
        },
    )

    assert normalized.kind == "frigate_event"
    assert normalized.key == "event-1"
    assert normalized.event_type == "end"
    assert normalized.source_event_at == datetime(2026, 10, 28, 10, 0, 4, tzinfo=UTC)
    assert normalized.values["recording_path"] is None
    assert normalized.values["face_identity"] == "Zain"
    assert normalized.values["face_score"] == 0.91


def test_normalize_frigate_converts_live_sub_label_tuple_to_identity_and_score():
    normalized = normalize_frigate(
        "frigate/events",
        {
            "type": "end",
            "after": {
                "id": "event-face-1",
                "camera": "iphone_camera",
                "label": "person",
                "sub_label": ["Zain", 0.84],
                "frame_time": 1793263270.835063,
            },
        },
    )

    assert normalized.values["sub_label"] == "Zain"
    assert normalized.values["face_identity"] == "Zain"
    assert normalized.values["face_score"] == 0.84


def test_normalize_home_assistant_state_change_extracts_context_and_attributes():
    normalized = normalize_home_assistant(
        {
            "event_type": "state_changed",
            "time_fired": "2026-10-28T10:00:00Z",
            "data": {
                "entity_id": "sensor.temperature",
                "old_state": {"state": "21", "attributes": {"unit": "°C"}},
                "new_state": {
                    "state": "22",
                    "attributes": {"unit": "°C"},
                    "last_changed": "2026-10-28T10:00:00Z",
                    "last_updated": "2026-10-28T10:00:00Z",
                },
            },
            "context": {"id": "ctx", "parent_id": "parent", "user_id": "user"},
        }
    )

    assert normalized.kind == "home_assistant_state_change"
    assert normalized.key == "sensor.temperature"
    assert normalized.values["old_state"] == "21"
    assert normalized.values["new_state"] == "22"
    assert normalized.values["new_attributes"] == {"unit": "°C"}
    assert normalized.values["context_id"] == "ctx"
