from pathlib import Path
from collections import namedtuple


DiskUsage = namedtuple("DiskUsage", "total used free")

from app.monitoring.disk import DiskMonitor


def test_directory_size_is_calculated_without_following_external_links(tmp_path: Path):
    (tmp_path / "one.bin").write_bytes(b"1234")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "two.bin").write_bytes(b"567890")

    monitor = DiskMonitor(
        path=tmp_path,
        max_database_bytes=1024,
        warning_free_bytes=1,
        critical_free_bytes=0,
        warning_free_ratio=0.01,
        critical_free_ratio=0.0,
    )

    assert monitor.directory_size() == 10


def test_disk_status_marks_database_limit_as_critical(monkeypatch, tmp_path: Path):
    monitor = DiskMonitor(
        path=tmp_path,
        max_database_bytes=10,
        warning_free_bytes=1,
        critical_free_bytes=0,
        warning_free_ratio=0.01,
        critical_free_ratio=0.0,
    )
    monkeypatch.setattr(monitor, "directory_size", lambda: 11)
    monkeypatch.setattr(
        "app.monitoring.disk.shutil.disk_usage",
        lambda path: DiskUsage(100, 90, 10),
    )

    status = monitor.status()

    assert status.level == "critical"
    assert "database_max_bytes_reached" in status.reasons
