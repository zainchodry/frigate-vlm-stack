"""Backfill actual Frigate SQLite events without modifying the source database.

This importer is intentionally separate from live MQTT ingestion. Imported
rows are marked with source ``frigate_sqlite`` so a demo can distinguish them
from events received live through Mosquitto.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.dialects.postgresql import insert

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from app.config import AppSettings  # noqa: E402
from app.domain.normalizers import normalize_frigate  # noqa: E402
from app.models import MediaAsset  # noqa: E402
from app.services.media_archive import MediaArchivePolicy, build_archive_path  # noqa: E402
from app.services.ingestion import apply_projection  # noqa: E402
from app.services.ingestion import IngestEnvelope, ingest_message  # noqa: E402


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


def event_payload(row: sqlite3.Row) -> tuple[dict[str, Any], datetime | None]:
    event_id = str(row["id"])
    start_time = _timestamp(row["start_time"])
    end_time = _timestamp(row["end_time"])
    data = _json(row["data"], {})
    after = {
        "id": event_id,
        "label": row["label"],
        "camera": row["camera"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "top_score": row["top_score"],
        "zones": _json(row["zones"], []),
        "thumbnail": row["thumbnail"],
        "has_clip": bool(row["has_clip"]),
        "has_snapshot": bool(row["has_snapshot"]),
        "sub_label": row["sub_label"],
        "score": row["score"],
        "data": data,
    }
    if after["has_snapshot"]:
        after["snapshot_path"] = f"/api/events/{event_id}/snapshot.jpg"
    if after["has_clip"]:
        after["recording_path"] = f"/api/events/{event_id}/clip.mp4"
    return {"type": "historical_import", "after": after}, end_time or start_time


def _read_rows(database_path: Path, limit: int | None) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = "select * from event order by start_time"
    if limit is not None:
        query += " limit ?"
        rows = connection.execute(query, (limit,)).fetchall()
    else:
        rows = connection.execute(query).fetchall()
    result = []
    for row in rows:
        payload, source_event_at = event_payload(row)
        result.append({"payload": payload, "source_event_at": source_event_at})
    connection.close()
    return result


async def _import(database_url: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    inserted = 0
    duplicates = 0
    try:
        for item in rows:
            event_id = item["payload"]["after"]["id"]
            envelope = IngestEnvelope(
                source="frigate_sqlite",
                topic="frigate/events",
                idempotency_key=f"frigate_sqlite:event:{event_id}",
                source_event_at=item["source_event_at"],
                payload=item["payload"],
            )
            async with session_factory() as session:
                result = await ingest_message(session, envelope)
                if result.duplicate:
                    # The first version of this importer persisted the
                    # source envelopes before the SQLite source branch was
                    # enabled. Re-projecting an existing, source-labeled
                    # envelope is an idempotent repair; it does not create a
                    # second ingest message.
                    async with session.begin():
                        await apply_projection(
                            session,
                            result.message_id,
                            normalize_frigate(envelope.topic, envelope.payload),
                            envelope.received_at,
                            source_system="frigate_sqlite",
                        )
            inserted += int(result.inserted)
            duplicates += int(result.duplicate)
    finally:
        await engine.dispose()
    return inserted, duplicates


async def _enqueue_recording(
    settings: AppSettings,
    session_factory: async_sessionmaker,
    sqlite_path: Path,
    event_id: str,
) -> bool:
    connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute("select * from event where id = ?", (event_id,)).fetchone()
    connection.close()
    if row is None:
        raise ValueError(f"Frigate SQLite event not found: {event_id}")
    if not row["has_clip"]:
        raise ValueError(f"Frigate event has no completed clip: {event_id}")

    policy = MediaArchivePolicy(
        root=Path(settings.media_archive_root),
        max_bytes=settings.media_archive_max_bytes,
        reserve_free_bytes=settings.media_archive_reserve_free_bytes,
        retention_days=settings.media_archive_retention_days,
    )
    source_event_at = _timestamp(row["end_time"] or row["start_time"])
    archive_path = build_archive_path(policy, "frigate", event_id, "recording", ".mp4")
    values = {
        "asset_key": f"frigate:event:{event_id}:recording",
        "source": "frigate",
        "event_id": event_id,
        "media_type": "recording",
        "source_event_at": source_event_at,
        "source_url": f"{settings.frigate_url.rstrip('/')}/api/events/{event_id}/clip.mp4",
        "archive_path": str(archive_path),
        "storage_backend": "filesystem",
        "retention_until": datetime.now(UTC) + timedelta(days=settings.media_archive_retention_days),
        "availability_status": "pending",
        "copy_status": "pending",
    }
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                insert(MediaAsset)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["asset_key"])
                .returning(MediaAsset.id)
            )
            return result.scalar_one_or_none() is not None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=APP_ROOT.parent / "frigate/config/frigate.db",
        help="read-only Frigate SQLite database path",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--enqueue-recording",
        metavar="EVENT_ID",
        help="enqueue one completed SQLite event clip for the deployed archive worker",
    )
    args = parser.parse_args()
    settings = AppSettings(_env_file=APP_ROOT.parent / ".env")
    rows = _read_rows(args.sqlite.resolve(), args.limit)
    inserted, duplicates = asyncio.run(_import(settings.database_url, rows))
    enqueued = False
    if args.enqueue_recording:
        async def enqueue() -> bool:
            engine = create_async_engine(settings.database_url, pool_pre_ping=True)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                return await _enqueue_recording(settings, factory, args.sqlite.resolve(), args.enqueue_recording)
            finally:
                await engine.dispose()

        enqueued = asyncio.run(enqueue())
    print(
        f"source=frigate_sqlite rows={len(rows)} inserted={inserted} "
        f"duplicates={duplicates} recording_enqueued={str(enqueued).lower()}"
    )


if __name__ == "__main__":
    main()
