from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .timestamps import parse_timestamp


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _payload_time(payload: dict[str, Any]) -> datetime | None:
    candidates: list[Any] = [
        payload.get("time_fired"),
        payload.get("timestamp"),
        payload.get("frame_time"),
        payload.get("start_time"),
        payload.get("end_time"),
    ]
    for container_key in ("after", "before", "data"):
        nested = payload.get(container_key)
        if isinstance(nested, dict):
            candidates.extend(
                nested.get(field)
                for field in ("frame_time", "start_time", "end_time", "last_updated", "last_changed")
            )
    for candidate in candidates:
        if candidate is not None:
            try:
                return parse_timestamp(candidate)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def frigate_idempotency_key(topic: str, payload: dict[str, Any]) -> str:
    container = payload.get("after") or payload.get("before") or payload
    event_id = payload.get("id")
    if not event_id and isinstance(container, dict):
        event_id = container.get("id")

    event_type = payload.get("type") or payload.get("event_type") or "message"
    source_time = _payload_time(payload)
    time_key = source_time.isoformat() if source_time else "none"
    digest = canonical_sha256(payload)
    if event_id:
        return f"frigate:{topic}:{event_id}:{event_type}:{time_key}:{digest}"
    return f"frigate:{topic}:{event_type}:{time_key}:{digest}"


def home_assistant_idempotency_key(event: dict[str, Any]) -> str:
    event_type = event.get("event_type") or "event"
    if event_type in {"state_seed", "state_changed"}:
        data = event.get("data") or {}
        if event_type == "state_seed":
            entity_id = data.get("entity_id")
            state = data.get("state")
            last_changed = data.get("last_changed")
            last_updated = data.get("last_updated")
        else:
            new_state = data.get("new_state") or {}
            entity_id = data.get("entity_id") or new_state.get("entity_id")
            state = new_state.get("state")
            last_changed = new_state.get("last_changed")
            last_updated = new_state.get("last_updated")

        # HA image entities can rotate access-token query strings in
        # `entity_picture` without changing the semantic state. Use HA's
        # state timestamps as the idempotency boundary and keep volatile
        # attributes out of the key; the complete attributes remain stored
        # in the JSONB state row.
        state_key = {
            "entity_id": entity_id,
            "state": state,
            "last_changed": last_changed,
            "last_updated": last_updated,
        }
        source_time = _payload_time(event)
        time_key = source_time.isoformat() if source_time else "none"
        # A live state_changed message and a REST seed/history record can
        # describe the same HA state transition. They must share a logical
        # key even though their transport event types differ.
        return f"home_assistant:state:{time_key}:{canonical_sha256(state_key)}"

    source_time = _payload_time(event)
    time_key = source_time.isoformat() if source_time else "none"
    return f"home_assistant:{event_type}:{time_key}:{canonical_sha256(event)}"
