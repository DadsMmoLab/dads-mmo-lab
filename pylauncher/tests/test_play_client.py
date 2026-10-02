"""Tests for `yulon.play_client` (T181a): building a ready-to-play client folder.

Every test builds a small fake WoW client under `tmp_path`; nothing touches a
real client, a network or docker. The hard-link behaviour is pinned with
`reflink=lambda s, d: False`, so a test run on a copy-on-write filesystem
(btrfs, XFS) still exercises `link()` rather than silently reflinking.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
import shutil
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yulon import play_client

WHEN = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def no_reflink(src: Path, dst: Path) -> bool:
    return False


def fake_client(root: Path) -> Path:
    c = root / "WoW"
    (c / "Data" / "enUS").mkdir(parents=True)
    (c / "Data" / "common.MPQ").write_bytes(b"mpq" * 1000)
    (c / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"loc" * 100)
    (c / "Data" / "enUS" / "realmlist.wtf").write_text("set realmlist logon.example\n")
    (c / "Wow.exe").write_bytes(b"MZexe")
    (c / "DivxDecoder.dll").write_bytes(b"dll")
    (c / "WTF").mkdir()
    (c / "WTF" / "Config.wtf").write_text('SET locale "enUS"\n')
    (c / "Interface" / "AddOns" / "Foo").mkdir(parents=True)
    (c / "Interface" / "AddOns" / "Foo" / "Foo.toc").write_text("## Title: Foo\n")
    (c / "Cache").mkdir()
    (c / "Cache" / "x.wdb").write_bytes(b"c")
    return c


def partial_of(target: Path) -> Path:
    return target.with_name(target.name + ".yulon-partial")


def build(orig: Path, target: Path, tmp_path: Path, **kw: object) -> play_client.Marker:
    args: dict[str, object] = {
        "game": "g",
        "server_dir": tmp_path / "s",
        "allow_full_copy": False,
        "reflink": no_reflink,
        "now": lambda: WHEN,
        "sleep": lambda _seconds: None,
    }
    args.update(kw)
    return play_client.create(orig, target, **args)  # type: ignore[arg-type]


def test_mpq_and_dll_are_hard_links_and_everything_else_is_a_copy(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "WoW (Yu'lon – WoW WotLK)"
    play_client.create(
        orig,
        target,
        game="wow-wotlk",
        server_dir=tmp_path / "srv",
        allow_full_copy=False,
        reflink=no_reflink,
    )
    assert os.path.samefile(orig / "Data/common.MPQ", target / "Data/common.MPQ")
    assert os.path.samefile(
        orig / "Data/enUS/locale-enUS.MPQ", target / "Data/enUS/locale-enUS.MPQ"
    )
    assert os.path.samefile(orig / "DivxDecoder.dll", target / "DivxDecoder.dll")
    for rel in (
        "Wow.exe",
        "WTF/Config.wtf",
        "Data/enUS/realmlist.wtf",
        "Interface/AddOns/Foo/Foo.toc",
    ):
        assert (target / rel).read_bytes() == (orig / rel).read_bytes()
        assert not os.path.samefile(orig / rel, target / rel)
    assert not (target / "Cache").exists()
    marker = play_client.read_marker(target)
    assert marker is not None and marker.game == "wow-wotlk"
    assert not partial_of(target).exists()


def test_writing_any_copied_file_leaves_the_original_byte_identical(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    before = {p: p.read_bytes() for p in orig.rglob("*") if p.is_file()}
    build(orig, target, tmp_path)
    written = 0
    for p in target.rglob("*"):
        if p.is_file() and p.suffix.lower() not in {".mpq", ".dll"}:
            p.write_bytes(b"changed")
            written += 1
    assert written >= 5  # Wow.exe, Config.wtf, realmlist.wtf, Foo.toc, the marker
    assert {p: p.read_bytes() for p in orig.rglob("*") if p.is_file()} == before


def test_marker_records_where_the_folder_came_from(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    returned = build(orig, target, tmp_path, game="wow-tbc", server_dir=tmp_path / "srv")
    raw = json.loads((target / play_client.MARKER).read_text(encoding="utf-8"))
    assert raw == {
        "version": 1,
        "game": "wow-tbc",
        "server_dir": str(tmp_path / "srv"),
        "source_client_dir": str(orig),
        "created_at": "2026-09-30T12:00:00Z",
        "full_copy": False,
    }
    assert play_client.read_marker(target) == returned


def test_the_marker_is_written_before_any_client_file(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    seen: list[bool] = []

    def watching_link(src: Path, dst: Path) -> None:
        seen.append((partial_of(target) / play_client.MARKER).is_file())
        os.link(src, dst)

    build(orig, target, tmp_path, link=watching_link)
    assert seen and all(seen)


def test_cross_volume_needs_consent_then_copies(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def exdev(src: Path, dst: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    with pytest.raises(play_client.PlayClientError, match="another drive"):
        build(orig, target, tmp_path, link=exdev)
    assert not target.exists() and not partial_of(target).exists()
    build(orig, target, tmp_path, link=exdev, allow_full_copy=True)
    assert (target / "Data/common.MPQ").read_bytes() == (orig / "Data/common.MPQ").read_bytes()
    assert not os.path.samefile(orig / "Data/common.MPQ", target / "Data/common.MPQ")


def test_linked_files_are_reflinked_when_the_filesystem_can(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    reflinked: list[Path] = []

    def copying_reflink(src: Path, dst: Path) -> bool:
        shutil.copy2(src, dst)
        reflinked.append(Path(src).relative_to(orig))
        return True

    def forbidden_link(src: Path, dst: Path) -> None:
        raise AssertionError(f"link() called for {src} after reflink succeeded")

    build(orig, target, tmp_path, reflink=copying_reflink, link=forbidden_link)
    assert sorted(reflinked) == sorted(
        [Path("Data/common.MPQ"), Path("Data/enUS/locale-enUS.MPQ"), Path("DivxDecoder.dll")]
    )
    assert not os.path.samefile(orig / "Data/common.MPQ", target / "Data/common.MPQ")
    assert (target / "Data/common.MPQ").read_bytes() == (orig / "Data/common.MPQ").read_bytes()


def test_any_other_failure_removes_the_partial_folder(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def denied(src: Path, dst: Path) -> None:
        raise PermissionError(errno.EACCES, "Access is denied")

    with pytest.raises(play_client.PlayClientError, match="Access is denied"):
        build(orig, target, tmp_path, link=denied)
    assert not target.exists() and not partial_of(target).exists()
    assert (orig / "Data/common.MPQ").read_bytes() == b"mpq" * 1000


def test_running_out_of_space_says_how_much_was_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def full_disk(src: object, dst: object, **kw: object) -> object:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(play_client.shutil, "copy2", full_disk)
    with pytest.raises(play_client.PlayClientError, match=r"ran out of space.*needs \d"):
        build(orig, target, tmp_path)
    assert not target.exists() and not partial_of(target).exists()


def test_a_marked_leftover_partial_is_replaced(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    partial = partial_of(target)
    partial.mkdir()
    (partial / "half.bin").write_bytes(b"x")
    stale = play_client.Marker(
        game="g", server_dir=tmp_path / "s", source_client_dir=orig, created_at=WHEN
    )
    (partial / play_client.MARKER).write_text(stale.model_dump_json(), encoding="utf-8")
    build(orig, target, tmp_path)
    assert not partial.exists()
    assert not (target / "half.bin").exists()
    assert (target / "Wow.exe").is_file()


def test_an_unmarked_folder_named_like_a_partial_is_left_alone(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    partial = partial_of(target)
    partial.mkdir()
    (partial / "mine.txt").write_text("the player's own")
    with pytest.raises(play_client.PlayClientError, match="yulon-partial"):
        build(orig, target, tmp_path)
    assert (partial / "mine.txt").read_text() == "the player's own"
    assert not target.exists()


def test_an_existing_ready_to_play_client_is_not_built_over(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    build(orig, target, tmp_path)
    (target / "WTF" / "Config.wtf").write_text("the player's settings")
    with pytest.raises(play_client.PlayClientError, match="already"):
        build(orig, target, tmp_path)
    assert (target / "WTF" / "Config.wtf").read_text() == "the player's settings"


def test_left_out_folders_are_matched_top_level_and_case_insensitively(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    (orig / "logs").mkdir()
    (orig / "logs" / "a.log").write_text("l")
    (orig / "SCREENSHOTS").mkdir()
    (orig / "SCREENSHOTS" / "s.jpg").write_bytes(b"j")
    (orig / "Errors").mkdir()
    (orig / "Errors" / "e.txt").write_text("e")
    (orig / "Interface" / "Cache").mkdir()
    (orig / "Interface" / "Cache" / "kept.txt").write_text("k")
    p = play_client.plan(orig, tmp_path / "t")
    everything = set(p.linked) | set(p.copied)
    assert Path("Interface/Cache/kept.txt") in everything
    assert not any(rel.parts[0].lower() in play_client.LEFT_OUT for rel in everything)


def test_plan_classifies_by_suffix_case_insensitively(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    (orig / "Data" / "patch-2.mpq").write_bytes(b"p")
    (orig / "extra.DLL").write_bytes(b"d")
    p = play_client.plan(orig, tmp_path / "t")
    assert set(p.linked) == {
        Path("Data/common.MPQ"),
        Path("Data/enUS/locale-enUS.MPQ"),
        Path("Data/patch-2.mpq"),
        Path("DivxDecoder.dll"),
        Path("extra.DLL"),
    }
    assert set(p.copied) == {
        Path("Wow.exe"),
        Path("Data/enUS/realmlist.wtf"),
        Path("WTF/Config.wtf"),
        Path("Interface/AddOns/Foo/Foo.toc"),
    }


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="POSIX symlinks")
def test_plan_does_not_follow_symlinks(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "big.MPQ").write_bytes(b"e")
    os.symlink(elsewhere, orig / "Data" / "linked-dir")
    os.symlink(elsewhere / "big.MPQ", orig / "Data" / "linked.MPQ")
    p = play_client.plan(orig, tmp_path / "t")
    everything = set(p.linked) | set(p.copied)
    assert Path("Data/linked.MPQ") not in everything
    assert Path("Data/linked-dir/big.MPQ") not in everything
    assert Path("Data/common.MPQ") in p.linked  # the walk itself still ran


def test_target_inside_the_original_is_refused(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    with pytest.raises(play_client.PlayClientError, match="inside"):
        play_client.plan(orig, orig / "sub")


def test_target_that_is_the_original_is_refused(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    with pytest.raises(play_client.PlayClientError, match="is your own client"):
        play_client.plan(orig, tmp_path / "WoW" / ".." / "WoW")


def test_existing_unmarked_target_is_refused(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    (tmp_path / "t").mkdir()
    with pytest.raises(play_client.PlayClientError, match="already exists"):
        play_client.plan(orig, tmp_path / "t")


def test_an_original_without_a_data_folder_is_refused(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    shutil.rmtree(orig / "Data")
    with pytest.raises(play_client.PlayClientError, match="Data"):
        play_client.plan(orig, tmp_path / "t")


def test_default_target_is_a_sibling_named_after_the_game(tmp_path: Path) -> None:
    assert (
        play_client.default_target(tmp_path / "WoW 3.3.5a", "WoW WotLK")
        == tmp_path / "WoW 3.3.5a (Yu'lon – WoW WotLK)"
    )


def test_plan_counts_shared_and_own_bytes(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    p = play_client.plan(orig, tmp_path / "t")
    assert p.shared_bytes == 3000 + 300 + 3 and p.same_volume
    own = (
        len("set realmlist logon.example\n")
        + 5
        + len('SET locale "enUS"\n')
        + len("## Title: Foo\n")
    )
    assert p.own_bytes == own


def test_read_marker_is_none_when_absent_or_invalid(tmp_path: Path) -> None:
    assert play_client.read_marker(tmp_path) is None
    (tmp_path / play_client.MARKER).write_text('{"version": 1, "game": "g"}', encoding="utf-8")
    assert play_client.read_marker(tmp_path) is None
    (tmp_path / play_client.MARKER).write_text("not json", encoding="utf-8")
    assert play_client.read_marker(tmp_path) is None


def test_try_reflink_never_raises(tmp_path: Path) -> None:
    src = tmp_path / "a.MPQ"
    src.write_bytes(b"abc")
    dst = tmp_path / "b.MPQ"
    if play_client.try_reflink(src, dst):
        assert dst.read_bytes() == b"abc"
    else:
        assert not dst.exists()  # a failed clone leaves nothing for link() to trip over
    assert play_client.try_reflink(tmp_path / "missing", tmp_path / "c") is False
    assert not (tmp_path / "c").exists()


def test_try_reflink_is_false_off_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "a.MPQ"
    src.write_bytes(b"abc")
    monkeypatch.setattr(play_client.sys, "platform", "win32")
    assert play_client.try_reflink(src, tmp_path / "b.MPQ") is False
    assert not (tmp_path / "b.MPQ").exists()


def test_a_marker_in_the_original_is_not_copied_over_the_new_one(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    theirs = play_client.Marker(
        game="other", server_dir=tmp_path / "other", source_client_dir=tmp_path, created_at=WHEN
    )
    (orig / play_client.MARKER).write_text(theirs.model_dump_json(), encoding="utf-8")
    target = tmp_path / "t"
    assert Path(play_client.MARKER) not in play_client.plan(orig, target).copied
    build(orig, target, tmp_path, game="mine")
    marker = play_client.read_marker(target)
    assert marker is not None and marker.game == "mine"


# -- fix round 1 --------------------------------------------------------------


def test_a_marked_partial_of_another_server_is_left_alone(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    partial = partial_of(target)
    partial.mkdir()
    (partial / "half.bin").write_bytes(b"x")
    theirs = play_client.Marker(
        game="g", server_dir=tmp_path / "other-server", source_client_dir=orig, created_at=WHEN
    )
    (partial / play_client.MARKER).write_text(theirs.model_dump_json(), encoding="utf-8")
    with pytest.raises(play_client.PlayClientError, match="another server.*[Cc]hoose another"):
        build(orig, target, tmp_path, game="g", server_dir=tmp_path / "s")
    assert (partial / "half.bin").read_bytes() == b"x"
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
def test_cleanup_leaves_a_read_only_original_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    mpq = orig / "Data" / "common.MPQ"
    os.chmod(mpq, 0o444)
    before = mpq.stat().st_mode
    blocked: list[str] = []

    def windows_like_unlink(path: object) -> None:
        # Windows refuses to delete a file whose read-only attribute is set.
        if not os.lstat(path).st_mode & 0o200:  # type: ignore[arg-type]
            blocked.append(str(path))
            raise PermissionError(errno.EACCES, "Access is denied", str(path))
        os.unlink(path)  # type: ignore[arg-type]

    real_remove = play_client.remove_folder
    monkeypatch.setattr(
        play_client,
        "remove_folder",
        lambda folder, **kw: real_remove(folder, **kw, unlink=windows_like_unlink),
    )
    linked: list[Path] = []

    def counting_link(src: Path, dst: Path) -> None:
        os.link(src, dst)
        linked.append(Path(src))

    def broken_copy(src: object, dst: object, **kw: object) -> object:
        raise OSError(errno.EIO, "I/O error")  # after every linked file is in place

    monkeypatch.setattr(play_client.shutil, "copy2", broken_copy)
    with pytest.raises(play_client.PlayClientError, match="I/O error"):
        build(orig, target, tmp_path, link=counting_link)
    assert mpq in linked
    assert str(partial_of(target) / "Data" / "common.MPQ") in blocked, "read-only path not hit"
    assert not partial_of(target).exists()
    assert mpq.stat().st_mode == before


def test_default_target_stays_plain_when_free_or_this_servers(tmp_path: Path) -> None:
    orig = tmp_path / "WoW"
    plain = tmp_path / "WoW (Yu'lon – WoW WotLK)"
    assert play_client.default_target(orig, "WoW WotLK", tmp_path / "srv") == plain
    plain.mkdir()
    mine = play_client.Marker(
        game="g", server_dir=tmp_path / "srv", source_client_dir=orig, created_at=WHEN
    )
    (plain / play_client.MARKER).write_text(mine.model_dump_json(), encoding="utf-8")
    assert play_client.default_target(orig, "WoW WotLK", tmp_path / "srv") == plain


def test_default_target_names_the_server_when_another_server_has_the_plain_name(
    tmp_path: Path,
) -> None:
    orig = tmp_path / "WoW"
    plain = tmp_path / "WoW (Yu'lon – WoW WotLK)"
    plain.mkdir()
    theirs = play_client.Marker(
        game="g", server_dir=tmp_path / "srv-a", source_client_dir=orig, created_at=WHEN
    )
    (plain / play_client.MARKER).write_text(theirs.model_dump_json(), encoding="utf-8")
    assert play_client.default_target(orig, "WoW WotLK", tmp_path / "srv-b") == (
        tmp_path / "WoW (Yu'lon – WoW WotLK, srv-b)"
    )


def test_default_target_names_the_server_when_the_plain_name_is_not_yulons(
    tmp_path: Path,
) -> None:
    orig = tmp_path / "WoW"
    (tmp_path / "WoW (Yu'lon – WoW WotLK)").mkdir()
    assert play_client.default_target(orig, "WoW WotLK", tmp_path / "srv-b") == (
        tmp_path / "WoW (Yu'lon – WoW WotLK, srv-b)"
    )


def test_plan_names_the_other_server_a_target_belongs_to(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    build(orig, target, tmp_path, server_dir=tmp_path / "srv-a")
    with pytest.raises(play_client.PlayClientError, match="another server at .*srv-a") as info:
        play_client.plan(orig, target, server_dir=tmp_path / "srv-b")
    assert "Refresh" not in str(info.value)


@pytest.mark.parametrize(
    "code",
    sorted({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EMLINK}),
    ids=errno.errorcode.get,
)
def test_a_drive_that_cannot_hard_link_needs_consent_then_copies(tmp_path: Path, code: int) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def cannot(src: Path, dst: Path) -> None:
        raise OSError(code, os.strerror(code))

    with pytest.raises(play_client.PlayClientError, match="cannot share files.*full copy needs"):
        build(orig, target, tmp_path, link=cannot)
    assert not target.exists() and not partial_of(target).exists()
    build(orig, target, tmp_path, link=cannot, allow_full_copy=True)
    assert (target / "Data/common.MPQ").read_bytes() == (orig / "Data/common.MPQ").read_bytes()
    assert not os.path.samefile(orig / "Data/common.MPQ", target / "Data/common.MPQ")


def test_windows_invalid_function_on_link_needs_consent(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def invalid_function(src: Path, dst: Path) -> None:
        exc = OSError(errno.EINVAL, "Incorrect function")
        exc.winerror = 1  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(play_client.PlayClientError, match="cannot share files"):
        build(orig, target, tmp_path, link=invalid_function)


def test_a_cleanup_that_fails_does_not_claim_the_folder_was_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def stuck(folder: Path, **kw: object) -> None:
        raise PermissionError(errno.EACCES, "in use", str(folder))

    def denied(src: Path, dst: Path) -> None:
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(play_client, "remove_folder", stuck)
    with pytest.raises(play_client.PlayClientError, match="could not be removed") as info:
        build(orig, target, tmp_path, link=denied)
    assert "was removed" not in str(info.value)
    assert "next attempt" in str(info.value)
    assert play_client.read_marker(partial_of(target)) is not None


def test_a_full_copy_that_runs_out_of_space_counts_the_shared_files_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    with open(orig / "Data" / "common.MPQ", "r+b") as f:
        f.truncate(64 * 1024**2)  # 0.0625 GiB: "0.1 GB" counted, "0.0 GB" if left out
    target = tmp_path / "t"

    def exdev(src: Path, dst: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    def full_disk(src: object, dst: object, **kw: object) -> object:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(play_client.shutil, "copy2", full_disk)
    with pytest.raises(play_client.PlayClientError, match=r"needs 0\.1 GB"):
        build(orig, target, tmp_path, link=exdev, allow_full_copy=True)


def test_running_out_of_space_while_linking_counts_only_the_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    with open(orig / "Data" / "common.MPQ", "r+b") as f:
        f.truncate(64 * 1024**2)
    target = tmp_path / "t"

    def full_disk(src: object, dst: object, **kw: object) -> object:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(play_client.shutil, "copy2", full_disk)
    with pytest.raises(play_client.PlayClientError, match=r"needs 0\.0 GB"):
        build(orig, target, tmp_path)


# -- fix round 2 --------------------------------------------------------------


def _as_junction(monkeypatch: pytest.MonkeyPatch, *junctions: Path, tag: int | None = None) -> None:
    """Make `play_client._lstat` report `junctions` the way Windows reports a junction."""
    real = os.lstat
    marked = {str(j) for j in junctions}
    reparse_tag = play_client.IO_REPARSE_TAG_MOUNT_POINT if tag is None else tag

    def fake(path: object) -> object:
        st = real(path)  # type: ignore[arg-type]
        if os.fspath(path) not in marked:  # type: ignore[call-overload]
            return st
        return types.SimpleNamespace(
            st_mode=st.st_mode,
            st_file_attributes=play_client.FILE_ATTRIBUTE_REPARSE_POINT | 0x10,  # | DIRECTORY
            st_reparse_tag=reparse_tag,
        )

    monkeypatch.setattr(play_client, "_lstat", fake)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_remove_folder_removes_a_symlinked_folder_inside_but_not_what_it_points_at(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.MPQ").write_bytes(b"keep")
    folder = tmp_path / "t.yulon-partial"
    (folder / "Data").mkdir(parents=True)
    os.symlink(elsewhere, folder / "Data" / "linked")
    play_client.remove_folder(folder)
    assert not folder.exists()
    assert (elsewhere / "precious.MPQ").read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_remove_folder_refuses_a_folder_that_is_itself_a_link(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.MPQ").write_bytes(b"keep")
    folder = tmp_path / "t.yulon-partial"
    os.symlink(elsewhere, folder)
    with pytest.raises(OSError, match="link"):
        play_client.remove_folder(folder)
    assert folder.is_symlink()
    assert (elsewhere / "precious.MPQ").read_bytes() == b"keep"


def test_remove_folder_never_walks_into_a_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "t.yulon-partial"
    junction = folder / "Data" / "junction"
    junction.mkdir(parents=True)
    (junction / "precious.MPQ").write_bytes(b"keep")  # what the junction's target holds
    _as_junction(monkeypatch, junction)
    removed: list[str] = []

    def recording_unlink(path: Path) -> None:
        removed.append(os.fspath(path))
        os.unlink(path)

    with pytest.raises(OSError):  # a real directory, so removing "the link" cannot succeed
        play_client.remove_folder(folder, unlink=recording_unlink)
    assert (junction / "precious.MPQ").read_bytes() == b"keep"
    assert os.fspath(junction / "precious.MPQ") not in removed


def test_plan_leaves_out_a_junctioned_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    _as_junction(monkeypatch, orig / "Interface")
    p = play_client.plan(orig, tmp_path / "t")
    assert not any(rel.parts[0] == "Interface" for rel in p.copied + p.linked)
    assert Path("WTF/Config.wtf") in p.copied


def test_plan_keeps_a_cloud_placeholder_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OneDrive "files on demand" are reparse points too, but not links: dropping them
    # would build a client without the player's archives.
    orig = fake_client(tmp_path)
    _as_junction(monkeypatch, orig / "Data", tag=0x9000001A)  # IO_REPARSE_TAG_CLOUD_6
    p = play_client.plan(orig, tmp_path / "t")
    assert Path("Data/common.MPQ") in p.linked


def test_a_folder_that_could_not_be_made_is_not_reported_removed(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    (tmp_path / "afile").write_text("x")
    target = tmp_path / "afile" / "t"
    with pytest.raises(play_client.PlayClientError) as info:
        build(orig, target, tmp_path)
    assert "was removed" not in str(info.value)
    assert "Nothing was created" in str(info.value)


def test_a_client_drive_that_cannot_share_says_the_full_copy_is_the_way(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)

    def eperm(src: Path, dst: Path) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    with pytest.raises(
        play_client.PlayClientError,
        match="drive your client .* is on.*The only way is the full copy",
    ) as info:
        build(orig, tmp_path / "t", tmp_path, link=eperm)
    assert "the one your client is on" not in str(info.value)


def test_cannot_share_across_drives_keeps_the_choose_a_folder_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    real_plan = play_client.plan
    monkeypatch.setattr(
        play_client,
        "plan",
        lambda *a, **kw: dataclasses.replace(real_plan(*a, **kw), same_volume=False),
    )

    def eperm(src: Path, dst: Path) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    with pytest.raises(play_client.PlayClientError, match="the one your client is on"):
        build(orig, tmp_path / "t", tmp_path, link=eperm)


def _leftover(tmp_path: Path, orig: Path, *, game: str, server: str) -> Path:
    partial = partial_of(tmp_path / "t")
    partial.mkdir()
    m = play_client.Marker(
        game=game, server_dir=tmp_path / server, source_client_dir=orig, created_at=WHEN
    )
    (partial / play_client.MARKER).write_text(m.model_dump_json(), encoding="utf-8")
    return partial


def test_a_leftover_of_another_game_says_it_is_another_game(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    partial = _leftover(tmp_path, orig, game="wow-tbc", server="s")
    with pytest.raises(play_client.PlayClientError, match="another game, wow-tbc") as info:
        build(orig, tmp_path / "t", tmp_path, game="g", server_dir=tmp_path / "s")
    assert "another server" not in str(info.value)
    assert partial.exists()


def test_a_leftover_of_another_server_names_that_server(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    _leftover(tmp_path, orig, game="g", server="other")
    with pytest.raises(play_client.PlayClientError, match="another server at .*other") as info:
        build(orig, tmp_path / "t", tmp_path, game="g", server_dir=tmp_path / "s")
    assert "another game" not in str(info.value)


def test_a_failed_mode_restore_does_not_hide_the_delete_error(tmp_path: Path) -> None:
    folder = tmp_path / "t.yulon-partial"
    folder.mkdir()
    (folder / "f.MPQ").write_bytes(b"x")
    calls: list[int] = []

    def refusing_unlink(path: Path) -> None:
        calls.append(1)
        if len(calls) == 3:
            os.unlink(path)  # gone, so putting its mode back fails too
            raise PermissionError(errno.EACCES, "late refusal", str(path))
        raise PermissionError(errno.EACCES, "refused", str(path))

    with pytest.raises(PermissionError, match="late refusal"):
        play_client.remove_folder(folder, unlink=refusing_unlink)


# -- Task 2: stale, refresh, delete, leftover partials -----------------------


def exdev(src: Path, dst: Path) -> None:
    raise OSError(errno.EXDEV, "cross-device")


def refresh(play: Path, orig: Path, tmp_path: Path, **kw: object) -> tuple[Path, ...]:
    args: dict[str, object] = {
        "game": "g",
        "server_dir": tmp_path / "s",
        "reflink": no_reflink,
        "exe_patch": None,
    }
    args.update(kw)
    return play_client.refresh(play, orig, **args)  # type: ignore[arg-type]


def replace_file(path: Path, data: bytes) -> None:
    """What a patcher does: a new file under the old name, not a write into the old one."""
    path.unlink()
    path.write_bytes(data)


def mark(folder: Path, orig: Path, *, game: str = "g", server_dir: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    m = play_client.Marker(
        game=game, server_dir=server_dir, source_client_dir=orig, created_at=WHEN
    )
    (folder / play_client.MARKER).write_text(m.model_dump_json(), encoding="utf-8")


def test_a_replaced_original_archive_is_stale_and_refresh_shares_it_again(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    assert play_client.stale(play, orig) == ()
    (play / "WTF" / "Config.wtf").write_text("the player's settings")
    replace_file(orig / "Data" / "common.MPQ", b"patched" * 100)

    assert play_client.stale(play, orig) == (Path("Data/common.MPQ"),)
    changed = refresh(play, orig, tmp_path)

    assert changed == (Path("Data/common.MPQ"),)
    assert os.path.samefile(orig / "Data/common.MPQ", play / "Data/common.MPQ")
    assert (play / "WTF" / "Config.wtf").read_text() == "the player's settings"
    assert play_client.stale(play, orig) == ()
    assert not list(play.rglob("*.yulon-refresh"))


def test_refresh_writes_the_new_file_beside_the_old_one_before_replacing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    replace_file(orig / "Data" / "common.MPQ", b"patched")
    seen: list[tuple[bool, Path]] = []

    def watching_link(src: Path, dst: Path) -> None:
        seen.append(((play / "Data" / "common.MPQ").exists(), Path(dst)))
        os.link(src, dst)

    dst = play / "Data" / "common.MPQ"
    unlinked: list[str] = []
    replaced: list[tuple[str, str]] = []
    real_unlink, real_replace = os.unlink, os.replace

    def recording_unlink(path: object, *a: object, **kw: object) -> None:
        unlinked.append(os.fspath(path))  # type: ignore[call-overload]
        real_unlink(path, *a, **kw)  # type: ignore[arg-type]

    def recording_replace(src: object, target: object, *a: object, **kw: object) -> None:
        replaced.append((os.fspath(src), os.fspath(target)))  # type: ignore[call-overload]
        real_replace(src, target, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(play_client.os, "unlink", recording_unlink)
    monkeypatch.setattr(play_client.os, "remove", recording_unlink)
    monkeypatch.setattr(play_client.os, "replace", recording_replace)
    refresh(play, orig, tmp_path, link=watching_link)
    assert len(seen) == 1
    old_still_there, temp = seen[0]
    assert old_still_there
    assert temp.parent == play / "Data" and temp.name != "common.MPQ"
    assert str(dst) not in unlinked, "the old file was removed before the new one took its name"
    assert replaced == [(str(temp), str(dst))]


def test_a_changed_wow_exe_is_stale_and_refresh_copies_it(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    stamp = (orig / "Wow.exe").stat()
    (orig / "Wow.exe").write_bytes(b"MZnew")  # same size: only the bytes tell
    os.utime(orig / "Wow.exe", ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    assert play_client.stale(play, orig) == (Path("Wow.exe"),)
    assert refresh(play, orig, tmp_path) == (Path("Wow.exe"),)
    assert (play / "Wow.exe").read_bytes() == b"MZnew"
    assert not os.path.samefile(orig / "Wow.exe", play / "Wow.exe")
    assert play_client.stale(play, orig) == ()


def test_an_archive_the_original_lacks_is_never_stale_and_survives_refresh(
    tmp_path: Path,
) -> None:
    # A module's patch, a player's own patch-Z.MPQ, an archive the original's patcher
    # removed: after the switch the ready-to-play client may hold the only copy.
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    (play / "Data" / "patch-Z.MPQ").write_bytes(b"only here")
    (orig / "DivxDecoder.dll").unlink()
    replace_file(orig / "Data" / "common.MPQ", b"new!")  # so refresh has something to do
    assert play_client.stale(play, orig) == (Path("Data/common.MPQ"),)
    assert refresh(play, orig, tmp_path) == (Path("Data/common.MPQ"),)
    assert (play / "Data" / "patch-Z.MPQ").read_bytes() == b"only here"
    assert (play / "DivxDecoder.dll").read_bytes() == b"dll"


def test_a_full_copy_is_stale_only_when_size_or_time_differ(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path, link=exdev, allow_full_copy=True)
    marker = play_client.read_marker(play)
    assert marker is not None and marker.full_copy
    assert play_client.stale(play, orig) == ()
    mpq = orig / "Data" / "common.MPQ"
    st = mpq.stat()
    os.utime(mpq, ns=(st.st_atime_ns, st.st_mtime_ns + 3 * 10**9))

    assert play_client.stale(play, orig) == (Path("Data/common.MPQ"),)

    def no_sharing(src: Path, dst: Path) -> None:
        raise AssertionError("a full copy is refreshed by copying, not by sharing")

    assert refresh(play, orig, tmp_path, link=no_sharing, reflink=no_sharing) == (
        Path("Data/common.MPQ"),
    )
    assert not os.path.samefile(mpq, play / "Data/common.MPQ")
    assert play_client.stale(play, orig) == ()


def test_a_time_within_two_seconds_is_the_same_file(tmp_path: Path) -> None:
    # A FAT32 copy keeps times to two seconds; exactness would offer Refresh forever.
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path, link=exdev, allow_full_copy=True)
    st = (play / "Data" / "common.MPQ").stat()
    os.utime(play / "Data" / "common.MPQ", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert play_client.stale(play, orig) == ()


@pytest.mark.parametrize(("wtf", "interface"), [("WTF", "INTERFACE"), ("wtf", "interface")])
def test_refresh_never_touches_wtf_or_interface_whatever_their_case(
    tmp_path: Path, wtf: str, interface: str
) -> None:
    orig = fake_client(tmp_path)
    (orig / "Interface").rename(orig / interface)
    if wtf != "WTF":
        (orig / "WTF").rename(orig / wtf)
    (orig / interface / "AddOns" / "Foo" / "helper.dll").write_bytes(b"old")
    (orig / wtf / "odd.MPQ").write_bytes(b"old")
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    replace_file(orig / interface / "AddOns" / "Foo" / "helper.dll", b"new!")
    replace_file(orig / wtf / "odd.MPQ", b"new!")
    replace_file(orig / "Data" / "common.MPQ", b"new!")  # so refresh has something to do

    assert play_client.stale(play, orig) == (Path("Data/common.MPQ"),)
    refresh(play, orig, tmp_path)
    assert (play / interface / "AddOns" / "Foo" / "helper.dll").read_bytes() == b"old"
    assert (play / wtf / "odd.MPQ").read_bytes() == b"old"


def test_refresh_needs_a_marker(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    replace_file(orig / "Data" / "common.MPQ", b"new!")
    (play / play_client.MARKER).unlink()
    with pytest.raises(play_client.PlayClientError, match="not made by Yu'lon"):
        refresh(play, orig, tmp_path)
    assert (play / "Data" / "common.MPQ").read_bytes() == b"mpq" * 1000


def test_refresh_refuses_a_client_it_was_not_made_from(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    other = fake_client(tmp_path / "elsewhere")
    with pytest.raises(play_client.PlayClientError, match="was made from"):
        refresh(play, other, tmp_path)
    assert os.path.samefile(orig / "Data/common.MPQ", play / "Data/common.MPQ")


def test_refresh_of_a_shared_client_that_cannot_share_any_more_changes_nothing(
    tmp_path: Path,
) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    replace_file(orig / "Data" / "common.MPQ", b"new!")
    with pytest.raises(play_client.PlayClientError, match="make it again.*full copy"):
        refresh(play, orig, tmp_path, link=exdev)
    assert (play / "Data" / "common.MPQ").read_bytes() == b"mpq" * 1000
    assert not list(play.rglob("*.yulon-refresh"))


def _windows_like_replace(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Windows refuses to rename onto a file whose read-only attribute is set."""
    real = os.replace
    refused: list[str] = []

    def replace(src: object, dst: object) -> None:
        if os.path.isfile(dst) and not os.lstat(dst).st_mode & 0o200:  # type: ignore[arg-type]
            refused.append(os.fspath(dst))  # type: ignore[call-overload]
            raise PermissionError(errno.EACCES, "Access is denied", os.fspath(dst))  # type: ignore[call-overload]
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(play_client.os, "replace", replace)
    return refused


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
def test_refresh_replaces_a_read_only_archive_that_is_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    os.chmod(orig / "Data" / "common.MPQ", 0o444)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    replace_file(orig / "Data" / "common.MPQ", b"new!")
    refused = _windows_like_replace(monkeypatch)
    refresh(play, orig, tmp_path)
    assert refused == [str(play / "Data" / "common.MPQ")], "read-only path not hit"
    assert os.path.samefile(orig / "Data/common.MPQ", play / "Data/common.MPQ")


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
def test_refresh_does_not_unprotect_an_archive_another_client_shares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    os.chmod(orig / "Data" / "common.MPQ", 0o444)
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    build(orig, mine, tmp_path)
    build(orig, theirs, tmp_path, server_dir=tmp_path / "s2")
    replace_file(orig / "Data" / "common.MPQ", b"new!")
    _windows_like_replace(monkeypatch)
    with pytest.raises(play_client.PlayClientError, match="read-only") as info:
        refresh(mine, orig, tmp_path)
    assert "Delete the other ready-to-play client" in str(info.value)
    assert "flag yourself" not in str(info.value)
    assert (theirs / "Data" / "common.MPQ").stat().st_mode & 0o777 == 0o444
    assert (mine / "Data" / "common.MPQ").read_bytes() == b"mpq" * 1000


def test_delete_removes_the_client_and_keeps_the_originals_files(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    os.chmod(orig / "Data" / "common.MPQ", 0o444)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    play_client.delete(play, game="g", server_dir=tmp_path / "s")
    assert not play.exists()
    assert (orig / "Data" / "common.MPQ").read_bytes() == b"mpq" * 1000
    assert (orig / "Data" / "common.MPQ").stat().st_mode & 0o777 == 0o444
    assert (orig / "WTF" / "Config.wtf").is_file()


def test_delete_refuses_a_folder_without_a_marker(tmp_path: Path) -> None:
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "mine.txt").write_text("the player's own")
    with pytest.raises(play_client.PlayClientError, match="not made by Yu'lon"):
        play_client.delete(folder, game="g", server_dir=tmp_path / "s")
    assert (folder / "mine.txt").read_text() == "the player's own"


def test_delete_refuses_another_servers_client(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path, server_dir=tmp_path / "other")
    with pytest.raises(play_client.PlayClientError, match="another server at .*other"):
        play_client.delete(play, game="g", server_dir=tmp_path / "s")
    assert (play / "Wow.exe").is_file()


def test_delete_refuses_another_games_client(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path, game="wow-tbc")
    with pytest.raises(play_client.PlayClientError, match="another game, wow-tbc"):
        play_client.delete(play, game="g", server_dir=tmp_path / "s")
    assert (play / "Wow.exe").is_file()


def test_delete_of_a_folder_that_is_gone_does_nothing(tmp_path: Path) -> None:
    play_client.delete(tmp_path / "t", game="g", server_dir=tmp_path / "s")
    assert not (tmp_path / "t").exists()


def test_a_delete_that_stops_half_way_keeps_the_marker_so_it_can_be_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    real_remove = play_client.remove_folder

    def in_use(path: Path) -> None:
        if Path(path).name == "Wow.exe":
            raise PermissionError(errno.EACCES, "in use", str(path))
        os.unlink(path)

    monkeypatch.setattr(
        play_client,
        "remove_folder",
        lambda folder, **kw: real_remove(folder, **kw, unlink=in_use),
    )
    real_scandir = os.scandir

    class MarkerFirst:
        """The folder listed with the marker first, whatever order the disk keeps."""

        def __init__(self, path: object) -> None:
            with real_scandir(path) as it:  # type: ignore[call-overload]
                self.entries = sorted(it, key=lambda e: e.name != play_client.MARKER)

        def __enter__(self) -> list[os.DirEntry[str]]:
            return self.entries

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(play_client.os, "scandir", MarkerFirst)
    with pytest.raises(play_client.PlayClientError, match="try again"):
        play_client.delete(play, game="g", server_dir=tmp_path / "s")
    assert play_client.read_marker(play) is not None


def test_refresh_leaves_wtf_and_interface_alone_even_when_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    (orig / "WTF" / "odd.MPQ").write_bytes(b"old")
    (orig / "Interface" / "helper.dll").write_bytes(b"old")
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    replace_file(orig / "WTF" / "odd.MPQ", b"new!")
    replace_file(orig / "Interface" / "helper.dll", b"new!")
    listed = (Path("Interface/helper.dll"), Path("WTF/odd.MPQ"))
    monkeypatch.setattr(play_client, "stale", lambda play_dir, original: listed)
    assert refresh(play, orig, tmp_path) == ()
    assert (play / "WTF" / "odd.MPQ").read_bytes() == b"old"
    assert (play / "Interface" / "helper.dll").read_bytes() == b"old"


def test_clean_partials_removes_this_servers_marked_leftover(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    partial = partial_of(tmp_path / "t")
    mark(partial, orig, server_dir=tmp_path / "s")
    (partial / "half.bin").write_bytes(b"x")
    assert play_client.clean_partials(tmp_path / "t", game="g", server_dir=tmp_path / "s")
    assert not partial.exists()


def test_clean_partials_leaves_an_unmarked_leftover_and_create_then_refuses(
    tmp_path: Path,
) -> None:
    orig = fake_client(tmp_path)
    partial = partial_of(tmp_path / "t")
    partial.mkdir()
    (partial / "mine.txt").write_text("the player's own")
    assert not play_client.clean_partials(tmp_path / "t", game="g", server_dir=tmp_path / "s")
    assert (partial / "mine.txt").read_text() == "the player's own"
    with pytest.raises(play_client.PlayClientError, match="not made by Yu'lon"):
        build(orig, tmp_path / "t", tmp_path)
    assert (partial / "mine.txt").read_text() == "the player's own"


def test_clean_partials_leaves_another_servers_leftover(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    partial = partial_of(tmp_path / "t")
    mark(partial, orig, server_dir=tmp_path / "other")
    assert not play_client.clean_partials(tmp_path / "t", game="g", server_dir=tmp_path / "s")
    assert play_client.read_marker(partial) is not None


def test_clean_partials_with_nothing_there_is_false(tmp_path: Path) -> None:
    assert not play_client.clean_partials(tmp_path / "t", game="g", server_dir=tmp_path / "s")


@pytest.mark.parametrize(
    "tag",
    [0xA000001D, 0xA0000019],  # IO_REPARSE_TAG_LX_SYMLINK, IO_REPARSE_TAG_GLOBAL_REPARSE
    ids=["lx-symlink", "global-reparse"],
)
def test_plan_leaves_out_any_name_surrogate_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: int
) -> None:
    orig = fake_client(tmp_path)
    _as_junction(monkeypatch, orig / "Interface", tag=tag)
    p = play_client.plan(orig, tmp_path / "t")
    assert not any(rel.parts[0] == "Interface" for rel in p.copied + p.linked)
    assert Path("WTF/Config.wtf") in p.copied


def test_plan_keeps_a_deduplicated_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orig = fake_client(tmp_path)
    _as_junction(monkeypatch, orig / "Data", tag=0x80000013)  # IO_REPARSE_TAG_DEDUP
    p = play_client.plan(orig, tmp_path / "t")
    assert Path("Data/common.MPQ") in p.linked


def test_remove_folder_does_not_enter_what_it_could_not_look_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "t.yulon-partial"
    unknown = folder / "Data" / "unknown"
    unknown.mkdir(parents=True)
    (unknown / "precious.MPQ").write_bytes(b"keep")
    real = os.lstat

    def failing(path: object) -> object:
        if os.fspath(path) == str(unknown):  # type: ignore[call-overload]
            raise PermissionError(errno.EACCES, "Access is denied", str(path))
        return real(path)  # type: ignore[arg-type]

    monkeypatch.setattr(play_client, "_lstat", failing)
    with pytest.raises(OSError, match="Access is denied"):
        play_client.remove_folder(folder)
    assert (unknown / "precious.MPQ").read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_remove_folder_takes_folderness_from_the_look_it_decided_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The entry was a file when looked at and a link to a folder a moment later:
    # a second, following look must not be what sends the remover inside.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.MPQ").write_bytes(b"keep")
    folder = tmp_path / "t.yulon-partial"
    folder.mkdir()
    swapped = folder / "swapped"
    os.symlink(elsewhere, swapped)
    real = os.lstat

    def as_a_file(path: object) -> object:
        st = real(path)  # type: ignore[arg-type]
        if os.fspath(path) != str(swapped):  # type: ignore[call-overload]
            return st
        return types.SimpleNamespace(st_mode=0o100644, st_file_attributes=0, st_nlink=1)

    monkeypatch.setattr(play_client, "_lstat", as_a_file)
    play_client.remove_folder(folder)
    assert (elsewhere / "precious.MPQ").read_bytes() == b"keep"
    assert not folder.exists()


# -- Task 2 fix round 1 --------------------------------------------------------


def test_refresh_refuses_another_servers_client(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path, server_dir=tmp_path / "other")
    replace_file(orig / "Data" / "common.MPQ", b"new!")
    with pytest.raises(play_client.PlayClientError, match="another server at .*other"):
        refresh(play, orig, tmp_path, game="g", server_dir=tmp_path / "s")
    assert (play / "Data" / "common.MPQ").read_bytes() == b"mpq" * 1000


def test_a_cloned_client_that_cannot_share_is_not_taken_for_a_full_copy(tmp_path: Path) -> None:
    # A clone shares no inode with the original, just like a full copy; only the
    # marker tells them apart, and only a full copy may be re-copied unasked.
    orig = fake_client(tmp_path)
    play = tmp_path / "t"

    def copying_reflink(src: Path, dst: Path) -> bool:
        shutil.copy2(src, dst)
        return True

    build(orig, play, tmp_path, reflink=copying_reflink)
    marker = play_client.read_marker(play)
    assert marker is not None and not marker.full_copy
    replace_file(orig / "Data" / "common.MPQ", b"new!")
    with pytest.raises(play_client.PlayClientError, match="make it again.*full copy"):
        refresh(play, orig, tmp_path, link=exdev)
    assert (play / "Data" / "common.MPQ").read_bytes() == b"mpq" * 1000


def test_a_marker_without_full_copy_reads_as_shared(tmp_path: Path) -> None:
    raw = {
        "version": 1,
        "game": "g",
        "server_dir": str(tmp_path / "s"),
        "source_client_dir": str(tmp_path / "WoW"),
        "created_at": "2026-09-30T12:00:00Z",
    }
    (tmp_path / play_client.MARKER).write_text(json.dumps(raw), encoding="utf-8")
    marker = play_client.read_marker(tmp_path)
    assert marker is not None and marker.full_copy is False


def _unlookable(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    real = os.lstat

    def failing(path: object) -> object:
        if os.fspath(path) == str(target):  # type: ignore[call-overload]
            raise PermissionError(errno.EACCES, "Access is denied", str(path))
        return real(path)  # type: ignore[arg-type]

    monkeypatch.setattr(play_client, "_lstat", failing)


def test_remove_folder_stops_when_the_folder_itself_cannot_be_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "t.yulon-partial"
    folder.mkdir()
    (folder / "precious.MPQ").write_bytes(b"keep")
    _unlookable(monkeypatch, folder)
    with pytest.raises(OSError, match="Access is denied"):
        play_client.remove_folder(folder)
    assert (folder / "precious.MPQ").read_bytes() == b"keep"


def test_delete_stops_when_the_folder_itself_cannot_be_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    _unlookable(monkeypatch, play)
    with pytest.raises(play_client.PlayClientError, match="could not be looked at"):
        play_client.delete(play, game="g", server_dir=tmp_path / "s")
    assert (play / "Wow.exe").is_file()


def test_refresh_keeps_an_archive_the_original_lacks_even_when_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The original's archive vanished between stale() and refresh(): still keep ours.
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    (orig / "DivxDecoder.dll").unlink()
    monkeypatch.setattr(play_client, "stale", lambda play_dir, original: (Path("DivxDecoder.dll"),))
    assert refresh(play, orig, tmp_path) == ()
    assert (play / "DivxDecoder.dll").read_bytes() == b"dll"


# -- Task 4: a module's patch in the ready-to-play client is kept -------------


def module_patch_over(play: Path, orig: Path) -> Path:
    """A module's patch installed after the switch, under a name the original also has.

    The original's `patch-A.MPQ` is the player's (or an older module's); the
    ready-to-play client's is its own file with other bytes, as a module install
    after the switch leaves it. Sizes differ, so it reads as out of date.
    """
    (orig / "Data" / "patch-A.MPQ").write_bytes(b"the player's own patch")
    build(orig, play, orig.parent)
    replace_file(play / "Data" / "patch-A.MPQ", b"the module's patch, longer" * 10)
    return Path("Data") / "patch-A.MPQ"


def test_stale_never_lists_a_kept_module_file_whose_original_differs(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    rel = module_patch_over(play, orig)
    assert play_client.stale(play, orig) == (rel,), "the fixture must be stale without keep"

    assert play_client.stale(play, orig, keep=(rel,)) == ()


def test_refresh_never_touches_a_kept_module_file_even_when_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh honours `keep` itself, not only through `stale()`'s list."""
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    rel = module_patch_over(play, orig)
    ours = (play / rel).read_bytes()
    monkeypatch.setattr(play_client, "stale", lambda play_dir, original: (rel,))

    assert refresh(play, orig, tmp_path, keep=(rel,)) == ()
    assert (play / rel).read_bytes() == ours
    assert (orig / rel).read_bytes() == b"the player's own patch"


# -- T181a final review fixes ----------------------------------------------------

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def test_vanillas_top_level_wdb_cache_is_left_out(tmp_path: Path) -> None:
    """Finding 2: Vanilla and Tortoise keep their cache at the top as `WDB/`, not `Cache/WDB`."""
    orig = fake_client(tmp_path)
    (orig / "WDB").mkdir()
    (orig / "WDB" / "creaturecache.wdb").write_bytes(b"another server's creatures")

    p = play_client.plan(orig, tmp_path / "t")

    assert not [rel for rel in p.copied if rel.parts[0] == "WDB"]


def _rename_failing(
    monkeypatch: pytest.MonkeyPatch, target: Path, *, failures: int
) -> list[tuple[str, str]]:
    """`os.replace` refusing the final rename onto `target` `failures` times, as Windows
    does while Defender holds a file inside; every other rename is the real one."""
    real = os.replace
    tried: list[tuple[str, str]] = []

    def replace(src: object, dst: object) -> None:
        if os.fspath(dst) == os.fspath(target):  # type: ignore[arg-type]
            tried.append((os.fspath(src), os.fspath(dst)))  # type: ignore[arg-type]
            if len(tried) <= failures:
                raise PermissionError(errno.EACCES, "Access is denied", os.fspath(dst))  # type: ignore[arg-type]
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(play_client.os, "replace", replace)
    return tried


def test_the_final_rename_is_tried_again_while_a_file_inside_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 3: a rename refused for a moment is tried again, not failed at once."""
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    tried = _rename_failing(monkeypatch, target, failures=2)
    slept: list[float] = []

    build(orig, target, tmp_path, sleep=slept.append)

    assert len(tried) == 3
    assert len(slept) == 2 and sum(slept) <= 5
    assert play_client.read_marker(target) is not None
    assert not partial_of(target).exists()


def test_a_rename_refused_for_good_fails_as_before_after_its_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    tried = _rename_failing(monkeypatch, target, failures=10**6)
    slept: list[float] = []

    with pytest.raises(play_client.PlayClientError, match="Access is denied"):
        build(orig, target, tmp_path, sleep=slept.append)

    assert len(tried) == play_client._RENAME_TRIES
    assert 4 <= sum(slept) <= 6, "about five seconds of retries"
    assert not target.exists() and not partial_of(target).exists()


def test_a_cleanup_refused_for_a_moment_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 3: `_discard`'s removal is retried the same way."""
    orig = fake_client(tmp_path)
    target = tmp_path / "t"
    real_remove = play_client.remove_folder
    calls: list[Path] = []

    def held_twice(folder: Path, **kw: object) -> None:
        calls.append(folder)
        if len(calls) <= 2:
            raise PermissionError(errno.EACCES, "in use", str(folder))
        real_remove(folder, **kw)  # type: ignore[arg-type]

    def denied(src: Path, dst: Path) -> None:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(play_client, "remove_folder", held_twice)
    with pytest.raises(play_client.PlayClientError) as info:
        build(orig, target, tmp_path, link=denied)

    assert len(calls) == 3
    assert "unfinished folder was removed" in str(info.value)
    assert not partial_of(target).exists()


def test_a_data_folder_that_is_a_link_is_refused(tmp_path: Path) -> None:
    """Finding 6: the walk never enters a link, so a linked Data would make an empty client."""
    real = fake_client(tmp_path / "real")
    orig = tmp_path / "WoW"
    orig.mkdir()
    (orig / "Wow.exe").write_bytes(b"MZexe")
    os.symlink(real / "Data", orig / "Data", target_is_directory=True)

    with pytest.raises(play_client.PlayClientError, match="Data folder .* is a link"):
        play_client.plan(orig, tmp_path / "t")


def test_a_client_with_no_mpq_at_all_is_refused(tmp_path: Path) -> None:
    orig = tmp_path / "WoW"
    (orig / "Data").mkdir(parents=True)
    (orig / "Wow.exe").write_bytes(b"MZexe")

    with pytest.raises(play_client.PlayClientError, match=r"no \.MPQ .*real client folder"):
        play_client.plan(orig, tmp_path / "t")


def test_linked_folders_left_out_are_named_in_the_plan(tmp_path: Path) -> None:
    """Finding 6: e.g. `Interface/AddOns` shared between two clients by a link."""
    orig = fake_client(tmp_path)
    shared = tmp_path / "shared AddOns"
    shared.mkdir()
    shutil.rmtree(orig / "Interface" / "AddOns")
    os.symlink(shared, orig / "Interface" / "AddOns", target_is_directory=True)

    p = play_client.plan(orig, tmp_path / "t")

    assert p.skipped_links == (Path("Interface") / "AddOns",)


@pytest.mark.skipif(_ROOT or os.name == "nt", reason="needs a folder its owner cannot read")
def test_a_folder_that_cannot_be_read_stops_the_plan_and_is_named(tmp_path: Path) -> None:
    """Finding 6: `os.walk` skips an unreadable folder in silence unless told not to."""
    orig = fake_client(tmp_path)
    locked = orig / "Interface" / "AddOns"
    os.chmod(locked, 0)
    try:
        with pytest.raises(play_client.PlayClientError, match="could not be read") as info:
            play_client.plan(orig, tmp_path / "t")
    finally:
        os.chmod(locked, 0o755)
    assert str(locked) in str(info.value)


@pytest.mark.parametrize("variable", play_client.ONEDRIVE_VARIABLES)
def test_a_folder_inside_onedrive_is_recognised_on_windows(tmp_path: Path, variable: str) -> None:
    """Finding 7: the env is the injected one, never this box's."""
    root = tmp_path / "OneDrive - Home"
    env = {variable: str(root)}
    inside = root / "Games" / "WoW (Yu'lon)"

    assert play_client.onedrive_folder(inside, env=env, os_name="windows") == root
    assert play_client.onedrive_folder(inside, env=env, os_name="linux") is None
    assert (
        play_client.onedrive_folder(tmp_path / "WoW (Yu'lon)", env=env, os_name="windows") is None
    )
    sibling = tmp_path / "OneDrive - Home2" / "WoW"
    assert play_client.onedrive_folder(sibling, env=env, os_name="windows") is None


def _held(monkeypatch: pytest.MonkeyPatch) -> None:
    def stuck(folder: Path, **_kw: object) -> None:
        raise PermissionError(errno.EACCES, "in use", str(folder))

    monkeypatch.setattr(play_client, "remove_folder", stuck)


def test_a_delete_that_cannot_try_again_says_to_finish_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 8: after an Uninstall there is no tab to press Delete on again."""
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    _held(monkeypatch)

    with pytest.raises(play_client.PlayClientError) as info:
        play_client.delete(play, game="g", server_dir=tmp_path / "s", can_try_again=False)

    assert f"Delete what is left by hand at {play}" in str(info.value)
    assert "try again" not in str(info.value)


@pytest.mark.parametrize("windows", [True, False])
def test_on_windows_a_failed_delete_names_the_players_own_client_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool
) -> None:
    """Finding 8: a shared archive held open by the player's own WoW blocks the delete."""
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    _held(monkeypatch)
    monkeypatch.setattr(play_client, "_on_windows", lambda: windows)

    with pytest.raises(play_client.PlayClientError) as info:
        play_client.delete(play, game="g", server_dir=tmp_path / "s")

    assert ("from your own client" in str(info.value)) is windows
    assert "then try again" in str(info.value)


def test_windows_not_supported_on_link_needs_consent(tmp_path: Path) -> None:
    """Finding 9: an SMB share answers ERROR_NOT_SUPPORTED (50) to a hard link."""
    orig = fake_client(tmp_path)
    target = tmp_path / "t"

    def not_supported(src: Path, dst: Path) -> None:
        exc = OSError(errno.EINVAL, "The request is not supported")
        exc.winerror = 50  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(play_client.PlayClientError, match="cannot share files"):
        build(orig, target, tmp_path, link=not_supported)
    assert not target.exists()


def test_archives_new_in_the_original_are_named_not_added(tmp_path: Path) -> None:
    """Finding 11: Refresh never adds them; `left_out_archives()` names them."""
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    (orig / "Data" / "patch-5.MPQ").write_bytes(b"new in the original")
    (orig / "Data" / "enUS" / "patch-enUS-5.mpq").write_bytes(b"new too")
    (orig / "WTF" / "odd.MPQ").write_bytes(b"the player's own")
    (orig / "Cache" / "y.MPQ").write_bytes(b"left out anyway")
    (orig / "Data" / "notes.txt").write_text("not an archive")

    assert play_client.left_out_archives(play, orig) == (
        Path("Data") / "enUS" / "patch-enUS-5.mpq",
        Path("Data") / "patch-5.MPQ",
    )
    assert refresh(play, orig, tmp_path) == ()
    assert not (play / "Data" / "patch-5.MPQ").exists()


def test_a_taken_back_record_is_kept_only_in_this_servers_marked_folder(tmp_path: Path) -> None:
    """T181a fix round: a module's Remove is not marker-gated, so the record write is."""
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    rel = Path("Data") / "Patch-A.MPQ"

    for game, server_dir in (("g", tmp_path / "other"), ("other", tmp_path / "s")):
        with pytest.raises(play_client.PlayClientError):
            play_client.record_taken_back(play, (rel,), game=game, server_dir=server_dir)
        assert not (play / play_client.TAKEN_BACK).exists()

    unmarked = tmp_path / "unmarked"
    (unmarked / "Data").mkdir(parents=True)
    with pytest.raises(play_client.PlayClientError):
        play_client.record_taken_back(unmarked, (rel,), game="g", server_dir=tmp_path / "s")
    assert sorted(unmarked.iterdir()) == [unmarked / "Data"], "an unmarked folder gained a file"

    play_client.record_taken_back(play, (rel,), game="g", server_dir=tmp_path / "s")
    play_client.record_taken_back(play, (rel,), game="g", server_dir=tmp_path / "s")
    assert play_client.taken_back(play) == (rel,)
    assert not (orig / play_client.TAKEN_BACK).exists(), "the original gained a record"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '["Data/Patch-A.MPQ"]',
        '{"version": 1}',
        '{"version": 1, "paths": "Data/Patch-A.MPQ"}',
        '{"version": 1, "paths": {"a": 1}}',
    ],
)
def test_a_malformed_taken_back_record_reads_as_nothing(tmp_path: Path, text: str) -> None:
    (tmp_path / play_client.TAKEN_BACK).write_text(text, encoding="utf-8")

    assert play_client.taken_back(tmp_path) == ()


def test_a_taken_back_record_entry_that_is_not_a_path_is_skipped(tmp_path: Path) -> None:
    (tmp_path / play_client.TAKEN_BACK).write_bytes(b'{"paths": ["Data/a.MPQ", 3, "", null]}')

    assert play_client.taken_back(tmp_path) == (Path("Data") / "a.MPQ",)


def test_an_unreadable_taken_back_record_reads_as_nothing(tmp_path: Path) -> None:
    (tmp_path / play_client.TAKEN_BACK).write_bytes(b"\xff\xfe not utf-8")

    assert play_client.taken_back(tmp_path) == ()


def test_nothing_is_left_out_of_a_client_just_made(tmp_path: Path) -> None:
    orig = fake_client(tmp_path)
    play = tmp_path / "t"
    build(orig, play, tmp_path)

    assert play_client.left_out_archives(play, orig) == ()


# --- a patched Wow.exe (T181 c) ---------------------------------------------------------------


def _exe_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, bytes, Any]:
    """A ready-to-play client whose Wow.exe Yu'lon patched, and the patch that did it."""
    import hashlib
    import random

    from yulon import client_exe, client_packs, platform
    from yulon.catalog.catalog import ExePatch

    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path / "config")
    stock = random.Random(7).randbytes(300_000)
    patch = ExePatch.model_validate(
        {
            "expect_sha256": hashlib.sha256(stock).hexdigest(),
            "expect_size": len(stock),
            "clean_sources": [{"url": "https://clean.example.org/c.zip", "member": "Wow.exe"}],
            "writes": [{"offset": 100, "bytes": "313233"}],
            "options": {
                "borderless": {
                    "label": "Borderless",
                    "default": True,
                    "on": [{"offset": 200, "bytes": "eb"}],
                    "off": [],
                }
            },
            "build": 12342,
        }
    )
    orig = fake_client(tmp_path)
    (orig / "Wow.exe").write_bytes(stock)
    play = tmp_path / "t"
    build(orig, play, tmp_path)
    made = client_exe.apply(play, orig, patch, {"borderless": True})
    client_packs.write_record(
        play,
        client_packs.PackRecord({}, made, {"packs": {}, "exe_options": {"borderless": True}}),
        game="g",
        server_dir=tmp_path / "s",
    )
    return play, orig, stock, patch


def test_a_patched_exe_is_not_stale_just_because_it_differs_from_the_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    play, orig, stock, _patch = _exe_world(tmp_path, monkeypatch)
    assert (play / "Wow.exe").read_bytes() != (orig / "Wow.exe").read_bytes()
    assert play_client.stale(play, orig) == ()


def test_refresh_never_copies_the_originals_exe_over_a_patched_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    play, orig, stock, patch = _exe_world(tmp_path, monkeypatch)
    patched = (play / "Wow.exe").read_bytes()
    # Something unpatched lands in the ready-to-play client: stale, and re-made from stock bytes.
    (play / "Wow.exe").write_bytes(stock)
    assert play_client.stale(play, orig) == (Path("Wow.exe"),)
    assert refresh(play, orig, tmp_path, exe_patch=patch) == (Path("Wow.exe"),)
    assert (play / "Wow.exe").read_bytes() == patched
    assert play_client.stale(play, orig) == ()


def test_refresh_after_the_originals_exe_changed_keeps_the_exe_patched_from_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    play, orig, stock, patch = _exe_world(tmp_path, monkeypatch)
    patched = (play / "Wow.exe").read_bytes()
    (orig / "Wow.exe").write_bytes(b"MZ the player's own modified exe" * 100)  # non-stock
    assert play_client.stale(play, orig) == ()
    assert refresh(play, orig, tmp_path, exe_patch=patch) == ()
    assert (play / "Wow.exe").read_bytes() == patched
    # And when the ready-to-play one is damaged too, the stock bytes come from the cache's
    # nowhere: neither exe is stock, so refresh refuses and leaves it as it was.
    (play / "Wow.exe").write_bytes(b"damaged")
    with pytest.raises(play_client.PlayClientError, match="stock Wow.exe"):
        refresh(play, orig, tmp_path, exe_patch=patch, opener=_no_network)
    assert (play / "Wow.exe").read_bytes() == b"damaged"
    assert (orig / "Wow.exe").read_bytes().startswith(b"MZ the player")


def test_refresh_without_the_patch_puts_the_originals_exe_back_and_forgets_the_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalog dropped the exe patch: the patched exe goes, and is not stale forever."""
    from yulon import client_packs

    play, orig, stock, _patch = _exe_world(tmp_path, monkeypatch)
    (play / "Wow.exe").write_bytes(b"damaged")
    assert refresh(play, orig, tmp_path) == (Path("Wow.exe"),)
    assert (play / "Wow.exe").read_bytes() == (orig / "Wow.exe").read_bytes()
    assert not os.path.samefile(play / "Wow.exe", orig / "Wow.exe")
    assert client_packs.read_record(play).exe is None
    assert play_client.stale(play, orig) == ()


def test_refresh_keeps_the_patch_and_its_record_when_the_originals_exe_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import client_packs

    play, orig, stock, _patch = _exe_world(tmp_path, monkeypatch)
    patched = (play / "Wow.exe").read_bytes()
    original_exe = (orig / "Wow.exe").read_bytes()
    (orig / "Wow.exe").unlink()  # the drive is unplugged, the folder is still there
    assert refresh(play, orig, tmp_path) == ()
    assert (play / "Wow.exe").read_bytes() == patched
    assert client_packs.read_record(play).exe is not None
    (orig / "Wow.exe").write_bytes(original_exe)
    assert refresh(play, orig, tmp_path) == (Path("Wow.exe"),)
    assert client_packs.read_record(play).exe is None


def test_refresh_treats_an_unreadable_original_exe_like_a_missing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import client_packs

    play, orig, stock, _patch = _exe_world(tmp_path, monkeypatch)
    patched = (play / "Wow.exe").read_bytes()

    def denied(_path: Path) -> object:
        raise PermissionError(13, "device not ready")

    monkeypatch.setattr(play_client, "_original_file", denied)
    assert refresh(play, orig, tmp_path) == ()
    assert (play / "Wow.exe").read_bytes() == patched
    assert client_packs.read_record(play).exe is not None


def test_refresh_remakes_the_exe_with_the_options_the_player_chose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import client_packs

    play, orig, stock, patch = _exe_world(tmp_path, monkeypatch)
    record = client_packs.read_record(play)
    client_packs.write_record(
        play,
        client_packs.PackRecord(
            record.packs, record.exe, {"packs": {}, "exe_options": {"borderless": False}}
        ),
        game="g",
        server_dir=tmp_path / "s",
    )
    (play / "Wow.exe").write_bytes(stock)
    refresh(play, orig, tmp_path, exe_patch=patch)
    assert (play / "Wow.exe").read_bytes()[200] == stock[200]  # borderless off: stock byte
    assert client_packs.read_record(play).exe["options"] == {"borderless": False}  # type: ignore[index]


def _no_network(*args: Any, **kwargs: Any) -> Any:
    raise ConnectionResetError("offline")
