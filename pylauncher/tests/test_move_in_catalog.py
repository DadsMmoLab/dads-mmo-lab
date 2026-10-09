"""T601 level 2: the Catalog tile's "Bring from another computer…" press.

The view is the questions in front of `move_server`: the file, the folders, the plan shown in
one question, then the run through the tile's normal install machinery. The plan and the
engine are doubles here; `test_move_server_flow.py` tests them.
"""

from __future__ import annotations

from pathlib import Path

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
    folder = tmp_path / WOTLK.install.default_server_dir
    assert mover.planned == [(tmp_path / "w.zip", folder)]
    assert [a[0] for a in asked] == ["Bring this server in?"]
    assert len(mover.engines) == 1


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
    assert shown and BRING_FROM_ANOTHER in shown[0]


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
