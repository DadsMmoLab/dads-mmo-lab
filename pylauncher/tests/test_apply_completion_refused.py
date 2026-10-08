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
