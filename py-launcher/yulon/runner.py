"""Shared subprocess streaming wrapper.

Provides live line-by-line stdout/stderr streaming so the UI can display
install/build/container output without buffering.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator


def stream(command: list[str], cwd: str | None = None) -> Iterator[str]:
    """Run a command, yielding each output line as it arrives."""
    raise NotImplementedError


def run(command: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a command to completion and return the completed process."""
    raise NotImplementedError