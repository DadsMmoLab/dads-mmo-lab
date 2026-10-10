"""T624 round 3: where a whole-server package may put a conf or Lua file, through the real path.

A crafted package renamed its Lua member to `.git/hooks/post-checkout` (the core's checkout IS the
server folder, so that is a live git hook), `Dockerfile`, a compose override or a module's C++
file, and the plan allowed it and the run wrote it. Every probe here crafts a REAL zip and feeds
it to the reader, the planner and the run an import uses.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tests.test_move_folder_module_hardening import FINE, MINE, craft
from tests.test_move_server import facts, wotlk_server
from tests.test_move_server_flow import (
    WOTLK,
    Move,
    _plan_with,
    folder_facts,
    plan_for,
    whole_facts,
    whole_package,
)
from yulon import move, move_server
from yulon.catalog.catalog import LUA_SCRIPTS_DIR
from yulon.manifest import Manifest
from yulon.move import PackedModule, PackFile, ServerFacts, ServerSpec
from yulon.move_flows import MoveError
from yulon.support.sources import CONF_DIRS

LUA = "env/dist/etc/modules/lua_scripts/mine.lua"
CONF = "env/dist/etc/worldserver.conf"

LUA_EVIL = [
    ".git/hooks/post-checkout",
    "server-wow-wotlk/source/.git/hooks/post-checkout",
    "Dockerfile",
    "docker-compose.override.yml",
    "modules/mod-playerbots/src/evil.cpp",
    ".Git/config",
    f"{LUA_SCRIPTS_DIR}/.git/hooks/post-checkout",
    f"{LUA_SCRIPTS_DIR}/sub/.GIT/config",
    f"{LUA_SCRIPTS_DIR}/GIT~1/hooks/post-checkout",
    f"{LUA_SCRIPTS_DIR}/.hidden.lua",
    f"{LUA_SCRIPTS_DIR}X/a.lua",
    "env/dist/etc/modules/a.lua",
]

CONF_EVIL = [
    ".git/hooks/x.conf",
    ".Git/x.conf",
    "modules/mod-playerbots/src/evil.conf",
    "env/dist/etc/sub/a.conf",
    f"{LUA_SCRIPTS_DIR}/a.conf",
    "ETC/a.conf",
    "env/dist/etc/.conf",
    "a.conf",
]


def refused_sentence(name: str, target: str) -> str:
    return (
        f"{name} holds a file meant for {target}, which is not a place a move package puts "
        "files, so Yu'lon will not open it. Nothing was brought in."
    )


@pytest.mark.parametrize("target", LUA_EVIL)
def test_a_lua_member_outside_the_lua_folder_is_refused_by_the_reader(
    tmp_path: Path, target: str
) -> None:
    bad = craft(whole_package(tmp_path), tmp_path / "bad.zip", rename={LUA: target})
    with pytest.raises(move.MovePackageError) as raised:
        move.read_package(bad)
    assert str(raised.value) == refused_sentence("bad.zip", target)


@pytest.mark.parametrize("target", CONF_EVIL)
def test_a_conf_member_outside_the_conf_folders_is_refused_by_the_reader(
    tmp_path: Path, target: str
) -> None:
    bad = craft(whole_package(tmp_path), tmp_path / "bad.zip", rename={CONF: target})
    with pytest.raises(move.MovePackageError) as raised:
        move.read_package(bad)
    assert str(raised.value) == refused_sentence("bad.zip", target)


@pytest.mark.parametrize("target", [".git/hooks/post-checkout", "Dockerfile", ".Git/config"])
def test_the_plan_refuses_the_crafted_lua_target_and_writes_nothing(
    tmp_path: Path, target: str
) -> None:
    bad = craft(whole_package(tmp_path), tmp_path / "bad.zip", rename={LUA: target})
    new = tmp_path / "new"
    plan = plan_for(bad, new)
    assert plan.refusals == (
        "bad.zip holds a file meant for " + target + ", which is not a place a move package "
        "puts files, so Yu'lon will not open it. Nothing was brought in.",
    )
    assert not new.exists()


def lenient(kind: str, target: str) -> None:
    """The reader's place rule taken away, so the run's own check is all that is left."""


RUN_EVIL = [
    ("lua", ".git/hooks/post-checkout"),
    ("lua", "Dockerfile"),
    ("lua", "docker-compose.override.yml"),
    ("lua", "modules/mod-playerbots/src/evil.cpp"),
    ("lua", ".Git/config"),
    ("lua", f"{LUA_SCRIPTS_DIR}/.git/hooks/post-checkout"),
    ("conf", "modules/mod-playerbots/src/evil.conf"),
    ("conf", ".git/x.conf"),
]


@pytest.mark.parametrize(("kind", "target"), RUN_EVIL)
def test_the_run_refuses_the_target_itself_when_the_reader_let_it_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, target: str
) -> None:
    mv = Move(tmp_path)
    mv.path = craft(mv.path, tmp_path / "bad.zip", rename={LUA if kind == "lua" else CONF: target})
    monkeypatch.setattr(move, "_check_target", lenient)
    mv.plan = plan_for(mv.path, mv.target.server_dir)
    assert mv.plan.allowed, mv.plan.refusals
    with pytest.raises(MoveError) as raised:
        mv.run()
    assert str(raised.value) == (
        f"{target} is not a place a move package puts files, so Yu'lon wrote no settings or Lua "
        "file. Nothing more was changed."
    )
    server = mv.target.server_dir
    assert not server.joinpath(*target.split("/")).exists()
    # Every target is checked before the first is written: the install's own conf is untouched.
    assert (
        (server / "env" / "dist" / "etc" / "worldserver.conf")
        .read_bytes()
        .startswith(b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\n')
    )


@pytest.mark.parametrize(
    "target", [f"{d}/x.conf" for d in CONF_DIRS] + [f"{d}/.x.conf" for d in CONF_DIRS]
)
def test_every_place_the_pack_reads_a_conf_from_is_a_conf_target(target: str) -> None:
    PackFile(kind="conf", target=target, data=b"x")


@pytest.mark.parametrize("rest", ["a.lua", "sub/dir/b.lua", "x.txt"])
def test_every_place_the_pack_reads_lua_from_is_a_lua_target(rest: str) -> None:
    PackFile(kind="lua", target=f"{LUA_SCRIPTS_DIR}/{rest}", data=b"x")


def test_a_lua_file_the_pack_skips_is_never_a_target() -> None:
    for rest in (".a.lua", "sub/.d/b.lua", ""):
        with pytest.raises(ValueError):
            PackFile(kind="lua", target=f"{LUA_SCRIPTS_DIR}/{rest}", data=b"x")


# ----------------------------------------------------------- names Windows cannot hold

WINDOWS_INVALID = ["a?b", "a*b", "a<b", "a>b", "a|b", 'a"b']


@pytest.mark.parametrize("char_name", WINDOWS_INVALID)
def test_a_module_member_with_a_name_windows_cannot_hold_is_refused_by_the_reader(
    tmp_path: Path, char_name: str
) -> None:
    good = whole_package(tmp_path, facts=folder_facts())
    bad = craft(good, tmp_path / "bad.zip", rename={f"{MINE}src/mine.cpp": f"{MINE}{char_name}"})
    with pytest.raises(move.MovePackageError) as raised:
        move.read_package(bad)
    assert "could write outside the folder" in str(raised.value)


@pytest.mark.parametrize("char_name", WINDOWS_INVALID)
def test_a_lua_target_with_a_name_windows_cannot_hold_is_refused(char_name: str) -> None:
    assert move._unsafe_name(f"{LUA_SCRIPTS_DIR}/{char_name}.lua")
    with pytest.raises(ValueError):
        PackFile(kind="lua", target=f"{LUA_SCRIPTS_DIR}/{char_name}.lua", data=b"x")


def test_a_lua_file_named_so_windows_cannot_hold_it_refuses_the_pack_in_a_sentence(
    tmp_path: Path,
) -> None:
    server = wotlk_server(tmp_path)
    scripts = server.joinpath(*LUA_SCRIPTS_DIR.split("/"))
    scripts.mkdir(parents=True)
    (scripts / "who?.lua").write_bytes(b"-- x\n")
    with pytest.raises(MoveError) as raised:
        facts(server)
    assert str(raised.value) == (
        "A file in the server has a name or place a package cannot carry (a colon, a "
        'backslash, one of ? * < > | ", a device name such as CON, a name ending in a dot or '
        "space, or a control character). Rename or remove it, then pack again. Nothing was "
        "packed."
    )


# ----------------------------------------------------------- the bound on conf and Lua files

TOO_MANY = (
    "The package holds more settings and Lua files than Yu'lon brings in (the limit is {mb} MB "
    "and {n} files), so nothing was brought in. Pack it again on the old computer with less in "
    "it."
)


def test_too_many_conf_and_lua_files_are_refused_before_any_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = whole_package(tmp_path)
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, *a, **k):  # type: ignore[no-untyped-def]
        opened.append(name if isinstance(name, str) else name.filename)
        return real_open(self, name, *a, **k)

    monkeypatch.setattr(move, "SETTINGS_MAX_FILES", 1)
    monkeypatch.setattr(zipfile.ZipFile, "open", spy)
    plan = plan_for(path, tmp_path / "new")
    assert plan.refusals == (TOO_MANY.format(mb=50, n=1),)
    assert not [n for n in opened if n.startswith(("lua/", "conf/"))], opened


def test_conf_and_lua_files_over_the_byte_bound_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = whole_package(tmp_path)
    monkeypatch.setattr(move, "SETTINGS_MAX_BYTES", 10)
    assert plan_for(path, tmp_path / "new").refusals == (TOO_MANY.format(mb=0, n=5000),)


def test_two_lua_targets_differing_only_in_case_are_refused(tmp_path: Path) -> None:
    bad = craft(
        whole_package(tmp_path),
        tmp_path / "bad.zip",
        add={f"{LUA_SCRIPTS_DIR}/MINE.lua": b"-- other\n"},
    )
    assert plan_for(bad, tmp_path / "new").refusals == (
        "The package holds two settings or Lua files whose names differ only in capital letters "
        f"({LUA_SCRIPTS_DIR}/MINE.lua and {LUA}), which one Windows folder cannot keep apart, "
        "so nothing was brought in. Pack again on the old computer.",
    )


def test_the_run_checks_the_conf_and_lua_bound_again_before_it_reads_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = move.read_package(whole_package(tmp_path))
    server = tmp_path / "new"
    server.mkdir()
    monkeypatch.setattr(move, "SETTINGS_MAX_FILES", 1)
    monkeypatch.setattr(move.Package, "file_bytes", lambda *a: pytest.fail("read before the bound"))
    with pytest.raises(MoveError, match="more settings and Lua files than Yu'lon brings in"):
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert list(server.iterdir()) == []


def test_the_run_refuses_a_case_clash_the_reader_let_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = craft(
        whole_package(tmp_path),
        tmp_path / "bad.zip",
        add={f"{LUA_SCRIPTS_DIR}/MINE.lua": b"-- other\n"},
    )
    monkeypatch.setattr(move, "settings_problem", lambda members: None)
    package = move.read_package(bad)
    monkeypatch.undo()
    server = tmp_path / "new"
    server.mkdir()
    with pytest.raises(MoveError, match="differ only in capital letters"):
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert list(server.iterdir()) == []


def test_the_pack_refuses_more_conf_and_lua_than_a_bring_in_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = wotlk_server(tmp_path)
    monkeypatch.setattr(move, "SETTINGS_MAX_FILES", 0)
    with pytest.raises(MoveError) as raised:
        facts(server)
    assert str(raised.value) == (
        "The server's settings and Lua files are more than Yu'lon packs (the limit is 50 MB and "
        "0 files). Take out Lua scripts the server does not need, then pack again. Nothing was "
        "packed."
    )
    monkeypatch.undo()
    monkeypatch.setattr(move, "SETTINGS_MAX_BYTES", 10)
    with pytest.raises(MoveError, match="more than Yu'lon packs"):
        facts(server)


# ----------------------------------------------------------- the link route's source


def link_facts(**source: object) -> ServerFacts:
    base = whole_facts()
    linked = Manifest.model_validate(
        {
            "id": "mod-linked",
            "name": "Linked",
            "type": "module",
            "game": "wow-wotlk",
            "source": {"repo": "someone/mod-linked", **source},
            "origin": {"kind": "link", "added": "2026-10-01"},
        }
    )
    return ServerFacts(
        spec=ServerSpec(
            sources=base.spec.sources,
            modules=(
                PackedModule(
                    type="module",
                    id="mod-linked",
                    origin="link",
                    repo="someone/mod-linked",
                    commit="d" * 40,
                ),
            ),
        ),
        files=(
            PackFile(
                kind="manifest",
                target="module/mod-linked",
                data=linked.model_dump_json().encode(),
            ),
        ),
    )


@pytest.mark.parametrize(
    "source",
    [
        {"branch": "main"},
        {"branch": "--upload-pack=touch /tmp/x"},
        {"depth": None},
        {"depth": 5000},
        {"sparse_path": "../../.."},
        {"sparse_path": "src"},
    ],
    ids=["branch", "branch-option", "full-clone", "deep", "sparse-escape", "sparse"],
)
def test_a_link_description_with_a_source_a_link_never_derives_is_refused(
    tmp_path: Path, source: dict[str, object]
) -> None:
    path = whole_package(tmp_path, facts=lambda: link_facts(**source))
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert plan.refusals == (
        "The package's description of mod-linked asks for more than a module added from a link "
        "can ask for (source), so Yu'lon will not install it. Pack again on the old computer.",
    )


def test_a_link_description_as_a_link_derives_it_plans(tmp_path: Path) -> None:
    path = whole_package(tmp_path, facts=link_facts)
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert plan.allowed, plan.refusals
    (planned,) = plan.modules
    assert planned.manifest.source is not None
    assert planned.manifest.source.rev == "d" * 40
