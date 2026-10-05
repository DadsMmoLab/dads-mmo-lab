"""Does this machine's temp disk tell `a` from `A`? Asked of the disk, never of the platform (T227).

Windows and macOS disks usually ignore case, but not always (a case-sensitive
APFS volume, a WSL folder with case sensitivity switched on), and Linux disks
usually honour it, but not always (a mounted FAT stick). So the answer is a
probe: make a file and look for it under the other case.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def _probe() -> bool:
    with tempfile.TemporaryDirectory(prefix="yulon-case-") as folder:
        (Path(folder) / "Probe").write_bytes(b"")
        return not (Path(folder) / "probe").exists()


CASE_SENSITIVE_DISK = _probe()
"""True when two names that differ only in case are two files on the temp disk."""

needs_case_sensitive_disk = pytest.mark.skipif(
    not CASE_SENSITIVE_DISK,
    reason="needs a disk that tells names apart by case; this temp disk does not",
)
