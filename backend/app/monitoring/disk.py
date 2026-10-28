from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiskStatus:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    free_ratio: float
    database_bytes: int
    database_max_bytes: int
    level: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "free_ratio": round(self.free_ratio, 4),
            "database_bytes": self.database_bytes,
            "database_max_bytes": self.database_max_bytes,
            "level": self.level,
            "reasons": list(self.reasons),
        }


class DiskMonitor:
    def __init__(
        self,
        path: str | os.PathLike[str],
        max_database_bytes: int,
        warning_free_bytes: int,
        critical_free_bytes: int,
        warning_free_ratio: float,
        critical_free_ratio: float,
    ) -> None:
        self.path = Path(path)
        self.max_database_bytes = max_database_bytes
        self.warning_free_bytes = warning_free_bytes
        self.critical_free_bytes = critical_free_bytes
        self.warning_free_ratio = warning_free_ratio
        self.critical_free_ratio = critical_free_ratio

    def directory_size(self) -> int:
        if not self.path.exists():
            return 0
        total = 0
        for root, directories, files in os.walk(self.path, followlinks=False):
            directories[:] = [directory for directory in directories if not (Path(root) / directory).is_symlink()]
            for filename in files:
                candidate = Path(root) / filename
                try:
                    if not candidate.is_symlink():
                        total += candidate.stat().st_size
                except OSError:
                    continue
        return total

    def status(self) -> DiskStatus:
        usage_path = self.path if self.path.exists() else self.path.parent
        usage = shutil.disk_usage(usage_path)
        free_ratio = usage.free / usage.total if usage.total else 0.0
        database_bytes = self.directory_size()
        reasons: list[str] = []
        level = "healthy"

        if usage.free <= self.critical_free_bytes or free_ratio <= self.critical_free_ratio:
            level = "critical"
            reasons.append("filesystem_free_space_critical")
        elif usage.free <= self.warning_free_bytes or free_ratio <= self.warning_free_ratio:
            level = "warning"
            reasons.append("filesystem_free_space_warning")

        if database_bytes >= self.max_database_bytes:
            level = "critical"
            reasons.append("database_max_bytes_reached")
        elif database_bytes >= int(self.max_database_bytes * 0.8):
            if level == "healthy":
                level = "warning"
            reasons.append("database_size_warning")

        return DiskStatus(
            path=str(self.path),
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            free_ratio=free_ratio,
            database_bytes=database_bytes,
            database_max_bytes=self.max_database_bytes,
            level=level,
            reasons=tuple(reasons),
        )
