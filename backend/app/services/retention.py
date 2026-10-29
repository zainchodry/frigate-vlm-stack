from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select, update

from app.models import MediaAsset


@dataclass(frozen=True)
class RetentionCandidate:
    asset_id: str
    archive_path: Path
    copy_status: str
    retention_until: datetime | None


def plan_expired_assets(
    candidates: Iterable[RetentionCandidate],
    *,
    now: datetime | None = None,
    archive_root: Path | None = None,
) -> list[RetentionCandidate]:
    """Return only completed, expired assets within the controlled archive.

    Planning is deliberately separate from deletion. A caller must explicitly
    opt into applying the plan, which prevents a storage emergency from
    turning into an uncontrolled bulk deletion.
    """
    cutoff = now or datetime.now(UTC)
    root = archive_root.resolve(strict=False) if archive_root else None
    result: list[RetentionCandidate] = []
    for candidate in candidates:
        if candidate.copy_status not in {"archived", "expiring"}:
            continue
        if candidate.retention_until is None or candidate.retention_until > cutoff:
            continue
        path = candidate.archive_path.resolve(strict=False)
        if root is not None and not path.is_relative_to(root):
            continue
        result.append(candidate)
    return result


async def expire_archived_assets(
    session_factory: Any,
    archive_root: Path,
    *,
    now: datetime | None = None,
    apply: bool = False,
) -> dict[str, int]:
    """Plan or apply archive expiry while preserving metadata rows."""
    cutoff = now or datetime.now(UTC)
    async with session_factory() as session:
        result = await session.execute(
            select(MediaAsset).where(
                MediaAsset.copy_status.in_(("archived", "expiring")),
                MediaAsset.retention_until.is_not(None),
                MediaAsset.retention_until <= cutoff,
            )
        )
        rows = list(result.scalars().all())

    candidates = plan_expired_assets(
        (
            RetentionCandidate(
                asset_id=str(row.id),
                archive_path=Path(row.archive_path or ""),
                copy_status=row.copy_status,
                retention_until=row.retention_until,
            )
            for row in rows
        ),
        now=cutoff,
        archive_root=archive_root,
    )
    summary = {"planned": len(candidates), "expired": 0, "missing": 0, "unsafe": len(rows) - len(candidates)}
    if not apply:
        return summary

    root = archive_root.resolve(strict=False)
    for candidate in candidates:
        path = candidate.archive_path.resolve(strict=False)
        if path.is_symlink() or not path.is_relative_to(root):
            summary["unsafe"] += 1
            continue
        if not path.is_file():
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        update(MediaAsset)
                        .where(MediaAsset.id == candidate.asset_id)
                        .values(
                            copy_status="missing",
                            availability_status="missing",
                            last_error="archive_file_missing",
                        )
                    )
            summary["missing"] += 1
            continue

        # Move the row out of ``archived`` before unlinking. If the process
        # stops after this transaction, reconciliation can safely inspect an
        # ``expiring`` row; it will never leave metadata claiming an archived
        # file exists after the file has been removed.
        if candidate.copy_status == "archived":
            async with session_factory() as session:
                async with session.begin():
                    result = await session.execute(
                        update(MediaAsset)
                        .where(
                            MediaAsset.id == candidate.asset_id,
                            MediaAsset.copy_status == "archived",
                        )
                        .values(copy_status="expiring", availability_status="expiring")
                    )
                    if result.rowcount != 1:
                        continue

        try:
            path.unlink()
        except OSError as exc:
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        update(MediaAsset)
                        .where(MediaAsset.id == candidate.asset_id)
                        .values(
                            copy_status="failed",
                            availability_status="failed",
                            last_error=f"retention_delete_failed:{type(exc).__name__}",
                        )
                    )
            continue

        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == candidate.asset_id)
                    .values(
                        copy_status="expired",
                        availability_status="expired",
                        last_error="retention_expired",
                        completed_at=None,
                    )
                )
        summary["expired"] += 1
    return summary
