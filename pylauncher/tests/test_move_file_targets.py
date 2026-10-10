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
        'backslash, one of ? * < > | ", a device name such as CON, a Windows short name such as '
        "GIT~1, a name ending in a dot or space, or a control character). Rename or remove it, "
        "then pack again. Nothing was packed."
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


# ----------------------------------------------------------- round 3 re-review: no raw crash


@pytest.mark.parametrize(
    "part", ["git~2", "GIT~13", "YULON-~1.JSO", "YU1A2B~1.JSO", ".GIT~1", "yulon-~2"]
)
def test_a_windows_short_name_of_any_number_is_refused(tmp_path: Path, part: str) -> None:
    target = f"{LUA_SCRIPTS_DIR}/a/{part}/x.lua"
    bad = craft(whole_package(tmp_path), tmp_path / "bad.zip", rename={LUA: target})
    assert plan_for(bad, tmp_path / "new").refusals == (refused_sentence("bad.zip", target),)
    with pytest.raises(ValueError):
        PackFile(kind="module", target=f"{MINE}src/{part}/a.cpp", data=b"x")


def test_a_short_name_in_the_server_refuses_the_pack_in_a_sentence(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    scripts = server.joinpath(*LUA_SCRIPTS_DIR.split("/"))
    scripts.mkdir(parents=True)
    (scripts / "BACKUP~1.LUA").write_bytes(b"-- x\n")
    with pytest.raises(MoveError, match="a Windows short name such as GIT~1"):
        facts(server)


@pytest.mark.parametrize(
    ("file", "inside"),
    [("a", "a/b.lua"), ("A", "a/x.lua"), ("sub/A.d", "SUB/a.d/deep/x.lua")],
)
def test_a_target_that_is_a_file_and_a_folder_of_another_is_refused_by_the_plan(
    tmp_path: Path, file: str, inside: str
) -> None:
    first, second = f"{LUA_SCRIPTS_DIR}/{file}", f"{LUA_SCRIPTS_DIR}/{inside}"
    bad = craft(whole_package(tmp_path), tmp_path / "bad.zip", add={first: b"x", second: b"y"})
    assert plan_for(bad, tmp_path / "new").refusals == (
        f"The package holds {first} as a file and as a folder ({second}), which no disk can "
        "keep, so nothing was brought in. Pack again on the old computer.",
    )


def test_the_run_refuses_a_file_and_folder_clash_before_it_lays_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = craft(
        whole_package(tmp_path),
        tmp_path / "bad.zip",
        add={f"{LUA_SCRIPTS_DIR}/a": b"x", f"{LUA_SCRIPTS_DIR}/a/b.lua": b"y"},
    )
    monkeypatch.setattr(move, "settings_problem", lambda members: None)
    package = move.read_package(bad)  # the reader's check taken away
    monkeypatch.undo()
    server = tmp_path / "new"
    server.mkdir()
    with pytest.raises(MoveError, match="as a file and as a folder"):
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert list(server.iterdir()) == []


def test_a_folder_where_a_file_goes_is_said_up_front_and_a_second_press_carries_on(
    tmp_path: Path,
) -> None:
    mv = Move(tmp_path)
    real_run = mv.engine.run
    blocker = mv.target.server_dir.joinpath(*LUA.split("/"))

    def run_then_block(options, **kw):  # type: ignore[no-untyped-def]
        yield from real_run(options, **kw)
        blocker.mkdir(parents=True, exist_ok=True)

    mv.engine.run = run_then_block  # type: ignore[method-assign]
    with pytest.raises(MoveError) as raised:
        mv.run()
    assert str(raised.value) == (
        f"{LUA} is a folder in the new server, where the package puts a file, so Yu'lon wrote "
        "no settings or Lua file. Move that folder away, then press Bring from another "
        "computer… again with the same file and folder to carry on."
    )
    server = mv.target.server_dir
    assert b"Rate.XP.Kill = 1" in (server / CONF).read_bytes()  # the conf was not laid either
    blocker.rmdir()
    mv.engine.run = real_run  # type: ignore[method-assign]
    mv.run()
    assert (server / CONF).read_bytes() == (
        b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\r\n'
        b"Rate.XP.Kill = 3\r\n"
    )
    assert blocker.read_bytes() == b"-- mine\n"


def test_a_file_where_a_folder_goes_is_said_up_front(tmp_path: Path) -> None:
    package = move.read_package(
        craft(
            whole_package(tmp_path),
            tmp_path / "deep.zip",
            rename={LUA: f"{LUA_SCRIPTS_DIR}/sub/mine.lua"},
        )
    )
    server = tmp_path / "new"
    scripts = server.joinpath(*LUA_SCRIPTS_DIR.split("/"))
    scripts.mkdir(parents=True)
    (scripts / "sub").write_bytes(b"in the way")
    with pytest.raises(MoveError) as raised:
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert str(raised.value) == (
        f"{LUA_SCRIPTS_DIR}/sub is a file in the new server, where the package needs a folder "
        f"for {LUA_SCRIPTS_DIR}/sub/mine.lua, so Yu'lon wrote no settings or Lua file. Move "
        "that file away, then press Bring from another computer… again with the same file and "
        "folder to carry on."
    )
    assert not (server / CONF).exists()


def test_a_target_that_is_a_link_is_refused_before_the_first_file_is_laid(tmp_path: Path) -> None:
    package = move.read_package(whole_package(tmp_path))
    server = tmp_path / "new"
    scripts = server.joinpath(*LUA_SCRIPTS_DIR.split("/"))
    scripts.mkdir(parents=True)
    outside = tmp_path / "outside.lua"
    outside.write_bytes(b"theirs")
    (scripts / "mine.lua").symlink_to(outside)
    with pytest.raises(MoveError, match="is a link, so Yu'lon will not write through it"):
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert not (server / CONF).exists()  # the conf, laid first, was not written either
    assert outside.read_bytes() == b"theirs"


def test_a_conf_link_is_never_read_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = move.read_package(whole_package(tmp_path))
    server = tmp_path / "new"
    etc = server / "env" / "dist" / "etc"
    etc.mkdir(parents=True)
    outside = tmp_path / "outside.conf"
    outside.write_bytes(b"Secret = 1\n")
    (etc / "worldserver.conf").symlink_to(outside)
    read: list[Path] = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: read.append(self) or real(self))
    with pytest.raises(MoveError, match="is a link"):
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert etc / "worldserver.conf" not in read


def test_a_write_that_fails_part_way_is_a_sentence_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = move.read_package(whole_package(tmp_path))
    server = tmp_path / "new"
    server.mkdir()

    def full(target: Path, data: bytes) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(move_server, "_write_bytes", full)
    with pytest.raises(MoveError) as raised:
        list(move_server._lay_files(package, server, WOTLK, "password"))
    assert str(raised.value) == (
        f"Yu'lon could not lay {CONF}: No space left on device. Fix that, then press Bring from "
        "another computer… again with the same file and folder to carry on."
    )


def test_the_plan_says_the_scripts_and_settings_run_like_code(tmp_path: Path) -> None:
    plan = plan_for(whole_package(tmp_path), tmp_path / "new")
    assert plan.allowed, plan.refusals
    assert (
        "The Lua scripts and settings files in this package run on this server like code, so "
        "bring in only a package you made yourself or got from someone you trust."
    ) in plan.text().splitlines()


def test_a_folder_module_with_a_file_and_a_folder_of_one_name_is_refused(tmp_path: Path) -> None:
    files = {"src/A": b"x", "src/a/x.cpp": b"y"}
    bad = craft(
        whole_package(tmp_path, facts=folder_facts()),
        tmp_path / "bad.zip",
        add={f"{MINE}{rel}": data for rel, data in files.items()},
    )
    assert _plan_with(bad, tmp_path / "new", FINE).refusals == (
        "mod-mine holds src/A as a file and as a folder (src/a/x.cpp), which no disk can keep, "
        "so nothing was brought in. Pack again on the old computer.",
    )


def test_the_pack_refuses_a_folder_module_with_a_file_and_folder_in_other_capitals(
    tmp_path: Path,
) -> None:
    from tests.test_move_server import folder_module, pack_folder

    server = wotlk_server(tmp_path)
    mine = folder_module(server, {"src/A": b"x", "src/a/x.cpp": b"y"})
    with pytest.raises(MoveError) as raised:
        pack_folder(server, mine)
    assert str(raised.value) == (
        "My Module holds src/A as a file and, in other capitals, as a folder (src/a/x.cpp), which "
        "one Windows folder cannot keep apart. Rename one, then pack again. Nothing was packed."
    )


def test_the_pack_refuses_lua_files_one_windows_folder_cannot_keep_apart(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    scripts = server.joinpath(*LUA_SCRIPTS_DIR.split("/"))
    (scripts / "a").mkdir(parents=True)
    (scripts / "A").write_bytes(b"-- x\n")
    (scripts / "a" / "x.lua").write_bytes(b"-- y\n")
    with pytest.raises(MoveError) as raised:
        facts(server)
    assert str(raised.value) == (
        "The server has two settings or Lua files that one Windows folder cannot keep apart "
        f"({LUA_SCRIPTS_DIR}/A and {LUA_SCRIPTS_DIR}/a/x.lua). Rename one, then pack again. "
        "Nothing was packed."
    )


def test_the_pack_refuses_lua_files_that_differ_only_in_case(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    scripts = server.joinpath(*LUA_SCRIPTS_DIR.split("/"))
    scripts.mkdir(parents=True)
    (scripts / "Mine.lua").write_bytes(b"-- x\n")
    (scripts / "mine.lua").write_bytes(b"-- y\n")
    with pytest.raises(MoveError, match=f"\\({LUA_SCRIPTS_DIR}/Mine.lua and "):
        facts(server)
