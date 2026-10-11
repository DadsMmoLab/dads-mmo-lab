"""T706: a Rebuild runs the database importer before the new build first starts.

Live on yulon-ubuntu, 2026-10-11, on fresh v0.9.16 WotLK and Unbound installs:
Install City Bots, then Rebuild. The compile linked, and the world aborted on
`Table 'acore_world.city_bot_poi' doesn't exist`, because the module's
`data/sql/db-world` files are applied by AzerothCore's importer one-shot alone
(the world server runs with `Updates.EnableDatabases` 0), and a plain Rebuild
never ran it: `rebuild_stages()` has no `import`, and the recreate names only the
long-running services. A fresh install runs it in its `import` stage, and the
update route in its servers-down window (T220); the Rebuild press had neither.

Driven through the real `rebuild()` on `tests/support_native.Recorder`, so these
are facts about the order of the asks, not evidence that a server starts. The live
proof is in the ticket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine, install
from yulon import docker, resources
from yulon.catalog import native
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

IMPORTER = f"one-shot:{ENTRY.containers.db_import}"


def a_finished_install(rec: Recorder, tmp_path: Path) -> Path:
    """A WotLK folder as the real install leaves it, its calls then forgotten."""
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    rec.one_shot_modules.clear()
    return server_dir


def test_a_rebuild_imports_with_the_servers_down_before_the_new_build_starts(
    tmp_path: Path,
) -> None:
    """THE test: stopped, database up, importer run, and only then the new build started."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)

    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count(IMPORTER) == 1, rec.calls
    at = rec.calls.index
    assert at("build") < at("stop_servers") < at(IMPORTER) < at("recreate"), rec.calls
    assert at("start-db") < at(IMPORTER), rec.calls
    assert any("database updates" in line for line in said), said


def test_the_importer_is_given_the_modules_the_caller_allows(tmp_path: Path) -> None:
    """The Modules tab's own split (T78) reaches the importer, asked of this folder."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    asked: list[Path] = []

    def allowed(folder: Path) -> str:
        asked.append(folder)
        return "mod-a,mod-b"

    made = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
        importer_modules=allowed,
    )

    list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.one_shot_modules == ["mod-a,mod-b"], rec.one_shot_modules
    assert asked == [server_dir], asked


def test_with_no_module_list_the_importer_runs_as_the_compose_file_says(tmp_path: Path) -> None:
    """No list from the caller is no `-e` in argv: the plain `up` the update route runs."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)

    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.one_shot_modules == [None], rec.one_shot_modules


def test_an_importer_that_fails_puts_the_build_from_before_back(tmp_path: Path) -> None:
    """The new build never starts on half its SQL; the rollback starts the old one, once."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.one_shot_result = docker.AttachedRun(1, ("ERROR: Applying of file failed",))

    with pytest.raises(InstallerError, match="database updates") as caught:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count(IMPORTER) == 1, "the rollback must not import again"
    assert rec.calls.count("recreate") == 1, "only the rollback's start: the new build never began"
    assert rec.calls.index(IMPORTER) < rec.calls.index("recreate"), rec.calls
    assert "put back" in str(caught.value) or "rolled back" in str(caught.value), caught.value


def test_a_stop_during_the_import_is_honoured_once_the_import_is_in(tmp_path: Path) -> None:
    """Never killed part-way (a half-applied update ledger is worse than a late Stop)."""
    import threading

    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    cancel = threading.Event()
    real = rec.seams

    def seams(**overrides: object) -> native.Seams:
        made = real(**overrides)
        inner = made.one_shot

        def one_shot(service: str, folder: Path, **kwargs: object) -> docker.AttachedRun:
            assert kwargs.get("cancel") is None, "the import must not be cancellable part-way"
            cancel.set()
            return inner(service, folder, **kwargs)  # type: ignore[arg-type]

        from dataclasses import replace

        return replace(made, one_shot=one_shot)

    rec.seams = seams  # type: ignore[method-assign]

    with pytest.raises(InstallerError):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir), cancel=cancel))

    assert rec.calls.count(IMPORTER) == 1, rec.calls
    assert rec.calls.count("recreate") == 1, "the new build must not start after a Stop"


def test_an_update_to_latest_imports_once_not_twice(tmp_path: Path) -> None:
    """The update route already imports in its own window (T220); a Rebuild's adds nothing."""
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = "a" * 40
        rec.upstream[server_dir / source.dest] = "b" * 40
    rec.calls.clear()

    list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count(IMPORTER) == 1, rec.calls


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-unbound"])
def test_the_app_hands_a_rebuild_the_modules_tabs_own_split(tmp_path: Path, game: str) -> None:
    """ARAC's SQL is applied by this app itself, so the importer is not given it (T78)."""
    from yulon import install_wiring
    from yulon.catalog.catalog import load_catalog

    server_dir = tmp_path / "srv"
    for name in ("mod-arac", "mod-city-bots", "mod-playerbots"):
        (server_dir / "modules" / name).mkdir(parents=True)
    made = install_wiring.installer_for_app(load_catalog().get(game))
    allowed = made._importer_modules  # type: ignore[attr-defined]

    assert allowed is not None
    assert allowed(server_dir) == "mod-city-bots,mod-playerbots"


def test_a_game_with_no_importer_is_handed_no_module_list() -> None:
    from tests.test_families_cmangos import ENTRY as TBC
    from yulon import install_wiring

    assert install_wiring.importer_modules_for(TBC) is None


def test_the_updates_press_on_wotlk_names_the_presses_that_apply_module_sql(
    tmp_path: Path,
) -> None:
    """The refusal the live hand met ("carries no phase") now says what to press instead."""
    from yulon.ui.controller_view import MODULE_SQL_BUTTON_LABEL

    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)

    with pytest.raises(InstallerError) as caught:
        list(engine(rec).update_databases(InstallOptions(server_dir=server_dir)))

    said = str(caught.value)
    assert "carries no phase" in said, said
    assert f"“{MODULE_SQL_BUTTON_LABEL}”" in said, said
    assert native.MODULE_SQL_PRESS == MODULE_SQL_BUTTON_LABEL
    assert "Rebuild the server…" in said, said


def test_a_game_with_no_importer_is_not_sent_to_apply_module_sql(tmp_path: Path) -> None:
    from tests.test_families_cmangos import ENTRY as TBC
    from tests.test_families_cmangos import engine as tbc_engine

    made = tbc_engine(Recorder())
    if native.update_phases(TBC):
        pytest.skip("TBC's plan carries a re-runnable phase, so this refusal is not reached")
    with pytest.raises(InstallerError) as caught:
        list(made.update_databases(InstallOptions(server_dir=tmp_path / "srv")))
    assert "Apply module SQL" not in str(caught.value), caught.value
