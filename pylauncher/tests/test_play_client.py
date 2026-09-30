"""Tests for `yulon.play_client` (T181a): building a ready-to-play client folder.

Every test builds a small fake WoW client under `tmp_path`; nothing touches a
real client, a network or docker. The hard-link behaviour is pinned with
`reflink=lambda s, d: False`, so a test run on a copy-on-write filesystem
(btrfs, XFS) still exercises `link()` rather than silently reflinking.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

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
