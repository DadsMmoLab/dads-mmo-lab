"""T612: a Tortoise server puts its two client addons into the game client by itself.

Driven through the shipped manifests and the real `Applier` over the same fake checkout the
T30 addon tests use, so what is asserted is what lands in the client's `Interface/AddOns`
and in the server folder, not what a helper was called with.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_tortoise_modules import ADDONS, _a_release, _AddonGit, _RecordingSql
from yulon import default_addons
from yulon.apply import Applier, ApplyError, ApplyReport
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.manifest import Manifest

IDS = ("tortoise-bots-manager", "tortoise-gm-manager")


class _Git(_AddonGit):
    """One fake that writes whichever addon is being cloned, by the folder it is cloned to."""

    def __init__(self) -> None:
        super().__init__("", ())

    def clone(self, spec: object) -> None:
        item = Path(spec.dest).name  # type: ignore[attr-defined]
        _folder, self.toc, self.files = ADDONS[item]
        super().clone(spec)


class _Spy(Applier):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.calls: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    def install(self, manifest: Manifest, *args: object, **kwargs: object) -> ApplyReport:
        self.calls.append(("install", manifest.id))
        if manifest.id in self.fail:
            raise ApplyError(f"{manifest.id} could not be cloned")
        return super().install(manifest, *args, **kwargs)  # type: ignore[arg-type]

    def update(self, manifest: Manifest, *args: object, **kwargs: object) -> ApplyReport:
        self.calls.append(("update", manifest.id))
        return ApplyReport("update", manifest.id, family="mod")


def _manifests() -> list[Manifest]:
    return [tortoise_modules.store().load("mod", item) for item in IDS]


class _Box:
    def __init__(self, tmp_path: Path, *, client: bool = True) -> None:
        self.server = tmp_path / "server"
        self.server.mkdir()
        self.client = tmp_path / "TurtleWoW"
        (self.client / "Interface" / "AddOns").mkdir(parents=True)
        self.sql = _RecordingSql()
        self.applier = _Spy(
            self.server,
            git=_Git(),
            sql=self.sql,
            client_dir=self.client if client else None,
            newest_release=_a_release,
        )

    def installed(self) -> set[str]:
        return {
            folder
            for folder, _toc, _files in ADDONS.values()
            if (self.client / "Interface" / "AddOns" / folder / f"{folder}.toc").is_file()
        }

    def put_in(self, **kwargs: object) -> default_addons.Outcome:
        return default_addons.put_in(self.server, self.applier, _manifests(), IDS, **kwargs)  # type: ignore[arg-type]


def test_a_new_install_gets_both_addons_in_the_client(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    out = box.put_in()
    assert box.installed() == {"TortoiseBotsManager", "TortoiseGMManager"}
    assert out.installed == IDS
    assert out.failed == {}


def test_an_addon_already_there_and_not_behind_is_left_alone(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    box.applier.calls.clear()
    out = box.put_in()
    assert box.applier.calls == []
    assert out.installed == () and out.updated == ()


def test_an_addon_that_is_behind_is_updated_at_play_and_the_other_is_not(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    box.applier.calls.clear()
    out = box.put_in(behind={"tortoise-gm-manager"})
    assert box.applier.calls == [("update", "tortoise-gm-manager")]
    assert out.updated == ("tortoise-gm-manager",)


def test_a_removal_is_remembered_and_play_never_puts_the_addon_back(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    gm = tortoise_modules.store().load("mod", "tortoise-gm-manager")
    report = box.applier.remove(gm)
    default_addons.remember(box.server, report, IDS)
    box.applier.calls.clear()
    out = box.put_in(behind={"tortoise-gm-manager"})
    assert box.applier.calls == []
    assert out.installed == () and out.updated == ()
    assert not (box.server / "sql_scripts" / "clones" / "tortoise-gm-manager").exists()


def test_the_memory_is_per_server(tmp_path: Path) -> None:
    mine, yours = tmp_path / "a", tmp_path / "b"
    mine.mkdir()
    yours.mkdir()
    default_addons.decline(mine, "tortoise-gm-manager")
    assert default_addons.declined(mine) == {"tortoise-gm-manager"}
    assert default_addons.declined(yours) == frozenset()


def test_installing_one_by_hand_lifts_its_decline(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    default_addons.decline(box.server, "tortoise-gm-manager")
    gm = tortoise_modules.store().load("mod", "tortoise-gm-manager")
    default_addons.remember(box.server, box.applier.install(gm), IDS)
    assert default_addons.declined(box.server) == frozenset()


def test_another_modules_report_changes_nothing(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    default_addons.remember(box.server, ApplyReport("remove", "mod-arac", family="module"), IDS)
    default_addons.remember(box.server, ApplyReport("update", "tortoise-gm-manager", family="mod"), IDS)
    assert default_addons.declined(box.server) == frozenset()


def test_nothing_server_side_is_written_or_restarted(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    out = box.put_in()
    assert box.sql.statements == [] and box.sql.files == []
    assert out.restart_recommended is False and out.rebuild_required is False
    assert sorted(p.name for p in box.server.iterdir()) == ["sql_scripts"]


def test_with_no_client_folder_nothing_is_cloned_at_all(tmp_path: Path) -> None:
    box = _Box(tmp_path, client=False)
    out = box.put_in()
    assert box.applier.calls == []
    assert not (box.server / "sql_scripts").exists()
    assert out.installed == ()


def test_one_addon_failing_does_not_stop_the_other_or_raise(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.applier.fail.add("tortoise-bots-manager")
    out = box.put_in()
    assert "tortoise-bots-manager" in out.failed
    assert out.installed == ("tortoise-gm-manager",)
    assert box.installed() == {"TortoiseGMManager"}


def test_a_memory_file_nobody_can_read_installs_nothing(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    (box.server / default_addons.DECLINED_FILE).write_text("{not json", encoding="utf-8")
    out = box.put_in()
    assert box.applier.calls == []
    assert out.installed == ()


@pytest.mark.parametrize("junk", ['["x"]', '{"declined": "x"}', '{"declined": [1, "a/b"]}'])
def test_a_memory_file_of_the_wrong_shape_reads_as_unreadable(tmp_path: Path, junk: str) -> None:
    (tmp_path / default_addons.DECLINED_FILE).write_text(junk, encoding="utf-8")
    assert default_addons.declined(tmp_path) is None


def test_the_memory_is_written_whole_and_names_only_ids(tmp_path: Path) -> None:
    default_addons.decline(tmp_path, "tortoise-gm-manager")
    default_addons.decline(tmp_path, "tortoise-bots-manager")
    data = json.loads((tmp_path / default_addons.DECLINED_FILE).read_text(encoding="utf-8"))
    assert data == {"declined": ["tortoise-bots-manager", "tortoise-gm-manager"]}
    assert [p.name for p in tmp_path.iterdir()] == [default_addons.DECLINED_FILE]


def test_tortoise_names_exactly_these_two_as_its_defaults() -> None:
    assert tortoise_modules.DEFAULT_ADDONS == IDS


# ------------------------------------------------------ keep_in_step: the Play-time entry


def _row(key: str, behind: object, family: str = "mod") -> object:
    from yulon.apply import ModuleUpdate

    return ModuleUpdate(key=key, path=Path(key), is_checkout=True, behind=behind, family=family)  # type: ignore[arg-type]


def test_keep_in_step_updates_what_the_cached_count_says_is_behind(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    box.applier.calls.clear()
    rows = [_row("tortoise-bots-manager", 0), _row("tortoise-gm-manager", 3)]
    out = default_addons.keep_in_step(
        box.server, box.applier, _manifests(), IDS, updates=lambda: rows
    )
    assert box.applier.calls == [("update", "tortoise-gm-manager")]
    assert out.updated == ("tortoise-gm-manager",)


def test_keep_in_step_ignores_a_module_of_the_same_name_in_another_family(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    box.applier.calls.clear()
    default_addons.keep_in_step(
        box.server,
        box.applier,
        _manifests(),
        IDS,
        updates=lambda: [_row("tortoise-gm-manager", 3, family="module")],
    )
    assert box.applier.calls == []


def test_keep_in_step_survives_a_count_that_could_not_be_asked(tmp_path: Path) -> None:
    box = _Box(tmp_path)

    def broken() -> list[object]:
        raise OSError("no network")

    out = default_addons.keep_in_step(box.server, box.applier, _manifests(), IDS, updates=broken)
    assert out.installed == IDS


def test_a_failure_note_does_not_claim_an_install_when_it_was_an_update(tmp_path: Path) -> None:
    out = default_addons.Outcome(failed={"tortoise-gm-manager": "no network"})
    (line,) = out.notes()
    assert "Tortoise GM Manager" in line and "no network" in line
