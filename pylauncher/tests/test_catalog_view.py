"""Tests for `CatalogView` (roadmap 4.2): tiles, folder prompts, delegation to the installer."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget

from tests.conftest import process_events
from yulon import runner
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import Installer, InstallOptions
from yulon.ui import catalog_view
from yulon.ui.catalog_view import CatalogView
from yulon.ui.widgets.log_panel import LogPanel


def _completed() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


CATALOG = load_catalog()


class _FakeInstaller(Installer):
    """An `Installer` whose run() is a canned stream; preflight is a no-op."""

    def __init__(self, entry: CatalogEntry, lines: list[str], *, installs: bool = True) -> None:
        super().__init__(entry, docker_check=lambda: True)
        self.lines = lines
        self.installs = installs
        self.ran_with: list[InstallOptions] = []
        self.cancels: list[threading.Event | None] = []
        self.asks: list[object] = []

    def preflight(self, options: InstallOptions) -> None:
        if self.entry.install.requires_client_dir and options.client_dir is None:
            raise AssertionError("view must not run without a client dir")

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: object = None,
    ) -> Iterator[str]:
        opts = options or InstallOptions()
        self.ran_with.append(opts)
        self.cancels.append(cancel)
        self.asks.append(ask)
        yield from self.lines
        # A real install leaves a `docker-compose.yml` in the server dir: it
        # comes out of the clone, and it is the one artefact every install of
        # every game has. `installs=False` models the OTHER way these scripts
        # exit 0 — "Keeping existing install — exiting." — which the view must
        # not read as a finished install.
        if self.installs and opts.server_dir is not None:
            opts.server_dir.mkdir(parents=True, exist_ok=True)
            (opts.server_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


class _CancellableInstaller(Installer):
    """Shaped like the real thing under cancel: it yields, then RETURNS — never raises.

    That is what `runner.interact()` does when its cancel event is set, and it
    is the whole difficulty: from anywhere downstream a stopped install looks
    exactly like a finished one.
    """

    def __init__(self, entry: CatalogEntry) -> None:
        super().__init__(entry, docker_check=lambda: True)
        self.streaming = threading.Event()

    def preflight(self, options: InstallOptions, cancel: threading.Event | None = None) -> None:
        return None

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: object = None,
    ) -> Iterator[str]:
        yield "cloning"
        self.streaming.set()
        while cancel is not None and not cancel.is_set():
            time.sleep(0.005)


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


def test_use_existing_does_not_pin_the_compose_project(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attaching must not write COMPOSE_PROJECT_NAME, and must not shell out at all.

    `pin_project_name()` records whatever compose calls the project *now*, which
    is the folder's current basename. An already-moved install is exactly what
    "Use existing…" exists to adopt, so pinning here writes the one value that
    is wrong — and a pin outranks the basename and is never revised, so the
    server could never be stopped from Yu'lon again. It also made this an
    accidental Docker-dependent test: `docker compose config` really ran, and
    only a missing binary kept the default suite honest (review, 2026-08-22).
    """
    ran: list[list[str]] = []
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: ran.append(cmd) or _completed()  # type: ignore[func-returns-value]
    )
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert view.attach_existing(CATALOG.get("wow-wotlk")) is True
    assert not (tmp_path / ".env").exists(), "attach pinned a project name"
    assert ran == [], f"attach shelled out to {ran}"


def test_use_existing_cancel_emits_nothing(qapp: object) -> None:
    panel = LogPanel()
    view = CatalogView(CATALOG, lambda e: _FakeInstaller(e, []), panel, pick_dir=lambda *_: None)
    got: list[object] = []
    view.installed.connect(lambda *a: got.append(a))
    assert view.attach_existing(CATALOG.get("wow-tbc")) is False
    assert got == []


def test_a_script_that_exits_0_without_installing_is_not_remembered(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0 is not proof of an install, and the scripts have a path that proves it.

    Press Install on a folder a previous attempt left behind: line 961 finds no
    built worldserver image, the existing-folder branch asks "Remove it and
    start fresh? (y/n):", and `PROMPT_RULES` answers "n" because nothing in the
    GUI ever sets `reinstall`. The script prints "Keeping existing install —
    exiting." and exits 0. That used to pin a compose project name into the
    folder and grow a permanent tab for a server that was never built — and the
    pin is inherited by any copy of the folder, so Stop in the copy can stop the
    original's server (review, 2026-08-23).
    """
    from PySide6.QtWidgets import QMessageBox

    ran: list[list[str]] = []
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: ran.append(cmd) or _completed()  # type: ignore[func-returns-value]
    )
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))  # type: ignore[attr-defined]

    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, ["Keeping existing install - exiting."], installs=False),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
        platform_id=lambda: "linux",
    )
    events: list[str] = []
    view.install_finished.connect(lambda g, ok, m: events.append(f"finished:{ok}"))
    view.installed.connect(lambda g, s, c: events.append("installed"))

    assert view.start_install(CATALOG.get("wow-wotlk")) is True
    _wait(panel)

    assert events == ["finished:False"], "an install that never happened was registered"
    assert not (tmp_path / ".env").exists(), "a folder with no install was pinned"
    assert ran == [], f"the pin shelled out to {ran}"
    assert warned and "docker-compose.yml" in warned[0]


def test_a_cancelled_install_is_not_remembered_and_says_what_it_left(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping an install must not register it (roadmap 6.5, "honest cancel copy").

    Driven through this button against the real install script on Ubuntu and
    stopped during the source clone, this path reported `ok=True` with the
    message "done", wrote a `COMPOSE_PROJECT_NAME` pin into the half-cloned
    folder, emitted `installed` — which `main.py` saves into `state.json` and
    turns into a permanent tab — and showed no message at all about the 2.3 GB
    it had left behind (install gate, 2026-08-23). On a run stopped earlier
    still, the folder it registered did not exist.
    """
    from PySide6.QtWidgets import QMessageBox

    ran: list[list[str]] = []
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: ran.append(cmd) or _completed()  # type: ignore[func-returns-value]
    )
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append((a[1], a[2])))  # type: ignore[attr-defined]
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))  # type: ignore[attr-defined]

    panel = LogPanel()
    made: list[_CancellableInstaller] = []

    def factory(entry: CatalogEntry) -> Installer:
        inst = _CancellableInstaller(entry)
        made.append(inst)
        return inst

    view = CatalogView(
        CATALOG,
        factory,
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
        platform_id=lambda: "linux",
    )
    events: list[tuple[str, ...]] = []
    view.install_finished.connect(lambda g, ok, m: events.append(("finished", g, str(ok), m)))
    view.installed.connect(lambda g, s, c: events.append(("installed", g, str(s), str(c))))

    assert view.start_install(CATALOG.get("wow-wotlk")) is True
    deadline = time.monotonic() + 5.0
    while not made[0].streaming.is_set() and time.monotonic() < deadline:
        process_events(10)
    panel.stop()
    _wait(panel)

    assert [event[0] for event in events] == ["finished"], "a cancelled install was registered"
    assert events[0][2] == "False"
    assert not (tmp_path / ".env").exists(), "a cancelled install pinned a compose project"
    assert ran == [], f"a cancelled install shelled out to {ran}"
    assert warned == [], "cancelling is not a failure"
    assert told and told[0][0] == "Install cancelled"
    # The copy has to carry three things, and the folder is the one a user acts on.
    assert str(tmp_path) in told[0][1]
    assert "NOT been remembered as an install" in told[0][1]
    assert "build cache" in told[0][1]
    # And it must not send them back into the bug this test exists for: pressing
    # Install again on a folder with no built images answers "n" to "Remove it
    # and start fresh?" and exits 0, which is a false registration.
    assert "carry on" not in told[0][1], "the cancel copy still promises a resume"
    assert told[0][1] == events[0][3]
    assert panel.status_text() == "cancelled"
    assert view.button_for("wow-wotlk").isEnabled() is True  # and the tiles come back


def test_unsupported_platform_is_said_on_the_tile_and_refused_before_any_prompt(
    qapp: object, monkeypatch: object
) -> None:
    """Roadmap 6.1: no folder dialog, no subprocess — just an honest message.

    Asked of TBC rather than WotLK since 6.2 made WotLK installable on macOS
    through the native engine. The tile gate is unchanged; the entry that
    demonstrates it moved.
    """
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

    assert view.button_for("wow-tbc").isEnabled() is False  # said on the tile
    assert view.start_install(CATALOG.get("wow-tbc")) is False
    assert prompted == []  # never asked where to install it
    assert shown and "cannot be installed on macOS" in shown[0]
    assert finished == [("wow-tbc", False, shown[0])]
    assert panel.running is False
    # And the entry that DID gain a macOS path is offered rather than gated,
    # which is the other half of the same rule.
    assert view.button_for("wow-wotlk").isEnabled() is True


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


def test_the_picker_opens_somewhere_that_exists(tmp_path: Path) -> None:
    """The first install's suggested folder does not exist, and that was a dead end.

    `QFileDialog.getExistingDirectory()` handed a missing path opens its PARENT
    with the missing name typed in, `Choose` disabled, and Enter answering
    "Directory not found." The app suggested `~/wow-server-playerbots` and then
    refused its own suggestion — measured on a clean Arch box, 2026-08-24, where
    it is the first thing a new user meets.

    Asserted on the helper rather than through Qt because what went wrong is the
    PATH handed to the dialog, not the dialog: driving a real modal here would
    test PySide6's behaviour on this machine and hide the argument that caused
    it.
    """
    missing = tmp_path / "wow-server-playerbots"
    assert not missing.exists()
    assert catalog_view._existing_ancestor(missing) == tmp_path

    # Several levels of missing still land on something real.
    assert catalog_view._existing_ancestor(missing / "a" / "b" / "c") == tmp_path

    # An existing directory is returned unchanged - "Use existing..." must still
    # open IN the install, not one above it.
    missing.mkdir()
    assert catalog_view._existing_ancestor(missing) == missing


def test_the_picker_gives_up_rather_than_looping_on_a_root_that_is_not_there() -> None:
    """Walking up has to terminate even when nothing on the way exists.

    `Path('/nonexistent').parent` is `/`, and `Path('/').parent` is `/` again —
    a walk that only checks `is_dir()` spins forever on a machine where the root
    of the given path is not mounted (a stale drive letter on Windows is the
    realistic case). The loop stops when the parent stops changing.
    """
    from pathlib import PureWindowsPath

    weird = Path(PureWindowsPath("Q:/gone/deeper").as_posix())
    got = catalog_view._existing_ancestor(weird)
    assert got is None or got.is_dir()
