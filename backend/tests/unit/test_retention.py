from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.retention import RetentionCandidate, plan_expired_assets


def test_retention_plan_only_selects_archived_assets_past_deadline(tmp_path: Path):
    now = datetime.now(UTC)
    candidates = plan_expired_assets(
        [
            RetentionCandidate("expired", tmp_path / "expired.jpg", "archived", now - timedelta(seconds=1)),
            RetentionCandidate("resumable", tmp_path / "resumable.jpg", "expiring", now - timedelta(seconds=1)),
            RetentionCandidate("future", tmp_path / "future.jpg", "archived", now + timedelta(days=1)),
            RetentionCandidate("failed", tmp_path / "failed.jpg", "failed", now - timedelta(days=1)),
        ],
        now=now,
    )

    assert [candidate.asset_id for candidate in candidates] == ["expired", "resumable"]


def test_retention_plan_never_allows_paths_outside_archive_root(tmp_path: Path):
    outside = tmp_path.parent / "outside.mp4"
    candidates = plan_expired_assets(
        [RetentionCandidate("outside", outside, "archived", datetime.now(UTC) - timedelta(days=1))],
        now=datetime.now(UTC),
        archive_root=tmp_path,
    )

    assert candidates == []
