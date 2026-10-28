from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
try:
    from websockets.asyncio.client import connect
except ImportError:  # websockets 13 compatibility
    from websockets import connect

from app.domain.normalizers import normalize_home_assistant
from app.domain.timestamps import parse_timestamp
from app.models import ConnectorCheckpoint
from app.services.media_archive import MediaArchiver
from app.services.ingestion import build_home_assistant_envelope, ingest_message
from app.services.retry import retry_async


class HomeAssistantConnector:
    checkpoint_name = "home_assistant"

    def __init__(
        self,
        session_factory: Callable[[], Any],
        ha_url: str,
        ha_token: str,
        event_types: tuple[str, ...],
        history_max_lookback_seconds: int = 900,
        history_overlap_seconds: int = 5,
        retry_max_attempts: int = 8,
        retry_base_seconds: float = 0.5,
        retry_max_delay_seconds: float = 30.0,
        media_archiver: MediaArchiver | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ha_url = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._event_types = event_types
        self._history_max_lookback_seconds = history_max_lookback_seconds
        self._history_overlap_seconds = history_overlap_seconds
        self._retry_max_attempts = retry_max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._media_archiver = media_archiver
        self._logger = logger or logging.getLogger(__name__)
        self._http: httpx.AsyncClient | None = None
        self._websocket: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_source_at: datetime | None = None
        self._checkpoint_exists = False
        self._recovery_needed = False
        self._seed_complete = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._http = httpx.AsyncClient(
            base_url=self._ha_url,
            headers={"Authorization": f"Bearer {self._ha_token}"},
            timeout=20,
        )
        self._task = asyncio.create_task(self._run(), name="home-assistant-connector")

    async def stop(self) -> None:
        self._stop_event.set()
        websocket = self._websocket
        if websocket is not None:
            await websocket.close()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def _run(self) -> None:
        await self._load_checkpoint()
        delay = self._retry_base_seconds
        while not self._stop_event.is_set():
            try:
                if not self._seed_complete:
                    await self._seed_states()
                    self._seed_complete = True
                await self._connect_and_consume()
                delay = self._retry_base_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Home Assistant connector cycle failed")
                self._recovery_needed = self._last_source_at is not None
                await self._write_checkpoint(state="degraded", gap_reason="websocket disconnected; recovery pending")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(self._retry_max_delay_seconds, delay * 2)

    async def _seed_states(self) -> None:
        had_checkpoint = self._checkpoint_exists
        states = await self._request_json("/api/states")
        if not isinstance(states, list):
            raise ValueError("Home Assistant /api/states returned a non-list response")

        latest_seed_time: datetime | None = None
        for state in states:
            if not isinstance(state, dict) or not state.get("entity_id"):
                continue
            source_event_at = parse_timestamp(state.get("last_updated") or state.get("last_changed"))
            event = {
                "event_type": "state_seed",
                "time_fired": source_event_at.isoformat() if source_event_at else None,
                "data": {
                    "entity_id": state["entity_id"],
                    "state": state.get("state"),
                    "attributes": state.get("attributes") or {},
                    "last_changed": state.get("last_changed"),
                    "last_updated": state.get("last_updated"),
                },
            }
            await self._handle_event(event)
            if source_event_at and (latest_seed_time is None or source_event_at > latest_seed_time):
                latest_seed_time = source_event_at

        if not had_checkpoint and latest_seed_time:
            self._last_source_at = latest_seed_time
            await self._write_checkpoint(state="healthy", last_source_at=latest_seed_time)
        self._recovery_needed = had_checkpoint
        self._logger.info("Home Assistant state seed complete", extra={"state_count": len(states)})

    async def _connect_and_consume(self) -> None:
        websocket_url = self._websocket_url()
        async with connect(websocket_url, open_timeout=20, ping_interval=20, ping_timeout=20) as websocket:
            self._websocket = websocket
            try:
                await self._authenticate(websocket)
                await self._subscribe(websocket)
                if self._recovery_needed and self._last_source_at:
                    await self._recover_gap(self._last_source_at, datetime.now(UTC))
                    self._recovery_needed = False
                await self._write_checkpoint(state="healthy", gap_reason=None)

                while not self._stop_event.is_set():
                    raw_message = await websocket.recv()
                    if raw_message is None:
                        raise ConnectionError("Home Assistant websocket closed")
                    message = json.loads(raw_message)
                    if message.get("type") == "event" and isinstance(message.get("event"), dict):
                        event = message["event"]
                        if event.get("event_type") in self._event_types:
                            await self._handle_event(event)
            finally:
                self._websocket = None

    async def _authenticate(self, websocket: Any) -> None:
        hello = json.loads(await websocket.recv())
        if hello.get("type") != "auth_required":
            raise ConnectionError("Home Assistant websocket did not request authentication")
        await websocket.send(json.dumps({"type": "auth", "access_token": self._ha_token}))
        authenticated = json.loads(await websocket.recv())
        if authenticated.get("type") != "auth_ok":
            raise PermissionError("Home Assistant websocket authentication failed")

    async def _subscribe(self, websocket: Any) -> None:
        for subscription_id, event_type in enumerate(self._event_types, start=1):
            await websocket.send(
                json.dumps({"id": subscription_id, "type": "subscribe_events", "event_type": event_type})
            )

    async def _handle_event(self, event: dict[str, Any]) -> None:
        normalized = normalize_home_assistant(event)
        source_event_at = normalized.source_event_at
        envelope = build_home_assistant_envelope(event, source_event_at)
        async with self._session_factory() as session:
            await ingest_message(session, envelope)
        if self._media_archiver is not None:
            await self._media_archiver.observe_home_assistant(normalized)

        if source_event_at and (self._last_source_at is None or source_event_at > self._last_source_at):
            self._last_source_at = source_event_at
            await self._write_checkpoint(last_source_at=source_event_at, state="healthy")

    async def _recover_gap(self, last_source_at: datetime, now: datetime) -> None:
        requested_start = last_source_at - timedelta(seconds=self._history_overlap_seconds)
        requested_end = now
        gap_reason = (
            "bounded REST history recovery is state-only; automation/call_service events and purged history "
            "may be missing"
        )
        if (requested_end - requested_start).total_seconds() > self._history_max_lookback_seconds:
            requested_start = requested_end - timedelta(seconds=self._history_max_lookback_seconds)
            gap_reason += "; recovery window capped by HA_HISTORY_MAX_LOOKBACK_SECONDS"

        await self._write_checkpoint(
            state="recovering",
            recovery_requested_start=requested_start,
            recovery_requested_end=requested_end,
            recovery_complete=False,
            gap_reason=gap_reason,
        )

        try:
            history = await self._request_json(
                f"/api/history/period/{requested_start.isoformat()}",
                params={"end_time": requested_end.isoformat(), "minimal_response": "false"},
            )
            recovered_start: datetime | None = None
            recovered_end: datetime | None = None
            for entity_history in history if isinstance(history, list) else []:
                if not isinstance(entity_history, list):
                    continue
                for state in entity_history:
                    if not isinstance(state, dict) or not state.get("entity_id"):
                        continue
                    state_time = parse_timestamp(state.get("last_updated") or state.get("last_changed"))
                    event = {
                        "event_type": "state_seed",
                        "time_fired": state_time.isoformat() if state_time else None,
                        "data": {
                            "entity_id": state["entity_id"],
                            "state": state.get("state"),
                            "attributes": state.get("attributes") or {},
                            "last_changed": state.get("last_changed"),
                            "last_updated": state.get("last_updated"),
                        },
                    }
                    await self._handle_event(event)
                    if state_time and (recovered_start is None or state_time < recovered_start):
                        recovered_start = state_time
                    if state_time and (recovered_end is None or state_time > recovered_end):
                        recovered_end = state_time
            await self._write_checkpoint(
                state="degraded",
                recovered_start=recovered_start,
                recovered_end=recovered_end,
                recovery_complete=False,
                gap_reason=gap_reason,
            )
        except Exception as exc:
            self._logger.exception("Home Assistant REST history recovery failed")
            await self._write_checkpoint(
                state="degraded",
                recovery_complete=False,
                gap_reason=f"REST history recovery request failed: {type(exc).__name__}",
            )

    async def _load_checkpoint(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ConnectorCheckpoint).where(ConnectorCheckpoint.name == self.checkpoint_name)
            )
            checkpoint = result.scalar_one_or_none()
        if checkpoint:
            self._checkpoint_exists = True
            self._last_source_at = checkpoint.last_source_at

    async def _write_checkpoint(self, **values: Any) -> None:
        values = {key: value for key, value in values.items() if value is not None or key in {"gap_reason"}}
        values["name"] = self.checkpoint_name
        stmt = insert(ConnectorCheckpoint).values(**values)
        update_values = {key: getattr(stmt.excluded, key) for key in values if key != "name"}
        update_values["updated_at"] = datetime.now(UTC)
        stmt = stmt.on_conflict_do_update(index_elements=[ConnectorCheckpoint.name], set_=update_values)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(stmt)
        self._checkpoint_exists = True

    async def _request_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._ha_url,
                headers={"Authorization": f"Bearer {self._ha_token}"},
                timeout=20,
            )

        async def request() -> Any:
            response = await self._http.get(path, params=params)
            response.raise_for_status()
            return response.json()

        return await retry_async(
            request,
            max_attempts=self._retry_max_attempts,
            base_delay=self._retry_base_seconds,
            max_delay=self._retry_max_delay_seconds,
            logger=self._logger,
        )

    def _websocket_url(self) -> str:
        parsed = urlparse(self._ha_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "/api/websocket", "", "", ""))
