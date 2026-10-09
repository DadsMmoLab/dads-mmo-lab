"""T596: a completion refused on a FIRST install takes the new folder back.

A manifest derived from a link or a folder is finished from what landed
(`Completer`), and on Tortoise that is where "this is not something Yu'lon can
install" is found out. Before T596 a raise there left the fresh clone or copy in
place under `sql_scripts/clones/` or `modules/`, so the refusal's "Nothing was
changed" was false and the next press met "already has files in it". On a first
install there was nothing at the path, so taking the folder back makes it true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon.apply import Applier, ApplyRefusal, CompletionRefused, FolderSource, write_clone_claim
from yulon.git import CloneSpec
from yulon.manifest import Manifest, parse_manifest

ITEM = "mobstats"


class _Clone:
    def clone(self, spec: CloneSpec) -> None:
        (spec.dest / "src").mkdir(parents=True)
        (spec.dest / "src" / "core.cpp").write_text("int x;\n", encoding="utf-8")
        (spec.dest / ".git").mkdir()


def _manifest(*, source: bool) -> Manifest:
    data: dict[str, object] = {
        "schema_version": 1,
        "id": ITEM,
        "name": "MobStats",
        "type": "mod",
        "game": "wow-tortoise",
        "build": {"rebuild": False, "restart": False},
    }
    if source:
        data["source"] = {"repo": "refaim/MobStats"}
    return parse_manifest(data)


def _refuse(manifest: Manifest, clone: Path) -> Manifest:
    raise CompletionRefused("MobStats holds C++ source in src/, which is a server module.")


def _copier(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True)
    (dest / "src").mkdir()


def test_a_refused_completion_of_a_first_clone_takes_the_clone_back(tmp_path: Path) -> None:
    applier = Applier(tmp_path, git=_Clone())
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(_manifest(source=True), complete=_refuse)
    clone = tmp_path / "sql_scripts" / "clones" / ITEM
    assert not clone.exists()
    assert str(refused.value) == (
        "MobStats holds C++ source in src/, which is a server module. Nothing was changed."
    )


def test_a_refused_completion_of_a_first_copy_takes_the_copy_back(tmp_path: Path) -> None:
    applier = Applier(tmp_path)
    source = tmp_path / "MobStats"
    source.mkdir()
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(
            _manifest(source=False), folder=FolderSource(source, _copier), complete=_refuse
        )
    assert not (tmp_path / "sql_scripts" / "clones" / ITEM).exists()
    assert str(refused.value).endswith("Nothing was changed.")


def test_any_failure_of_a_first_completion_takes_the_folder_back_and_keeps_its_type(
    tmp_path: Path,
) -> None:
    def broken(manifest: Manifest, clone: Path) -> Manifest:
        raise ValueError("a completer bug")

    applier = Applier(tmp_path, git=_Clone())
    with pytest.raises(ValueError, match="a completer bug"):
        applier.install(_manifest(source=True), complete=broken)
    assert not (tmp_path / "sql_scripts" / "clones" / ITEM).exists()


def test_a_refused_completion_over_a_folder_that_was_there_keeps_it_and_says_so(
    tmp_path: Path,
) -> None:
    """Not a first install: the folder was this app's before, and is not deleted now."""
    clone = tmp_path / "sql_scripts" / "clones" / ITEM
    clone.mkdir(parents=True)
    write_clone_claim(clone, item_id=ITEM, url="", completed=True)
    applier = Applier(tmp_path)
    source = tmp_path / "MobStats"
    source.mkdir()

    def copier(src: Path, dest: Path) -> None:
        (dest / "src").mkdir(exist_ok=True)

    with pytest.raises(ApplyRefusal) as refused:
        applier.install(
            _manifest(source=False), folder=FolderSource(source, copier), complete=_refuse
        )
    assert clone.is_dir()
    said = str(refused.value)
    assert "Nothing was changed" not in said
    assert "sql_scripts/clones/mobstats" in said


def test_a_first_install_refused_after_its_completion_takes_its_folder_back_too(
    tmp_path: Path,
) -> None:
    """Codex review: the running-world refusal comes after the completion; the folder goes too."""

    class _Sql:
        def run_file(self, db: str, path: Path) -> None:
            raise AssertionError("nothing may be sent")

        def run_statement(self, db: str, statement: str) -> None:
            raise AssertionError("nothing may be sent")

    def with_sql(manifest: Manifest, clone: Path) -> Manifest:
        (clone / "a.sql").write_text("DELETE FROM x;\n", encoding="utf-8")
        return parse_manifest({**manifest.model_dump(), "sql": [{"db": "world", "path": "a.sql"}]})

    applier = Applier(tmp_path, git=_Clone(), sql=_Sql(), world_running=lambda: True)
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(_manifest(source=True), complete=with_sql)
    assert not (tmp_path / "sql_scripts" / "clones" / ITEM).exists()
    assert "world server is running" in str(refused.value)
    assert str(refused.value).endswith(
        "sql_scripts/clones/mobstats, which this press had just made, was taken back."
    )


def test_a_first_install_that_failed_while_sending_sql_keeps_its_folder(tmp_path: Path) -> None:
    """Once anything went to the database, the folder and its record stay for Remove."""
    from yulon.apply import ApplyError

    class _Sql:
        def run_file(self, db: str, path: Path) -> None:
            raise ApplyError("ERROR 1146: no such table")

        def run_statement(self, db: str, statement: str) -> None:
            raise ApplyError("ERROR 1146: no such table")

    def with_sql(manifest: Manifest, clone: Path) -> Manifest:
        (clone / "a.sql").write_text("DELETE FROM x;\n", encoding="utf-8")
        return parse_manifest({**manifest.model_dump(), "sql": [{"db": "world", "path": "a.sql"}]})

    applier = Applier(tmp_path, git=_Clone(), sql=_Sql(), world_running=lambda: False)
    with pytest.raises(ApplyError):
        applier.install(_manifest(source=True), complete=with_sql)
    assert (tmp_path / "sql_scripts" / "clones" / ITEM).is_dir()


def test_a_wotlk_first_install_refused_after_completion_forgets_its_record(
    tmp_path: Path,
) -> None:
    """The WotLK binding persists in `complete()`; a folder taken back takes the record too."""
    import os

    from yulon.controller_wow_wotlk import modules as wotlk_modules

    target = tmp_path / "elsewhere.txt"
    target.write_text("not the module's\n", encoding="utf-8")

    class _LinkedConf:
        def clone(self, spec: CloneSpec) -> None:
            (spec.dest / "conf").mkdir(parents=True)
            os.symlink(target, spec.dest / "conf" / "linked.conf.dist")
            (spec.dest / ".git").mkdir()

    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, git=_LinkedConf())
    manifest = wotlk_modules.derive_link("https://github.com/you/mod-linked-conf")
    with pytest.raises(ApplyRefusal) as refused:
        wotlk_modules.install_custom(applier)(manifest, None)
    assert "was taken back" in str(refused.value)
    assert not (server / "modules" / "mod-linked-conf").exists()
    assert "mod-linked-conf" not in {m.id for m in wotlk_modules.store().load_all("module")}


def test_a_failed_install_keeps_a_record_that_was_there_before_the_press(tmp_path: Path) -> None:
    """Codex review: only the record THIS press's completion wrote is taken back."""
    from yulon import module_source
    from yulon.apply import ApplyError
    from yulon.controller_wow_wotlk import modules as wotlk_modules
    from yulon.git import GitError

    class _Unreachable:
        def clone(self, spec: CloneSpec) -> None:
            raise GitError("could not reach github.com")

    server = tmp_path / "server"
    server.mkdir()
    manifest = wotlk_modules.derive_link("https://github.com/you/mod-earlier")
    module_source.persist(
        wotlk_modules.user_manifests_dir(), manifest, shipped_ids=wotlk_modules.shipped_ids()
    )
    applier = Applier(server, git=_Unreachable())
    with pytest.raises(ApplyError):
        wotlk_modules.install_custom(applier)(manifest, None)
    assert "mod-earlier" in {m.id for m in wotlk_modules.store().load_all("module")}
