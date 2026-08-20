"""Entry point for the Yu'lon launcher (PySide6).

Wires the pieces together and nothing more: logging into `config_dir()`,
the catalog tab (CatalogView over a shared LogPanel) and one ControllerView
tab per remembered install (`state.json`). New installs reported by the
catalog view are remembered and get a tab. Everything else lives in `yulon/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from yulon import platform
from yulon.log import configure, get_logger

logger = get_logger(__name__)


def build_window() -> object:
    """Create the main window (imports Qt lazily so `--help`-style tooling stays cheap)."""
    from PySide6.QtWidgets import QMainWindow, QSplitter, QTabWidget, QWidget

    from yulon import __version__
    from yulon.catalog.catalog import load_catalog
    from yulon.catalog.installer import Installer
    from yulon.state import KnownInstall, load_state, save_state
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.controller_view import ControllerServices, ControllerView
    from yulon.ui.widgets.log_panel import LogPanel

    catalog = load_catalog()
    state = load_state()
    window = QMainWindow()
    window.setWindowTitle(f"Yu'lon — Dad's MMO Lab launcher {__version__}")
    tabs = QTabWidget(window)
    window.setCentralWidget(tabs)

    log_panel = LogPanel()
    catalog_view = CatalogView(catalog, lambda entry: Installer(entry), log_panel)
    splitter = QSplitter()
    splitter.addWidget(catalog_view)
    splitter.addWidget(log_panel)
    tabs.addTab(splitter, "Catalog")

    def add_controller(game: str, server_dir: Path, client_dir: Path | None) -> None:
        entry = catalog.get(game)
        services = ControllerServices.for_wotlk(entry, server_dir, client_dir)
        view = ControllerView(entry, services)
        tabs.addTab(view, f"{entry.name} — {server_dir.name}")

    for install in state.installs:
        try:
            add_controller(install.game, install.server_dir, install.client_dir)
        except KeyError:
            logger.warning(f"state.json names unknown game {install.game!r}; skipping")

    def on_installed(game: str, server_dir: object, client_dir: object) -> None:
        sd = Path(str(server_dir))
        cd = Path(str(client_dir)) if client_dir is not None else None
        state.remember(KnownInstall(game=game, server_dir=sd, client_dir=cd))
        save_state(state)
        add_controller(game, sd, cd)

    catalog_view.installed.connect(on_installed)
    window.resize(1100, 750)
    assert isinstance(window, QWidget)
    return window


def main() -> int:
    """Start the launcher."""
    configure(config_dir=platform.config_dir())
    logger.info("Yu'lon launcher starting")
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    window = build_window()
    assert isinstance(window, QMainWindow)
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
