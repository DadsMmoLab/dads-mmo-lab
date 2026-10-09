"""T601 level 1: the move package, its writer, its reader and the refusals around them.

No Docker and no server: a package is a zip, and everything here is the zip and the
sentences. Every refusal is asserted verbatim, because a sentence that drifts is a
sentence the player stops trusting.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from yulon import move
from yulon.move import (
    Counts,
    DumpFile,
    Evidence,
    Header,
    MovePackageError,
    read_package,
    write_package,
)

AT = datetime(2026, 10, 9, 15, 30)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def header(**over: object) -> Header:
    base: dict[str, object] = {
        "game_id": "wow-wotlk",
        "game_name": "WoW WotLK",
        "realm_name": "My Realm",
        "channel_account": "YULON_ABC123",
        "bot_prefix": "RNDBOT",
        "counts": Counts(accounts=2, characters=3, bot_accounts=500),
        "schema_evidence": {"acore_auth": Evidence(kind="updates", count=10, digest=DIGEST_A)},
        "excluded": ("acore_world",),
        "made": AT,
    }
    base.update(over)
    return Header(**base)  # type: ignore[arg-type]


def dump(tmp_path: Path, schema: str, role: str = "auth", body: bytes | None = None) -> DumpFile:
    path = tmp_path / "src" / f"{schema}.sql"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(
        body
        if body is not None
        else b"-- yulon-backup: game=wow-wotlk\n-- MySQL dump 10.13\nUSE `"
        + schema.encode()
        + b"`;\n"
    )
    return DumpFile(schema, role, path)  # type: ignore[arg-type]


def pack(tmp_path: Path, *schemas: str) -> Path:
    dumps = [dump(tmp_path, s, "auth" if s.endswith("auth") else "characters") for s in schemas]
    dest = tmp_path / "out.zip"
    write_package(dest, header(), dumps)
    return dest


def rewrite(
    source: Path,
    dest: Path,
    *,
    replace: dict[str, bytes] | None = None,
    drop: tuple[str, ...] = (),
    add: dict[str, bytes] | None = None,
    manifest: dict | None = None,
) -> Path:
    """A copy of `source` with members replaced, dropped or added: the corruptions."""
    replace = replace or {}
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w") as out:
        for info in src.infolist():
            if info.filename in drop:
                continue
            data = replace.get(info.filename, src.read(info.filename))
            if info.filename == move.MANIFEST_NAME and manifest is not None:
                data = json.dumps(manifest).encode()
            out.writestr(info.filename, data)
        for name, data in (add or {}).items():
            out.writestr(name, data)
    return dest


def manifest_of(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(move.MANIFEST_NAME))


# ------------------------------------------------------------------ round trip


def test_a_package_survives_write_then_read(tmp_path: Path) -> None:
    path = pack(tmp_path, "acore_auth", "acore_characters")
    package = read_package(path)
    m = package.manifest
    assert m.format == move.FORMAT
    assert m.kind == "characters"
    assert m.game.id == "wow-wotlk"
    assert [d.schema_name for d in m.databases] == ["acore_auth", "acore_characters"]
    assert m.counts == Counts(accounts=2, characters=3, bot_accounts=500)
    assert m.realm_name == "My Realm"
    assert m.channel_account == "YULON_ABC123"
    assert m.schema_evidence["acore_auth"].digest == DIGEST_A
    assert m.excluded == ("acore_world",)


def test_the_member_checksums_are_the_real_ones(tmp_path: Path) -> None:
    path = pack(tmp_path, "acore_auth")
    package = read_package(path)
    body = (tmp_path / "src" / "acore_auth.sql").read_bytes()
    member = package.manifest.databases[0]
    assert member.sha256 == hashlib.sha256(body).hexdigest()
    assert member.bytes == len(body)


def test_extract_writes_the_dump_byte_for_byte(tmp_path: Path) -> None:
    path = pack(tmp_path, "acore_auth")
    out = tmp_path / "out"
    out.mkdir()
    written = read_package(path).extract("acore_auth", out)
    assert written.read_bytes() == (tmp_path / "src" / "acore_auth.sql").read_bytes()
    assert written.name == "acore_auth.sql"


def test_head_gives_the_first_bytes_without_extracting(tmp_path: Path) -> None:
    path = pack(tmp_path, "acore_auth")
    assert read_package(path).head("acore_auth").startswith(b"-- yulon-backup: game=wow-wotlk\n")


def test_the_file_name_carries_the_warning_and_the_game() -> None:
    assert (
        move.package_filename("wow-wotlk", AT)
        == "yulon-move-wow-wotlk-20261009-1530-keep-private.zip"
    )


def test_the_manifest_says_plainly_that_it_holds_logins(tmp_path: Path) -> None:
    secrets = read_package(pack(tmp_path, "acore_auth")).manifest.secrets
    assert "login verifier" in secrets
    assert "Keep it private" in secrets
    assert "database password and the command-channel credential are not in it" in secrets


def test_writing_leaves_no_partial_and_refuses_to_overwrite(tmp_path: Path) -> None:
    path = pack(tmp_path, "acore_auth")
    assert not list(tmp_path.glob("*.partial"))
    with pytest.raises(MovePackageError, match="already exists"):
        write_package(path, header(), [dump(tmp_path, "acore_auth")])


def test_a_package_that_does_not_read_back_is_never_given_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: Path) -> object:
        raise MovePackageError("read-back failed")

    monkeypatch.setattr(move, "read_package", broken)
    dest = tmp_path / "out.zip"
    with pytest.raises(MovePackageError, match="read-back failed"):
        write_package(dest, header(), [dump(tmp_path, "acore_auth")])
    assert not dest.exists()
    assert not list(tmp_path.glob("*.partial"))


# ------------------------------------------------------------------ corruptions


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(MovePackageError) as raised:
        read_package(junk)
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_a_zip_without_a_manifest_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(MovePackageError) as raised:
        read_package(path)
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_a_newer_format_says_which_yulon_made_it(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    raw = manifest_of(good)
    raw["format"] = move.FORMAT + 1
    raw["made_by"]["yulon"] = "9.9.9"
    raw["something_new"] = True  # a newer manifest has fields this one has not heard of
    bad = rewrite(good, tmp_path / "newer.zip", manifest=raw)
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert str(raised.value) == (
        "This file was made by a newer Yu'lon (9.9.9). Update Yu'lon on this computer, "
        "then try again."
    )


def test_a_changed_member_is_refused_before_anything_else(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    bad = rewrite(good, tmp_path / "bad.zip", replace={"db/acore_auth.sql": b"tampered"})
    with pytest.raises(MovePackageError, match="does not match the list inside it"):
        read_package(bad)


def test_a_same_length_substitution_is_refused(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    original = (tmp_path / "src" / "acore_auth.sql").read_bytes()
    swapped = bytes([original[0] ^ 1]) + original[1:]
    bad = rewrite(good, tmp_path / "bad.zip", replace={"db/acore_auth.sql": swapped})
    with pytest.raises(MovePackageError, match="does not match the list inside it"):
        read_package(bad)


def test_a_missing_member_is_refused(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth", "acore_characters")
    bad = rewrite(good, tmp_path / "bad.zip", drop=("db/acore_characters.sql",))
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert str(raised.value) == (
        "bad.zip is missing db/acore_characters.sql, which its list names, so nothing was "
        "brought in."
    )


def test_a_member_the_list_does_not_name_is_refused(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    bad = rewrite(good, tmp_path / "bad.zip", add={"db/extra.sql": b"DROP DATABASE x;"})
    with pytest.raises(
        MovePackageError, match=r"holds a file its list does not name \(db/extra.sql\)"
    ):
        read_package(bad)


@pytest.mark.parametrize(
    "name",
    [
        "../evil.sql",
        "db/../../evil.sql",
        "/etc/passwd",
        "C:/evil.sql",
        "c:evil.sql",
        "db\\evil.sql",
        "\\evil.sql",
        "db/./x.sql",
    ],
)
def test_a_name_that_could_write_outside_the_folder_is_refused(tmp_path: Path, name: str) -> None:
    good = pack(tmp_path, "acore_auth")
    bad = rewrite(good, tmp_path / "bad.zip", add={name: b"x"})
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert "could write outside the folder" in str(raised.value)


def test_two_members_of_one_name_are_refused(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    bad = tmp_path / "dup.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, src.read(info.filename))
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out.writestr("db/acore_auth.sql", b"a second one")
    with pytest.raises(MovePackageError, match="two files of the same name"):
        read_package(bad)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("channel_account", "x'; DROP TABLE account; --"),
        ("channel_account", "yulon_lower"),
        ("realm_name", "bad\nname"),
        ("realm_name", ""),
        ("bot_prefix", "RND%"),
        ("bot_prefix", "a'b"),
    ],
)
def test_a_manifest_string_that_could_reach_sql_unfit_is_not_a_package(
    tmp_path: Path, field: str, value: str
) -> None:
    good = pack(tmp_path, "acore_auth")
    raw = manifest_of(good)
    raw[field] = value
    bad = rewrite(good, tmp_path / "bad.zip", manifest=raw)
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_a_database_file_name_must_be_db_slash_schema(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    raw = manifest_of(good)
    raw["databases"][0]["file"] = "other/acore_auth.sql"
    bad = rewrite(good, tmp_path / "bad.zip", manifest=raw)
    with pytest.raises(MovePackageError) as raised:
        read_package(bad)
    assert str(raised.value) == move.NOT_A_PACKAGE


def test_extract_refuses_a_member_that_changed_since_it_was_read(tmp_path: Path) -> None:
    good = pack(tmp_path, "acore_auth")
    package = read_package(good)
    rewrite(good, tmp_path / "swap.zip", replace={"db/acore_auth.sql": b"other"})
    (tmp_path / "swap.zip").replace(good)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(MovePackageError, match="does not match the list inside it"):
        package.extract("acore_auth", out)
    assert not list(out.iterdir())


# ------------------------------------------------------------------ the game


def test_a_package_from_another_game_is_refused_in_plain_words(tmp_path: Path) -> None:
    manifest = read_package(pack(tmp_path, "acore_auth")).manifest
    move.refuse_another_game(manifest, "wow-wotlk", "WoW WotLK")  # same game: silent
    with pytest.raises(MovePackageError) as raised:
        move.refuse_another_game(manifest, "wow-unbound", "WoW Unbound")
    assert str(raised.value) == (
        "This file holds WoW WotLK characters; this server is WoW Unbound. "
        "Characters can only go into a server of the same game."
    )


# ------------------------------------------------------------------ versions


def ev(digest: str = DIGEST_A, count: int = 10, kind: str = "updates") -> Evidence:
    return Evidence(kind=kind, count=count, digest=digest)


def test_equal_evidence_is_the_same_version() -> None:
    assert move.version_difference({"s": ev()}, {"s": ev()}) is None


def test_a_different_digest_is_refused_with_both_counts() -> None:
    said = move.version_difference({"acore_auth": ev(count=10)}, {"acore_auth": ev(DIGEST_B, 12)})
    assert said == (
        "The databases in this file are not at the same version as this server's "
        "(acore_auth: 10 updates in the file, 12 here), and Yu'lon cannot convert characters "
        "between versions. Put both servers on the same version (press Update the server to "
        "latest… on the one that is behind, and pack again if it was the old one), then try again."
    )


def test_a_different_kind_of_evidence_is_refused() -> None:
    assert move.version_difference({"s": ev()}, {"s": ev(kind="migrations")}) is not None


def test_an_unreadable_target_version_is_not_a_match() -> None:
    said = move.version_difference({"s": ev()}, {"s": None})
    assert said is not None
    assert "this server's version could not be read" in said


def test_a_schema_missing_from_the_target_is_not_a_match() -> None:
    assert move.version_difference({"s": ev()}, {}) is not None
