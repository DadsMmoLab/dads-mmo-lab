"""T601: the Maintenance tab's "Move to another computer" group.

The panel is the questions in front of `move_flows`; the engine is faked here and tested
in `test_move_flows.py`. What is asserted is what the player is asked and what the engine
is then told: the stop is covered by the yes that names it, a replace carries the plan's own
token, a No does nothing, and the buttons are off while a job runs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from yulon import move
from yulon.move import Counts, Evidence, Manifest
from yulon.move_flows import (
    ExportPlan,
    ExportResult,
    ImportPlan,
    ImportResult,
    MoveError,
    MoveServices,
    Replaces,
)
from yulon.ui.widgets import move_panel
from yulon.ui.widgets.job import run_inline
from yulon.ui.widgets.move_panel import MovePanel

DIGEST = "a" * 64


def manifest(realm: str | None = "Old Realm") -> Manifest:
    return Manifest(
        format=1,
        made_by=move.MadeBy(yulon="0.9.14", platform="linux", made="2026-10-09T15:30:00"),
        game=move.GameRef(id="wow-wotlk", name="WoW WotLK"),
        databases=(
            move.Member(
                schema="acore_auth",
                role="auth",
                file="db/acore_auth.sql",
                bytes=1,
                sha256=DIGEST,
                tables=("account",),
            ),
        ),
        schema_evidence={"acore_auth": Evidence(kind="updates", count=1, digest=DIGEST)},
        realm_name=realm,
        counts=Counts(accounts=2, characters=3, bot_accounts=500),
        secrets=move.SECRETS,
    )


def allowed_plan(
    path: Path,
    *,
    running: bool = False,
    replaces: Replaces | None = None,
    realm: str | None = "Old Realm",
) -> ImportPlan:
    return ImportPlan(
        path=path,
        manifest=manifest(realm),
        refusals=(),
        schemas=("acore_auth", "acore_characters"),
        server_running=running,
        counts=(0, 0),
        replaces=replaces,
    )


class Engine:
    """The four presses, recorded."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls: list[tuple[str, tuple]] = []
        self.export_plan = ExportPlan(server_running=False, refusals=())
        self.import_plan = allowed_plan(tmp_path / "p.zip")
        self.raise_on_export: Exception | None = None
        self.raise_on_import: Exception | None = None

    def services(self) -> MoveServices:
        def export(folder: Path, stop_allowed: bool) -> ExportResult:
            self.calls.append(("export", (folder, stop_allowed)))
            if self.raise_on_export:
                raise self.raise_on_export
            out = folder / "x.zip"
            out.write_bytes(b"x")
            return ExportResult(path=out, manifest=manifest(), restarted=None)

        def run_import(
            plan: ImportPlan, confirm: str | None, old: bool, stop: bool
        ) -> ImportResult:
            self.calls.append(("import", (confirm, old, stop)))
            if self.raise_on_import:
                raise self.raise_on_import
            assert plan.manifest is not None
            return ImportResult(
                manifest=plan.manifest, schemas=plan.schemas, copies=(), stopped=stop
            )

        return MoveServices(
            plan_export=lambda: self.export_plan,
            export=export,
            plan_import=lambda path: self.import_plan,
            run_import=run_import,
            default_folder=lambda: self.tmp_path,
        )


class Screen:
    """The tab around the panel: what it was shown, asked and told."""

    def __init__(
        self,
        engine: Engine,
        *,
        yes: bool = True,
        ticked: bool = False,
        folder: Path | None = None,
        package: Path | None = None,
    ) -> None:
        self.reports: list[str] = []
        self.failures: list[tuple[object, str]] = []
        self.changed = 0
        self.asked: list[tuple[str, str, str | None]] = []
        self.yes, self.ticked = yes, ticked
        self.folder = folder if folder is not None else engine.tmp_path
        self.package = package if package is not None else engine.tmp_path / "p.zip"
        self.panel_args = dict(
            jobs=run_inline,
            report=self.reports.append,
            failed=lambda exc, said: self.failures.append((exc, said)),
            changed=self._changed,
            ask=self._ask,
            pick_folder=lambda title, start: self.folder,
            pick_package=lambda title, start: self.package,
        )

    def _changed(self) -> None:
        self.changed += 1

    def _ask(self, title: str, text: str, option: str | None) -> tuple[bool, bool]:
        self.asked.append((title, text, option))
        return self.yes, self.ticked


def panel(engine: Engine, screen: Screen, qapp: object) -> MovePanel:
    return MovePanel(engine.services(), **screen.panel_args)  # type: ignore[arg-type]


# ------------------------------------------------------------------ pack


def test_packing_asks_once_and_says_the_three_things(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.export_plan = ExportPlan(server_running=True, refusals=())
    screen = Screen(engine)
    panel(engine, screen, qapp).pack()
    assert len(screen.asked) == 1
    text = screen.asked[0][1]
    assert "keep it private" in text
    assert "world" in text and "is not in it" in text
    assert "stopped while Yu'lon packs it" in text and "started again afterwards" in text


def test_a_yes_on_a_running_server_lets_the_engine_stop_it(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.export_plan = ExportPlan(server_running=True, refusals=())
    screen = Screen(engine)
    panel(engine, screen, qapp).pack()
    assert engine.calls == [("export", (tmp_path, True))]


def test_a_yes_on_a_stopped_server_does_not_allow_a_stop(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    panel(engine, screen, qapp).pack()
    assert engine.calls == [("export", (tmp_path, False))]
    assert "stopped while" not in screen.asked[0][1]


def test_a_no_packs_nothing(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine, yes=False)
    panel(engine, screen, qapp).pack()
    assert engine.calls == []
    assert screen.reports[-1] == "Nothing was packed."


def test_no_folder_chosen_packs_nothing(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    screen.panel_args["pick_folder"] = lambda title, start: None
    panel(engine, screen, qapp).pack()
    assert engine.calls == []
    assert "no folder was chosen" in screen.reports[-1]


def test_a_refused_pack_shows_every_reason_and_asks_nothing(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.export_plan = ExportPlan(server_running=False, refusals=("first", "second"))
    screen = Screen(engine)
    panel(engine, screen, qapp).pack()
    assert screen.asked == []
    assert screen.reports[-1] == "This cannot go ahead:\n  - first\n  - second"


def test_the_result_of_a_pack_is_shown_and_the_tab_told(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    p = panel(engine, screen, qapp)
    p.pack()
    assert "Packed 2 accounts and 3 characters" in screen.reports[-1]
    assert screen.changed >= 1
    assert p.running is False and p.pack_button.isEnabled()


def test_a_pack_that_fails_goes_to_the_tabs_failure_line(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.raise_on_export = MoveError("no room")
    screen = Screen(engine)
    p = panel(engine, screen, qapp)
    p.pack()
    assert screen.failures and screen.failures[0][1] == move_panel.PACK_FAILED
    assert str(screen.failures[0][0]) == "no room"
    assert p.running is False


def test_the_buttons_are_off_while_a_job_runs(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    held: list[tuple] = []
    screen.panel_args["jobs"] = lambda work, done, fail: held.append((work, done, fail))
    p = panel(engine, screen, qapp)
    p.pack()
    assert p.running and not p.pack_button.isEnabled() and not p.bring_in_button.isEnabled()
    p.pack()  # a second press while one is running does nothing
    assert len(held) == 1
    p.bring_in()
    assert len(held) == 1


# ------------------------------------------------------------------ bring in


def test_a_refused_plan_is_shown_in_full_and_nothing_is_asked(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.import_plan = ImportPlan(path=tmp_path / "p.zip", manifest=None, refusals=("a", "b"))
    screen = Screen(engine)
    panel(engine, screen, qapp).bring_in()
    assert screen.reports[-1] == "This cannot go ahead:\n  - a\n  - b"
    assert screen.asked == []
    assert engine.calls == []


def test_no_file_chosen_does_nothing(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    screen.panel_args["pick_package"] = lambda title, start: None
    panel(engine, screen, qapp).bring_in()
    assert screen.reports == [] and engine.calls == []


def test_a_no_brings_nothing_in(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine, yes=False)
    panel(engine, screen, qapp).bring_in()
    assert engine.calls == []
    assert screen.reports[-1] == "Nothing was brought in."


def test_a_clean_target_needs_no_token_and_keeps_its_own_realm_name(
    tmp_path: Path, qapp: object
) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    panel(engine, screen, qapp).bring_in()
    assert engine.calls == [("import", (None, False, False))]
    assert screen.asked[0][2] == "Use the old realm name (Old Realm)"


def test_replacing_players_hands_the_engine_the_plans_own_token(
    tmp_path: Path, qapp: object
) -> None:
    engine = Engine(tmp_path)
    engine.import_plan = allowed_plan(
        tmp_path / "p.zip",
        replaces=Replaces(1, 2, "This server already has 2 characters on 1 account. Replace?"),
    )
    screen = Screen(engine)
    panel(engine, screen, qapp).bring_in()
    assert engine.calls == [("import", (engine.import_plan.token, False, False))]
    assert "This server already has 2 characters on 1 account. Replace?" in screen.asked[0][1]


def test_the_ticked_realm_name_and_a_running_server_reach_the_engine(
    tmp_path: Path, qapp: object
) -> None:
    engine = Engine(tmp_path)
    engine.import_plan = allowed_plan(tmp_path / "p.zip", running=True)
    screen = Screen(engine, ticked=True)
    panel(engine, screen, qapp).bring_in()
    assert engine.calls == [("import", (None, True, True))]
    assert "stays stopped afterwards" in screen.asked[0][1]


def test_a_package_that_names_no_realm_offers_no_tick_box(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.import_plan = allowed_plan(tmp_path / "p.zip", realm=None)
    screen = Screen(engine)
    panel(engine, screen, qapp).bring_in()
    assert screen.asked[0][2] is None


def test_the_result_of_bringing_in_is_shown(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    panel(engine, screen, qapp).bring_in()
    assert "Brought in 2 accounts and 3 characters" in screen.reports[-1]
    assert "Start the server to play" in screen.reports[-1]


def test_a_bring_in_that_fails_goes_to_the_failure_line(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    engine.raise_on_import = MoveError("the load failed")
    screen = Screen(engine)
    p = panel(engine, screen, qapp)
    p.bring_in()
    assert screen.failures[0][1] == move_panel.BRING_IN_FAILED
    assert p.running is False


def test_every_default_sentence_is_the_one_the_owner_decided_on() -> None:
    assert "keep it private" in move_panel.EXPLAIN.lower()
    assert "world itself" in move_panel.EXPLAIN


@pytest.mark.parametrize("when", [datetime(2026, 10, 9, 15, 30)])
def test_the_question_shows_when_it_was_packed(when: datetime) -> None:
    text = move_panel.bring_in_question(allowed_plan(Path("p.zip")))
    assert "2026-10-09 15:30" in text


# ------------------------------------------------------------------ pack the whole server (level 2)


def whole_services(engine: Engine) -> MoveServices:
    from dataclasses import replace

    def export_server(folder: Path, stop_allowed: bool) -> ExportResult:
        engine.calls.append(("export_server", (folder, stop_allowed)))
        out = folder / "w.zip"
        out.write_bytes(b"x")
        return ExportResult(path=out, manifest=manifest(), restarted=None)

    return replace(engine.services(), export_server=export_server)


def test_the_whole_server_press_is_drawn_only_when_wired(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    assert panel(engine, screen, qapp).pack_server_button is None
    wired = MovePanel(whole_services(engine), **screen.panel_args)  # type: ignore[arg-type]
    assert wired.pack_server_button is not None
    assert wired.pack_server_button.text() == move_panel.PACK_SERVER_BUTTON


def test_the_whole_server_press_asks_its_own_question_and_packs_the_server(
    tmp_path: Path, qapp: object
) -> None:
    engine = Engine(tmp_path)
    engine.export_plan = ExportPlan(server_running=True, refusals=())
    screen = Screen(engine)
    MovePanel(whole_services(engine), **screen.panel_args).pack_server()  # type: ignore[arg-type]
    assert [a[0] for a in screen.asked] == ["Pack the whole server?"]
    text = screen.asked[0][1]
    assert "the world too" in text and "keep it private" in text
    assert "stopped while Yu'lon packs it" in text
    assert engine.calls == [("export_server", (tmp_path, True))]


def test_the_accounts_press_still_packs_accounts_beside_it(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine)
    wired = MovePanel(whole_services(engine), **screen.panel_args)  # type: ignore[arg-type]
    wired.pack_server()
    wired.pack()
    assert [c[0] for c in engine.calls] == ["export_server", "export"]
    assert [a[0] for a in screen.asked] == [
        "Pack the whole server?",
        "Pack accounts and characters?",
    ]


def test_a_no_to_the_whole_server_packs_nothing(tmp_path: Path, qapp: object) -> None:
    engine = Engine(tmp_path)
    screen = Screen(engine, yes=False)
    MovePanel(whole_services(engine), **screen.panel_args).pack_server()  # type: ignore[arg-type]
    assert engine.calls == []
    assert screen.reports[-1] == "Nothing was packed."
