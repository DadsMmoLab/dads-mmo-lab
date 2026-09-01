"""Tests for `CatalogView` (roadmap 4.2): tiles, folder prompts, delegation to the installer."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton, QScrollArea, QSplitter, QWidget

from main import DEFAULT_WINDOW_SIZE
from tests.conftest import process_events
from yulon import runner, wsl
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import Installer, InstallOptions
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import CloneSpec, RunnerGit
from yulon.ui import catalog_view
from yulon.ui.catalog_view import CatalogView, Identification
from yulon.ui.widgets.log_panel import LogPanel


def _completed() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


CATALOG = load_catalog()


class _FakeInstaller(Installer):
    """An `Installer` whose run() is a canned stream; preflight is a no-op."""

    def __init__(
        self,
        entry: CatalogEntry,
        lines: list[str],
        *,
        installs: bool = True,
        compose_name: str = "docker-compose.yml",
    ) -> None:
        super().__init__(entry, docker_check=lambda: True)
        self.lines = lines
        self.installs = installs
        self.compose_name = compose_name
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
        # A real install leaves a compose file in the server dir, and that is the
        # one artefact every install of every game has — but NOT always under the
        # same name, which is what `compose_name` exists to model: the WotLK and
        # Tortoise scripts write `docker-compose.yml`, the TBC and Vanilla ones
        # write `compose.yml`. `installs=False` models the OTHER way these
        # scripts exit 0 — "Keeping existing install — exiting." — which the view
        # must not read as a finished install.
        if self.installs and opts.server_dir is not None:
            opts.server_dir.mkdir(parents=True, exist_ok=True)
            (opts.server_dir / self.compose_name).write_text("services: {}\n", encoding="utf-8")


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


@pytest.mark.parametrize("compose_name", ["compose.yml", "compose.yaml", "docker-compose.yaml"])
def test_use_existing_accepts_every_name_compose_itself_accepts(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compose_name: str
) -> None:
    """A server the app cannot see is a server the app cannot manage.

    The TBC and Vanilla installers write `compose.yml`; only WotLK and Tortoise
    write `docker-compose.yml`. The view checked for the latter alone, so a TBC
    install could finish a multi-hour compile and "Use existing…" would still
    say there was nothing there — measured 2026-08-25. Docker Compose has
    accepted all four spellings for years and picks whichever it finds, so the
    app has no business being stricter than the tool it drives.
    """
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: _completed()  # type: ignore[arg-type]
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)  # type: ignore[attr-defined]
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
    )
    got: list[tuple[str, object, object]] = []
    view.installed.connect(lambda g, s, c: got.append((g, s, c)))
    (tmp_path / compose_name).write_text("services: {}\n", encoding="utf-8")
    assert view.attach_existing(CATALOG.get("wow-wotlk")) is True
    assert got == [("wow-wotlk", tmp_path, None)]


def test_an_install_that_wrote_compose_yml_is_remembered(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same blindness on the other path, and this one costs a whole install.

    `start_install()` refuses to remember a folder with no compose file, which
    is right — exit 0 is not proof of an install. But it asked only for
    `docker-compose.yml`, so a successful TBC or Vanilla install was discarded
    at the finish line and the user was told there was nothing installed.
    """
    ran: list[list[str]] = []
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: ran.append(cmd) or _completed()  # type: ignore[func-returns-value]
    )
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, ["done"], compose_name="compose.yml"),
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
    assert "installed" in events, f"a finished install was discarded: {events}"
    assert "finished:True" in events


def test_use_existing_refuses_a_folder_docker_cannot_mount(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder rule was wired to Install and not to the button users press.

    Reported by a tester on 2026-08-26: a WotLK server installed by the DML
    Launcher lives INSIDE a WSL distro, at `\\\\wsl.localhost\\dml-arch\\...`. They
    could reach it by pasting the path into the picker's address bar, and
    "Use existing..." accepted it - because this path only ever checked for a
    compose file. `start_install()` refuses such a folder through
    `server_dir_problem()`; attaching one did not, so the failure would arrive
    later as a container that mounted nothing.

    Measured on Windows 11 with Docker running: `docker run -v
    \\\\wsl.localhost\\...:/probe` fails with "is not a valid Windows path".
    """
    from PySide6.QtWidgets import QMessageBox

    ran: list[list[str]] = []
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: ran.append(cmd) or _completed()  # type: ignore[func-returns-value]
    )
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))  # type: ignore[attr-defined]

    # A real compose file, so the ONLY thing that can refuse it is the folder rule.
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
        dir_problem=lambda _p: "that folder is inside WSL and Docker cannot mount it",
    )
    got: list[object] = []
    view.installed.connect(lambda *a: got.append(a))

    assert view.attach_existing(CATALOG.get("wow-wotlk")) is False
    assert got == [], "a folder Docker cannot mount was attached anyway"
    assert warned and "Docker cannot mount" in warned[0]
    assert ran == [], f"it shelled out before refusing: {ran}"


def test_use_existing_still_accepts_a_folder_with_no_problem(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not refuse the ordinary case it was added around."""
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None, timeout=None: _completed()  # type: ignore[arg-type]
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
        dir_problem=lambda _p: None,
    )
    got: list[object] = []
    view.installed.connect(lambda *a: got.append(a))
    assert view.attach_existing(CATALOG.get("wow-wotlk")) is True
    assert len(got) == 1


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


# ------------------------------------------------- adopting a WSL-resident server

_WSL_SERVER = wsl.FoundServer(
    distro="dml-arch",
    project="wow-server-playerbots",
    running=True,
    server_dir=Path(r"\\wsl.localhost\dml-arch\home\dml\games\wow-server-playerbots"),
)


def test_adopting_a_wsl_server_remembers_the_distro_it_lives_in(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distro is the whole point: without it every later command asks the wrong docker.

    Yu'lon is replacing the DML Launcher, so a server already built inside a
    distro has to be adoptable - asking a user to repeat a multi-hour compile is
    not a migration path.
    """
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (_WSL_SERVER,))
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_wsl_server=lambda _found: _WSL_SERVER,
    )
    got: list[tuple[object, ...]] = []
    view.adopted.connect(lambda *a: got.append(a))

    assert view.adopt_from_wsl(CATALOG.get("wow-wotlk")) is True
    assert got == [("wow-wotlk", _WSL_SERVER.server_dir, None, "dml-arch")]


def test_adopting_is_declined_without_complaint(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the picker is an answer, not an error."""
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (_WSL_SERVER,))
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_wsl_server=lambda _found: None,
    )
    got: list[object] = []
    view.adopted.connect(lambda *a: got.append(a))
    assert view.adopt_from_wsl(CATALOG.get("wow-wotlk")) is False
    assert got == []


def test_finding_nothing_says_so_rather_than_opening_an_empty_picker(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty list is a message, not a dialog with nothing in it."""
    from PySide6.QtWidgets import QMessageBox

    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))  # type: ignore[attr-defined]
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): ())
    opened: list[object] = []
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_wsl_server=lambda found: opened.append(found),  # type: ignore[func-returns-value,arg-type]
    )
    assert view.adopt_from_wsl(CATALOG.get("wow-wotlk")) is False
    assert opened == [], "an empty picker was opened"
    assert told and "WSL" in told[0]


def test_a_client_folder_is_still_asked_for_when_the_game_needs_one(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adopting changes where the SERVER is, not whether a client is required.

    TBC asks for one; the folder lives on the Windows side either way, because
    it is the user's own WoW install and nothing about it moved into a distro.
    """
    tbc_server = wsl.FoundServer(
        distro="dml-arch",
        project="wow-tbc-server",
        running=False,
        server_dir=Path(r"\\wsl.localhost\dml-arch\home\dml\tbc"),
    )
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (tbc_server,))
    client = tmp_path / "client"
    client.mkdir()
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_dir=lambda *_: client,
        pick_wsl_server=lambda _f: tbc_server,
    )
    got: list[tuple[object, ...]] = []
    view.adopted.connect(lambda *a: got.append(a))
    assert view.adopt_from_wsl(CATALOG.get("wow-tbc")) is True
    assert got == [("wow-tbc", tbc_server.server_dir, client, "dml-arch")]


def test_an_adoption_the_user_cancels_is_never_announced_as_unverified(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cancelling the client picker means nothing was adopted, so nothing may say it was.

    The `UNVERIFIED` warning and its log line used to sit ABOVE the client-folder
    prompt. A user who dismissed that picker got `adopt_from_wsl()` returning False
    and no adoption at all - after being told the server was being adopted, and
    with the log recording an unverified adoption that never happened.

    Only the three `requires_client_dir` entries could reach that ordering, and
    none of them carries `has_manifests`, so no file was ever at risk. The record
    was simply false, and a false record of a security-relevant decision is worth
    a test on its own.

    This asserts the ORDER by its consequence rather than by reading the source:
    the folder is unidentifiable (no compose file at all, so `_identify()` answers
    `UNVERIFIED`) and the picker refuses, so the only way to reach the warning is
    to emit it before asking. Moving the block back above the prompt fails here.
    """
    server = wsl.FoundServer(
        distro="dml-arch",
        project="wow-tbc-server",
        running=False,
        server_dir=tmp_path / "unidentifiable",
    )
    server.server_dir.mkdir()
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (server,))
    assert catalog_view.compose_file(server.server_dir) is None, (
        "fixture must be UNVERIFIED: a readable compose file here would make this "
        "test pass through the MATCHES/DIFFERENT branch instead"
    )
    entry = CATALOG.get("wow-tbc")
    assert entry.install.requires_client_dir, (
        "fixture must reach the client-dir prompt; an entry that skips it cannot "
        "exercise the ordering this test exists for"
    )

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        catalog_view.QMessageBox,
        "warning",
        lambda _parent, title, text, *a, **k: shown.append((title, text)),
    )
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_dir=lambda *_: None,  # the user cancels
        pick_wsl_server=lambda _f: server,
    )
    got: list[tuple[object, ...]] = []
    view.adopted.connect(lambda *a: got.append(a))

    with caplog.at_level("WARNING"):
        assert view.adopt_from_wsl(entry) is False

    assert got == [], "nothing was adopted, so nothing may have been emitted"
    assert shown == [], f"the user was told about an adoption that did not happen: {shown}"
    assert not [
        r for r in caplog.records if "UNVERIFIED" in r.getMessage()
    ], "the log recorded an unverified adoption that never happened"


def test_the_wsl_adopt_button_exists_where_a_distro_could_hold_a_server(
    qapp: object, tmp_path: Path
) -> None:
    """A feature with no button is a feature nobody has.

    `adopt_from_wsl()` shipped with its signal, its persistence and its tests,
    and nothing in the running app could reach any of it - the method had no
    caller at all. Asserted on the widget tree rather than on the method, since
    that is the difference the user experiences.
    """
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        wsl_distros=lambda: ("dml-arch",),
    )
    button = view.findChild(QPushButton, "adopt-wsl-wow-wotlk")
    assert button is not None, "no way to reach adopt_from_wsl from the UI"
    assert button.isEnabled()


def test_no_wsl_adopt_button_where_there_are_no_distros(qapp: object, tmp_path: Path) -> None:
    """On Linux, macOS, and a Windows box with no WSL, the offer cannot be honoured.

    `wsl_distros()` answers () for all three, so one check covers every case
    rather than a platform test that would have to be kept in step.
    """
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        wsl_distros=lambda: (),
    )
    assert view.findChild(QPushButton, "adopt-wsl-wow-wotlk") is None


def test_the_adopt_button_actually_calls_adopt_from_wsl(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wired, not merely present - a button connected to nothing looks identical."""
    called: list[str] = []
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        wsl_distros=lambda: ("dml-arch",),
    )
    monkeypatch.setattr(
        view, "adopt_from_wsl", lambda entry: called.append(entry.id) or True  # type: ignore[func-returns-value]
    )
    button = view.findChild(QPushButton, "adopt-wsl-wow-wotlk")
    assert button is not None
    button.click()
    assert called == ["wow-wotlk"]


def _wsl_server_at(tmp_path: Path, compose: str) -> wsl.FoundServer:
    """A FoundServer whose folder really exists, so the check can read it."""
    (tmp_path / "docker-compose.yml").write_text(compose, encoding="utf-8")
    return wsl.FoundServer(
        distro="dml-arch", project="some-server", running=True, server_dir=tmp_path
    )


def test_adopting_refuses_a_project_that_is_a_different_game(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery finds compose projects, not WoW servers, and cannot tell them apart.

    `docker compose ls` reports every project in the distro - a TBC server, a
    Nextcloud, someone's blog. Adopting one under the wrong catalog entry builds
    a tab whose every button names containers that do not exist, and each one
    fails separately and confusingly rather than once and clearly.

    The catalog's container names are the signal; the project name is just a
    folder name and proves nothing.
    """
    from PySide6.QtWidgets import QMessageBox

    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: told.append(a[2]))  # type: ignore[attr-defined]
    other_game = _wsl_server_at(tmp_path, "services:\n  db:\n    container_name: mangos-db\n")
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (other_game,))

    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_wsl_server=lambda _f: other_game,
    )
    got: list[object] = []
    view.adopted.connect(lambda *a: got.append(a))

    assert view.adopt_from_wsl(CATALOG.get("wow-wotlk")) is False
    assert got == [], "a different game was adopted as WotLK"
    assert told and "WoW WotLK" in told[0]


def test_adopting_accepts_a_project_whose_containers_match(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the check must not refuse the server it was written to accept."""
    spec = CATALOG.get("wow-wotlk").container_spec()
    ours = _wsl_server_at(tmp_path, f"services:\n  db:\n    container_name: {spec.db}\n")
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (ours,))

    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_wsl_server=lambda _f: ours,
    )
    got: list[object] = []
    view.adopted.connect(lambda *a: got.append(a))
    assert view.adopt_from_wsl(CATALOG.get("wow-wotlk")) is True
    assert len(got) == 1


def test_adopting_continues_when_there_is_no_compose_file_to_read(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder with no compose file in it is not evidence of the wrong game.

    The folder lives inside a distro and is reached over a UNC path; that read
    can fail for reasons that have nothing to do with which game it is. Refusing
    on "I could not check" would block the migration this feature exists to
    provide, so the check only fires on a file it actually read.

    Renamed 2026-09-02 from `test_adopting_allows_a_compose_file_it_cannot_read`:
    `tmp_path / "gone"` does not exist, so `compose_file()` answers None and this
    never reached the `OSError` arm it was named for. The read that raises is
    covered by
    `test_identify_is_unverified_when_the_compose_file_is_there_but_unreadable`.
    """
    unreadable = wsl.FoundServer(
        distro="dml-arch",
        project="wow",
        running=True,
        server_dir=tmp_path / "gone",
    )
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (unreadable,))
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        panel,
        home=tmp_path,
        pick_wsl_server=lambda _f: unreadable,
    )
    got: list[object] = []
    view.adopted.connect(lambda *a: got.append(a))
    assert view.adopt_from_wsl(CATALOG.get("wow-wotlk")) is True
    assert len(got) == 1


# ------------------------------------- three answers, and what each one costs
#
# `_looks_like()` answered "is this folder this game?" with a bool, so "I could
# not check" left the function spelled exactly like "yes, it is". `_identify()`
# widened that to three members. The tests below hold both halves of the
# widening down: that each member is PRODUCED by the folder that deserves it,
# and that each member gets its own CONSEQUENCE at the call site.


def _folder(tmp_path: Path, name: str, compose: str | None) -> Path:
    """A real directory, with a `docker-compose.yml` in it only when `compose` is given."""
    folder = tmp_path / name
    folder.mkdir()
    if compose is not None:
        (folder / "docker-compose.yml").write_text(compose, encoding="utf-8")
    return folder


def _server_in(folder: Path, project: str = "some-server") -> wsl.FoundServer:
    return wsl.FoundServer(distro="dml-arch", project=project, running=True, server_dir=folder)


def _compose_naming(container: str) -> str:
    return f"services:\n  db:\n    container_name: {container}\n"


def test_identify_produces_every_answer_it_declares(qapp: object, tmp_path: Path) -> None:
    """One folder per member, and no member left without a folder that reaches it.

    Enumerated over `Identification` rather than written out as three asserts:
    a fourth member added later fails here until something produces it, which is
    the whole point of having named the third one.

    Each fixture differs from the next in exactly one thing. `ours` and `theirs`
    are both readable compose files and differ ONLY in the container name, so
    `DIFFERENT` cannot be passing because the file was missing; `silent` is a
    directory that exists and simply holds no compose file, so `UNVERIFIED`
    cannot be passing because the name failed to match.
    """
    entry = CATALOG.get("wow-wotlk")
    spec = entry.container_spec()
    folders = {
        Identification.MATCHES: _folder(tmp_path, "ours", _compose_naming(spec.db)),
        Identification.DIFFERENT: _folder(tmp_path, "theirs", _compose_naming("mangos-db")),
        Identification.UNVERIFIED: _folder(tmp_path, "silent", None),
    }
    assert set(folders) == set(Identification), "an Identification member no fixture reaches"
    assert {want: catalog_view._identify(entry, folder) for want, folder in folders.items()} == {
        want: want for want in folders
    }


def test_identify_is_unverified_when_the_compose_file_is_there_but_unreadable(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `OSError` arm, reached by denying a read of a file that IS there.

    The compose file names ANOTHER game's container, so a read that quietly
    succeeded would answer `DIFFERENT` rather than `UNVERIFIED`. That is what this
    fixture pins, and it is the whole of what it pins.

    WITHDRAWN, because it was false: "a read that was skipped would answer
    `MATCHES`". A skipped read answers `UNVERIFIED` too, through the missing-file
    arm one line above - the SAME member - so this fixture cannot by itself tell
    the two `UNVERIFIED` arms apart. Shown by mutation on 2026-09-02: replacing
    `compose = compose_file(server_dir)` with `compose = None` leaves this test
    PASSING, and the mutant dies instead in
    `test_identify_produces_every_answer_it_declares` via its `MATCHES` fixture.
    The `assert catalog_view.compose_file(folder) == denied` line below proves the
    file exists; it does not prove which arm ran.

    A discrimination claim that does not discriminate is exactly the failure this
    branch exists to fix, so it is recorded here rather than quietly corrected.
    """
    entry = CATALOG.get("wow-wotlk")
    folder = _folder(tmp_path, "unreadable", _compose_naming("mangos-db"))
    denied = folder / "docker-compose.yml"
    real_read_text = Path.read_text

    def deny(self: Path, *args: object, **kwargs: object) -> str:
        if self == denied:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)
    # The file IS found - so this is the read failing, not the file missing.
    assert catalog_view.compose_file(folder) == denied
    assert catalog_view._identify(entry, folder) is Identification.UNVERIFIED


def _adopt_with_identification(
    answer: Identification,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bool, list[tuple[object, ...]], list[tuple[str, str]]]:
    """Drive `adopt_from_wsl()` with `_identify()` pinned to `answer`.

    Returns what the view answered, what it emitted, and every warning dialog
    it raised as (title, text). Pinning the identification is deliberate: the
    branch is the subject here, and what produces each member is the subject of
    the two tests above.
    """
    from PySide6.QtWidgets import QMessageBox

    dialogs: list[tuple[str, str]] = []

    def record_warning(*args: object, **kwargs: object) -> object:
        dialogs.append((str(args[1]), str(args[2])))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", record_warning)
    folder = _folder(tmp_path, f"server-{answer.value}", _compose_naming("whatever"))
    server = _server_in(folder)
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (server,))
    monkeypatch.setattr(catalog_view, "_identify", lambda entry, server_dir: answer)

    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        home=tmp_path,
        pick_wsl_server=lambda _f: server,
    )
    emitted: list[tuple[object, ...]] = []
    view.adopted.connect(lambda *a: emitted.append(a))
    answered = view.adopt_from_wsl(CATALOG.get("wow-wotlk"))
    return answered, emitted, dialogs


def test_each_identification_gets_its_own_answer_at_the_call_site(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse / proceed silently / proceed and say so - one outcome per member.

    The bool version had two outcomes for three questions, so this asserts the
    third is not quietly spelled like either of the others: `MATCHES` must show
    NO dialog (a warning on the happy path teaches people to click through
    them), and `UNVERIFIED` must both emit and warn.

    Enumerated for the same reason as the production test: a fourth member
    lands here with no outcome recorded and fails.
    """
    outcomes: dict[Identification, tuple[bool, int, tuple[str, ...]]] = {}
    for answer in Identification:
        with monkeypatch.context() as patch:
            answered, emitted, dialogs = _adopt_with_identification(
                answer, tmp_path=tmp_path, monkeypatch=patch
            )
        outcomes[answer] = (answered, len(emitted), tuple(title for title, _ in dialogs))

    assert outcomes == {
        Identification.MATCHES: (True, 1, ()),
        Identification.DIFFERENT: (False, 0, ("That is a different server",)),
        Identification.UNVERIFIED: (True, 1, ("Adopted without checking",)),
    }
    assert set(outcomes) == set(Identification), "an Identification member with no outcome"


def test_an_unverified_adoption_names_the_folder_in_the_dialog_it_shows(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dialog has to identify WHICH folder, or it cannot be acted on.

    "Yu'lon could not check" with no path in it is not something a user can do
    anything about; the three facts they need to recognise the folder are the
    path, the project and the distro.
    """
    _, _, dialogs = _adopt_with_identification(
        Identification.UNVERIFIED, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    assert len(dialogs) == 1
    text = dialogs[0][1]
    folder = tmp_path / f"server-{Identification.UNVERIFIED.value}"
    for fact in (str(folder), "some-server", "dml-arch", "WoW WotLK"):
        assert fact in text, f"the dialog never names {fact!r}"


def test_an_unverified_adoption_is_recorded_in_the_log_as_a_warning(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A dialog is gone the moment it is dismissed; the log is what is left afterwards.

    WARNING and not INFO: the adopt path already logs an INFO line for every
    adoption, so an unverified one recorded at INFO would be indistinguishable
    from a checked one in the file a support question arrives with.
    """
    folder = tmp_path / f"server-{Identification.UNVERIFIED.value}"
    with caplog.at_level(logging.WARNING, logger="yulon.ui.catalog_view"):
        _adopt_with_identification(
            Identification.UNVERIFIED, tmp_path=tmp_path, monkeypatch=monkeypatch
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "UNVERIFIED" in message
    assert "wow-wotlk" in message
    assert str(folder) in message


class _StopAfterClone(Exception):
    """Ends `Applier.install()` at the clone, before steps this test is not about.

    Not a `GitError`: `install()` catches those and re-raises them as
    `ApplyError`, which would hide where the run actually stopped.
    """


class _RealCloneThenStop:
    """The real `RunnerGit.clone()`, then stop. Satisfies `yulon.git.Git` (one method)."""

    def __init__(self) -> None:
        self.specs: list[CloneSpec] = []

    def clone(self, spec: CloneSpec) -> None:
        self.specs.append(spec)
        RunnerGit().clone(spec)
        raise _StopAfterClone(str(spec.dest))


def test_an_unverified_adoption_reaches_the_applier_with_no_ownership_check(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing between "adopted without evidence" and deleting files in that folder.

    This is the cost the `UNVERIFIED` dialog is paid for, asserted end to end
    rather than described: a folder nothing identified is adopted, the entry it
    was adopted under carries `has_manifests`, so `controller_view` builds an
    `Applier` rooted at that folder, and the first thing `RunnerGit.clone()`
    does at a destination that exists and is not a git checkout is
    `shutil.rmtree()` - before it contacts a remote, so a clone that was never
    going to succeed still deletes.

    Checked at the level of the code on 2026-09-02, on branch
    `fix/adoption-guard-fails-open` at fda035d: `yulon/git.py` `RunnerGit.clone`
    rmtrees `spec.dest` when `(spec.dest / ".git").is_dir()` is False and
    `spec.dest.exists()` is True; `yulon/apply.py` `Applier.clone_dir` returns
    `server_dir / CLONE_DIRS[type] / id`; `catalog.json` gives `wow-wotlk`
    `"has_manifests": true` and it is the only entry that has it;
    `yulon/ui/controller_view.py` passes that same `server_dir` to
    `wotlk_modules.applier()`.

    The only double ON THE CLONE PATH is `yulon.runner.run`, the subprocess seam,
    so the deletion, the paths and the ordering are all the shipping code's.

    Narrowed from a flat "the ONLY double here", which was false: `wsl.find_servers`
    is patched too (there is no other way to reach this code), and three autouse
    conftest fixtures are live, one of which suppresses modal dialogs. None of them
    touches the clone, so the conclusion stands - but a categorical claim that is
    not true is precisely what this branch was written to stop, and it should not
    appear in the test that carries the branch's own argument. The seam
    records whether the file was still there at the moment git was first asked
    to do anything, which is what "before it fetches anything" means.
    """
    entry = CATALOG.get("wow-wotlk")

    # 1. Adopted although nothing identified it.
    folder = _folder(tmp_path, "someones-folder", None)
    assert catalog_view._identify(entry, folder) is Identification.UNVERIFIED
    server = _server_in(folder, project="wow-server-playerbots")
    monkeypatch.setattr(wsl, "find_servers", lambda include=(): (server,))
    view = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        home=tmp_path,
        pick_wsl_server=lambda _f: server,
    )
    emitted: list[tuple[object, ...]] = []
    view.adopted.connect(lambda *a: emitted.append(a))
    assert view.adopt_from_wsl(entry) is True
    adopted_dir = Path(str(emitted[0][1]))

    # 2. That folder, and nothing else, is what the tab's applier is rooted at.
    assert entry.has_manifests is True, "the tab builds no Applier without this"
    git = _RealCloneThenStop()
    applier = wotlk_modules.applier(adopted_dir, git=git)
    assert applier.server_dir == adopted_dir

    manifest = next(m for m in wotlk_modules.store().load_all("module") if m.source is not None)
    clone = applier.clone_dir(manifest)
    assert clone.is_relative_to(adopted_dir), "the deletion below would be outside the folder"

    # 3. Something already living where the clone is about to go.
    clone.mkdir(parents=True)
    victim = clone / "please-keep-this.txt"
    victim.write_text("a file the user had in that folder", encoding="utf-8")

    still_there_when_git_ran: list[bool] = []

    def fake_run(
        argv: list[str], cwd: Path | None = None, env: object = None, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        still_there_when_git_ran.append(victim.exists())
        return _completed()

    monkeypatch.setattr(runner, "run", fake_run)

    with pytest.raises(_StopAfterClone):
        applier.install(manifest)

    # 4. Gone, and gone before git was asked for anything - so no failure of the
    #    clone itself could have saved it.
    assert git.specs and git.specs[0].dest == clone
    assert still_there_when_git_ran, "git was never invoked; the assertion below would be empty"
    assert still_there_when_git_ran[0] is False
    assert not victim.exists()
    assert not clone.exists()


def _catalog_in_the_default_window(
    view: CatalogView, panel: LogPanel
) -> tuple[QSplitter, QScrollArea]:
    """Lay the catalog out exactly as `build_window()` does, at the size it opens at.

    The tiles' width budget is not the window's — the Catalog tab is a splitter
    with the log panel beside it, so the view gets roughly half of it. Rebuilding
    that arrangement rather than resizing the view to some chosen number is the
    point: the budget has to be the app's own, or the test is measuring a window
    that does not exist.

    The splitter comes back with the scroll area because `addWidget()` reparents
    both children onto it: dropping it here would delete the C++ side of the very
    widgets the caller is about to measure.
    """
    splitter = QSplitter()
    splitter.addWidget(view)
    splitter.addWidget(panel)
    splitter.resize(*DEFAULT_WINDOW_SIZE)
    splitter.show()
    process_events()
    scroll = view.findChild(QScrollArea)
    assert isinstance(scroll, QScrollArea)
    return splitter, scroll


def test_every_install_button_is_inside_the_default_window(qapp: object) -> None:
    """Four tiles, four reachable Install buttons — at the size the app opens at.

    The tile description was a plain `QLabel`, so a tile was as wide as its
    longest unwrapped line, and WotLK's description is 118 characters. Two of
    those per grid row asked for some 3200px where the Catalog tab has under
    700, and WoW TBC and WoW Tortoise sat past the right edge of the viewport
    with their Install buttons drawn where nobody could see them. Clicking blind
    still opened the folder picker, which is why this survived: the control was
    never broken, only offscreen (measured on the shipped v0.6.51, 2026-08-30).

    Asserted as geometry rather than as `wordWrap() is True` deliberately. The
    property is one way to keep the promise; the promise is that the buttons are
    on screen, and it has to break again the day someone writes a description
    longer than wrapping can absorb.
    """
    panel = LogPanel()
    view = CatalogView(CATALOG, lambda e: _FakeInstaller(e, []), panel, pick_dir=lambda *_: None)
    window, scroll = _catalog_in_the_default_window(view, panel)
    assert window.isHidden() is False  # geometry is only meaningful once laid out
    viewport = scroll.viewport()

    offscreen = []
    for game in CATALOG.games:
        button = view.button_for(game.id)
        left = button.mapTo(viewport, button.rect().topLeft()).x()
        right = left + button.width()
        if left < 0 or right > viewport.width():
            offscreen.append(f"{game.id}: x {left}..{right} of {viewport.width()}")
    assert offscreen == []

    # And nothing is merely *scrollable* into reach: a horizontal scrollbar with
    # anything to scroll means the grid still does not fit, and the user meets
    # the same hidden tiles on the first frame.
    assert scroll.horizontalScrollBar().maximum() == 0
    grid = scroll.widget()
    assert grid is not None and grid.minimumSizeHint().width() <= viewport.width()
