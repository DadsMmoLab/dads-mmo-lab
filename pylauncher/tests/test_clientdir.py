"""Tests for `yulon.catalog.families.clientdir` — every rule, refuse vs warn, for the three specs.

Pure: a `tmp_path` tree stands in for the client and `free_bytes` is a lambda. A refusal is
what the design asks for where the scripts asked "Continue anyway?" — the engine cannot ask.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon.catalog.catalog import ClientSpec
from yulon.catalog.families import clientdir
from yulon.catalog.preflight import GIB

PLENTY = 100 * GIB

TBC = ClientSpec(
    required_file="Data/expansion.MPQ",
    min_mpq=6,
    mpq_depth="recursive",
    locale_mpq_required=True,
    near_client_warn_gb=8,
)
VANILLA = ClientSpec(required_file="Data/dbc.MPQ", min_mpq=5, mpq_depth=1)
TORTOISE = ClientSpec(required_file=None, min_mpq=5, mpq_depth=2)


def client(
    root: Path, *, mpqs: int = 8, locale: str | None = "enUS", required: bool = True
) -> Path:
    """A client folder that passes TBC's spec unless told otherwise."""
    data = root / "client" / "Data"
    data.mkdir(parents=True)
    for index in range(mpqs):
        (data / f"patch-{index}.MPQ").write_bytes(b"MPQ")
    if required:
        (data / "expansion.MPQ").write_bytes(b"MPQ")
        (data / "dbc.MPQ").write_bytes(b"MPQ")
    if locale is not None:
        (data / locale).mkdir()
        (data / locale / f"locale-{locale}.MPQ").write_bytes(b"MPQ")
    return root / "client"


def verdicts(checks: tuple[object, ...]) -> dict[str, str]:
    return {check.name: check.verdict for check in checks}  # type: ignore[attr-defined]


def test_no_client_folder_is_a_refusal_that_names_what_to_pick() -> None:
    """Nothing chosen is its own sentence: there is no path to name back at the user."""
    (check,) = clientdir.validate(None, TBC, free_bytes=lambda _p: PLENTY)
    assert check.verdict == "refuse"
    assert "no client folder was chosen" in check.detail
    assert clientdir.DATA_DIR in check.remedy


def test_a_folder_without_data_is_refused(tmp_path: Path) -> None:
    """The folder is real but holds no `Data/`, which is what "not a client" means here."""
    folder = tmp_path / "client"
    folder.mkdir()
    (check,) = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert check.verdict == "refuse"
    assert check.detail == (
        f"{folder} has no {clientdir.DATA_DIR} directory, so it is not a game client"
    )


def test_a_missing_required_file_is_refused_by_name(tmp_path: Path) -> None:
    folder = client(tmp_path, required=False)
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    refused = [check for check in checks if check.verdict == "refuse"]
    assert len(refused) == 1
    assert "expansion.MPQ" in refused[0].detail


def test_required_file_none_disables_only_that_rule(tmp_path: Path) -> None:
    folder = client(tmp_path, required=False)
    checks = clientdir.validate(folder, TORTOISE, free_bytes=lambda _p: PLENTY)
    assert not [check for check in checks if check.verdict == "refuse"]


def test_a_good_client_passes_every_rule(tmp_path: Path) -> None:
    folder = client(tmp_path)
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert set(verdicts(checks).values()) == {"pass"}, verdicts(checks)


# --- the rules the plan's five tests leave unpinned ------------------------------


def test_a_file_chosen_instead_of_a_folder_is_refused(tmp_path: Path) -> None:
    """The picker can hand back Wow.exe; `is_dir()` is what tells that apart from a folder.

    The sentence is asserted and not merely the refusal. `Wow.exe / "Data"` is
    not a directory either, so deleting this rule still refuses — with the
    generic "no Data directory" line, which tells someone holding a file to go
    looking inside it for a folder. Only the detail tells the two apart.
    """
    chosen = tmp_path / "Wow.exe"
    chosen.write_bytes(b"MZ")
    (check,) = clientdir.validate(chosen, TBC, free_bytes=lambda _p: PLENTY)
    assert check.verdict == "refuse"
    assert check.detail == f"{chosen} is a file, not a folder"
    assert check.remedy


def test_a_client_dir_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """A path remembered from a previous install that has since been deleted.

    The same trap as the file above: a path that is gone has no `Data/` either,
    so the generic refusal covers for this rule unless the sentence is pinned.
    "Is not there" is the one that sends the user back to the picker instead of
    into a folder that no longer exists.
    """
    gone = tmp_path / "gone"
    (check,) = clientdir.validate(gone, TBC, free_bytes=lambda _p: PLENTY)
    assert check.verdict == "refuse"
    assert check.detail == f"{gone} is not there"
    assert check.remedy


def test_every_refusal_says_what_to_do_next(tmp_path: Path) -> None:
    """A refusal with no remedy leaves the user stuck: the engine cannot ask, so it must tell."""
    (tmp_path / "empty").mkdir()
    cases = [
        clientdir.validate(None, TBC, free_bytes=lambda _p: PLENTY),
        clientdir.validate(tmp_path / "gone", TBC, free_bytes=lambda _p: PLENTY),
        clientdir.validate(tmp_path / "empty", TBC, free_bytes=lambda _p: PLENTY),
        clientdir.validate(client(tmp_path, required=False), TBC, free_bytes=lambda _p: PLENTY),
    ]
    for checks in cases:
        for check in checks:
            if check.verdict == "refuse":
                assert check.remedy.strip(), check
                assert check.detail.strip(), check


def test_the_required_file_rule_reads_the_spec_not_a_game_literal(tmp_path: Path) -> None:
    """Vanilla's proof is `dbc.MPQ`; the module holds no game name of its own (style-guide §3)."""
    data = tmp_path / "client" / "Data"
    data.mkdir(parents=True)
    (data / "expansion.MPQ").write_bytes(b"MPQ")
    checks = clientdir.validate(tmp_path / "client", VANILLA, free_bytes=lambda _p: PLENTY)
    (refused,) = [check for check in checks if check.verdict == "refuse"]
    assert "dbc.MPQ" in refused.detail
    assert "expansion.MPQ" not in refused.detail


def test_a_refusal_stops_the_rules_that_would_bury_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One MPQ, no locale, no required file: the user reads the refusal, not four warnings.

    The warn table is stubbed rather than counted, because at I.1 it is empty
    and a test that counted its output would pass with the early return
    deleted. What is asserted is that it was never consulted — which is the
    thing Task I.2 must not quietly undo when it fills the table in.
    """
    consulted: list[object] = []
    monkeypatch.setattr(clientdir, "_warnings", lambda *args: consulted.append(args) or [])
    data = tmp_path / "client" / "Data"
    data.mkdir(parents=True)
    (data / "patch.MPQ").write_bytes(b"MPQ")
    checks = clientdir.validate(tmp_path / "client", TBC, free_bytes=lambda _p: None)
    assert [check.verdict for check in checks] == ["pass", "refuse"]
    assert consulted == []


def test_a_client_that_clears_the_refusals_reaches_the_warn_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam Task I.2 fills in is actually called, with the folder, `Data/`, spec and seam."""
    consulted: list[object] = []
    monkeypatch.setattr(clientdir, "_warnings", lambda *args: consulted.append(args) or [])
    folder = client(tmp_path)
    clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert len(consulted) == 1
    assert consulted[0][:3] == (folder, folder / clientdir.DATA_DIR, TBC)  # type: ignore[index]


def test_a_volume_that_will_not_answer_is_never_a_refusal(tmp_path: Path) -> None:
    """`free_bytes` may answer `None` — "could not ask", which is not "no space".

    This does not guard the boundary; at I.1 there is nothing to guard. No rule
    here reads the value (the space rule is Task I.2), so no None-versus-zero
    mistake could make this test fail. What it records is the shape I.2 starts
    from — a `None` changes no verdict and drops no row — so that a diff which
    breaks that shape is visible. The guard itself belongs to I.2, written
    against the rule that finally reads the number.
    """
    folder = client(tmp_path)
    unmeasured = clientdir.validate(folder, TBC, free_bytes=lambda _p: None)
    measured = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert not [check for check in unmeasured if check.verdict == "refuse"]
    assert [check.name for check in unmeasured] == [check.name for check in measured]


# --- `mpq_files` and `locale_dirs`, which I.2 and I.8 build on -------------------


def _tree(root: Path) -> Path:
    data = root / "Data"
    (data / "enUS").mkdir(parents=True)
    (data / "enUS" / "deep").mkdir()
    (data / "common.MPQ").write_bytes(b"MPQ")
    (data / "readme.txt").write_bytes(b"hi")
    (data / "enUS" / "locale-enUS.mpq").write_bytes(b"MPQ")
    (data / "enUS" / "deep" / "buried.MPQ").write_bytes(b"MPQ")
    return data


def test_mpq_depth_one_is_the_data_folder_itself(tmp_path: Path) -> None:
    data = _tree(tmp_path)
    assert [p.name for p in clientdir.mpq_files(data, 1)] == ["common.MPQ"]


def test_mpq_depth_two_adds_the_locale_folder(tmp_path: Path) -> None:
    data = _tree(tmp_path)
    assert [p.name for p in clientdir.mpq_files(data, 2)] == ["common.MPQ", "locale-enUS.mpq"]


def test_recursive_finds_every_level(tmp_path: Path) -> None:
    data = _tree(tmp_path)
    found = {p.name for p in clientdir.mpq_files(data, "recursive")}
    assert found == {"common.MPQ", "locale-enUS.mpq", "buried.MPQ"}


def test_a_folder_that_cannot_be_read_raises_at_every_depth(tmp_path: Path) -> None:
    """Both branches answer the same way, so I.8 can tell "unreadable" from "empty".

    `Path.rglob` swallows the OSError and returns a short list, which would
    report an unreadable client as "too few archives" — the wrong sentence, and
    the one client whose spec is `recursive` is the one this would hide.
    """
    missing = tmp_path / "gone"
    for depth in (1, 2, "recursive"):
        with pytest.raises(OSError):
            clientdir.mpq_files(missing, depth)  # type: ignore[arg-type]


def test_locale_dirs_are_the_subfolders_that_hold_an_archive(tmp_path: Path) -> None:
    data = _tree(tmp_path)
    (data / "Cache").mkdir()
    (data / "Cache" / "notes.txt").write_bytes(b"hi")
    assert [folder.name for folder in clientdir.locale_dirs(data)] == ["enUS"]
