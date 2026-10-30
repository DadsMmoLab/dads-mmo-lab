"""T601 level 2: the Catalog tile's "Bring from another computer…" press.

The view is the questions in front of `move_server`: the file, the folders, the plan shown in
one question, then the run through the tile's normal install machinery. The plan and the
engine are doubles here; `test_move_server_flow.py` tests them.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import wait_for_panel
from tests.test_catalog_view import _FakeInstaller
from yulon import move_server
from yulon.catalog.catalog import load_catalog
from yulon.ui.catalog_view import BRING_FROM_ANOTHER, CatalogView
from yulon.ui.move_in import MoveIn
from yulon.ui.widgets.log_panel import LogPanel

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")


class Mover:
    def __init__(self, tmp_path: Path, *, refusals: tuple[str, ...] = (), game: str = "wow-wotlk"):
        self.tmp_path = tmp_path
        self.planned: list[tuple[Path, Path]] = []
        self.engines: list[_FakeInstaller] = []
        self.refusals = refusals
        self.game = game

    def plan(self, path: Path, server_dir: Path) -> move_server.ServerImportPlan:
        self.planned.append((path, server_dir))
        entry = CATALOG.get(self.game)
        return move_server.ServerImportPlan(
            path=path,
            manifest=None,
            refusals=self.refusals,
            entry=entry,
            pinned=entry,
            server_dir=server_dir,
        )

    def installer(self, plan: move_server.ServerImportPlan) -> _FakeInstaller:
        engine = _FakeInstaller(WOTLK, ["building"])
        self.engines.append(engine)
        return engine

    def move_in(self) -> MoveIn:
        return MoveIn(plan=self.plan, installer=self.installer)  # type: ignore[arg-type]


def view(tmp_path: Path, mover: Mover | None, *, yes: bool = True, package: Path | None = None):
    asked: list[tuple[str, str]] = []

    def ask_yes(_parent: object, title: str, text: str) -> bool:
        asked.append((title, text))
        return yes

    built = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        pick_dir=lambda _p, _t, _s: tmp_path / "client",
        ask_suggestion=lambda _p, _g, _s: True,
        home=tmp_path,
        move_in=mover.move_in() if mover is not None else None,
        pick_package=lambda _p, _t, _s: package if package is not None else tmp_path / "w.zip",
        ask_yes=ask_yes,
        platform_id=lambda: "linux",
    )
    return built, asked


def test_the_press_is_on_each_tile_only_when_wired(qapp: object, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QPushButton

    plain, _ = view(tmp_path, None)
    assert plain.findChild(QPushButton, f"move-in-{WOTLK.id}") is None
    wired, _ = view(tmp_path, Mover(tmp_path))
    bring = wired.findChild(QPushButton, f"move-in-{WOTLK.id}")
    assert bring is not None and bring.text() == BRING_FROM_ANOTHER


def test_a_yes_to_the_plan_runs_its_engine_in_the_suggested_folder(
    qapp: object, tmp_path: Path
) -> None:
    mover = Mover(tmp_path)
    built, asked = view(tmp_path, mover)
    assert built.bring_from_another_computer(WOTLK) is True
    # The job it started runs on the panel's thread: let it end here (T771).
    wait_for_panel(built._log)
    folder = tmp_path / WOTLK.install.default_server_dir
    assert mover.planned == [(tmp_path / "w.zip", folder)]
    assert [a[0] for a in asked] == ["Bring this server in?"]
    assert len(mover.engines) == 1


def test_the_move_runs_its_engine_with_no_first_install_flag(qapp: object, tmp_path: Path) -> None:
    """T723: only the Install button may switch a channel on at install; the move must not."""
    mover = Mover(tmp_path)
    built, _ = view(tmp_path, mover)
    assert built.bring_from_another_computer(WOTLK) is True
    wait_for_panel(built._log)
    assert [opts.first_install for opts in mover.engines[0].ran_with] == [False]


def test_the_install_button_runs_its_engine_as_a_first_install(
    qapp: object, tmp_path: Path
) -> None:
    """T723: the Install button is the press that says this is an install run."""
    made: list[_FakeInstaller] = []

    def factory(entry: object) -> _FakeInstaller:
        made.append(_FakeInstaller(entry, ["building"]))  # type: ignore[arg-type]
        return made[-1]

    built = CatalogView(
        CATALOG,
        factory,  # type: ignore[arg-type]
        LogPanel(),
        pick_dir=lambda _p, _t, _s: tmp_path / "client",
        ask_suggestion=lambda _p, _g, _s: True,
        home=tmp_path,
        platform_id=lambda: "linux",
    )
    assert built.start_install(WOTLK) is True
    wait_for_panel(built._log)
    assert [opts.first_install for opts in made[-1].ran_with] == [True]


def test_a_no_runs_nothing(qapp: object, tmp_path: Path) -> None:
    mover = Mover(tmp_path)
    built, _ = view(tmp_path, mover, yes=False)
    assert built.bring_from_another_computer(WOTLK) is False
    assert mover.engines == []


def test_a_refused_plan_is_shown_and_never_asked_about(
    qapp: object, tmp_path: Path, monkeypatch: object
) -> None:
    shown: list[str] = []
    import yulon.ui.catalog_view as cv

    monkeypatch.setattr(cv, "show_information", lambda _p, _t, text: shown.append(text))  # type: ignore[attr-defined]
    mover = Mover(tmp_path, refusals=("the folder holds a server",))
    built, asked = view(tmp_path, mover)
    assert built.bring_from_another_computer(WOTLK) is False
    assert shown == ["the folder holds a server"]
    assert asked == [] and mover.engines == []


def test_a_package_of_another_game_is_sent_to_its_tile(
    qapp: object, tmp_path: Path, monkeypatch: object
) -> None:
    shown: list[str] = []
    import yulon.ui.catalog_view as cv

    monkeypatch.setattr(cv, "show_information", lambda _p, _t, text: shown.append(text))  # type: ignore[attr-defined]
    mover = Mover(tmp_path, game="wow-unbound")
    built, asked = view(tmp_path, mover)
    assert built.bring_from_another_computer(WOTLK) is False
    assert shown == [
        f"This file holds a WoW Unbound server. Use the WoW Unbound tile's {BRING_FROM_ANOTHER}"
    ]
    assert mover.engines == []


def test_no_file_chosen_asks_nothing_more(qapp: object, tmp_path: Path) -> None:
    mover = Mover(tmp_path)
    built = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        home=tmp_path,
        move_in=mover.move_in(),
        pick_package=lambda _p, _t, _s: None,
        ask_suggestion=lambda _p, _g, _s: (_ for _ in ()).throw(AssertionError("asked")),
        platform_id=lambda: "linux",
    )
    assert built.bring_from_another_computer(WOTLK) is False
    assert mover.planned == []


def test_an_installed_game_offers_no_second_server(qapp: object, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QPushButton

    mover = Mover(tmp_path)
    built = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        home=tmp_path,
        installed_games={WOTLK.id: tmp_path / "already"},
        move_in=mover.move_in(),
        platform_id=lambda: "linux",
    )
    bring = built.findChild(QPushButton, f"move-in-{WOTLK.id}")
    assert bring is not None and not bring.isEnabled()


def test_removing_the_install_offers_bring_from_another_computer_again(
    qapp: object, tmp_path: Path
) -> None:
    from PySide6.QtWidgets import QPushButton

    built = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        home=tmp_path,
        installed_games={WOTLK.id: tmp_path / "already"},
        move_in=Mover(tmp_path).move_in(),
        platform_id=lambda: "linux",
    )
    bring = built.findChild(QPushButton, f"move-in-{WOTLK.id}")
    assert bring is not None and not bring.isEnabled()
    built.forget_installed(WOTLK.id, {})
    assert bring.isEnabled()
    built.forget_installed(WOTLK.id, {WOTLK.id: tmp_path / "second"})
    assert not bring.isEnabled()
    built.forget_installed(WOTLK.id, {})
    built._gated.add(WOTLK.id)
    built.forget_installed(WOTLK.id, {WOTLK.id: tmp_path / "x"})
    built.forget_installed(WOTLK.id, {})
    assert not bring.isEnabled()


def test_install_into_an_unfinished_move_sends_the_player_to_bring_in(
    qapp: object, tmp_path: Path, monkeypatch: object
) -> None:
    import yulon.ui.catalog_view as cv
    from yulon.catalog import native

    assert cv.MOVE_IN_FILE == native.MOVE_IN_FILE
    shown: list[str] = []
    monkeypatch.setattr(cv, "show_information", lambda _p, _t, text: shown.append(text))  # type: ignore[attr-defined]
    folder = tmp_path / WOTLK.install.default_server_dir
    folder.mkdir()
    (folder / native.MOVE_IN_FILE).write_text("{}", encoding="utf-8")
    built, _ = view(tmp_path, Mover(tmp_path))
    assert built.start_install(WOTLK) is False
    assert shown == [
        f"A bring-in on this folder is not finished. Press {BRING_FROM_ANOTHER} again with the "
        "same file and folder to carry on (nothing is lost)."
    ]
    assert (folder / native.MOVE_IN_FILE).exists()


def test_attaching_an_unfinished_move_sends_the_player_to_bring_in(
    qapp: object, tmp_path: Path, monkeypatch: object
) -> None:
    import yulon.ui.catalog_view as cv
    from yulon.catalog import native

    shown: list[str] = []
    monkeypatch.setattr(cv, "show_information", lambda _p, _t, text: shown.append(text))  # type: ignore[attr-defined]
    folder = tmp_path / "half-moved"
    folder.mkdir()
    (folder / native.MOVE_IN_FILE).write_text("{}", encoding="utf-8")
    (folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    built = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        pick_dir=lambda _p, _t, _s: folder,
        home=tmp_path,
        move_in=Mover(tmp_path).move_in(),
        platform_id=lambda: "linux",
    )
    assert built.attach_existing(WOTLK) is False
    assert shown and BRING_FROM_ANOTHER in shown[0]


# ============================================================ T830: start over


class StartingOver(Mover):
    """A `MoveIn` whose start over is wired: the engine it builds, and what it was given."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.made: list[tuple[object, ...]] = []

    def start_over(
        self, entry: object, server_dir: Path, client_dir: object, plain: object, expected: object
    ) -> _FakeInstaller:
        engine = _FakeInstaller(WOTLK, ["reset", "installed"])
        self.made.append((entry, server_dir, client_dir, plain, expected, engine))
        return engine

    def move_in(self) -> MoveIn:
        return MoveIn(plan=self.plan, installer=self.installer, start_over=self.start_over)  # type: ignore[arg-type]


def _unfinished_folder(tmp_path: Path) -> Path:
    folder = tmp_path / WOTLK.install.default_server_dir
    folder.mkdir()
    move_server.write_marker(folder, "f" * 64, ["revs", "answers"])
    return folder


def test_install_into_an_unfinished_move_asks_to_start_over_and_runs_it(
    qapp: object, tmp_path: Path
) -> None:
    """T830: the one question, then the start over as a first install, in the folder asked about.

    Mutation: `start_install()` runs the plain engine after the question -> the start over is
    never made and the plain install runs on what the bring-in left.
    """
    from yulon import start_over

    folder = _unfinished_folder(tmp_path)
    mover = StartingOver(tmp_path)
    built, asked = view(tmp_path, mover)
    assert built.start_install(WOTLK) is True
    wait_for_panel(built._log)
    assert asked == [(start_over.QUESTION_TITLE, start_over.QUESTION)]
    assert len(mover.made) == 1
    entry, server_dir, _client, plain, expected, engine = mover.made[0]
    assert entry is WOTLK and server_dir == folder
    assert expected == move_server.read_marker(folder)
    assert isinstance(plain, _FakeInstaller) and plain.ran_with == []
    assert [opts.first_install for opts in engine.ran_with] == [True]  # type: ignore[attr-defined]


def test_a_no_to_starting_over_changes_nothing(qapp: object, tmp_path: Path) -> None:
    folder = _unfinished_folder(tmp_path)
    record = (folder / move_server.MOVE_IN_FILE).read_bytes()
    mover = StartingOver(tmp_path)
    built, asked = view(tmp_path, mover, yes=False)
    assert built.start_install(WOTLK) is False
    assert len(asked) == 1 and mover.made == []
    assert (folder / move_server.MOVE_IN_FILE).read_bytes() == record


def test_a_start_over_that_stopped_is_finished_from_install(qapp: object, tmp_path: Path) -> None:
    """Only the reset's record is left (its last step before the plain install): Install asks
    to finish it, never runs the plain engine bare over it."""
    from yulon import start_over
    from yulon.catalog import native

    folder = tmp_path / WOTLK.install.default_server_dir
    folder.mkdir()
    (folder / native.RESET_FILE).write_text('{"package": "x"}', encoding="utf-8")
    mover = StartingOver(tmp_path)
    built, asked = view(tmp_path, mover)
    assert built.start_install(WOTLK) is True
    wait_for_panel(built._log)
    assert asked == [(start_over.FINISH_TITLE, start_over.FINISH_QUESTION)]
    assert [m[4] for m in mover.made] == [None]


def test_attaching_a_folder_being_started_over_is_refused(
    qapp: object, tmp_path: Path, monkeypatch: object
) -> None:
    import yulon.ui.catalog_view as cv
    from yulon.catalog import native

    shown: list[str] = []
    monkeypatch.setattr(cv, "show_information", lambda _p, _t, text: shown.append(text))  # type: ignore[attr-defined]
    folder = tmp_path / "resetting"
    folder.mkdir()
    (folder / native.RESET_FILE).write_text("{}", encoding="utf-8")
    (folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    built = CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        pick_dir=lambda _p, _t, _s: folder,
        home=tmp_path,
        move_in=StartingOver(tmp_path).move_in(),
        platform_id=lambda: "linux",
    )
    assert built.attach_existing(WOTLK) is False
    assert shown == [move_server.START_OVER_UNFINISHED]
