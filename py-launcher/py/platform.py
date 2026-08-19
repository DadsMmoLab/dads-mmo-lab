"""OS detection + silent Docker/WSL2 provisioning.

Platform-specific "ensure a Linux container environment exists" logic lives
here while keeping the rest of the app 100% shared. See pyplan/README.md §3.
"""

from __future__ import annotations


def detect() -> str:
    """Return a normalized platform identifier."""
    raise NotImplementedError


def ensure_docker() -> None:
    """Ensure Docker is installed and running (silent provisioning)."""
    raise NotImplementedError


def ensure_wsl2() -> None:
    """Ensure WSL2 + Docker Desktop on Windows. Placeholder — Phase 5."""
    raise NotImplementedError