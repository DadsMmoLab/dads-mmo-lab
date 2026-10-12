"""T702: the drive and the file are judged before the folder pickers; a refusal is no failure.

The view is driven for real (`start_install()`, `bring_from_another_computer()`, the finished
handler); the disk and the package are the only doubles, and each answers the way its real
function answers when it refuses.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.conftest import wait_for_panel
from tests.test_catalog_view import _FakeInstaller
from yulon.catalog import native, preflight
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.ui import catalog_view
from yulon.ui.catalog_view import BRING_FROM_ANOTHER, CatalogView
from yulon.ui.move_in import MoveIn
from yulon.ui.widgets.log_panel import LogPanel

CATALOG = load_catalog()
TBC = CATALOG.get("wow-tbc")
GIB = preflight.GIB


class _Dialogs:
    """Every picker and box, recorded in the order the player meets them."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.infos: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def info(_parent: object, title: str, text: str) -> None:
            self.events.append(f"box:{title}")
            self.infos.append((title, text))

        monkeypatch.setattr(catalog_view, "show_information", info)

    def pick_dir(self, _parent: object, title: str, _start: object) -> Path:
        self.events.append(f"pick:{title}")
        return self.chosen

    def suggest(self, _parent: object, _game: str, _suggested: Path) -> bool:
        self.events.append("suggestion")
        return True

    chosen: Path = Path("/nowhere")


def _view(
    tmp_path: Path,
    dialogs: _Dialogs,
    *,
    ready_check: object = None,
    move_in: object = None,
    package: Path | None = None,
) -> CatalogView:
    dialogs.chosen = tmp_path / "client"

    def pick_package(_parent: object, _title: str, _start: object) -> Path:
        dialogs.events.append("pick:package")
        return package or tmp_path / "w.zip"

    return CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, [], installs=False),
        LogPanel(),
        pick_dir=dialogs.pick_dir,
        ask_suggestion=dialogs.suggest,
        home=tmp_path,
        platform_id=lambda: "linux",
        ready_check=ready_check,  # type: ignore[arg-type]
        move_in=move_in,
        pick_package=pick_package,
    )


def test_a_full_drive_is_refused_before_the_client_folder_is_asked_for(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = _Dialogs()
    dialogs.install(monkeypatch)
    judged: list[tuple[str, Path]] = []

    def not_enough(entry: CatalogEntry, server_dir: Path) -> str | None:
        judged.append((entry.id, server_dir))
        return "12 GB is free on that drive, and WoW TBC needs 40 GB."

    view = _view(tmp_path, dialogs, ready_check=not_enough)
    assert view.start_install(TBC) is False

    assert judged == [("wow-tbc", tmp_path / TBC.install.default_server_dir)]
    # The server folder was settled (the suggestion), and nothing was asked after the refusal.
    assert dialogs.events == ["suggestion", "box:Cannot install yet"]
    assert dialogs.infos[0][1] == "12 GB is free on that drive, and WoW TBC needs 40 GB."


def test_a_drive_with_room_goes_on_to_the_client_folder(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = _Dialogs()
    dialogs.install(monkeypatch)
    view = _view(tmp_path, dialogs, ready_check=lambda _entry, _dir: None)
    view.start_install(TBC)
    # The install it started runs on the panel's thread: let it end here (T771).
    wait_for_panel(view._log)
    assert dialogs.events[0] == "suggestion"
    assert dialogs.events[1].startswith("pick:Select your"), dialogs.events
    assert not dialogs.infos


class _Mover:
    """A `MoveIn` whose file check refuses the way `move_in_for_app().check` does."""

    def __init__(self, refusal: tuple[str, str] | None) -> None:
        self.refusal = refusal
        self.planned: list[Path] = []
        self.checked: list[tuple[Path, str]] = []

    def check(self, path: Path, entry: CatalogEntry) -> tuple[str, str] | None:
        self.checked.append((path, entry.id))
        return self.refusal

    def plan(self, path: Path, server_dir: Path) -> object:
        self.planned.append(server_dir)
        raise AssertionError("the plan is for after the folders; the file was refused first")

    def move_in(self) -> MoveIn:
        return MoveIn(plan=self.plan, installer=lambda _plan: None, check=self.check)  # type: ignore[arg-type]


def test_a_wrong_package_is_refused_before_either_folder_is_asked_for(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = _Dialogs()
    dialogs.install(monkeypatch)
    mover = _Mover(("This server cannot be brought in", "This file is not a Yu'lon move package."))
    view = _view(tmp_path, dialogs, move_in=mover.move_in())
    assert view.bring_from_another_computer(TBC) is False

    assert dialogs.events == ["pick:package", "box:This server cannot be brought in"]
    assert mover.checked == [(tmp_path / "w.zip", "wow-tbc")]
    assert mover.planned == []


def test_a_good_package_still_goes_on_to_the_folders(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = _Dialogs()
    dialogs.install(monkeypatch)
    mover = _Mover(None)
    view = _view(tmp_path, dialogs, move_in=mover.move_in())
    with pytest.raises(AssertionError, match="the plan is for after the folders"):
        view.bring_from_another_computer(TBC)
    assert dialogs.events[:3] == [
        "pick:package",
        "suggestion",
        dialogs.events[2],
    ]
    assert dialogs.events[2].startswith("pick:Select your")


def test_bring_in_also_judges_the_drive_before_the_client_folder(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = _Dialogs()
    dialogs.install(monkeypatch)
    mover = _Mover(None)
    view = _view(
        tmp_path,
        dialogs,
        move_in=mover.move_in(),
        ready_check=lambda _entry, _dir: "Only 3 GB is free there.",
    )
    assert view.bring_from_another_computer(TBC) is False
    assert dialogs.events == ["pick:package", "suggestion", "box:Cannot install yet"]
    assert BRING_FROM_ANOTHER  # the tile's press under test


# ---- the refusal's own title ------------------------------------------------------------


class _Refuses(_FakeInstaller):
    def __init__(self, entry: CatalogEntry, message: str) -> None:
        super().__init__(entry, [], installs=False)
        self.message = message

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: object = None,
    ) -> Iterator[str]:
        yield "Checking Docker."
        raise InstallerError(self.message)


@pytest.mark.parametrize(
    ("message", "title"),
    [
        (f"{preflight.CANNOT_INSTALL_YET}\n- free space: 12 GB free", "Cannot install yet"),
        ("The source clone failed: the network went away.", "Install failed"),
    ],
)
def test_a_refusal_is_not_titled_a_failure(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    title: str,
) -> None:
    from yulon import platform

    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))  # type: ignore[attr-defined]
    monkeypatch.setattr(platform, "_process_group_names", lambda gids: {"docker"})
    panel = LogPanel()
    view = CatalogView(
        CATALOG,
        lambda e: _Refuses(e, message),
        panel,
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
        platform_id=lambda: "linux",
    )
    assert view.start_install(TBC) is True
    wait_for_panel(panel)
    assert warned == [title]


def test_the_engine_refuses_with_the_words_the_view_looks_for() -> None:
    """The engine's raise and the view's test share one constant (the strings cannot drift)."""
    source = Path(native.__file__).read_text(encoding="utf-8")
    assert "preflight.CANNOT_INSTALL_YET" in source
    assert catalog_view.CANNOT_INSTALL_YET is preflight.CANNOT_INSTALL_YET


# ---- the disk rule itself ---------------------------------------------------------------


def _free(gb: float | None):  # type: ignore[no-untyped-def]
    return lambda _path: None if gb is None else int(gb * GIB)


NATIVE = TBC.install.native
assert NATIVE is not None


def test_a_drive_below_the_server_floor_is_refused_in_one_sentence() -> None:
    said = preflight.early_space_refusal(
        TBC,
        Path("/home/pk/yulon-tbc"),
        platform_id="linux",
        data_root=None,
        free=_free(NATIVE.min_server_dir_gb - 5),
    )
    assert said is not None
    assert f"{NATIVE.min_server_dir_gb - 5:.0f} GB is free" in said
    assert f"needs {NATIVE.min_server_dir_gb:.0f} GB" in said
    assert "Install failed" not in said and "\n" not in said


def test_a_drive_with_room_is_not_refused() -> None:
    assert (
        preflight.early_space_refusal(
            TBC,
            Path("/home/pk/yulon-tbc"),
            platform_id="linux",
            data_root=None,
            free=_free(NATIVE.min_server_dir_gb + 1),
        )
        is None
    )


def test_a_reading_that_could_not_be_taken_is_not_a_refusal() -> None:
    assert (
        preflight.early_space_refusal(
            TBC, Path("/x"), platform_id="linux", data_root=None, free=_free(None)
        )
        is None
    )


def test_images_on_the_same_drive_add_to_the_need_and_the_drive_is_named_once(
    tmp_path: Path,
) -> None:
    both = NATIVE.floors_gb(same_volume=True)[0]
    said = preflight.early_space_refusal(
        TBC,
        tmp_path / "server",
        platform_id="linux",
        data_root=tmp_path,  # the same filesystem as the server folder, so one pool
        free=_free(both - 1),
    )
    assert said is not None
    assert f"needs {both:.0f} GB" in said
    assert said.count("Docker") == 1


def test_a_folder_that_holds_an_install_record_is_left_to_the_full_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resume owes less than a fresh install; refusing it early would refuse it wrongly."""
    asked: list[Path] = []
    monkeypatch.setattr(
        preflight,
        "early_space_refusal",
        lambda entry, server_dir, **_kw: asked.append(server_dir) or "too small",
    )
    fresh = tmp_path / "fresh"
    assert native.early_refusal(TBC, fresh) == "too small"
    resumed = tmp_path / "resumed"
    resumed.mkdir()
    (resumed / native.STATE_FILE).write_text("{}", encoding="utf-8")
    assert native.early_refusal(TBC, resumed) is None
    assert asked == [fresh]


# ---- the same-drive point is made once ----------------------------------------------------


def test_the_same_drive_point_is_made_once_in_the_refusal() -> None:
    from tests.test_preflight import facts

    report = preflight.evaluate(
        TBC,
        Path("/home/user/wow"),
        facts(data_root_free=4 * GIB, server_dir_free=4 * GIB, same_volume=True),
    )
    text = report.message()
    mentions = text.count("same drive") + text.count("share one drive")
    assert mentions == 1, text


# ---- review round 2


def test_a_linux_box_never_guesses_where_docker_keeps_its_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leftover /var/lib/docker (moved data-root, rootless, Desktop) must not mean "shared"."""
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    assert preflight.cheap_data_root("linux") is None


def test_the_linux_early_check_is_handed_no_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def spy(entry: object, server_dir: object, **kw: object) -> None:
        seen.update(kw)

    monkeypatch.setattr(preflight, "early_space_refusal", spy)
    monkeypatch.setattr(native.platform, "detect", lambda: "linux")
    monkeypatch.setattr(Path, "is_dir", lambda self: True)  # a /var/lib/docker is "there"
    native.early_refusal(TBC, tmp_path / "server")
    assert seen["data_root"] is None and seen["platform_id"] == "linux"


def test_a_folder_holding_an_unfinished_move_is_left_to_the_move_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight, "early_space_refusal", lambda *_a, **_k: "too small")
    folder = tmp_path / "half-moved"
    folder.mkdir()
    (folder / native.MOVE_IN_FILE).write_text("{}", encoding="utf-8")
    assert native.early_refusal(TBC, folder) is None


def test_the_package_is_read_once_for_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import move
    from yulon.ui import move_in

    reads: list[Path] = []
    monkeypatch.setattr(move, "read_package", lambda path: reads.append(path) or object())
    file = tmp_path / "w.zip"
    file.write_bytes(b"one")
    read = move_in._one_read()
    first = read(file)
    assert read(file) is first and len(reads) == 1
    # A different file content (size) is a different file: read again.
    file.write_bytes(b"three")
    assert read(file) is not first and len(reads) == 2


def test_a_failed_read_is_never_remembered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yulon import move
    from yulon.ui import move_in

    answers = iter([move.MovePackageError("bad"), "good"])

    def flaky(_path: Path) -> object:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(move, "read_package", flaky)
    file = tmp_path / "w.zip"
    file.write_bytes(b"x")
    read = move_in._one_read()
    with pytest.raises(move.MovePackageError):
        read(file)
    assert read(file) == "good"


def test_check_and_plan_share_one_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yulon import move_server
    from yulon.ui.move_in import move_in_for_app

    readers: list[object] = []

    def spy(path: Path, **kw: object) -> move_server.ServerImportPlan:
        readers.append(kw["read_package"])
        return move_server.ServerImportPlan(path=path, manifest=None, refusals=("x",))

    monkeypatch.setattr(move_server, "plan_server_import", spy)
    moved = move_in_for_app(CATALOG)
    assert moved.check is not None
    moved.check(tmp_path / "w.zip", TBC)
    moved.plan(tmp_path / "w.zip", tmp_path / "s")
    assert len(readers) == 2 and readers[0] is readers[1]


# ---- the production wiring, not the doubles


def test_the_real_move_in_refuses_a_package_of_another_game_before_any_folder(
    tmp_path: Path,
) -> None:
    from tests.test_move_server_flow import whole_package
    from yulon.ui.catalog_view import another_game_message
    from yulon.ui.move_in import move_in_for_app

    moved = move_in_for_app(CATALOG)
    assert moved.check is not None
    package = whole_package(tmp_path)  # a WotLK server
    refused = moved.check(package, TBC)
    assert refused == ("Another game", another_game_message(CATALOG.get("wow-wotlk").name))
    assert moved.check(package, CATALOG.get("wow-wotlk")) is None


def test_the_real_move_in_refuses_a_file_that_is_not_a_package(tmp_path: Path) -> None:
    from yulon.ui.move_in import move_in_for_app

    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    moved = move_in_for_app(CATALOG)
    assert moved.check is not None
    refused = moved.check(junk, TBC)
    assert refused is not None and refused[0] == "This server cannot be brought in"
