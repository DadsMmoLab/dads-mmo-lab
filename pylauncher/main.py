"""Entry point for the Yu'lon launcher (PySide6)."""

from __future__ import annotations

from yulon.log import configure, get_logger

# NOTE: `configure()` is called with no `config_dir` here, so logging is
# stderr-only for now. `yulon.platform.config_dir()` (Phase 1) is not yet
# implemented; once it is, call `configure(config_dir=platform.config_dir())`
# here to enable the rotating file log described in README §11.


def main() -> None:
    """Start the launcher. Placeholder — Phase 4 in pyplan/README.md."""
    configure()
    logger = get_logger(__name__)
    logger.info("Yu'lon launcher starting (Phase 4 UI not yet implemented)")
    raise NotImplementedError


if __name__ == "__main__":
    main()
