from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from app import db
from app.api import router
from app.config import AppSettings, get_settings
from app.connectors.frigate_mqtt import FrigateMqttConnector
from app.connectors.home_assistant import HomeAssistantConnector
from app.logging import configure_logging
from app.monitoring.disk import DiskMonitor
from app.monitoring.heartbeats import write_heartbeat
from app.services.media_archive import MediaArchivePolicy, MediaArchiver
from app.services.retention import expire_archived_assets


def camera_health_status(camera: dict[str, Any]) -> str:
    quality = camera.get("connection_quality")
    camera_fps = float(camera.get("camera_fps") or 0)
    process_fps = float(camera.get("process_fps") or 0)
    if quality in {"unusable", "poor"} or camera_fps <= 0 or process_fps <= 0:
        return "degraded"
    return "healthy"


async def _frigate_health(frigate_url: str) -> tuple[str, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(base_url=frigate_url.rstrip("/"), timeout=5) as client:
            response = await client.get("/api/stats")
            response.raise_for_status()
            payload = response.json()
        camera = (payload.get("cameras") or {}).get("iphone_camera") or {}
        return camera_health_status(camera), {
            "camera_fps": camera.get("camera_fps"),
            "process_fps": camera.get("process_fps"),
            "detection_fps": camera.get("detection_fps"),
            "skipped_fps": camera.get("skipped_fps"),
            "connection_quality": camera.get("connection_quality"),
            "reconnects_last_hour": camera.get("reconnects_last_hour"),
            "stalls_last_hour": camera.get("stalls_last_hour"),
        }
    except Exception as exc:
        return "unavailable", {"error": type(exc).__name__}


async def _safe_heartbeat(
    session_factory: Any,
    name: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        await write_heartbeat(session_factory, name, status, details)
    except Exception:
        logging.getLogger(__name__).exception("service heartbeat write failed", extra={"service": name})


async def _heartbeat_loop(
    session_factory: Any,
    settings: AppSettings,
    disk_monitor: DiskMonitor,
    mqtt_connector: FrigateMqttConnector,
    ha_connector: HomeAssistantConnector,
    media_archiver: MediaArchiver,
    stop_event: asyncio.Event,
) -> None:
    retention_due = True
    last_retention_at = 0.0
    interval = max(5, settings.heartbeat_interval_seconds)
    while not stop_event.is_set():
        disk = disk_monitor.status()
        ingestor_status = "degraded" if disk.level == "critical" else "healthy"
        await _safe_heartbeat(
            session_factory,
            "ingestor",
            ingestor_status,
            {"disk": disk.as_dict()},
        )
        mqtt_health = mqtt_connector.health_snapshot()
        await _safe_heartbeat(session_factory, "mqtt", mqtt_health["status"], mqtt_health)
        ha_health = ha_connector.health_snapshot()
        await _safe_heartbeat(session_factory, "home_assistant", ha_health["status"], ha_health)
        media_health = media_archiver.health_snapshot()
        await _safe_heartbeat(session_factory, "media_archive", media_health["status"], media_health)

        frigate_status, frigate_details = await _frigate_health(settings.frigate_url)
        await _safe_heartbeat(session_factory, "frigate", frigate_status, frigate_details)

        if settings.retention_enabled and retention_due:
            try:
                summary = await expire_archived_assets(
                    session_factory,
                    Path(settings.media_archive_root),
                    apply=settings.retention_apply,
                )
                await _safe_heartbeat(
                    session_factory,
                    "retention",
                    "degraded" if summary["unsafe"] else "healthy",
                    {**summary, "apply": settings.retention_apply},
                )
            except Exception:
                logging.getLogger(__name__).exception("retention scan failed")
                await _safe_heartbeat(session_factory, "retention", "degraded", {"error": "scan_failed"})
            retention_due = False
            last_retention_at = time.monotonic()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            retention_due = (
                settings.retention_enabled
                and time.monotonic() - last_retention_at >= max(60, settings.retention_interval_seconds)
            )


def create_app(settings: AppSettings | None = None, start_connectors: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_settings = application.state.settings or get_settings()
        configure_logging(active_settings.log_level)
        db.initialize_database(active_settings.database_url)
        application.state.disk_monitor = DiskMonitor(
            path=active_settings.postgres_data_path,
            max_database_bytes=active_settings.database_max_bytes,
            warning_free_bytes=active_settings.disk_warning_free_bytes,
            critical_free_bytes=active_settings.disk_critical_free_bytes,
            warning_free_ratio=active_settings.disk_warning_free_ratio,
            critical_free_ratio=active_settings.disk_critical_free_ratio,
        )

        connectors: list[object] = []
        media_archiver = None
        if application.state.start_connectors and db.session_factory is not None:
            media_archiver = MediaArchiver(
                session_factory=db.session_factory,
                policy=MediaArchivePolicy(
                    root=Path(active_settings.media_archive_root),
                    max_bytes=active_settings.media_archive_max_bytes,
                    reserve_free_bytes=active_settings.media_archive_reserve_free_bytes,
                    retention_days=active_settings.media_archive_retention_days,
                ),
                frigate_url=active_settings.frigate_url,
                ha_url=active_settings.ha_url,
                ha_token=active_settings.ha_token,
                retry_max_attempts=active_settings.media_retry_max_attempts,
                retry_interval_seconds=active_settings.media_retry_interval_seconds,
            )
            await media_archiver.start()
            mqtt_connector = FrigateMqttConnector(
                session_factory=db.session_factory,
                mqtt_host=active_settings.mqtt_host,
                mqtt_port=active_settings.mqtt_port,
                mqtt_client_id=active_settings.mqtt_client_id,
                mqtt_username=active_settings.mqtt_username,
                mqtt_password=active_settings.mqtt_password,
                mqtt_topics=active_settings.mqtt_topics,
                max_inflight=active_settings.mqtt_max_inflight,
                media_archiver=media_archiver,
            )
            ha_connector = HomeAssistantConnector(
                session_factory=db.session_factory,
                ha_url=active_settings.ha_url,
                ha_token=active_settings.ha_token,
                event_types=active_settings.ha_event_types,
                history_max_lookback_seconds=active_settings.ha_history_max_lookback_seconds,
                history_overlap_seconds=active_settings.ha_history_overlap_seconds,
                retry_max_attempts=active_settings.retry_max_attempts,
                retry_base_seconds=active_settings.retry_base_seconds,
                retry_max_delay_seconds=active_settings.retry_max_delay_seconds,
                media_archiver=media_archiver,
            )
            await mqtt_connector.start()
            await ha_connector.start()
            connectors.extend((mqtt_connector, ha_connector))
            application.state.connectors = connectors
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(
                    db.session_factory,
                    active_settings,
                    application.state.disk_monitor,
                    mqtt_connector,
                    ha_connector,
                    media_archiver,
                    heartbeat_stop,
                ),
                name="service-heartbeats",
            )
            application.state.heartbeat_stop = heartbeat_stop
            application.state.heartbeat_task = heartbeat_task

        try:
            yield
        finally:
            heartbeat_stop = getattr(application.state, "heartbeat_stop", None)
            heartbeat_task = getattr(application.state, "heartbeat_task", None)
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            for connector in reversed(connectors):
                try:
                    await connector.stop()  # type: ignore[attr-defined]
                except Exception:
                    logging.getLogger(__name__).exception("connector shutdown failed")
            if media_archiver is not None:
                await media_archiver.stop()
            await db.dispose_database()

    application = FastAPI(title="Home Server Ingestion", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.start_connectors = start_connectors
    application.include_router(router)
    return application


app = create_app()
