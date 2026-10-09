"""T601 level 2: the whole-server package, its extra members and the reader's checks of them.

A whole-server package is the level-1 zip plus the server's conf files, its module answers,
the derived manifests of outside link modules and the player's own Lua scripts, and a
`server` section naming the commit every source and module was built from. Every extra file
is hashed in the manifest and checked by the same reader before anything is used.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tests.test_move_package import AT, dump, header, manifest_of, rewrite
from yulon import move
from yulon.move import (
    MovePackageError,
    PackedModule,
    PackedSource,
    PackFile,
    ServerSpec,
    read_package,
    write_package,
)

SHA_CORE = "1" * 40
SHA_BOTS = "2" * 40
SHA_MOD = "3" * 40


def server_spec(**over: object) -> ServerSpec:
    base: dict[str, object] = {
        "sources": (
            PackedSource(
                repo="mod-playerbots/azerothcore-wotlk",
                dest=".",
                commit=SHA_CORE,
                catalog_pin=SHA_CORE,
            ),
            PackedSource(
                repo="mod-playerbots/mod-playerbots",
                dest="modules/mod-playerbots",
                commit=SHA_BOTS,
                catalog_pin="4" * 40,
            ),
        ),
        "modules": (
            PackedModule(
                type="module",
                id="mod-transmog",
                origin="catalog",
                repo="azerothcore/mod-transmog",
                commit=SHA_MOD,
            ),
        ),
    }
    base.update(over)
    return ServerSpec(**base)  # type: ignore[arg-type]


CONF = b"WorldServerPort = 8085\r\nRate.XP.Kill = 3\r\n"


def server_pack(tmp_path: Path, files: tuple[PackFile, ...] | None = None) -> Path:
    dumps = [
        dump(tmp_path, "acore_auth", "auth"),
        dump(tmp_path, "acore_characters", "characters"),
        dump(tmp_path, "acore_world", "world"),
    ]
    dest = tmp_path / "server.zip"
    write_package(
        dest,
        header(),
        dumps,
        server=server_spec(),
        files=(
            files
            if files is not None
            else (
                PackFile(kind="conf", target="env/dist/etc/worldserver.conf", data=CONF),
                PackFile(
                    kind="answers", target=".yulon-module-answers.json", data=b'{"modules": {}}'
                ),
                PackFile(kind="lua", target="lua_scripts/mine.lua", data=b"print('hi')\n"),
                PackFile(kind="manifest", target="module/mod-mine", data=b'{"id": "mod-mine"}'),
            )
        ),
    )
    return dest


def test_a_server_package_survives_write_then_read(tmp_path: Path) -> None:
    package = read_package(server_pack(tmp_path))
    m = package.manifest
    assert m.kind == "server"
    assert m.server is not None
    assert [s.commit for s in m.server.sources] == [SHA_CORE, SHA_BOTS]
    assert m.server.modules[0].id == "mod-transmog"
    assert [(f.kind, f.target) for f in m.server.files] == [
        ("conf", "env/dist/etc/worldserver.conf"),
        ("answers", ".yulon-module-answers.json"),
        ("lua", "lua_scripts/mine.lua"),
        ("manifest", "module/mod-mine"),
    ]
    assert [d.role for d in m.databases] == ["auth", "characters", "world"]


def test_a_conf_comes_back_byte_for_byte_with_its_line_endings(tmp_path: Path) -> None:
    package = read_package(server_pack(tmp_path))
    conf = package.file("conf", "env/dist/etc/worldserver.conf")
    assert package.file_bytes(conf) == CONF


def test_a_characters_package_has_no_server_section(tmp_path: Path) -> None:
    dest = tmp_path / "c.zip"
    write_package(dest, header(), [dump(tmp_path, "acore_auth")])
    m = read_package(dest).manifest
    assert (m.kind, m.server) == ("characters", None)


def test_a_server_kind_without_its_section_is_not_a_package(tmp_path: Path) -> None:
    good = server_pack(tmp_path)
    raw = manifest_of(good)
    del raw["server"]
    with pytest.raises(MovePackageError) as raised:
        read_package(rewrite(good, tmp_path / "bad.zip", manifest=raw))
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_a_characters_kind_with_a_server_section_is_not_a_package(tmp_path: Path) -> None:
    good = server_pack(tmp_path)
    raw = manifest_of(good)
    raw["kind"] = "characters"
    with pytest.raises(MovePackageError) as raised:
        read_package(rewrite(good, tmp_path / "bad.zip", manifest=raw))
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_a_changed_conf_is_refused(tmp_path: Path) -> None:
    good = server_pack(tmp_path)
    bad = rewrite(
        good, tmp_path / "bad.zip", replace={"conf/env/dist/etc/worldserver.conf": b"Rate = 9\n"}
    )
    with pytest.raises(MovePackageError, match="does not match the list inside it"):
        read_package(bad)


def test_a_missing_conf_is_refused_by_name(tmp_path: Path) -> None:
    good = server_pack(tmp_path)
    bad = rewrite(good, tmp_path / "bad.zip", drop=("conf/env/dist/etc/worldserver.conf",))
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert str(raised.value) == (
        "bad.zip is missing conf/env/dist/etc/worldserver.conf, which its list names, so "
        "nothing was brought in."
    )


def test_a_file_member_listed_under_another_name_is_not_a_package(tmp_path: Path) -> None:
    good = server_pack(tmp_path)
    raw = manifest_of(good)
    raw["server"]["files"][0]["target"] = "env/dist/etc/authserver.conf"
    with pytest.raises(MovePackageError) as raised:
        read_package(rewrite(good, tmp_path / "bad.zip", manifest=raw))
    assert str(raised.value) == move.NOT_A_PACKAGE


@pytest.mark.parametrize(
    "target",
    [
        "../etc/worldserver.conf",
        "/etc/worldserver.conf",
        "etc\\worldserver.conf",
        "C:/etc/worldserver.conf",
        ".yulon-install.json",
        "etc/.yulon-folder-id",
        ".db_password",
        "credentials/channel.json",
        "db-secrets/x",
    ],
)
def test_a_target_outside_the_server_or_on_a_record_is_refused_by_the_writer(
    tmp_path: Path, target: str
) -> None:
    with pytest.raises(ValueError):
        PackFile(kind="lua", target=target, data=b"x")


def test_the_answers_file_is_the_one_yulon_record_that_travels(tmp_path: Path) -> None:
    assert PackFile(kind="answers", target=".yulon-module-answers.json", data=b"{}")
    with pytest.raises(ValueError):
        PackFile(kind="answers", target=".yulon-install.json", data=b"{}")


def test_a_conf_must_be_a_conf_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PackFile(kind="conf", target="env/dist/etc/worldserver.conf.dist", data=b"x")


def test_a_hand_edited_manifest_naming_a_record_is_not_a_package(tmp_path: Path) -> None:
    good = server_pack(
        tmp_path, files=(PackFile(kind="lua", target="lua_scripts/a.lua", data=b"x"),)
    )
    raw = manifest_of(good)
    raw["server"]["files"][0]["target"] = ".yulon-install.json"
    raw["server"]["files"][0]["file"] = "lua/.yulon-install.json"
    with zipfile.ZipFile(good) as z:
        body = z.read("lua/lua_scripts/a.lua")
    bad = rewrite(
        good,
        tmp_path / "bad.zip",
        drop=("lua/lua_scripts/a.lua",),
        add={"lua/.yulon-install.json": body},
        manifest=raw,
    )
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_a_commit_that_is_not_a_full_sha_is_refused() -> None:
    with pytest.raises(ValueError):
        PackedSource(repo="a/b", dest=".", commit="abc1234", catalog_pin=None)
    with pytest.raises(ValueError):
        PackedModule(type="module", id="m", origin="catalog", repo="a/b", commit="HEAD")


def test_a_source_dest_outside_the_server_is_refused() -> None:
    with pytest.raises(ValueError):
        PackedSource(repo="a/b", dest="../x", commit=SHA_CORE, catalog_pin=None)


def test_a_server_package_name_says_server() -> None:
    assert (
        move.package_filename("wow-wotlk", AT, kind="server")
        == "yulon-move-server-wow-wotlk-20261009-1530-keep-private.zip"
    )


def test_two_file_members_on_one_target_are_refused_by_the_writer(tmp_path: Path) -> None:
    files = (
        PackFile(kind="lua", target="lua_scripts/a.lua", data=b"x"),
        PackFile(kind="lua", target="lua_scripts/a.lua", data=b"y"),
    )
    with pytest.raises(MovePackageError):
        server_pack(tmp_path, files=files)


def test_files_on_a_characters_package_are_refused_by_the_writer(tmp_path: Path) -> None:
    with pytest.raises(
        MovePackageError, match="^Only a whole-server package carries files besides its databases"
    ):
        write_package(
            tmp_path / "c.zip",
            header(),
            [dump(tmp_path, "acore_auth")],
            files=(PackFile(kind="lua", target="lua_scripts/a.lua", data=b"x"),),
        )


def test_the_manifest_member_names_a_type_and_an_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PackFile(kind="manifest", target="mod-mine", data=b"{}")
    with pytest.raises(ValueError):
        PackFile(kind="manifest", target="planet/mod-mine", data=b"{}")


def test_the_written_manifest_is_valid_json_with_the_server_section(tmp_path: Path) -> None:
    raw = manifest_of(server_pack(tmp_path))
    assert raw["kind"] == "server"
    assert json.dumps(raw["server"]["sources"][0]) == json.dumps(
        {
            "repo": "mod-playerbots/azerothcore-wotlk",
            "dest": ".",
            "commit": SHA_CORE,
            "catalog_pin": SHA_CORE,
        }
    )


def test_a_manifest_listing_one_file_twice_is_not_a_package(tmp_path: Path) -> None:
    good = server_pack(
        tmp_path, files=(PackFile(kind="lua", target="lua_scripts/a.lua", data=b"x"),)
    )
    raw = manifest_of(good)
    raw["server"]["files"] = raw["server"]["files"] * 2
    with pytest.raises(MovePackageError) as raised:
        read_package(rewrite(good, tmp_path / "bad.zip", manifest=raw))
    assert str(raised.value) == move.NOT_A_PACKAGE


# ------------------------------------------------- a folder module's members (T624)


def test_a_module_member_never_names_git_or_a_yulon_record_or_climbs() -> None:
    from yulon.move import PackFile

    for bad in (
        "module/mod-mine/.git/config",
        "module/mod-mine/sub/.GIT/HEAD",
        "module/mod-mine/.yulon-clone.json",
        "module/mod-mine/../x",
        "module/mod-mine/.env",
        "module/mod-mine/",
        "module/Mod_Mine/a",
    ):
        with pytest.raises(ValueError):
            PackFile(kind="module", target=bad, data=b"x")
    PackFile(kind="module", target="module/mod-mine/src/a.cpp", data=b"x")


def test_a_module_member_is_bounded_in_depth() -> None:
    from yulon.move import MODULE_FILE_DEPTH, PackFile

    deep = "/".join(["d"] * (MODULE_FILE_DEPTH + 1)) + "/f"
    with pytest.raises(ValueError):
        PackFile(kind="module", target=f"module/mod-mine/{deep}", data=b"x")


def test_a_folder_module_names_no_repository_and_any_other_names_both() -> None:
    from yulon.move import PackedModule

    PackedModule(type="module", id="mod-mine", origin="folder")
    with pytest.raises(ValueError):
        PackedModule(type="module", id="mod-mine", origin="folder", repo="a/b", commit="a" * 40)
    with pytest.raises(ValueError):
        PackedModule(type="module", id="mod-mine", origin="catalog")
    with pytest.raises(ValueError):
        PackedModule(type="module", id="mod-mine", origin="link", repo="a/bc")
