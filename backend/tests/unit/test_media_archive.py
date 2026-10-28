from pathlib import Path

import pytest

from app.services.media_archive import (
    ArchiveQuotaExceeded,
    MediaArchivePolicy,
    build_archive_path,
    copy_bytes_atomically,
    is_protected_source,
    choose_collision_safe_path,
)


def test_protected_training_sources_are_rejected():
    protected = Path("/srv/frigate/storage/clips/faces/Zain/person.png")
    regular = Path("/srv/frigate/storage/clips/recordings/2026-10-28/event.mp4")

    assert is_protected_source(protected, [Path("/srv/frigate/storage/clips/faces/Zain")]) is True
    assert is_protected_source(regular, [Path("/srv/frigate/storage/clips/faces/Zain")]) is False


def test_archive_path_is_deterministic_and_contained(tmp_path):
    policy = MediaArchivePolicy(root=tmp_path, max_bytes=10_000, reserve_free_bytes=1_000)

    path = build_archive_path(policy, "frigate", "event-123", "snapshot", ".jpg")

    assert path == tmp_path / "frigate" / "event-123" / "snapshot.jpg"
    assert path.is_relative_to(tmp_path)


def test_quota_fails_closed_before_copy(tmp_path):
    policy = MediaArchivePolicy(root=tmp_path, max_bytes=100, reserve_free_bytes=1_000)

    with pytest.raises(ArchiveQuotaExceeded):
        copy_bytes_atomically(policy, "asset-1", b"x" * 101, free_bytes=2_000)


def test_copy_is_atomic_and_records_checksum(tmp_path):
    policy = MediaArchivePolicy(root=tmp_path, max_bytes=10_000, reserve_free_bytes=1_000)

    result = copy_bytes_atomically(policy, "asset-1", b"snapshot-data", free_bytes=2_000)

    assert result.size_bytes == len(b"snapshot-data")
    assert result.sha256 == "d8fca1ea2aa01f9096115854cfda897797b895a3ffce34051457652f8c7147ee"
    assert result.path.read_bytes() == b"snapshot-data"
    assert not list(tmp_path.rglob("*.partial"))


def test_existing_archive_path_is_reused_for_same_content(tmp_path):
    destination = tmp_path / "entity" / "image.jpg"
    destination.parent.mkdir()
    destination.write_bytes(b"same-image")

    path = choose_collision_safe_path(
        destination,
        "fcc6824d4f99b1b5b6011e00c9b3db91555e6d2d8aab66693bc3a324c437bc6c",
        "asset-1",
    )

    assert path == destination


def test_existing_archive_path_gets_content_addressed_alternate(tmp_path):
    destination = tmp_path / "entity" / "image.jpg"
    destination.parent.mkdir()
    destination.write_bytes(b"old-image")

    path = choose_collision_safe_path(
        destination,
        "e8f4c5b4c9f5b0a94f3f1f9db3f3bde76b2e18d0e3b3b8a2f3e08c0be0fca2be",
        "asset-1",
    )

    assert path == destination.with_name("image-e8f4c5b4c9f5b0a9.jpg")
