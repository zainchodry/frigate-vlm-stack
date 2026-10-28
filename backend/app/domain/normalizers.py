from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .keys import canonical_sha256
from .timestamps import parse_timestamp


@dataclass(frozen=True)
class NormalizedMessage:
    kind: str
    key: str
    source_event_at: datetime | None
    event_type: str | None
    values: dict[str, Any]


def _timestamp(*values: Any) -> datetime | None:
    for value in values:
        if value is not None:
            try:
                return parse_timestamp(value)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("after", "before"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def normalize_frigate(topic: str, payload: dict[str, Any]) -> NormalizedMessage:
    event_type = payload.get("type") or payload.get("event_type")
    record = _record(payload)

    if topic.endswith("/events") and record.get("id"):
        source_event_at = _timestamp(
            record.get("frame_time"),
            record.get("end_time"),
            record.get("start_time"),
        )
        values = {
            "camera": record.get("camera"),
            "label": record.get("label"),
            "sub_label": record.get("sub_label"),
            "top_score": record.get("top_score"),
            "start_time": _timestamp(record.get("start_time")),
            "end_time": _timestamp(record.get("end_time")),
            "frame_time": _timestamp(record.get("frame_time")),
            "event_type": event_type,
            "latest_source_at": source_event_at,
            "zones": record.get("zones"),
            "snapshot_path": record.get("snapshot_path") or record.get("path"),
            "recording_path": record.get("recording_path"),
            "raw_before": payload.get("before"),
            "raw_after": payload.get("after"),
        }
        return NormalizedMessage("frigate_event", str(record["id"]), source_event_at, event_type, values)

    if topic.endswith("/reviews") and record.get("id"):
        source_event_at = _timestamp(
            record.get("end_time"),
            record.get("start_time"),
        )
        values = {
            "camera": record.get("camera"),
            "severity": record.get("severity"),
            "start_time": _timestamp(record.get("start_time")),
            "end_time": _timestamp(record.get("end_time")),
            "latest_source_at": source_event_at,
            "event_type": event_type,
            "detections": record.get("detections"),
            "objects": record.get("objects"),
            "sub_labels": record.get("sub_labels"),
            "zones": record.get("zones"),
            "audio": record.get("audio"),
            "thumbnail_path": record.get("thumbnail_path") or record.get("thumb_path"),
            "raw_before": payload.get("before"),
            "raw_after": payload.get("after"),
        }
        return NormalizedMessage("frigate_review", str(record["id"]), source_event_at, event_type, values)

    return NormalizedMessage(
        "frigate_message",
        f"{topic}:{canonical_sha256(payload)}",
        _timestamp(payload.get("timestamp"), payload.get("time_fired")),
        event_type,
        {},
    )


def _context_values(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context") or {}
    return {
        "context_id": context.get("id"),
        "parent_context_id": context.get("parent_id"),
        "user_id": context.get("user_id"),
    }


def normalize_home_assistant(event: dict[str, Any]) -> NormalizedMessage:
    event_type = event.get("event_type") or "event"
    data = event.get("data") or {}
    source_event_at = _timestamp(
        event.get("time_fired"),
        data.get("new_state", {}).get("last_updated") if isinstance(data.get("new_state"), dict) else None,
        data.get("last_updated"),
    )

    if event_type == "state_seed":
        entity_id = str(data["entity_id"])
        state_time = _timestamp(data.get("last_updated"), data.get("last_changed"), source_event_at)
        return NormalizedMessage(
            "home_assistant_state_seed",
            entity_id,
            state_time,
            event_type,
            {
                "entity_id": entity_id,
                "old_state": None,
                "new_state": data.get("state"),
                "old_attributes": None,
                "new_attributes": data.get("attributes") or {},
                "last_changed": _timestamp(data.get("last_changed")),
                "last_updated": _timestamp(data.get("last_updated")),
                **_context_values(event),
            },
        )

    if event_type == "state_changed":
        old_state = data.get("old_state") or {}
        new_state = data.get("new_state") or {}
        entity_id = str(data.get("entity_id") or new_state.get("entity_id"))
        return NormalizedMessage(
            "home_assistant_state_change",
            entity_id,
            source_event_at,
            event_type,
            {
                "entity_id": entity_id,
                "old_state": old_state.get("state"),
                "new_state": new_state.get("state"),
                "old_attributes": old_state.get("attributes") or {},
                "new_attributes": new_state.get("attributes") or {},
                "last_changed": _timestamp(new_state.get("last_changed")),
                "last_updated": _timestamp(new_state.get("last_updated")),
                **_context_values(event),
            },
        )

    return NormalizedMessage(
        "home_assistant_event",
        str(event_type),
        source_event_at,
        str(event_type),
        {
            "data": data,
            **_context_values(event),
        },
    )
