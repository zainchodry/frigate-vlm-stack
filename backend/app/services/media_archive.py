from __future__ import annotations

import hashlib
import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.keys import canonical_sha256
from app.domain.normalizers import NormalizedMessage
from app.models import MediaAsset


class ArchiveQuotaExceeded(RuntimeError):
    """Raised when an archive operation would exceed a safety limit."""


@dataclass(frozen=True)
class MediaArchivePolicy:
    root: Path
    max_bytes: int
    reserve_free_bytes: int
    retention_days: int = 7


@dataclass(frozen=True)
class ArchiveCopyResult:
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class MediaSource:
    asset_key: str
    source: str
    media_type: str
    fetch_url: str
    stored_url: str
    suffix: str
    event_id: str | None = None
    review_id: str | None = None
    entity_id: str | None = None
    source_event_at: datetime | None = None


def is_protected_source(source: Path, protected_roots: Iterable[Path]) -> bool:
    candidate = source.resolve(strict=False)
    for protected in protected_roots:
        protected_path = protected.resolve(strict=False)
        if candidate == protected_path or protected_path in candidate.parents:
            return True
    return False


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip(".")
    return component[:160] or "unknown"


def build_archive_path(
    policy: MediaArchivePolicy,
    source: str,
    asset_key: str,
    media_type: str,
    suffix: str,
) -> Path:
    relative = Path(
        _safe_component(source),
        _safe_component(asset_key),
        f"{_safe_component(media_type)}{suffix}",
    )
    path = (policy.root / relative).resolve(strict=False)
    if not path.is_relative_to(policy.root.resolve(strict=False)):
        raise ValueError("archive path escaped archive root")
    return path


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.name.endswith(".partial"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_collision_safe_path(destination: Path, digest: str, asset_id: str) -> Path:
    """Reuse identical bytes, otherwise choose a path without overwriting."""
    if not destination.exists():
        return destination
    if destination.is_file() and _sha256_file(destination) == digest:
        return destination

    content_path = destination.with_name(f"{destination.stem}-{digest[:16]}{destination.suffix}")
    if not content_path.exists():
        return content_path
    if content_path.is_file() and _sha256_file(content_path) == digest:
        return content_path

    unique_path = destination.with_name(
        f"{destination.stem}-{digest[:16]}-{_safe_component(asset_id)}{destination.suffix}"
    )
    counter = 1
    while unique_path.exists():
        if unique_path.is_file() and _sha256_file(unique_path) == digest:
            return unique_path
        unique_path = destination.with_name(
            f"{destination.stem}-{digest[:16]}-{_safe_component(asset_id)}-{counter}{destination.suffix}"
        )
        counter += 1
    return unique_path


def _check_capacity(policy: MediaArchivePolicy, incoming_bytes: int, free_bytes: int) -> None:
    if incoming_bytes < 0:
        raise ValueError("incoming_bytes must not be negative")
    if _directory_size(policy.root) + incoming_bytes > policy.max_bytes:
        raise ArchiveQuotaExceeded("media archive quota would be exceeded")
    if free_bytes - incoming_bytes < policy.reserve_free_bytes:
        raise ArchiveQuotaExceeded("filesystem reserve would be breached")


def copy_bytes_atomically(
    policy: MediaArchivePolicy,
    asset_key: str,
    data: bytes,
    free_bytes: int,
) -> ArchiveCopyResult:
    _check_capacity(policy, len(data), free_bytes)
    destination = build_archive_path(policy, "archive", asset_key, "blob", ".bin")
    return _copy_chunks_atomically(policy, destination, [data], free_bytes)


def _copy_chunks_atomically(
    policy: MediaArchivePolicy,
    destination: Path,
    chunks: Iterable[bytes],
    free_bytes: int,
) -> ArchiveCopyResult:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    written = 0
    digest = hashlib.sha256()
    try:
        with partial.open("xb") as output:
            for chunk in chunks:
                if not chunk:
                    continue
                if _directory_size(policy.root) + written + len(chunk) > policy.max_bytes:
                    raise ArchiveQuotaExceeded("media archive quota would be exceeded")
                if free_bytes - written - len(chunk) < policy.reserve_free_bytes:
                    raise ArchiveQuotaExceeded("filesystem reserve would be breached")
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(partial, destination)
    except Exception:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
        raise
    return ArchiveCopyResult(destination, written, digest.hexdigest())


def _stored_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _content_suffix(url: str, default: str = ".jpg") -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"} else default


def _image_urls(attributes: dict[str, Any]) -> list[str]:
    allowed_keys = {
        "entity_picture",
        "entity_picture_local",
        "image_url",
        "thumbnail_url",
        "camera_image",
    }
    found: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and key in allowed_keys:
            if value.startswith("/api/") or value.startswith("http://") or value.startswith("https://"):
                found.append(value)

    walk(attributes)
    return list(dict.fromkeys(found))


class MediaArchiver:
    """Durable, quota-bound archive worker for completed source media."""

    def __init__(
        self,
        session_factory: Any,
        policy: MediaArchivePolicy,
        frigate_url: str,
        ha_url: str,
        ha_token: str,
        retry_max_attempts: int = 3,
        logger: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._policy = policy
        self._frigate_url = frigate_url.rstrip("/")
        self._ha_url = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._retry_max_attempts = max(1, retry_max_attempts)
        self._logger = logger
        self._client: httpx.AsyncClient | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        self._policy.root.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=5.0))
        await self.resume_pending()

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def resume_pending(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaAsset.id).where(
                    MediaAsset.copy_status.in_(("pending", "failed")),
                    MediaAsset.attempt_count < self._retry_max_attempts,
                )
            )
            asset_ids = [str(asset_id) for asset_id in result.scalars().all()]
        for asset_id in asset_ids:
            self._schedule(asset_id)

    async def observe_frigate(self, normalized: NormalizedMessage) -> None:
        if normalized.kind != "frigate_event" or not normalized.key:
            return
        event_type = (normalized.event_type or "").lower()
        if event_type not in {"new", "update", "end"}:
            return
        values = normalized.values
        camera = str(values.get("camera") or "unknown")
        snapshot_url = f"{self._frigate_url}/api/events/{normalized.key}/snapshot.jpg"
        await self._enqueue(
            MediaSource(
                asset_key=f"frigate:event:{normalized.key}:snapshot",
                source="frigate",
                media_type="snapshot",
                fetch_url=snapshot_url,
                stored_url=_stored_url(snapshot_url),
                suffix=".jpg",
                event_id=normalized.key,
                source_event_at=normalized.source_event_at,
            ),
            camera=camera,
        )
        # Only an end message is eligible for a recording/clip archive. This
        # avoids reading a recording while Frigate is still writing it.
        if event_type == "end":
            clip_url = f"{self._frigate_url}/api/events/{normalized.key}/clip.mp4"
            await self._enqueue(
                MediaSource(
                    asset_key=f"frigate:event:{normalized.key}:recording",
                    source="frigate",
                    media_type="recording",
                    fetch_url=clip_url,
                    stored_url=_stored_url(clip_url),
                    suffix=".mp4",
                    event_id=normalized.key,
                    source_event_at=normalized.source_event_at,
                ),
                camera=camera,
            )

    async def observe_home_assistant(
        self,
        normalized: NormalizedMessage,
    ) -> None:
        if normalized.kind not in {"home_assistant_state_change", "home_assistant_state_seed"}:
            return
        entity_id = str(normalized.values.get("entity_id") or "unknown")
        attributes = normalized.values.get("new_attributes") or {}
        if not isinstance(attributes, dict):
            return
        for image_url in _image_urls(attributes):
            fetch_url = urljoin(f"{self._ha_url}/", image_url)
            # HA image proxy URLs can carry rotating access-token query
            # strings. They identify the same entity image and must not
            # create a new archive row/file on every state seed or update.
            digest = canonical_sha256(_stored_url(fetch_url))[:24]
            await self._enqueue(
                MediaSource(
                    asset_key=f"home_assistant:entity:{entity_id}:{digest}",
                    source="home_assistant",
                    media_type="image",
                    fetch_url=fetch_url,
                    stored_url=_stored_url(fetch_url),
                    suffix=_content_suffix(fetch_url),
                    entity_id=entity_id,
                    source_event_at=normalized.source_event_at,
                ),
                camera=entity_id,
            )

    async def _enqueue(self, source: MediaSource, camera: str | None = None) -> None:
        archive_path = build_archive_path(
            self._policy,
            source.source,
            source.event_id or source.entity_id or source.asset_key,
            source.media_type,
            source.suffix,
        )
        retention_until = datetime.now(UTC) + timedelta(days=self._policy.retention_days)
        values = {
            "asset_key": source.asset_key,
            "source": source.source,
            "event_id": source.event_id,
            "review_id": source.review_id,
            "entity_id": source.entity_id,
            "media_type": source.media_type,
            "source_event_at": source.source_event_at,
            "source_url": source.stored_url,
            "archive_path": str(archive_path),
            "storage_backend": "filesystem",
            "retention_until": retention_until,
            "availability_status": "pending",
            "copy_status": "pending",
        }
        async with self._session_factory() as session:
            async with session.begin():
                stmt = (
                    insert(MediaAsset)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["asset_key"])
                    .returning(MediaAsset.id)
                )
                result = await session.execute(stmt)
                asset_id = result.scalar_one_or_none()
                if asset_id is None:
                    result = await session.execute(
                        select(MediaAsset.id).where(MediaAsset.asset_key == source.asset_key)
                    )
                    asset_id = result.scalar_one()
        self._schedule(str(asset_id))

    def _schedule(self, asset_id: str) -> None:
        if asset_id in self._tasks:
            return
        task = asyncio.create_task(self._process_asset(asset_id), name=f"media-archive-{asset_id}")
        self._tasks[asset_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(asset_id, None))

    async def _process_asset(self, asset_id: str) -> None:
        for attempt in range(self._retry_max_attempts):
            try:
                await self._mark_attempt(asset_id)
                await self._download_asset(asset_id)
                return
            except ArchiveQuotaExceeded as exc:
                await self._mark_failed(asset_id, "quota_exceeded", blocked=True)
                return
            except Exception as exc:
                if attempt + 1 < self._retry_max_attempts:
                    await asyncio.sleep(min(30, 2**attempt))
                    continue
                await self._mark_failed(asset_id, type(exc).__name__, blocked=False)

    async def _mark_attempt(self, asset_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == asset_id)
                    .values(
                        copy_status="copying",
                        availability_status="copying",
                        attempt_count=MediaAsset.attempt_count + 1,
                        last_attempt_at=datetime.now(UTC),
                    )
                )

    async def _mark_failed(self, asset_id: str, reason: str, blocked: bool) -> None:
        status = "blocked_quota" if blocked else "failed"
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == asset_id)
                    .values(
                        copy_status=status,
                        availability_status=status,
                        last_error=reason,
                        last_attempt_at=datetime.now(UTC),
                    )
                )

    async def _download_asset(self, asset_id: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
            asset = result.scalar_one()
            source_url = asset.source_url
            archive_path = Path(asset.archive_path or "")
            source = asset.source
        if not source_url or not archive_path:
            raise ValueError("media asset has no source or archive path")
        if not archive_path.resolve(strict=False).is_relative_to(self._policy.root.resolve(strict=False)):
            raise ValueError("archive destination escaped archive root")

        if self._client is None:
            raise RuntimeError("media archiver is not started")
        headers = {"Authorization": f"Bearer {self._ha_token}"} if source == "home_assistant" else {}
        # Frigate and HA URLs are stored without query strings so credentials
        # or temporary proxy tokens are never persisted in the database.
        fetch_url = source_url
        mime_type = "application/octet-stream"
        async with self._client.stream("GET", fetch_url, headers=headers) as response:
            response.raise_for_status()
            mime_type = response.headers.get("content-type", mime_type).split(";", 1)[0]
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                _check_capacity(
                    self._policy,
                    int(content_length),
                    shutil.disk_usage(self._policy.root).free,
                )
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            partial = archive_path.with_name(f".{archive_path.name}.{asset_id}.partial")
            digest = hashlib.sha256()
            written = 0
            try:
                with partial.open("xb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        free_bytes = shutil.disk_usage(self._policy.root).free
                        if _directory_size(self._policy.root) + written + len(chunk) > self._policy.max_bytes:
                            raise ArchiveQuotaExceeded("media archive quota would be exceeded")
                        if free_bytes - len(chunk) < self._policy.reserve_free_bytes:
                            raise ArchiveQuotaExceeded("filesystem reserve would be breached")
                        output.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                try:
                    partial.unlink()
                except FileNotFoundError:
                    pass
                raise

        # Reuse an already archived identical object when a state update or
        # retry points at the same bytes. If the logical destination already
        # contains different bytes, choose a content-addressed alternate path
        # and never overwrite either object.
        reusable_path: Path | None = None
        digest_hex = digest.hexdigest()
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaAsset.archive_path).where(
                    MediaAsset.sha256 == digest_hex,
                    MediaAsset.copy_status == "archived",
                    MediaAsset.archive_path.is_not(None),
                )
            )
            for (existing_path,) in result.all():
                candidate = Path(existing_path)
                if (
                    candidate.is_relative_to(self._policy.root.resolve(strict=False))
                    and candidate.is_file()
                ):
                    reusable_path = candidate
                    break

        if reusable_path is None:
            final_path = choose_collision_safe_path(archive_path, digest_hex, asset_id)
            if final_path.exists():
                reusable_path = final_path
            else:
                final_path = final_path.resolve(strict=False)
                if not final_path.is_relative_to(self._policy.root.resolve(strict=False)):
                    raise ValueError("archive destination escaped archive root")
                os.replace(partial, final_path)
        else:
            partial.unlink(missing_ok=True)
            final_path = reusable_path

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == asset_id)
                    .values(
                        archive_path=str(final_path),
                        sha256=digest_hex,
                        size_bytes=written,
                        mime_type=mime_type,
                        copy_status="archived",
                        availability_status="archived",
                        completed_at=datetime.now(UTC),
                        last_error=None,
                    )
                )
