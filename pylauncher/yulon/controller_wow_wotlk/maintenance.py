"""Cache clearing, backups, and SQL changes for the WotLK server."""

from __future__ import annotations


def clear_cache() -> None:
    """Clear worldserver cache. Placeholder — Phase 4 in pyplan/README.md."""
    raise NotImplementedError


def backup() -> None:
    """Dump databases to a timestamped backup. Placeholder."""
    raise NotImplementedError


def apply_sql(statements: list[str]) -> None:
    """Apply raw SQL against the appropriate database. Placeholder."""
    raise NotImplementedError
