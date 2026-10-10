"""T612: a Tortoise server puts its two client addons into the game client by itself.

Driven through the shipped manifests and the real `Applier` over the same fake checkout the
T30 addon tests use, so what is asserted is what lands in the client's `Interface/AddOns`
and in the server folder, not what a helper was called with.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_tortoise_modules import ADDONS, _a_release, _AddonGit, _RecordingSql
from yulon import default_addons, play_client
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
        raise AssertionError("Play and a fresh install never update an addon")


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

    def put_in(self, *, clone: bool = True, **kwargs: object) -> default_addons.Outcome:
        return default_addons.put_in(
            self.server, self.applier, _manifests(), IDS, clone=clone, **kwargs  # type: ignore[arg-type]
        )

    def addon_dir(self, item: str) -> Path:
        return self.client / "Interface" / "AddOns" / ADDONS[item][0]


def test_a_new_install_gets_both_addons_in_the_client(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    out = box.put_in()
    assert box.installed() == {"TortoiseBotsManager", "TortoiseGMManager"}
    assert out.installed == IDS
    assert out.failed == {}


def test_an_addon_already_there_is_left_alone(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    box.applier.calls.clear()
    out = box.put_in()
    assert box.applier.calls == []
    assert out.installed == () and out.restored == ()


def test_play_never_clones_an_addon_that_has_no_clone(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    out = box.put_in(clone=False)
    assert box.applier.calls == []
    assert out.installed == () and box.installed() == set()
    assert not (box.server / "sql_scripts").exists()


def test_a_client_made_again_gets_its_addons_back_from_the_clones_at_play(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    shutil.rmtree(box.client / "Interface")
    (box.client / "Interface" / "AddOns").mkdir(parents=True)
    assert box.installed() == set()
    box.applier.calls.clear()
    out = box.put_in(clone=False)
    assert box.installed() == {"TortoiseBotsManager", "TortoiseGMManager"}
    assert out.restored == IDS and out.installed == ()
    assert box.applier.calls == [], "putting files back must not go through install or update"
    assert (box.addon_dir("tortoise-gm-manager") / "assets" / "icon.tga").is_file()


def test_an_addon_folder_deleted_by_hand_comes_back_but_an_edited_file_stays(
    tmp_path: Path,
) -> None:
    box = _Box(tmp_path)
    box.put_in()
    shutil.rmtree(box.addon_dir("tortoise-bots-manager"))
    edited = box.addon_dir("tortoise-gm-manager") / "Core.lua"
    edited.write_text("-- the player's own change\n", encoding="utf-8")
    (box.addon_dir("tortoise-gm-manager") / "Lookup.lua").unlink()
    out = box.put_in(clone=False)
    assert out.restored == IDS
    assert (box.addon_dir("tortoise-bots-manager") / "Core.lua").is_file()
    assert (box.addon_dir("tortoise-gm-manager") / "Lookup.lua").is_file()
    assert edited.read_text(encoding="utf-8") == "-- the player's own change\n"


def test_nothing_is_put_back_when_nothing_is_missing(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    assert box.put_in(clone=False).restored == ()


def test_a_removed_addon_is_not_put_back_even_when_its_files_are_missing(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    default_addons.decline(box.server, "tortoise-gm-manager")
    shutil.rmtree(box.addon_dir("tortoise-gm-manager"))
    out = box.put_in(clone=False)
    assert out.restored == ()
    assert not box.addon_dir("tortoise-gm-manager").exists()


def test_a_file_put_back_never_goes_through_a_link(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    (box.client / play_client.MARKER).write_text("{}", encoding="utf-8")  # a ready-to-play client
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(box.addon_dir("tortoise-gm-manager"))
    box.addon_dir("tortoise-gm-manager").symlink_to(elsewhere, target_is_directory=True)
    out = box.put_in(clone=False)
    assert "tortoise-gm-manager" in out.failed
    assert list(elsewhere.iterdir()) == []
    assert default_addons._read(box.server) == (frozenset(), {}), "Play notes no failure"


def test_a_removal_is_remembered_and_play_never_puts_the_addon_back(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.put_in()
    gm = tortoise_modules.store().load("mod", "tortoise-gm-manager")
    report = box.applier.remove(gm)
    default_addons.remember(box.server, report, IDS)
    box.applier.calls.clear()
    out = box.put_in(clone=False)
    assert box.applier.calls == []
    assert out.installed == () and out.restored == ()
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
    default_addons.remember(
        box.server, ApplyReport("update", "tortoise-gm-manager", family="mod"), IDS
    )
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


# ------------------------------------------------------ a failed install is not retried at once


def test_a_failed_install_is_not_tried_again_within_the_day_and_is_after_it(tmp_path: Path) -> None:
    box = _Box(tmp_path)
    box.applier.fail.add("tortoise-bots-manager")
    clock = [1_000_000.0]
    box.put_in(now=lambda: clock[0])
    assert box.applier.calls.count(("install", "tortoise-bots-manager")) == 1
    clock[0] += 3600
    out = box.put_in(now=lambda: clock[0])
    assert box.applier.calls.count(("install", "tortoise-bots-manager")) == 1
    assert out.failed == {}
    clock[0] += default_addons.FAILED_BACKOFF_SECONDS
    box.applier.fail.clear()
    out = box.put_in(now=lambda: clock[0])
    assert out.installed == ("tortoise-bots-manager",)
    assert default_addons._read(box.server) == (frozenset(), {})


def test_the_play_log_names_the_addons_in_words() -> None:
    out = default_addons.Outcome(
        installed=("tortoise-bots-manager",), restored=("tortoise-gm-manager",)
    )
    assert out.notes() == (
        "Put TortoiseBots Manager into your game client.",
        "Put the missing files of Tortoise GM Manager back into your game client.",
    )


def test_an_unreadable_memory_warns_in_the_words_of_what_was_asked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    box = _Box(tmp_path)
    (box.server / default_addons.DECLINED_FILE).write_text("{not json", encoding="utf-8")
    box.put_in()
    box.put_in(what="outside add-on")
    texts = [r.getMessage() for r in caplog.records]
    assert any("no default add-on is put in" in t for t in texts)
    assert any("no outside add-on is put in" in t for t in texts)
