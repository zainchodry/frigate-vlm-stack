from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app import db
from app.api import router
from app.config import AppSettings, get_settings
from app.connectors.frigate_mqtt import FrigateMqttConnector
from app.connectors.home_assistant import HomeAssistantConnector
from app.logging import configure_logging
from app.monitoring.disk import DiskMonitor
from app.services.media_archive import MediaArchivePolicy, MediaArchiver


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

        try:
            yield
        finally:
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
