"""A support file from big old logs is made quickly, and says what it shortened (T638).

The cleaner (`Redactor.checked`) runs every pattern twice over a file, about 1.7 s per MiB on a
fast machine and several times that on a slow one, and it ran over every kept log up to 2 MiB
each: ~4 minutes on a box with many large old run logs. The bundle now shortens the logs BEFORE
cleaning them (old ones hard, the newest runs, app log, containers and confs to a shared budget)
and says so, per file, in MANIFEST.txt and in each file's first line.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest

from tests.test_support_bundle import _seams
from yulon import platform
from yulon.support import bundle, runlog
from yulon.support import sources as src
from yulon.support.redact import Redactor
from yulon.support.sources import Sources

KEY = "ab12cd34ef56" * 7 + "ab12cd34"  # 92 hex digits
SESSION = "0123456789abcdef" * 5  # exactly 80 hex digits, a login session key


def _runs(config: Path) -> Path:
    folder = runlog.runs_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_run(config: Path, stamp: str, lines: int, mtime: int, tag: str = "") -> str:
    """One run log of numbered lines, ~60 bytes each, a session key every 500 lines."""
    path = _runs(config) / f"install-wow-tbc-{stamp}.log"
    body = []
    for n in range(lines):
        body.append(f"{tag}line {n:07d} " + "x" * 40)
        if n % 500 == 0:
            body.append(f"{tag}realm sessionkey = {SESSION}")
    body.append(f"{tag}END OF LOG")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return f"runs/{path.name}"


def _members(dest: Path) -> dict[str, str]:
    with zipfile.ZipFile(dest) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def _build(tmp_path: Path, **kwargs: object) -> tuple[bundle.BundleReport, dict[str, str]]:
    dest = tmp_path / "s.zip"
    report = bundle.build(
        dest,
        Sources(platform.config_dir(), None, ()),
        Redactor.build([]),
        seams=_seams(),
        **kwargs,  # type: ignore[arg-type]
    )
    return report, _members(dest)


def _count_cleaned(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The sizes of every text the cleaner is asked to check."""
    seen: list[int] = []
    real = Redactor.checked

    def checked(self: Redactor, text: str) -> str:
        seen.append(len(text))
        return real(self, text)

    monkeypatch.setattr(Redactor, "checked", checked)
    return seen


def test_an_old_run_keeps_its_newest_lines_and_the_zip_says_what_it_cut(tmp_path: Path) -> None:
    """Mutation: skip the old-tail cut and the old run keeps its first lines and no notice."""
    config = platform.config_dir()
    old = _write_run(config, "20260901T100000Z", 40_000, 1_000)  # ~2.4 MB
    _write_run(config, "20260902T100000Z", 100, 2_000)
    report, members = _build(tmp_path)
    text = members[old]
    assert text.startswith("[earlier lines dropped"), text[:80]
    assert text.splitlines()[-1] == "END OF LOG"
    assert "line 0039999 " in text and "line 0000001 " not in text
    assert len(text.encode()) <= bundle.OLD_TAIL
    manifest = members["MANIFEST.txt"]
    section = manifest.split("Cut shorter to keep saving this file quick", 1)[1]
    assert f"  {old}  last " in section
    assert old in report.cut


def test_the_newest_run_is_not_cut_as_an_old_one(tmp_path: Path) -> None:
    """Mutation: treat every run as old and the newest loses all but its last 128 KiB."""
    config = platform.config_dir()
    _write_run(config, "20260901T100000Z", 5_000, 1_000)
    newest = _write_run(config, "20260902T100000Z", 5_000, 2_000)  # ~300 KB, over OLD_TAIL
    _, members = _build(tmp_path)
    assert not members[newest].startswith("[earlier lines dropped")
    assert "line 0000001 " in members[newest]


def test_old_logs_beyond_their_budget_are_left_out_oldest_first_and_named(tmp_path: Path) -> None:
    """Mutation: never drop for the old budget and 20 old runs are all cleaned."""
    config = platform.config_dir()
    names = [
        _write_run(config, f"202609{day:02d}T100000Z", 4_000, 1_000 + day) for day in range(1, 21)
    ]
    newest = _write_run(config, "20260930T100000Z", 100, 9_000)
    report, members = _build(tmp_path)
    assert newest in members
    kept_old = [name for name in names if name in members]
    assert kept_old == names[-len(kept_old) :], "the newest of the old ones are the ones kept"
    assert 0 < len(kept_old) < len(names)
    assert list(report.quick_dropped) == [n for n in names if n not in members]
    section = members["MANIFEST.txt"].split("Left out to keep saving this file quick", 1)[1]
    for name in report.quick_dropped:
        assert f"  {name}" in section
    assert not report.dropped, "not left out for the zip's size"


def test_what_the_cleaner_reads_stays_inside_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: remove the budget loop and every kept log goes to the cleaner whole."""
    config = platform.config_dir()
    for day in range(1, 13):
        _write_run(config, f"202609{day:02d}T100000Z", 30_000, 1_000 + day)
    _write_run(config, "20260920T100000Z", 100, 5_000)  # the newest: not an old log
    seen = _count_cleaned(monkeypatch)
    _build(tmp_path)
    # Each text is checked once; a few small extras (names, manifest) are not logs.
    assert sum(seen) <= bundle.OLD_BUDGET + 64 * 1024, sum(seen)


@pytest.mark.parametrize("filler", range(0, 97, 7))
def test_a_secret_next_to_the_cut_is_still_masked_and_never_half_kept(
    tmp_path: Path, filler: int
) -> None:
    """The cut is made on raw lines before cleaning, so no secret is split by it."""
    config = platform.config_dir()
    path = _runs(config) / "install-wow-tbc-20260901T100000Z.log"
    head = "p" * 70 + "\n"
    secret = f"DB_ROOT_PASSWORD=hunter{KEY}\nsessionkey = '{SESSION}'\n"
    # A secret line about OLD_TAIL from the end, shifted by `filler` bytes.
    tail_len = bundle.OLD_TAIL - len(secret) // 2 + filler
    path.write_text(head * 5000 + secret + ("t" * 59 + "\n") * (tail_len // 60), encoding="utf-8")
    os.utime(path, (1_000, 1_000))
    _write_run(config, "20260902T100000Z", 10, 2_000)
    _, members = _build(tmp_path)
    text = "\n".join(members.values())
    assert SESSION[:24] not in text
    assert KEY[:16] not in text and KEY[-16:] not in text


def test_progress_is_reported_before_during_and_after_the_cleaning(tmp_path: Path) -> None:
    """Mutation: never call `progress` and the press stays silent for minutes."""
    config = platform.config_dir()
    for day in (1, 2, 3):
        _write_run(config, f"202609{day:02d}T100000Z", 50, 1_000 + day)
    lines: list[str] = []
    _build(tmp_path, progress=lines.append)
    assert lines[0].startswith("Reading")
    cleaning = [line for line in lines if re.search(r"\d+ of \d+", line)]
    assert len(cleaning) >= 3
    counts = [int(re.search(r"(\d+) of", line).group(1)) for line in cleaning]  # type: ignore[union-attr]
    assert counts == sorted(counts)
    assert lines[-1].startswith("Writing")
    assert not any("install-wow-tbc" in line for line in lines), "a name can carry a secret"


def test_a_failing_progress_sink_does_not_break_the_zip(tmp_path: Path) -> None:
    def broken(line: str) -> None:
        raise RuntimeError("window gone")

    _, members = _build(tmp_path, progress=broken)
    assert "MANIFEST.txt" in members


def _held_view(dest: Path) -> tuple[object, list[object]]:
    """A Logs tab whose save job is held, not run: the press while the worker is busy."""
    from tests.test_logs_view import _view

    held: list[object] = []
    view = _view(
        pick_save_path=lambda parent, suggested: dest,
        jobs=lambda work, done, failed: held.append((work, done)),
    )
    view.save_for_support()
    return view, held


def test_the_press_shows_each_progress_line_while_it_works(qapp: object, tmp_path: Path) -> None:
    """Mutation: leave the relay unconnected and the press stays on its first line."""
    view, _held = _held_view(tmp_path / "s.zip")
    view._save_relay.emit_line("Taking passwords out of the logs: 3 of 9…")  # type: ignore[attr-defined]
    assert "3 of 9" in view.status.text()  # type: ignore[attr-defined]
    assert view.status.text().startswith("Saving the support file")  # type: ignore[attr-defined]


def test_a_progress_line_after_the_save_ended_is_dropped(qapp: object, tmp_path: Path) -> None:
    """Mutation: drop the `_saving_to` guard and a late line overwrites the Saved sentence."""
    view, held = _held_view(tmp_path / "s.zip")
    report = bundle.BundleReport(tmp_path / "s.zip", ("MANIFEST.txt",), (), (), 1_000)
    held[0][1](report)  # type: ignore[index]
    view._save_relay.emit_line("Packing the zip…")  # type: ignore[attr-defined]
    assert view.status.text().startswith("Saved ")  # type: ignore[attr-defined]


def test_the_press_counts_the_logs_left_out_to_keep_saving_quick(
    qapp: object, tmp_path: Path
) -> None:
    """Mutation: stop reading `report.quick_dropped` in `_left_out_text()` and this loses it."""
    from tests.test_support_status_names_left_out_logs import _status

    text = _status(tmp_path / "s.zip", quick_dropped=("runs/a.log", "runs/b.log"))
    assert "2 logs left out to keep saving quick" in text, text
    assert "MANIFEST.txt names them." in text, text


def test_the_newest_runs_and_the_app_log_are_never_cut_to_save_time(tmp_path: Path) -> None:
    """The start of a failed install is the diagnosis: only the size cap may shorten these.

    Mutation: give the newest tier a budget again and the app log or the run is cut here.
    """
    config = platform.config_dir()
    run = _write_run(config, "20260902T100000Z", 30_000, 2_000)  # ~1.8 MB
    app_log = tmp_path / "yulon.log"
    app_log.write_text("".join(f"app {n:07d} " + "y" * 40 + "\n" for n in range(30_000)), "utf-8")
    dest = tmp_path / "s.zip"
    report = bundle.build(dest, Sources(config, app_log, ()), Redactor.build([]), seams=_seams())
    members = _members(dest)
    assert not report.cut and not report.quick_dropped
    assert "line 0000001 " in members[run] and "app 0000001 " in members["app/yulon.log"]


def _long_last_line(config: Path, secret_line: str, pad: int) -> str:
    """An old run of short lines, then ONE long line (a `\\r` progress bar's shape) that starts
    with the secret and is `pad` bytes of filler long."""
    path = _runs(config) / "install-wow-tbc-20260901T100000Z.log"
    path.write_text(
        "short line\n" * 2000 + secret_line + "z" * pad, encoding="utf-8"
    )  # no final newline
    os.utime(path, (1_000, 1_000))
    _write_run(config, "20260902T100000Z", 10, 2_000)
    return f"runs/{path.name}"


@pytest.mark.parametrize("shift", range(0, 240, 8))
@pytest.mark.parametrize(
    "secret_line, piece",
    [
        (f"world: sessionkey = {SESSION} ", SESSION),
        ("MYSQL_ROOT_PASSWORD=Sup3rS3cretPw ", "Sup3rS3cretPw"),
    ],
    ids=["session-key", "password"],
)
def test_a_cut_never_keeps_the_end_of_a_line_whose_key_it_cut_off(
    tmp_path: Path, shift: int, secret_line: str, piece: str
) -> None:
    """Mutation: let `_cut` keep a tail that has no newline and 40 hex of the key survive."""
    config = platform.config_dir()
    _long_last_line(config, secret_line, bundle.OLD_TAIL - 150 + shift)
    _, members = _build(tmp_path)
    text = "\n".join(members.values())
    for start in range(0, len(piece) - 8):
        assert piece[start : start + 9] not in text, (shift, start)


def test_a_last_line_longer_than_the_limit_says_nothing_of_it_was_kept(tmp_path: Path) -> None:
    """Mutation: report the notice's bytes as 'last N bytes kept' and this fails."""
    config = platform.config_dir()
    name = _long_last_line(config, "progress ", bundle.OLD_TAIL * 2)
    report, members = _build(tmp_path)
    assert members[name].startswith("[earlier lines dropped")
    assert "zzzz" not in members[name] and "short line" not in members[name]
    section = members["MANIFEST.txt"].split("Cut shorter to keep saving this file quick", 1)[1]
    line = next(line for line in section.splitlines() if name in line)
    assert "nothing of it kept" in line and "last " not in line, line
    assert name in report.cut


def test_a_file_the_cleaner_refuses_is_not_listed_as_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave the refused name in the quick cuts and the manifest lists it as cut."""
    config = platform.config_dir()
    path = _runs(config) / "install-wow-tbc-20260901T100000Z.log"
    path.write_text("POISON\n" + "x" * 60 + "\n" * 1 + ("a line\n" * 40_000), encoding="utf-8")
    path.write_text(("a line\n" * 40_000) + "POISON\n", encoding="utf-8")
    os.utime(path, (1_000, 1_000))
    _write_run(config, "20260902T100000Z", 10, 2_000)
    real = Redactor.checked

    def checked(self: Redactor, text: str) -> str:
        if "POISON" in text:
            raise bundle.Unredactable("test")
        return real(self, text)

    monkeypatch.setattr(Redactor, "checked", checked)
    report, members = _build(tmp_path)
    name = f"runs/{path.name}"
    assert name not in members and name not in report.cut
    assert (name, bundle.LEFT_OUT) in report.skipped
    assert f"  {name}  last " not in members["MANIFEST.txt"]


def test_a_cut_file_the_size_cap_leaves_out_is_not_listed_as_cut(tmp_path: Path) -> None:
    """Mutation: do not reconcile the quick cuts with the final members."""
    import secrets as _secrets

    config = platform.config_dir()
    names = []
    for n in range(3):
        path = _runs(config) / f"install-wow-tbc-2026090{n + 1}T100000Z.log"
        path.write_text("\n".join(_secrets.token_urlsafe(60) for _ in range(5000)), "utf-8")
        os.utime(path, (1_000 + n, 1_000 + n))
        names.append(f"runs/{path.name}")
    report, members = _build(tmp_path, cap_bytes=150_000)
    assert report.dropped, "the cap must have left something out"
    for name in report.dropped:
        assert name not in report.cut
        assert f"  {name}  last " not in members["MANIFEST.txt"]


def test_a_rotation_cut_twice_is_listed_with_its_final_size(tmp_path: Path) -> None:
    """Quick-cut to 128 KiB, then halved by the size cap: both lines carry the final count."""
    import secrets as _secrets

    config = platform.config_dir()
    app_log = tmp_path / "yulon.log"
    app_log.write_text("x\n", encoding="utf-8")
    rotation = tmp_path / "yulon.log.1"
    rotation.write_text("\n".join(_secrets.token_urlsafe(60) for _ in range(6000)), "utf-8")
    report, members = _build_with(tmp_path, config, app_log, cap_bytes=100_000)
    manifest = members["MANIFEST.txt"]
    sizes = re.findall(r"  app/yulon\.log\.1  last (\d+) bytes kept", manifest)
    assert len(sizes) == 2 and sizes[0] == sizes[1], sizes
    assert len(members["app/yulon.log.1"].encode()) == int(sizes[0])


def _build_with(
    tmp_path: Path, config: Path, app_log: Path, **kwargs: object
) -> tuple[bundle.BundleReport, dict[str, str]]:
    dest = tmp_path / "s2.zip"
    report = bundle.build(
        dest, Sources(config, app_log, ()), Redactor.build([]), seams=_seams(), **kwargs  # type: ignore[arg-type]
    )
    return report, _members(dest)


def test_the_cut_of_a_text_without_a_newline_keeps_nothing_but_the_notice(tmp_path: Path) -> None:
    """`read_tail` of a file whose last line alone is over the limit: no half line (T638)."""
    path = tmp_path / "one-line.log"
    path.write_text("a" * 300 + "SECRET" + "b" * 300, encoding="utf-8")
    text = src.read_tail(path, 400)
    assert text == src._TRUNCATION_NOTICE


def test_the_press_promises_no_duration(qapp: object, tmp_path: Path) -> None:
    """A slow box with a stuck docker can exceed any promise; the progress line is enough."""
    view, _held = _held_view(tmp_path / "s.zip")
    assert "minute" not in view.status.text()  # type: ignore[attr-defined]
    assert "minute" not in (view.busy_reason() or "")  # type: ignore[attr-defined]
