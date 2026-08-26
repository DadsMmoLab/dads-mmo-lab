"""Tests for `CatalogView` (roadmap 4.2): tiles, folder prompts, delegation to the installer."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton, QWidget

from tests.conftest import process_events
from yulon import runner, wsl
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


def test_adopting_allows_a_compose_file_it_cannot_read(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file is not evidence of the wrong game.

    The folder lives inside a distro and is reached over a UNC path; that read
    can fail for reasons that have nothing to do with which game it is. Refusing
    on "I could not check" would block the migration this feature exists to
    provide, so the check only fires on a file it actually read.
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
