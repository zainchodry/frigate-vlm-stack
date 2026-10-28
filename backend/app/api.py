from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db
from app.config import AppSettings, get_settings
from app.monitoring.disk import DiskMonitor


router = APIRouter()


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
