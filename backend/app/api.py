from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select, text

from app import db
from app.config import AppSettings, get_settings
from app.monitoring.disk import DiskMonitor
from app.models import (
    FaceRecognitionResult,
    FrigateEvent,
    FrigateReview,
    HomeAssistantEntity,
    HomeAssistantStateChange,
    MediaAsset,
    ServiceHeartbeat,
)


router = APIRouter()


def api_token_is_valid(request: Request, expected_token: str) -> bool:
    if not expected_token:
        return False
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
    else:
        candidate = request.headers.get("x-api-key", "")
    return bool(candidate) and secrets.compare_digest(candidate, expected_token)


def _require_api_token(request: Request) -> None:
    settings = _settings(request)
    if not api_token_is_valid(request, settings.api_token):
        raise HTTPException(status_code=401, detail="authentication required")


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.key) for column in row.__table__.columns}


async def _media_for_events(session: Any, event_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not event_ids:
        return {}
    result = await session.execute(select(MediaAsset).where(MediaAsset.event_id.in_(event_ids)))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for asset in result.scalars().all():
        grouped.setdefault(asset.event_id, []).append(_row_dict(asset))
    return grouped


async def _face_results_for_events(session: Any, event_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not event_ids:
        return {}
    result = await session.execute(
        select(FaceRecognitionResult)
        .where(FaceRecognitionResult.event_id.in_(event_ids))
        .order_by(FaceRecognitionResult.recognized_at.desc())
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in result.scalars().all():
        if item.event_id is not None:
            grouped.setdefault(item.event_id, []).append(_row_dict(item))
    return grouped


def _settings(request: Request) -> AppSettings:
    return request.app.state.settings or get_settings()


def _disk_monitor(request: Request, settings: AppSettings) -> DiskMonitor:
    monitor = getattr(request.app.state, "disk_monitor", None)
    if monitor is None:
        monitor = DiskMonitor(
            path=settings.postgres_data_path,
            max_database_bytes=settings.database_max_bytes,
            warning_free_bytes=settings.disk_warning_free_bytes,
            critical_free_bytes=settings.disk_critical_free_bytes,
            warning_free_ratio=settings.disk_warning_free_ratio,
            critical_free_ratio=settings.disk_critical_free_ratio,
        )
        request.app.state.disk_monitor = monitor
    return monitor


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


async def _readiness(request: Request) -> tuple[int, dict[str, Any]]:
    settings = _settings(request)
    disk = _disk_monitor(request, settings).status()
    database: dict[str, Any] = {"status": "unavailable"}
    database_ok = False

    if db.session_factory is not None:
        try:
            async with db.session_factory() as session:
                await session.execute(text("SELECT 1"))
            database = {"status": "ok"}
            database_ok = True
        except Exception as exc:
            database = {"status": "unavailable", "error": type(exc).__name__}

    overall = "ok" if database_ok and disk.level != "critical" else "not_ready"
    status_code = 200 if overall == "ok" else 503
    return status_code, {
        "status": overall,
        "database": database,
        "disk": disk.as_dict(),
    }


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    status_code, content = await _readiness(request)
    return JSONResponse(status_code=status_code, content=content)


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    status_code, content = await _readiness(request)
    return JSONResponse(status_code=status_code, content=content)


@router.get("/health/services")
async def health_services() -> dict[str, Any]:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        result = await session.execute(select(ServiceHeartbeat).order_by(ServiceHeartbeat.name))
        services = [_row_dict(row) for row in result.scalars().all()]
    return {"services": services}


@router.get("/api/frigate/events")
async def frigate_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    camera: str | None = None,
    _authorized: None = Depends(_require_api_token),
) -> dict[str, Any]:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        statement = select(FrigateEvent).order_by(FrigateEvent.start_time.desc()).limit(limit)
        if camera:
            statement = statement.where(FrigateEvent.camera == camera)
        result = await session.execute(statement)
        rows = list(result.scalars().all())
        media = await _media_for_events(session, [row.event_id for row in rows])
        faces = await _face_results_for_events(session, [row.event_id for row in rows])
    return {
        "events": [
            {
                **_row_dict(row),
                "media": media.get(row.event_id, []),
                "face_recognition": faces.get(row.event_id, []),
            }
            for row in rows
        ]
    }


@router.get("/api/frigate/events/{event_id}")
async def frigate_event(
    event_id: str,
    request: Request,
    _authorized: None = Depends(_require_api_token),
) -> dict[str, Any]:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        event = await session.get(FrigateEvent, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        result = await session.execute(select(MediaAsset).where(MediaAsset.event_id == event_id))
        media = [_row_dict(asset) for asset in result.scalars().all()]
        result = await session.execute(
            select(FaceRecognitionResult)
            .where(FaceRecognitionResult.event_id == event_id)
            .order_by(FaceRecognitionResult.recognized_at.desc())
        )
        faces = [_row_dict(item) for item in result.scalars().all()]
    return {"event": _row_dict(event), "media": media, "face_recognition": faces}


@router.get("/api/frigate/face-recognition")
async def frigate_face_recognition(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    identity: str | None = None,
    _authorized: None = Depends(_require_api_token),
) -> dict[str, Any]:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        statement = (
            select(FaceRecognitionResult)
            .order_by(FaceRecognitionResult.recognized_at.desc())
            .limit(limit)
        )
        if identity:
            statement = statement.where(FaceRecognitionResult.identity == identity)
        result = await session.execute(statement)
        rows = [_row_dict(item) for item in result.scalars().all()]
    return {"results": rows}


@router.get("/api/media/{asset_id}/metadata")
async def media_metadata(
    asset_id: str,
    request: Request,
    _authorized: None = Depends(_require_api_token),
) -> dict[str, Any]:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="media asset not found")
    return {"media": _row_dict(asset)}


@router.get("/api/media/{asset_id}")
async def media_file(
    asset_id: str,
    request: Request,
    _authorized: None = Depends(_require_api_token),
) -> FileResponse:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="media asset not found")
    if asset.copy_status != "archived" or not asset.archive_path:
        raise HTTPException(status_code=409, detail="media asset is not archived")
    root = Path(_settings(request).media_archive_root).resolve(strict=False)
    path = Path(asset.archive_path).resolve(strict=False)
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="archived media file unavailable")
    return FileResponse(path, media_type=asset.mime_type or "application/octet-stream")


@router.get("/api/home-assistant/entities/{entity_id}")
async def home_assistant_entity(
    entity_id: str,
    request: Request,
    _authorized: None = Depends(_require_api_token),
) -> dict[str, Any]:
    if db.session_factory is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    async with db.session_factory() as session:
        entity = await session.get(HomeAssistantEntity, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        result = await session.execute(
            select(HomeAssistantStateChange)
            .where(HomeAssistantStateChange.entity_id == entity_id)
            .order_by(HomeAssistantStateChange.source_event_at.desc())
            .limit(100)
        )
        states = [_row_dict(state) for state in result.scalars().all()]
    return {"entity": _row_dict(entity), "states": states}
