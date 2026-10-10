"""T624 rework: crafted packages through the real path, and the pack's refusals.

A package is a file someone else made, so every probe here builds a REAL zip, edits it the way an
attacker would (names, descriptions, sizes), and feeds it to the same reader, planner and run an
import uses.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from tests.test_move_package import manifest_of
from tests.test_move_server import folder_module, pack_folder, wotlk_server
from tests.test_move_server_flow import (
    MINE_FILES,
    Move,
    _plan_with,
    folder_facts,
    whole_package,
)
from yulon import move, move_server
from yulon.manifest import Manifest
from yulon.move import PackFile
from yulon.move_flows import MoveError

FINE = move_server.ModuleLookup(load=lambda k, i: None, shipped=lambda k, i: False)
MINE = "module/mod-mine/"


def craft(
    source: Path,
    dest: Path,
    *,
    rename: dict[str, str] | None = None,
    swap: dict[str, bytes] | None = None,
    add: dict[str, bytes] | None = None,
) -> Path:
    """A copy of `source` whose file members are renamed (target), re-filled or added, with the
    manifest's names, sizes and hashes made to agree, so only the guards stand in the way."""
    rename = rename or {}
    swap = swap or {}
    raw = manifest_of(source)
    files = raw["server"]["files"]
    contents: dict[str, bytes] = {}
    with zipfile.ZipFile(source) as src:
        for entry in files:
            contents[entry["target"]] = src.read(entry["file"])
        others = {
            n: src.read(n)
            for n in src.namelist()
            if n != move.MANIFEST_NAME and n not in {e["file"] for e in files}
        }
    for target, data in (add or {}).items():
        kind = "module" if target.startswith(MINE) else "lua"
        files.append({"file": move.FILE_FOLDERS[kind] + target, "kind": kind, "target": target})
        contents[target] = data
    new_files = []
    for entry in files:
        old = entry["target"]
        target = rename.get(old, old)
        data = swap.get(old, contents[old])
        folder = move.FILE_FOLDERS[entry["kind"]]
        new_files.append(
            {
                "file": folder + target,
                "kind": entry["kind"],
                "target": target,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        contents[target] = data
    raw["server"]["files"] = new_files
    with zipfile.ZipFile(dest, "w") as out:
        out.writestr(move.MANIFEST_NAME, json.dumps(raw))
        for name, data in others.items():
            out.writestr(name, data)
        for entry in new_files:
            out.writestr(entry["file"], contents[entry["target"]])
    return dest


BAD_NAMES = [
    "sub/C:../C:../Users/p/AppData/Startup/x.bat",
    "C:../x.bat",
    "a:b",
    "sub/CON",
    "nul.txt",
    "sub/com1.cpp",
    "LPT3",
    "trail /x",
    "dot./x",
    "x.",
    "back\\slash",
    "ctl\x01char",
    ".YULON-clone.json",
    "sub/.GIT/config",
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_crafted_module_member_name_is_refused_by_the_reader(tmp_path: Path, name: str) -> None:
    good = whole_package(tmp_path, facts=folder_facts())
    bad = craft(good, tmp_path / "bad.zip", rename={f"{MINE}src/mine.cpp": MINE + name})
    with pytest.raises(move.MovePackageError):
        move.read_package(bad)


@pytest.mark.parametrize("name", BAD_NAMES[:12])
@pytest.mark.parametrize("kind", ["lua", "conf"])
def test_the_same_names_never_become_a_lua_or_conf_target(kind: str, name: str) -> None:
    target = f"env/dist/etc/modules/lua_scripts/{name}"
    if kind == "conf":
        target = f"env/dist/etc/{name}/a.conf"
    with pytest.raises(ValueError):
        PackFile(kind=kind, target=target, data=b"x")  # type: ignore[arg-type]


@pytest.mark.parametrize("flavour", [PureWindowsPath, PurePosixPath])
def test_a_target_that_would_leave_the_staging_folder_is_caught_by_the_join_check(
    flavour: type,
) -> None:
    root = flavour("C:/server/stage") if flavour is PureWindowsPath else flavour("/srv/stage")
    for escape in ("sub/C:../x.bat", "C:/x", "../x", "a/../../x", "/etc/x", "\\\\host\\s\\x"):
        assert not move_server.lands_inside(root, escape), escape
    assert move_server.lands_inside(root, "src/a.cpp")


# ----------------------------------------------------------- the description is not trusted


def with_description(tmp_path: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    good = whole_package(tmp_path, facts=folder_facts())
    raw = Manifest.model_validate_json(
        zipfile.ZipFile(good).read(move.FILE_FOLDERS["manifest"] + "module/mod-mine")
    ).model_dump(mode="json")
    mutate(raw)
    return craft(
        good,
        tmp_path / "desc.zip",
        swap={"module/mod-mine": json.dumps(raw).encode()},
    )


CRAFTED = {
    "patches": [{"file": "x", "find": "a", "replace": "b"}],
    "client": [{"src": "a", "dest": "data"}],
    "deploy": [{"src": "a", "dest": "b"}],
    "folders": ["x"],
    "requires": ["mod-playerbots"],
    "conflicts_with": ["mod-transmog"],
    "npcs": [{"entry": 1, "name": "N"}],
    "prompts": [{"key": "K", "question": "L"}],
    "server_dbc": [{"src": "a"}],
}


@pytest.mark.parametrize("field", sorted(CRAFTED))
def test_a_folder_description_with_more_than_a_derivation_gives_is_refused(
    tmp_path: Path, field: str
) -> None:
    def put(raw: dict) -> None:
        raw[field] = CRAFTED[field]

    path = with_description(tmp_path, put)
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert not plan.allowed
    assert plan.refusals == (
        "The package's description of mod-mine asks for more than a module added from a folder "
        f"can ask for ({field}), so Yu'lon will not install it. Pack again on the old computer.",
    )


def test_a_folder_description_for_another_game_is_refused(tmp_path: Path) -> None:
    path = with_description(tmp_path, lambda raw: raw.update(game="wow-tbc"))
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert plan.refusals == (
        "The package's description of mod-mine is for another game, so Yu'lon will not install "
        "it. Pack again on the old computer.",
    )


def test_a_crafted_conf_step_of_a_folder_description_is_refused(tmp_path: Path) -> None:
    path = with_description(
        tmp_path,
        lambda raw: raw.update(
            conf=[
                {
                    "file": "env/dist/etc/worldserver.conf",
                    "template": "conf/a.conf.dist",
                    "keys": [],
                }
            ]
        ),
    )
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert not plan.allowed and "(conf)" in plan.refusals[0], plan.refusals


def test_a_link_description_with_more_than_a_derivation_gives_is_refused(tmp_path: Path) -> None:
    from tests.test_move_server_flow import ServerFacts, ServerSpec, whole_facts
    from yulon.move import PackedModule

    def link_facts() -> ServerFacts:
        base = whole_facts()
        linked = Manifest.model_validate(
            {
                "id": "mod-linked",
                "name": "Linked",
                "type": "module",
                "game": "wow-wotlk",
                "source": {"repo": "someone/mod-linked"},
                "origin": {"kind": "link", "added": "2026-10-01"},
                "deploy": [{"src": "a", "dest": "b"}],
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

    path = whole_package(tmp_path, facts=link_facts)
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert plan.refusals == (
        "The package's description of mod-linked asks for more than a module added from a link "
        "can ask for (deploy), so Yu'lon will not install it. Pack again on the old computer.",
    )


# ----------------------------------------------------------- bounds on the way in


def test_a_package_over_the_bound_is_refused_before_any_byte_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = whole_package(tmp_path, facts=folder_facts())
    monkeypatch.setattr(move_server, "FOLDER_MODULE_MAX_FILES", 1)
    plan = _plan_with(path, tmp_path / "new", FINE)
    assert plan.refusals == (
        "mod-mine holds more than Yu'lon brings in from a folder module (the limit is 50 MB and "
        "1 files), so nothing was brought in. Pack it again on the old computer with less in it.",
    )
    monkeypatch.undo()
    monkeypatch.setattr(move_server, "FOLDER_MODULE_MAX_BYTES", 5)
    assert _plan_with(path, tmp_path / "new", FINE).refusals[0].startswith("mod-mine holds more")


def test_two_members_that_differ_only_in_case_are_refused(tmp_path: Path) -> None:
    good = whole_package(tmp_path, facts=folder_facts())
    bad = craft(good, tmp_path / "bad.zip", add={f"{MINE}src/MINE.cpp": b"// other\n"})
    plan = _plan_with(bad, tmp_path / "new", FINE)
    assert plan.refusals == (
        "mod-mine holds two files whose names differ only in capital letters "
        "(src/MINE.cpp and src/mine.cpp), which one Windows folder cannot keep apart, so "
        "nothing was brought in. Pack again on the old computer.",
    )


def test_the_run_checks_the_bound_again_before_it_stages_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mv = Move(tmp_path, facts=folder_facts())
    mv.plan = _plan_with(mv.path, mv.target.server_dir, FINE)
    assert mv.plan.allowed
    monkeypatch.setattr(move_server, "FOLDER_MODULE_MAX_FILES", 1)
    with pytest.raises(MoveError, match="holds more than Yu'lon brings in"):
        mv.run()
    assert mv.applier.folders == []


def test_a_member_that_grew_after_the_check_is_not_read_whole(tmp_path: Path) -> None:
    path = whole_package(tmp_path, facts=folder_facts())
    package = move.read_package(path)
    member = package.file("module", f"{MINE}src/mine.cpp")
    big = path.with_name("grown.zip")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(big, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            out.writestr(
                info.filename, data + b"0" * 5_000_000 if info.filename == member.file else data
            )
    big.replace(path)
    seen: list[int] = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, mode="r", *a, **k):  # type: ignore[no-untyped-def]
        handle = real_open(self, name, mode, *a, **k)
        real_read = handle.read

        def counted(n=-1):  # type: ignore[no-untyped-def]
            data = real_read(n)
            seen.append(len(data))
            return data

        handle.read = counted  # type: ignore[method-assign]
        return handle

    zipfile.ZipFile.open = spy  # type: ignore[method-assign]
    try:
        with pytest.raises(move.MovePackageError):
            package.file_bytes(member)
    finally:
        zipfile.ZipFile.open = real_open  # type: ignore[method-assign]
    assert sum(seen) <= member.bytes + 1


# ----------------------------------------------------------- the pack refuses, it does not crash


def refusal_of(server: Path, mine: Manifest) -> str:
    with pytest.raises(MoveError) as raised:
        pack_folder(server, mine)
    return str(raised.value)


def test_a_git_file_inside_a_folder_module_is_skipped_like_a_git_folder(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    mine = folder_module(server, {"src/a.cpp": b"x", "deps/sub/.git": b"gitdir: ../../.git/x"})
    got = pack_folder(server, mine)
    assert [f.target for f in got.files if f.kind == "module"] == ["module/mod-mine/src/a.cpp"]


def sentence(rel: str) -> str:
    return (
        f"My Module has a file Yu'lon cannot put in a package ({rel}): its name or place is "
        "not one a package can carry. Rename or remove it, then pack again. Nothing was packed."
    )


@pytest.mark.parametrize(
    ("rel", "shown"),
    [
        ("/".join(["d"] * 17) + "/f.txt", "/".join(["d"] * 17) + "/f.txt"),
        ("x" * 200 + "/" + "y" * 200 + "/z.txt", "x" * 200 + "/" + "y" * 200 + "/z.txt"),
        ("back\\slash.txt", "'back\\\\slash.txt'"),
        ("ctl\x01.txt", "'ctl\\x01.txt'"),
        ("CON", "CON"),
    ],
)
def test_a_name_a_package_cannot_carry_refuses_the_pack_in_a_sentence(
    tmp_path: Path, rel: str, shown: str
) -> None:
    server = wotlk_server(tmp_path)
    mine = folder_module(server, {"ok.txt": b"x", rel: b"x"})
    assert refusal_of(server, mine) == sentence(shown)


def test_a_file_that_cannot_be_read_refuses_the_pack_in_a_sentence(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    mine = folder_module(server, {"ok.txt": b"x", "locked.txt": b"x"})
    locked = server / "modules" / "mod-mine" / "locked.txt"
    locked.chmod(0)
    try:
        said = refusal_of(server, mine)
    finally:
        locked.chmod(0o644)
    assert said.startswith("My Module has a file Yu'lon could not read (locked.txt): ")
    assert said.endswith("Fix it or remove it, then pack again. Nothing was packed.")


def test_two_files_differing_only_in_case_refuse_the_pack(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    mine = folder_module(server, {"A.cpp": b"1", "a.cpp": b"2"})
    assert refusal_of(server, mine) == (
        "My Module holds two files whose names differ only in capital letters (A.cpp and "
        "a.cpp), which one Windows folder cannot keep apart. Rename one, then pack again. "
        "Nothing was packed."
    )


# ----------------------------------------------------------- the staging folder


def test_a_staging_folder_left_by_a_killed_install_is_swept_at_the_next_one(tmp_path: Path) -> None:
    mv = Move(tmp_path, facts=folder_facts())
    mv.plan = _plan_with(mv.path, mv.target.server_dir, FINE)
    real_run = mv.engine.run

    def run_then_leave(options, **kw):  # type: ignore[no-untyped-def]
        yield from real_run(options, **kw)
        stale = options.server_dir / ".yulon-move-folder-abandoned"
        (stale / "mod-mine").mkdir(parents=True)
        (stale / "mod-mine" / "x").write_text("x", encoding="utf-8")

    mv.engine.run = run_then_leave  # type: ignore[method-assign]
    mv.run()
    assert not [
        p for p in mv.target.server_dir.iterdir() if p.name.startswith(".yulon-move-folder")
    ]
    assert mv.applier.folders == [("mod-mine", MINE_FILES)]


def test_a_staging_folder_that_will_not_go_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mv = Move(tmp_path, facts=folder_facts())
    mv.plan = _plan_with(mv.path, mv.target.server_dir, FINE)

    def stuck(path, *a, **k):  # type: ignore[no-untyped-def]
        raise OSError("read-only")

    import types

    monkeypatch.setattr(move_server, "shutil", types.SimpleNamespace(rmtree=stuck))
    lines = mv.run()
    left = [line for line in lines if line.startswith("!! Yu'lon could not remove ")]
    assert len(left) == 1
    assert left[0].endswith(" delete that folder by hand.")


# ----------------------------------------------------------- the shape rule itself


def test_a_description_completed_by_the_real_derivation_is_plain(tmp_path: Path) -> None:
    from yulon import module_source

    clone = tmp_path / "mod-real"
    (clone / "conf").mkdir(parents=True)
    (clone / "conf" / "real.conf.dist").write_text("A = 1\n", encoding="utf-8")
    (clone / "data" / "sql" / "db-world").mkdir(parents=True)
    (clone / "data" / "sql" / "db-world" / "a.sql").write_text("-- x\n", encoding="utf-8")
    (clone / "data" / "sql" / "playerbots").mkdir(parents=True)
    derived = module_source.derive_folder(
        clone, "wow-wotlk", today=__import__("datetime").date(2026, 10, 9)
    )
    done = module_source.complete(derived, clone)
    assert done.conf and len(done.sql) == 2
    assert module_source.beyond_derived_shape(done) is None


def test_a_crafted_sql_step_is_beyond_the_shape() -> None:
    from yulon import module_source
    from yulon.manifest import SqlStep

    base = Manifest.model_validate(
        {
            "id": "mod-mine",
            "name": "M",
            "type": "module",
            "game": "wow-wotlk",
            "origin": {"kind": "folder", "added": "2026-10-01"},
        }
    )
    for step in (
        SqlStep(db="world", statement="DROP DATABASE acore_auth"),
        SqlStep(db="world", path="data/sql/db-world/**/*.sql"),  # direct, not db-import
        SqlStep(db="auth", path="data/sql/db-world/**/*.sql", applied_by="db-import"),
        SqlStep(db="world", path="../../x/**/*.sql", applied_by="db-import"),
        SqlStep(db="world", path="data/sql/other/**/*.sql", applied_by="db-import"),
    ):
        assert module_source.beyond_derived_shape(base.model_copy(update={"sql": (step,)})) == "sql"


def test_a_conf_step_that_writes_keys_is_beyond_the_shape() -> None:
    from yulon import module_source
    from yulon.manifest import ConfFile, ConfKey

    base = Manifest.model_validate(
        {
            "id": "mod-mine",
            "name": "M",
            "type": "module",
            "game": "wow-wotlk",
            "origin": {"kind": "folder", "added": "2026-10-01"},
        }
    )
    step = ConfFile(
        file="env/dist/etc/modules/a.conf",
        template="conf/a.conf.dist",
        keys=(ConfKey(key="K", default="1"),),
    )
    assert module_source.beyond_derived_shape(base.model_copy(update={"conf": (step,)})) == "conf"


def test_the_join_check_refuses_a_target_the_name_rules_did_not_see(tmp_path: Path) -> None:
    for escape in ("sub/C:../x.bat", "../x", "/etc/x"):
        with pytest.raises(MoveError, match="would be written outside"):
            move_server._inside(tmp_path, escape)
    assert move_server._inside(tmp_path, "a/b.txt") == tmp_path / "a" / "b.txt"


def test_a_member_declaring_more_than_any_file_may_is_not_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = whole_package(tmp_path, facts=folder_facts())
    monkeypatch.setattr(move, "MAX_FILE_MEMBER_BYTES", 5)
    with pytest.raises(move.MovePackageError):
        move.read_package(path)
