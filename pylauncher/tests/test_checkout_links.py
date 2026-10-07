"""T530: Yu'lon never reads or writes through a link in a module's own checkout.

A module repository may hold symlinks, and "Install from link..." lets any
repository be a module. A link in the checkout pointing outside it (`secret ->
/home/<user>/.ssh/id_rsa`, `up -> ../../..`) used to be followed by every step
that reads the checkout: the server deploy, the client copy, the conf template,
the SQL, the DBC copy and an in-checkout patch, which writes. So the rule
(`Applier._refuse_checkout_links()`): before an install, configure or remove
changes anything, every path the manifest names in the checkout is looked at
without following anything, and a link there, inside or outside the checkout,
refuses the action, naming the module, the link and where it points.

Every test drives the real `Applier` over a checkout `_LinkedGit` writes into
real folders under `tmp_path`, with its links made after. The assertion is the
one that matters: the folder outside the checkout is byte for byte what it was,
and nothing of it reached the server folder, the client, or the database.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.test_apply import LUA, _FakeDbc, _FakeGit, _FakeSql, _have_requirements
from yulon import apply as apply_module
from yulon import links
from yulon.apply import Applier, ApplyError, ApplyRefusal
from yulon.git import CloneSpec
from yulon.manifest import Manifest, parse_manifest

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")

SECRET = b"the player's private key"


class _LinkedGit(_FakeGit):
    """`_FakeGit`, then the links `made` (path in the checkout -> target text) on top."""

    def __init__(self, files: dict[str, str], made: dict[str, str]) -> None:
        super().__init__(files)
        self.made = made

    def clone(self, spec: CloneSpec) -> None:
        super().clone(spec)
        for rel, target in self.made.items():
            link = spec.dest / rel
            link.parent.mkdir(parents=True, exist_ok=True)
            here = link.parent / target
            if link.is_dir() and not link.is_symlink():  # a folder of the module, replaced
                shutil.rmtree(link)
            elif os.path.lexists(link):  # a file the module ships, replaced by the link
                link.unlink()
            try:
                os.symlink(target, link, target_is_directory=here.is_dir())
            except OSError:
                pytest.skip("this account may not make symlinks")


def _outside(tmp_path: Path) -> Path:
    """A folder beside the server, the player's own, with a private file in it."""
    outside = tmp_path / "home"
    (outside / ".ssh").mkdir(parents=True)
    (outside / ".ssh" / "id_rsa").write_bytes(SECRET)
    (outside / "notes.lua").write_bytes(SECRET)
    # Valid SQL holding the secret, so only the look stops it (a refusal of bad SQL would not).
    (outside / "stolen.sql").write_bytes(
        b"UPDATE t SET note = '" + SECRET.replace(b"'", b"''") + b"';\n"
    )
    return outside


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    """Every name under `folder` and every file's bytes, never following a link."""
    found: dict[str, bytes | None] = {}
    for here, dirs, files in os.walk(folder):
        for name in dirs:
            found[os.path.relpath(os.path.join(here, name), folder)] = None
        for name in files:
            path = Path(here) / name
            found[os.path.relpath(path, folder)] = None if path.is_symlink() else path.read_bytes()
    return found


def _holds_secret(folder: Path) -> list[str]:
    """Every file under `folder`, followed or not, whose bytes are the outside file's."""
    if not folder.exists():
        return []
    return [
        str(path)
        for path in folder.rglob("*")
        if path.is_file() and not path.is_symlink() and path.read_bytes() == SECRET
    ]


MODULE: dict[str, Any] = {
    "id": "mod-linked",
    "name": "Linked",
    "type": "module",
    "game": "wow-wotlk",
    "source": {"repo": "someone/mod-linked"},
    "build": {"rebuild": True},
    "deploy": [{"src": "lua", "dest": f"{LUA}/"}],
    "client": [{"src": "Interface/AddOns/Linked", "dest": "addons", "name": "Linked"}],
    "conf": [
        {
            "file": "env/dist/etc/modules/linked.conf",
            "template": "conf/linked.conf.dist",
            "keys": [],
        }
    ],
    "sql": [{"db": "world", "path": "sql/world.sql", "applied_by": "direct"}],
    "server_dbc": [{"src": "dbc"}],
}

FILES = {
    "lua/linked.lua": "-- lua",
    "Interface/AddOns/Linked/Linked.toc": "## Title: Linked",
    "conf/linked.conf.dist": "Linked.Enable = 1\n",
    "sql/world.sql": "SELECT 1;\n",
    "dbc/Spell.dbc": "dbc",
    "README.md": "readme",
}


class _Run:
    """One install of `MODULE` over a checkout holding `made`, and what it touched."""

    def __init__(self, tmp_path: Path, made: dict[str, str], manifest: Manifest | None = None):
        self.outside = _outside(tmp_path)
        self.before = _snapshot(self.outside)
        self.server = tmp_path / "server"
        self.client = tmp_path / "client"
        (self.client / "Interface" / "AddOns").mkdir(parents=True)
        self.manifest = manifest if manifest is not None else parse_manifest(MODULE)
        _have_requirements(self.server, self.manifest)
        self.sql = _FakeSql()
        self.dbc = _FakeDbc()
        made = {rel: target.replace("{outside}", str(self.outside)) for rel, target in made.items()}
        self.git = _LinkedGit(FILES, made)
        self.applier = Applier(
            self.server, git=self.git, sql=self.sql, client_dir=self.client, dbc=self.dbc
        )

    def refused(self, *named: str) -> str:
        with pytest.raises(ApplyRefusal) as refused:
            self.applier.install(self.manifest)
        said = str(refused.value)
        assert self.manifest.id in said, said
        for text in named:
            assert text in said, said
        self.nothing_reached()
        return said

    def nothing_reached(self) -> None:
        assert _snapshot(self.outside) == self.before
        assert not (self.server / LUA).exists(), "something was deployed"
        assert not (self.server / "env/dist/etc/modules/linked.conf").exists()
        assert not (self.client / "Interface" / "AddOns" / "Linked").exists()
        assert self.sql.files == [] and self.sql.statements == []
        assert self.dbc.dirs == []
        assert _holds_secret(self.server / LUA) == []
        assert _holds_secret(self.client) == []


# ------------------------------------------------- a link outside the checkout


@pytest.mark.parametrize(
    ("link", "target"),
    [
        pytest.param("lua/secret.lua", "{outside}/notes.lua", id="deploy: a file"),
        pytest.param("lua/ssh", "{outside}/.ssh", id="deploy: a folder"),
        pytest.param("lua/up", "../../../..", id="deploy: up the tree"),
        pytest.param("Interface/AddOns/Linked/key", "{outside}/.ssh/id_rsa", id="client: a file"),
        pytest.param("Interface/AddOns/Linked/home", "{outside}", id="client: a folder"),
        pytest.param("conf/linked.conf.dist", "{outside}/notes.lua", id="conf template"),
        pytest.param("sql/world.sql", "{outside}/notes.lua", id="sql file"),
        pytest.param("dbc/Key.dbc", "{outside}/.ssh/id_rsa", id="dbc file"),
        pytest.param("lua/sub/deep/secret.lua", "{outside}/notes.lua", id="deploy: deep down"),
        pytest.param(
            "Interface/AddOns/Linked/sub/deep/key", "{outside}/.ssh/id_rsa", id="client: deep down"
        ),
    ],
)
def test_a_link_leaving_the_checkout_is_refused_before_anything_is_done(
    tmp_path: Path, link: str, target: str
) -> None:
    run = _Run(tmp_path, {link: target})
    said = run.refused(link, "outside the module's own files")
    assert "Nothing of mod-linked was deployed, run or put into your game client." in said


def test_a_linked_folder_on_the_way_to_a_named_file_is_refused(tmp_path: Path) -> None:
    """`deploy: lua/x.lua` where `lua` itself is a link: the folder on the way is the link."""
    manifest = parse_manifest(
        {**MODULE, "deploy": [{"src": "scripts/notes.lua", "dest": f"{LUA}/"}]}
    )
    run = _Run(tmp_path, {"scripts": "{outside}"}, manifest)
    run.refused("scripts", "outside the module's own files")


def test_an_in_checkout_patch_through_a_link_is_refused_and_the_target_unchanged(
    tmp_path: Path,
) -> None:
    """A patch `in_clone` WRITES: through a link it would rewrite the player's file."""
    manifest = parse_manifest(
        {
            **MODULE,
            "patches": [
                {
                    "file": "src/linked.lua",
                    "find": "the",
                    "replace": "a",
                    "in_clone": True,
                    "when": "install",
                }
            ],
        }
    )
    run = _Run(tmp_path, {"src/linked.lua": "{outside}/notes.lua"}, manifest)
    run.refused("src/linked.lua")
    assert (run.outside / "notes.lua").read_bytes() == SECRET


# ----------------------------------------------- a link inside the checkout


@pytest.mark.parametrize(
    ("link", "target"),
    [
        pytest.param("lua/again.lua", "linked.lua", id="deploy: a twin file"),
        pytest.param("Interface/AddOns/Linked/up", "..", id="client: a loop"),
        pytest.param("lua/readme", "../README.md", id="deploy: another file of the module"),
    ],
)
def test_a_link_inside_the_checkout_is_refused_too(tmp_path: Path, link: str, target: str) -> None:
    """Refused, not followed: the smaller safe rule (no shipped module has a link)."""
    run = _Run(tmp_path, {link: target})
    run.refused(link, "inside the module's own files")


def test_a_link_where_the_manifest_does_not_look_is_left_alone(tmp_path: Path) -> None:
    """The check is on what Yu'lon reads, so a link in the module's docs installs."""
    run = _Run(tmp_path, {"docs/secret": "{outside}/.ssh/id_rsa", "docs/up": "../.."})
    report = run.applier.install(run.manifest)
    assert (run.server / LUA / "lua" / "linked.lua").read_text(encoding="utf-8") == "-- lua"
    assert (run.client / "Interface" / "AddOns" / "Linked" / "Linked.toc").is_file()
    assert run.sql.files == [("world", "world.sql")]
    assert not any("refus" in line for line in report.done)
    assert _snapshot(run.outside) == run.before


def test_a_junction_is_a_link_to_the_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """On Windows a junction is a folder to Python 3.11's walk; `yulon.links` says what it is.

    A real folder stands in, with `links._lstat` answering for it as Windows does
    for a junction (the reparse attribute and the mount-point tag).
    """
    run = _Run(tmp_path, {})
    real_lstat = os.lstat

    class _Junction:
        def __init__(self, st: os.stat_result) -> None:
            self.st_mode = stat.S_IFDIR | 0o755
            self.st_file_attributes = links.FILE_ATTRIBUTE_REPARSE_POINT
            self.st_reparse_tag = links.IO_REPARSE_TAG_MOUNT_POINT
            self.st_nlink = st.st_nlink

    def lstat(path: Any) -> Any:
        st = real_lstat(path)
        return _Junction(st) if Path(path).name == "junction" else st

    monkeypatch.setattr(links, "_lstat", lstat)
    run.git.files = {**FILES, "lua/junction/stolen.lua": "-- what is behind it"}
    run.refused("lua/junction")


def test_include_sh_that_is_a_dangling_link_is_not_followed_by_the_touch(tmp_path: Path) -> None:
    """`include.sh -> <outside>/made-by-yulon`: `touch()` followed it and made that file."""
    manifest = parse_manifest(
        {**MODULE, "deploy": [], "client": [], "conf": [], "sql": [], "server_dbc": []}
    )
    run = _Run(tmp_path, {"include.sh": "{outside}/made-by-yulon"}, manifest)
    run.applier.install(run.manifest)
    assert not (run.outside / "made-by-yulon").exists()
    assert _snapshot(run.outside) == run.before


# ---------------------------------------------------- configure and remove


def test_remove_does_not_list_a_deploy_folder_through_a_link(tmp_path: Path) -> None:
    """A checkout that gained a link after the install: Remove refuses, nothing removed."""
    run = _Run(tmp_path, {})
    run.applier.remote_url = lambda _dest: run.manifest.source.url  # type: ignore[union-attr]
    run.applier.install(run.manifest)
    deployed = run.server / LUA / "lua" / "linked.lua"
    assert deployed.is_file()
    clone = run.applier.clone_dir(run.manifest)
    for child in (clone / "lua").iterdir():
        child.unlink()
    (clone / "lua").rmdir()
    os.symlink(run.outside, clone / "lua", target_is_directory=True)
    with pytest.raises(ApplyRefusal) as refused:
        run.applier.remove(run.manifest)
    assert "lua" in str(refused.value) and "outside the module's own files" in str(refused.value)
    assert deployed.is_file(), "the refusal removed a file"
    assert clone.is_dir()
    assert _snapshot(run.outside) == run.before


def test_configure_does_not_read_sql_through_a_link(tmp_path: Path) -> None:
    manifest = parse_manifest(
        {**MODULE, "sql": [{"db": "world", "path": "sql/world.sql", "when": "configure"}]}
    )
    run = _Run(tmp_path, {}, manifest)
    run.applier.install(run.manifest)
    run.sql.files.clear()
    clone = run.applier.clone_dir(run.manifest)
    (clone / "sql" / "world.sql").unlink()
    os.symlink(run.outside / "notes.lua", clone / "sql" / "world.sql")
    with pytest.raises(ApplyRefusal) as refused:
        run.applier.configure(run.manifest)
    assert "sql/world.sql" in str(refused.value)
    assert run.sql.files == []


# ------------------------------------------------------- the copies themselves


def test_the_client_plan_never_walks_through_a_link(tmp_path: Path) -> None:
    """`_plan_onto()` plans nothing behind a link, and nothing for the link itself."""
    outside = _outside(tmp_path)
    src = tmp_path / "addon"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.lua").write_text("a", encoding="utf-8")
    os.symlink(outside, src / "home", target_is_directory=True)
    os.symlink(src, src / "sub" / "up", target_is_directory=True)
    os.symlink(outside / "notes.lua", src / "notes.lua")
    planned = apply_module._plan_onto(src, tmp_path / "dest")
    assert [source.relative_to(src).as_posix() for source, _dest in planned] == ["sub/a.lua"]


def test_the_server_deploy_refuses_a_link_it_meets(tmp_path: Path) -> None:
    """The belt: `_deploy()`'s own copy stops at a link even with no check before it."""
    outside = _outside(tmp_path)
    clone = tmp_path / "clone"
    (clone / "lua").mkdir(parents=True)
    (clone / "lua" / "linked.lua").write_text("-- lua", encoding="utf-8")
    os.symlink(outside / ".ssh", clone / "lua" / "ssh", target_is_directory=True)
    applier = Applier(tmp_path / "server")
    manifest = parse_manifest(MODULE)
    with pytest.raises(ApplyError) as stopped:
        applier._deploy(manifest, clone, apply_module._Log())
    assert "ssh" in str(stopped.value)
    assert _holds_secret(tmp_path / "server") == []


# ------------------------------------------- a link made after the look (belt)


@pytest.mark.parametrize(
    ("link", "target", "manifest_change"),
    [
        pytest.param("conf/linked.conf.dist", "{outside}/notes.lua", {}, id="conf template"),
        pytest.param("sql/world.sql", "{outside}/notes.lua", {}, id="direct sql"),
        pytest.param(
            "sql/world.sql",
            "{outside}/stolen.sql",
            {"sql": [{"db": "world", "path": "sql/world.sql", "then": ["sql/then.sql"]}]},
            id="sql in one transaction",
        ),
        pytest.param("dbc/Key.dbc", "{outside}/.ssh/id_rsa", {}, id="dbc"),
        pytest.param(
            "single/Linked.lua",
            "{outside}/notes.lua",
            {"client": [{"src": "single/Linked.lua", "dest": "addons", "name": "Linked"}]},
            id="client single file",
        ),
        pytest.param(
            "Interface",
            "{outside}",
            {},
            id="client folder under a linked folder",
        ),
        pytest.param(
            "src/linked.lua",
            "{outside}/notes.lua",
            {
                "patches": [
                    {
                        "file": "src/linked.lua",
                        "find": "the",
                        "replace": "a",
                        "in_clone": True,
                        "when": "install",
                    }
                ]
            },
            id="in-checkout patch",
        ),
    ],
)
def test_each_step_looks_again_when_it_uses_the_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    link: str,
    target: str,
    manifest_change: dict[str, Any],
) -> None:
    """A link made after the look before the install (`_refuse_checkout_links()` passed over
    here, as a link made in between would be) stops the step that would use it."""
    manifest = parse_manifest({**MODULE, **manifest_change})
    run = _Run(tmp_path, {link: target}, manifest)
    run.git.files = {**FILES, "single/Linked.lua": "-- one file", "sql/then.sql": "SELECT 2;\n"}
    if link == "Interface":
        (run.outside / "AddOns" / "Linked").mkdir(parents=True)
        (run.outside / "AddOns" / "Linked" / "stolen.lua").write_bytes(SECRET)
        run.before = _snapshot(run.outside)
    monkeypatch.setattr(Applier, "_refuse_checkout_links", lambda *_a, **_k: None)
    with pytest.raises(ApplyError) as stopped:
        run.applier.install(run.manifest)
    assert link in str(stopped.value), str(stopped.value)
    assert _snapshot(run.outside) == run.before
    if link == "sql/world.sql":  # the module's own SQL ran before a later step's link
        assert ("world", "world.sql") not in run.sql.files
    assert all("private key" not in text for _db, text in run.sql.statements)
    assert _holds_secret(run.server / "env") == []
    assert _holds_secret(run.client) == []
    assert run.dbc.dirs == []


def test_remove_does_not_list_a_deploy_folder_that_became_a_link_after_the_look(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_undeploy()` deletes the names it lists in the deploy source: never names from elsewhere.

    The look before the remove is passed over, as a link made after it would be. The
    deploy folder's listing through the link would name the outside folder's `notes.lua`,
    and the player's own script of that name in the server folder would be deleted.
    """
    run = _Run(tmp_path, {})
    run.applier.install(run.manifest)
    deployed = run.server / LUA / "lua"
    (deployed / "notes.lua").write_bytes(b"the player's own script")
    clone = run.applier.clone_dir(run.manifest)
    shutil.rmtree(clone / "lua")
    os.symlink(run.outside, clone / "lua", target_is_directory=True)
    monkeypatch.setattr(Applier, "_refuse_checkout_links", lambda *_a, **_k: None)

    report = run.applier.remove(run.manifest)

    assert (deployed / "notes.lua").read_bytes() == b"the player's own script"
    assert (deployed / "linked.lua").is_file(), "removed though its source could not be listed"
    assert any("lua" in line and "link" in line for line in report.skipped), report.skipped
    assert _snapshot(run.outside) == run.before


def test_the_deploy_copy_stops_in_a_folder_that_became_a_link_after_its_parent_was_listed(
    tmp_path: Path,
) -> None:
    """`copytree` enters a child folder by its path after the parent's look; the folder is asked."""
    outside = _outside(tmp_path)
    clone = tmp_path / "clone"
    (clone / "lua").mkdir(parents=True)
    os.symlink(outside, clone / "lua" / "swapped", target_is_directory=True)
    ignore = apply_module._stop_at_links(parse_manifest(MODULE), clone)
    with pytest.raises(ApplyError) as stopped:
        ignore(str(clone / "lua" / "swapped"), [".ssh", "notes.lua"])
    assert "lua/swapped" in str(stopped.value)
