"""Tests for `CatalogView` (roadmap 4.2): tiles, folder prompts, delegation to the installer."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

from PySide6.QtWidgets import QWidget

from tests.conftest import process_events
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import Installer, InstallOptions
from yulon.ui.catalog_view import CatalogView
from yulon.ui.widgets.log_panel import LogPanel

CATALOG = load_catalog()


class _FakeInstaller(Installer):
    """An `Installer` whose run() is a canned stream; preflight is a no-op."""

    def __init__(self, entry: CatalogEntry, lines: list[str]) -> None:
        super().__init__(entry, docker_check=lambda: True)
        self.lines = lines
        self.ran_with: list[InstallOptions] = []
        self.cancels: list[threading.Event | None] = []

    def preflight(self, options: InstallOptions) -> None:
        if self.entry.install.requires_client_dir and options.client_dir is None:
            raise AssertionError("view must not run without a client dir")

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        self.ran_with.append(options or InstallOptions())
        self.cancels.append(cancel)
        yield from self.lines


def _wait(panel: LogPanel, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while panel.running and time.monotonic() < deadline:
        process_events(20)
    panel.wait(1000)
    process_events(50)


def test_one_tile_per_catalog_entry_with_install_button(qapp: object) -> None:
    panel = LogPanel()
    view = CatalogView(CATALOG, lambda e: _FakeInstaller(e, []), panel, pick_dir=lambda *_: None)
    for game in CATALOG.games:
        assert view.button_for(game.id).text() == "Install"


def test_install_asks_for_folders_then_streams_the_installer(qapp: object, tmp_path: Path) -> None:
    panel = LogPanel()
    made: list[_FakeInstaller] = []
    prompts: list[str] = []

    def factory(entry: CatalogEntry) -> Installer:
        inst = _FakeInstaller(entry, ["cloning", "building", "done"])
        made.append(inst)
        return inst

    def pick(parent: QWidget, title: str, start: Path | None) -> Path | None:
        prompts.append(title)
        return tmp_path / ("client" if "client" in title else "server")

    (tmp_path / "client").mkdir()
    view = CatalogView(
        CATALOG,
        factory,
        panel,
        platform_id=lambda: "linux",  # this test is about the folder flow, not gating
        pick_dir=pick,
        home=tmp_path,
    )
    events: list[tuple[str, ...]] = []
    view.install_started.connect(lambda g: events.append(("started", g)))
    view.install_finished.connect(lambda g, ok, m: events.append(("finished", g, str(ok), m)))
    view.installed.connect(lambda g, sd, cd: events.append(("installed", g, str(sd), str(cd))))

    # WotLK: only the server folder is asked for.
    assert view.start_install(CATALOG.get("wow-wotlk")) is True
    assert len(prompts) == 1 and "client" not in prompts[0]
    assert view.button_for("wow-tbc").isEnabled() is False  # buttons locked while running
    _wait(panel)
    assert panel.text().splitlines() == ["cloning", "building", "done"]
    assert made[0].ran_with[0].server_dir == tmp_path / "server"
    assert events[0] == ("started", "wow-wotlk")
    assert events[1] == ("finished", "wow-wotlk", "True", "done")
    assert events[2][:2] == ("installed", "wow-wotlk")
    assert view.button_for("wow-tbc").isEnabled() is True

    # TBC: the client folder is asked for too (README §3a) and passed through.
    prompts.clear()
    assert view.start_install(CATALOG.get("wow-tbc")) is True
    assert len(prompts) == 2 and "client" in prompts[1]
    _wait(panel)
    assert made[1].ran_with[0].client_dir == tmp_path / "client"


def test_cancelling_the_folder_dialog_starts_nothing(qapp: object) -> None:
    panel = LogPanel()
    made: list[Installer] = []

    def factory(entry: CatalogEntry) -> Installer:
        inst = _FakeInstaller(entry, ["x"])
        made.append(inst)
        return inst

    view = CatalogView(CATALOG, factory, panel, pick_dir=lambda *_: None)
    assert view.start_install(CATALOG.get("wow-wotlk")) is False
    assert made == [] and panel.running is False


def test_use_existing_registers_a_folder_that_holds_an_install(
    qapp: object, tmp_path: Path, monkeypatch: object
) -> None:
    """Installs made by a script or the CLI harness get a controller through 'Use existing…'."""
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
    )
    assert view.existing_button_for("wow-wotlk").text() == "Use existing…"
    got: list[tuple[str, object, object]] = []
    view.installed.connect(lambda g, s, c: got.append((g, s, c)))
    # No compose file → refused (the warning dialog is patched out) and nothing emitted.
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))  # type: ignore[attr-defined]
    assert view.attach_existing(CATALOG.get("wow-wotlk")) is False
    assert got == [] and "docker-compose.yml" in warned[0]
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert view.attach_existing(CATALOG.get("wow-wotlk")) is True
    assert got == [("wow-wotlk", tmp_path, None)]


def test_use_existing_cancel_emits_nothing(qapp: object) -> None:
    panel = LogPanel()
    view = CatalogView(CATALOG, lambda e: _FakeInstaller(e, []), panel, pick_dir=lambda *_: None)
    got: list[object] = []
    view.installed.connect(lambda *a: got.append(a))
    assert view.attach_existing(CATALOG.get("wow-tbc")) is False
    assert got == []


def test_unsupported_platform_is_said_on_the_tile_and_refused_before_any_prompt(
    qapp: object, monkeypatch: object
) -> None:
    """Roadmap 6.1: no folder dialog, no subprocess — just an honest message."""
    from PySide6.QtWidgets import QMessageBox

    panel = LogPanel()
    prompted: list[str] = []

    def pick(_parent: object, title: str, _start: object) -> None:
        prompted.append(title)
        return None

    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, ["x"]),
        panel,
        pick_dir=pick,  # type: ignore[arg-type]
        platform_id=lambda: "macos",
    )
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))  # type: ignore[attr-defined]
    finished: list[tuple[str, bool, str]] = []
    view.install_finished.connect(lambda g, ok, m: finished.append((g, ok, m)))

    assert view.button_for("wow-wotlk").isEnabled() is False  # said on the tile
    assert view.start_install(CATALOG.get("wow-wotlk")) is False
    assert prompted == []  # never asked where to install it
    assert shown and "cannot be installed on macOS" in shown[0]
    assert finished == [("wow-wotlk", False, shown[0])]
    assert panel.running is False


def test_supported_platform_keeps_the_install_button(qapp: object) -> None:
    view = CatalogView(
        CATALOG, lambda e: _FakeInstaller(e, []), LogPanel(), platform_id=lambda: "linux"
    )
    assert view.button_for("wow-wotlk").isEnabled() is True


def test_unlocking_after_a_job_never_re_enables_a_gated_tile(qapp: object, tmp_path: Path) -> None:
    """The 6.1 gate must survive `_set_buttons_enabled(True)` (review finding 1.1).

    Latent while every entry is Linux-only; armed the moment 6.2 widens WotLK
    and leaves TBC/Vanilla/Tortoise behind — a mixed catalog, which is exactly
    what this builds.
    """
    from yulon.catalog.catalog import Catalog

    wotlk = CATALOG.get("wow-wotlk")
    widened = wotlk.model_copy(
        update={"install": wotlk.install.model_copy(update={"platforms": ("linux", "macos")})}
    )
    mixed = Catalog(games=(widened, CATALOG.get("wow-tbc")))

    panel = LogPanel()
    view = CatalogView(
        mixed,
        lambda e: _FakeInstaller(e, ["line"]),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
        platform_id=lambda: "macos",
    )
    assert view.button_for("wow-wotlk").isEnabled() is True  # widened
    assert view.button_for("wow-tbc").isEnabled() is False  # still Linux-only

    assert view.start_install(widened) is True
    _wait(panel)
    process_events(50)

    assert view.button_for("wow-wotlk").isEnabled() is True  # unlocked after the job
    assert view.button_for("wow-tbc").isEnabled() is False  # STILL gated
    assert view.existing_button_for("wow-tbc").isEnabled() is True  # never gated
