"""Tests for `yulon.module_source`: a module derived from a link or a folder.

No Qt, no git, no Docker anywhere in this file. That is the point of the module
under test: every refusal a user can meet is a pure function over a string or a
directory read, so each refusal SENTENCE is asserted here rather than through a
display that would have to be driven to show it.

Two properties are load-bearing and each has its own test rather than being a
clause inside another one:

* **A refusal writes nothing.** Every sentence ends "Nothing on this machine was
  changed.", and `test_every_refusal_sentence_ends_with_nothing_changed_...`
  asserts both halves of that claim together — the words, and an empty user root
  afterwards. A sentence that says nothing changed while something did is worse
  than no sentence.
* **A persist cannot tear.** The item file appears at its name whole or not at
  all, for `write_clone_claim()`'s reason: this app writes the file, this app
  reads it back on every start, and a half file is a list entry the user cannot
  remove from inside the app.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from yulon import module_source
from yulon.manifest import ALLOWED_REPO_HOSTS, Manifest, parse_index, parse_manifest
from yulon.module_source import DeriveError

GAME = "wow-wotlk"
TODAY = date(2026, 9, 8)
NOTHING_CHANGED = "Nothing on this machine was changed."


def _link(text: str, **kwargs: Any) -> Manifest:
    return module_source.derive_link(text, GAME, today=TODAY, **kwargs)


def _folder(path: Path, **kwargs: Any) -> Manifest:
    return module_source.derive_folder(path, GAME, today=TODAY, **kwargs)


def _module_folder(root: Path, name: str, *, marker: str = "src") -> Path:
    """A directory that `derive_folder` accepts: named `mod-*`, with one marker."""
    folder = root / name
    (folder / marker).mkdir(parents=True)
    return folder


def _refused(call: Any) -> str:
    with pytest.raises(DeriveError) as exc:
        call()
    return str(exc.value)


# -- deriving from a link --------------------------------------------------


def test_a_link_yields_a_module_manifest_named_for_its_repository() -> None:
    """The id is the repository's basename: `.git` and any trailing `/` gone, lower-cased.

    rust-main's `module_key_from_url` (`modmgr.rs:198-207`), ported. Everything
    else on the manifest is the minimum a clone needs: it is a C++ module, so it
    needs a rebuild, and nothing about its contents is known until it is cloned.
    """
    manifest = _link("https://github.com/You/Mod-My-Thing.git/")

    assert manifest.id == "mod-my-thing"
    assert manifest.name == "mod-my-thing"
    assert manifest.type == "module"
    assert manifest.game == GAME
    assert manifest.description == "Custom module (cloned from a URL you provided)."
    assert manifest.source is not None
    assert manifest.source.repo == "https://github.com/You/Mod-My-Thing.git/"
    assert manifest.source.depth == 1
    assert manifest.source.branch is None and manifest.source.rev is None
    assert manifest.build.rebuild is True
    assert manifest.build.restart is False
    assert manifest.prompts == ()
    assert manifest.conf == () and manifest.sql == () and manifest.deploy == ()
    assert manifest.origin is not None
    assert manifest.origin.kind == "link"
    assert manifest.origin.path is None
    assert manifest.origin.added == "2026-09-08"
    assert manifest.notes == (
        "Derived by Yu'lon from https://github.com/You/Mod-My-Thing.git/ on 2026-09-08; "
        "nothing here was written by the module's author.",
    )


def test_an_owner_name_slug_is_a_github_link() -> None:
    """`Source` already means GitHub by a bare `owner/name`; the derive adds no second rule."""
    manifest = _link("  you/mod-my-thing  ")

    assert manifest.id == "mod-my-thing"
    assert manifest.source is not None
    assert manifest.source.repo == "you/mod-my-thing"
    assert manifest.source.url == "https://github.com/you/mod-my-thing.git"


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/you/mod-.git",  # rust-main's own vector: needs >=1 char after
        "https://github.com/you/mod-under_score",  # rust-main's own vector
        "https://github.com/you/tools",
        "https://github.com/you/mod-trailing-",  # `Slug` refuses a trailing hyphen
        "https://github.com/you/mod--doubled",  # and a doubled one
    ],
)
def test_a_link_whose_repository_is_not_named_mod_is_refused_by_name(repo: str) -> None:
    """rust-main's `valid_cpp_key` vectors (`modules.rs:389-397`), plus `Slug`'s own.

    `mod-UPPER`, rust-main's third vector, is deliberately NOT here: that vector
    belongs to `valid_cpp_key`, which validates a DIRECTORY name already on disk,
    and the derive from a URL runs `module_key_from_url` first — basename, `.git`
    stripped, **lower-cased** (`RUST modmgr.rs:198-207`) — so `mod-UPPER` reaches
    the pattern as `mod-upper` and is a module. The test above pins that. (The
    design page listed all three vectors under this test while also specifying the
    lower-casing; the two cannot both hold, and refusing a capital letter would
    refuse a real repository that clones perfectly well.)

    `mod-trailing-` and `mod--doubled` are this module's addition rather than
    rust-main's: the design asserted `_CUSTOM_ID` is a subset of `manifest.Slug`
    and it is not — `Slug` forbids a trailing or doubled hyphen — so without the
    second check those two reach the user as a pydantic `ValidationError` instead
    of a sentence.
    """
    message = _refused(lambda: _link(repo))

    assert "must be named mod-<something>" in message
    assert "https://github.com/you/mod-my-thing" in message
    assert message.endswith(NOTHING_CHANGED)


def test_a_link_off_the_allowed_hosts_is_refused_with_the_hosts_named() -> None:
    """The derive goes through `Source`, so README §3a's fence is the only fence.

    The sentence quotes `ALLOWED_REPO_HOSTS` rather than retyping the hosts, so
    widening the tuple widens the message with it and the two cannot drift.
    """
    message = _refused(lambda: _link("https://warez.example/mod-loot.git"))

    assert "https://warez.example/mod-loot.git is not a link this app can clone from" in message
    for host in ALLOWED_REPO_HOSTS:
        assert host in message
    assert "owner/name for github.com" in message
    assert message.endswith(NOTHING_CHANGED)


def test_an_empty_link_asks_for_one_rather_than_explaining_the_host_rule() -> None:
    assert _refused(lambda: _link("   ")) == f"Paste a link first. {NOTHING_CHANGED}"


def test_a_link_to_a_shipped_module_says_to_use_the_tab_instead() -> None:
    """The shadow rule at the press: a user file may never stand in front of a shipped id.

    The sentence names a control that is really there. It said "select it in the
    list and press Install selected" until T42 replaced the list and its two
    toolbar buttons with a card per family and an Install on each row -- the
    same defect FACT 4 records for the rebuild sentence, which sent its reader
    hunting for a button no tab had.

    Mutation: put either old spelling back and this fails on the word.
    """
    message = _refused(lambda: _link("azerothcore/mod-aoe-loot", shipped_ids=("mod-aoe-loot",)))

    assert message == (
        "mod-aoe-loot is a module this app already ships — find it on the Modules "
        f"tab and press Install on its row. {NOTHING_CHANGED}"
    )
    assert "Install selected" not in message


# -- deriving from a folder ------------------------------------------------


def test_a_folder_yields_a_module_manifest_with_no_source_and_a_folder_origin(
    tmp_path: Path,
) -> None:
    """A local path is not a clone URL, so it is an `Origin`, never a `Source`.

    `Source.url` feeds `git.CloneSpec`; `same_repo()`/`remote_url()`, which every
    ownership guard asks about a source, have no meaning for a copy. So the
    manifest records where it came from in a field the guards do not read.
    """
    folder = _module_folder(tmp_path, "Mod-My-Thing")

    manifest = _folder(folder)

    assert manifest.id == "mod-my-thing"
    assert manifest.name == "mod-my-thing"
    assert manifest.type == "module"
    assert manifest.source is None
    assert manifest.description == "Custom module (copied from a folder you provided)."
    assert manifest.origin is not None
    assert manifest.origin.kind == "folder"
    assert manifest.origin.path == str(folder)
    assert manifest.origin.added == "2026-09-08"
    assert manifest.build.rebuild is True
    assert manifest.notes == (
        f"Derived by Yu'lon from {folder} on 2026-09-08; "
        "nothing here was written by the module's author.",
    )


def test_a_folder_not_named_mod_is_refused_with_the_rename_instruction(tmp_path: Path) -> None:
    """The folder's own sentence, not the link's: there is no URL to paste again."""
    folder = _module_folder(tmp_path, "tools")

    message = _refused(lambda: _folder(folder))

    assert message == (
        "The folder is named 'tools', and a custom module must be named "
        "mod-<something> in lowercase letters, digits and hyphens — rename the "
        f"folder and choose it again. {NOTHING_CHANGED}"
    )


@pytest.mark.parametrize("marker", ["src", "conf", "data"])
def test_a_folder_with_none_of_src_conf_or_data_is_refused_and_one_with_any_is_not(
    tmp_path: Path, marker: str
) -> None:
    """A module contributes code, configuration or data. Any ONE of the three is enough.

    Over-tightening this to "src and conf" would refuse a data-only module, which
    AzerothCore has (mod-arac ships SQL + DBC + MPQ and no C++ at all —
    `RUST modmgr.rs:1822-1826`).
    """
    empty = tmp_path / "empty" / "mod-my-thing"
    empty.mkdir(parents=True)

    message = _refused(lambda: _folder(empty))
    assert message == (
        f"{empty} does not look like a module: it has no src, conf or data "
        f"folder. {NOTHING_CHANGED}"
    )

    ok = _module_folder(tmp_path / marker, "mod-my-thing", marker=marker)
    assert _folder(ok).id == "mod-my-thing"


def test_a_missing_folder_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    """Not-a-directory is asked FIRST: a file named `mod-x` is not a module folder."""
    gone = tmp_path / "mod-my-thing"
    assert _refused(lambda: _folder(gone)) == (
        f"{gone} is not a folder this app can read. {NOTHING_CHANGED}"
    )

    a_file = tmp_path / "mod-a-file"
    a_file.write_text("not a folder", encoding="utf-8")
    assert _refused(lambda: _folder(a_file)) == (
        f"{a_file} is not a folder this app can read. {NOTHING_CHANGED}"
    )


# -- completing a derived manifest from what the clone turned out to hold ---


def test_complete_finds_every_conf_dist_and_maps_it_to_the_modules_conf_dir(
    tmp_path: Path,
) -> None:
    """The shape all 20 shipped conf-bearing manifests use, discovered instead of declared.

    No keys are written: `Applier._conf()` copies the template into place when the
    target does not exist and stops there, which is what rust-main's "activate with
    defaults" did (`modmgr.rs:1848-1858`). Top level of `conf/` only — a
    `.conf.dist` two levels down is somebody's example, not the module's, and
    activating it would put a stranger's file into the server's etc.
    """
    clone = tmp_path / "mod-my-thing"
    (clone / "conf").mkdir(parents=True)
    (clone / "conf" / "zeta.conf.dist").write_text("Zeta = 1\n", encoding="utf-8")
    (clone / "conf" / "alpha.conf.dist").write_text("Alpha = 1\n", encoding="utf-8")
    (clone / "conf" / "readme.md").write_text("hi\n", encoding="utf-8")
    (clone / "conf" / "examples").mkdir()
    (clone / "conf" / "examples" / "buried.conf.dist").write_text("no\n", encoding="utf-8")

    done = module_source.complete(_link("you/mod-my-thing"), clone)

    assert [(c.file, c.template, c.keys) for c in done.conf] == [
        ("env/dist/etc/modules/alpha.conf", "conf/alpha.conf.dist", ()),
        ("env/dist/etc/modules/zeta.conf", "conf/zeta.conf.dist", ()),
    ]


def test_complete_maps_each_sql_directory_to_its_database_and_ignores_the_rest(
    tmp_path: Path,
) -> None:
    """`sql_dbdir_target` (`RUST modmgr.rs:2625-2633`), both spellings, verbatim.

    The glob is recursive where the shipped manifests' is flat: a derivation cannot
    know whether the author used `base/`/`updates/` subfolders, and what the glob
    decides is only what the REPORT lists — the importer reads the folder from disk.
    `applied_by` is `db-import` because applying a C++ module's SQL by hand breaks
    AzerothCore's own `updates` ledger (`SqlStep`'s docstring).
    """
    clone = tmp_path / "mod-my-thing"
    for name in ("db-world", "db_characters", "db-auth", "playerbots", "db-foo", "README"):
        (clone / "data" / "sql" / name).mkdir(parents=True)
    (clone / "data" / "sql" / "loose.sql").write_text("SELECT 1;", encoding="utf-8")

    done = module_source.complete(_link("you/mod-my-thing"), clone)

    assert [(s.db, s.path, s.applied_by, s.when) for s in done.sql] == [
        ("auth", "data/sql/db-auth/**/*.sql", "db-import", "install"),
        ("world", "data/sql/db-world/**/*.sql", "db-import", "install"),
        ("characters", "data/sql/db_characters/**/*.sql", "db-import", "install"),
        ("playerbots", "data/sql/playerbots/**/*.sql", "db-import", "install"),
    ]


def test_complete_keeps_the_id_type_and_game_it_was_given(tmp_path: Path) -> None:
    """`complete()` fills a manifest in; it never returns a manifest for another item.

    The applier refuses a hook whose answer changed the identity (lane B), and this
    is the other half of that contract: the hook does not change it.
    """
    clone = tmp_path / "mod-my-thing"
    clone.mkdir()
    before = _link("you/mod-my-thing")

    done = module_source.complete(before, clone)

    assert (done.id, done.type, done.game) == (before.id, before.type, before.game)
    assert done.source == before.source and done.origin == before.origin
    assert done.notes == before.notes
    assert done.conf == () and done.sql == ()

    folder_before = _folder(_module_folder(tmp_path / "src", "mod-my-thing"))
    folder_done = module_source.complete(folder_before, clone)
    assert folder_done.source is None
    assert folder_done.origin == folder_before.origin


# -- persisting ------------------------------------------------------------


def test_persist_writes_the_item_and_rebuilds_the_index_from_the_files(tmp_path: Path) -> None:
    """The index is DERIVED from the directory, never appended to.

    A crash between the item write and the index write then leaves a file the next
    persist picks up, rather than an index naming a file that is not there. It also
    makes a second persist of the same id idempotent instead of a duplicate entry.
    """
    module_source.persist(tmp_path, _link("you/mod-my-thing"), shipped_ids=())
    module_source.persist(tmp_path, _link("you/mod-other"), shipped_ids=())
    module_source.persist(tmp_path, _link("you/mod-my-thing"), shipped_ids=())

    items = tmp_path / GAME / "modules"
    assert sorted(p.name for p in items.glob("*.json")) == ["mod-my-thing.json", "mod-other.json"]

    index = parse_index(json.loads((tmp_path / GAME / "modules.json").read_text(encoding="utf-8")))
    assert index.game == GAME and index.type == "module"
    assert index.items == ("mod-my-thing", "mod-other")

    reread = parse_manifest(json.loads((items / "mod-my-thing.json").read_text(encoding="utf-8")))
    assert reread == _link("you/mod-my-thing")


def test_persist_is_atomic_and_a_torn_write_leaves_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written user manifest is a list entry nothing in the app can remove.

    It does not parse, so it is a row the user asked for and cannot have. Since
    T46 that costs the row and not the family — the store skips a user item it
    cannot load and names it — but the row is still gone, and a torn file is
    still a list entry nothing in the app can remove. So the file appears at its
    name whole or not at all: written beside it, then renamed over.
    `write_clone_claim()` made this argument first and this is the same shape.

    Before T46 the cost was the whole family: the store raised, and the tab drew
    `!! could not load modules: …` with every shipped module gone behind one
    broken custom file. That is what this atomicity was originally defending.
    """
    module_source.persist(tmp_path, _link("you/mod-my-thing"), shipped_ids=())
    items = tmp_path / GAME / "modules"
    before = (items / "mod-my-thing.json").read_bytes()

    torn: list[Path] = []

    def half(self: Path, data: str, **kwargs: Any) -> int:
        torn.append(self)
        self.write_bytes(data[: max(1, len(data) // 2)].encode("utf-8"))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", half)

    with pytest.raises(OSError):
        module_source.persist(tmp_path, _link("you/mod-my-thing"), shipped_ids=())

    assert (items / "mod-my-thing.json").read_bytes() == before
    assert sorted(p.name for p in items.iterdir()) == ["mod-my-thing.json"]
    # Beside the file it replaces, not somewhere else: a rename is atomic within
    # one filesystem and a copy across two, and a copy is the tearing avoided.
    assert [p.parent for p in torn] == [items]


def test_persist_refuses_a_shipped_id_and_writes_nothing(tmp_path: Path) -> None:
    """The shadow rule at the write as well as at the press — a file can arrive by hand."""
    message = _refused(
        lambda: module_source.persist(
            tmp_path, _link("azerothcore/mod-aoe-loot"), shipped_ids=("mod-aoe-loot",)
        )
    )

    assert message.startswith("mod-aoe-loot is a module this app already ships")
    assert message.endswith(NOTHING_CHANGED)
    assert not (tmp_path / GAME).exists()


def test_forget_removes_the_file_and_the_index_entry_and_says_whether_it_did(
    tmp_path: Path,
) -> None:
    """A custom manifest is a RECORD of something the user brought, not an OFFER.

    A shipped manifest stays listed whether or not it is installed. A record of a
    folder that is gone would be a list entry whose Install re-clones a link the
    user already decided against — so Remove forgets it. The boolean is what the
    view reads to decide whether the list needs reloading; it never reads
    `manifest.origin` itself.
    """
    kept = _link("you/mod-other")
    gone = _link("you/mod-my-thing")
    module_source.persist(tmp_path, kept, shipped_ids=())
    module_source.persist(tmp_path, gone, shipped_ids=())

    assert module_source.forget(tmp_path, gone) is True

    items = tmp_path / GAME / "modules"
    assert sorted(p.name for p in items.glob("*.json")) == ["mod-other.json"]
    index = parse_index(json.loads((tmp_path / GAME / "modules.json").read_text(encoding="utf-8")))
    assert index.items == ("mod-other",)

    assert module_source.forget(tmp_path, gone) is False
    assert module_source.forget(tmp_path, _link("azerothcore/mod-aoe-loot")) is False
    assert module_source.forget(tmp_path / "never-written", kept) is False


def test_every_refusal_sentence_ends_with_nothing_changed_and_no_file_exists(
    tmp_path: Path,
) -> None:
    """Every sentence makes the same promise, and this asserts the promise is kept.

    The derivation runs before any write, so "nothing was changed" is true of all
    of them — but the clause is only worth printing if a test says so, and a
    refusal path that persisted first would pass every OTHER test in this file.
    """
    user_root = tmp_path / "user"
    a_file = tmp_path / "mod-a-file"
    a_file.write_text("x", encoding="utf-8")
    empty = tmp_path / "mod-empty"
    empty.mkdir()

    refusals = [
        lambda: _link(""),
        lambda: _link("https://warez.example/mod-loot.git"),
        lambda: _link("you/tools"),
        lambda: _link("you/mod-aoe-loot", shipped_ids=("mod-aoe-loot",)),
        lambda: _folder(tmp_path / "gone"),
        lambda: _folder(a_file),
        lambda: _folder(tmp_path / "tools-folder"),
        lambda: _folder(empty),
        lambda: module_source.copy_folder(
            tmp_path / "srv" / "modules" / "a", tmp_path / "srv" / "modules" / "b"
        ),
        lambda: module_source.persist(
            user_root, _link("you/mod-aoe-loot"), shipped_ids=("mod-aoe-loot",)
        ),
    ]
    (tmp_path / "tools-folder").mkdir()

    for refusal in refusals:
        assert _refused(refusal).endswith(NOTHING_CHANGED)

    assert not user_root.exists()


# -- copying a folder ------------------------------------------------------


def test_copy_folder_replaces_an_existing_copy_and_never_carries_git_metadata(
    tmp_path: Path,
) -> None:
    """A copy is a SNAPSHOT, not a checkout.

    `.git` is left behind deliberately: 8.7a's update check asks `git` about every
    folder under `modules/`, and a copy carrying a stranger's `.git` would report a
    commit count against a remote the user never chose. Without it the same folder
    reports `not a git checkout — nothing to compare`, which is the truth.

    And the destination is REPLACED rather than merged into: choosing the same
    folder again is how a newer version of it is brought over, and a merge would
    leave files the newer version deleted sitting in the module.
    """
    src = tmp_path / "mod-my-thing"
    (src / "src").mkdir(parents=True)
    (src / "src" / "kept.cpp").write_text("new\n", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    dest = tmp_path / "server" / "modules" / "mod-my-thing"
    dest.mkdir(parents=True)
    (dest / "stale.cpp").write_text("old\n", encoding="utf-8")

    module_source.copy_folder(src, dest)

    assert (dest / "src" / "kept.cpp").read_text(encoding="utf-8") == "new\n"
    assert not (dest / "stale.cpp").exists()
    assert not (dest / ".git").exists()


def test_copy_folder_refuses_a_source_inside_the_destination(tmp_path: Path) -> None:
    """A module is copied INTO the modules folder, never out of one and back in.

    The refusal covers the folder itself (copying `modules/mod-x` onto itself would
    delete it and then copy from what is no longer there) and any neighbour under
    the same `modules/`.
    """
    modules = tmp_path / "server" / "modules"
    (modules / "mod-my-thing" / "src").mkdir(parents=True)
    dest = modules / "mod-copy"

    for src in (modules / "mod-my-thing", modules, dest):
        message = _refused(lambda src=src: module_source.copy_folder(src, dest))  # type: ignore[misc]
        assert message == (
            f"{src} is already inside this server's modules folder — a module is "
            f"copied into it, not from it. {NOTHING_CHANGED}"
        )

    assert not dest.exists()
