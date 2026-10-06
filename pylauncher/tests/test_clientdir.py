"""Tests for `yulon.catalog.families.clientdir` — every rule, refuse vs warn, for the three specs.

Pure: a `tmp_path` tree stands in for the client and `free_bytes` is a lambda. A refusal is
what the design asks for where the scripts asked "Continue anyway?" — the engine cannot ask.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_case import needs_case_sensitive_disk
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


# --- Task I.2: the rules that warn, and the three answers each of them has -------


def spec(**overrides: object) -> ClientSpec:
    """A spec with no required-file rule, so a test can reach the warn table cheaply."""
    fields: dict[str, object] = {"required_file": None, "min_mpq": 5, "mpq_depth": 1}
    fields.update(overrides)
    return ClientSpec(**fields)  # type: ignore[arg-type]


def details(checks: tuple[object, ...]) -> dict[str, str]:
    return {check.name: check.detail for check in checks}  # type: ignore[attr-defined]


def test_too_few_mpqs_at_the_spec_depth_warns_and_does_not_refuse(tmp_path: Path) -> None:
    """An incomplete download is a warning: extraction's own counts are the real gate.

    The number found and the number wanted are both asserted. A bare
    `verdict == "warn"` is satisfied by any of the other three warn rules if
    the count rule were deleted and a neighbour renamed onto its key.
    """
    folder = client(tmp_path, mpqs=1, locale=None)
    checks = clientdir.validate(folder, VANILLA, free_bytes=lambda _p: PLENTY)
    assert verdicts(checks)[clientdir.MPQ_CHECK] == "warn"
    assert details(checks)[clientdir.MPQ_CHECK] == (
        f"3 MPQ archives directly in {folder / clientdir.DATA_DIR}; "
        f"a complete client has at least 5"
    )
    assert not [check for check in checks if check.verdict == "refuse"]


def test_the_archive_count_follows_the_spec_depth_and_says_where_it_looked(
    tmp_path: Path,
) -> None:
    """One tree, three specs, three answers — the depth is data, not a literal in the rule.

    The same six archives are four at depth 1 and six deeper, so a rule that
    ignored `spec.mpq_depth` and always walked one way would give the same
    verdict to all three. The sentence naming where it looked is asserted too:
    "4 archives" without "directly in" leaves a user hunting a folder that is
    not short of anything.
    """
    data = tmp_path / "client" / "Data"
    (data / "enUS").mkdir(parents=True)
    for index in range(4):
        (data / f"patch-{index}.MPQ").write_bytes(b"MPQ")
    for name in ("locale-enUS.MPQ", "speech-enUS.MPQ"):
        (data / "enUS" / name).write_bytes(b"MPQ")
    folder = tmp_path / "client"

    shallow = clientdir.validate(folder, spec(mpq_depth=1), free_bytes=lambda _p: PLENTY)
    assert verdicts(shallow)[clientdir.MPQ_CHECK] == "warn"
    assert details(shallow)[clientdir.MPQ_CHECK] == (
        f"4 MPQ archives directly in {data}; a complete client has at least 5"
    )

    two = clientdir.validate(folder, spec(mpq_depth=2), free_bytes=lambda _p: PLENTY)
    assert verdicts(two)[clientdir.MPQ_CHECK] == "pass"
    assert details(two)[clientdir.MPQ_CHECK] == f"6 MPQ archives in {data}, up to 2 folders deep"

    every = clientdir.validate(folder, spec(mpq_depth="recursive"), free_bytes=lambda _p: PLENTY)
    assert verdicts(every)[clientdir.MPQ_CHECK] == "pass"
    assert details(every)[clientdir.MPQ_CHECK] == f"6 MPQ archives anywhere under {data}"


def test_a_missing_locale_folder_warns_only_when_the_spec_requires_one(tmp_path: Path) -> None:
    """TBC's DBC data lives in `Data/<locale>/`; Vanilla's does not, so Vanilla has no row.

    The absent row is asserted by key, not by verdict: the repack rule fires on
    the very same folder, so "some check warned" would be true either way.
    """
    folder = client(tmp_path, locale=None)
    tbc = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert verdicts(tbc)[clientdir.LOCALE_CHECK] == "warn"
    assert details(tbc)[clientdir.LOCALE_CHECK] == (
        f"no locale folder holding archives under {folder / clientdir.DATA_DIR} "
        "(enUS, deDE, ...), and this expansion keeps its DBC data in one"
    )
    vanilla = clientdir.validate(folder, VANILLA, free_bytes=lambda _p: PLENTY)
    assert clientdir.LOCALE_CHECK not in verdicts(vanilla)


def test_a_present_locale_folder_is_named_back_to_the_user(tmp_path: Path) -> None:
    """The pass says which locale was found, so a user with the wrong one can see it."""
    folder = client(tmp_path, locale="deDE")
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert verdicts(checks)[clientdir.LOCALE_CHECK] == "pass"
    assert details(checks)[clientdir.LOCALE_CHECK] == "locale archives in deDE"


def test_realmlist_at_the_root_with_no_locale_smells_like_a_repack(tmp_path: Path) -> None:
    folder = client(tmp_path, locale=None)
    (folder / clientdir.REPACK_FILE).write_text("set realmlist 127.0.0.1\n", encoding="utf-8")
    checks = clientdir.validate(folder, VANILLA, free_bytes=lambda _p: PLENTY)
    assert verdicts(checks)[clientdir.REPACK_CHECK] == "warn"
    assert details(checks)[clientdir.REPACK_CHECK] == (
        f"{clientdir.REPACK_FILE} sits at the root of {folder} and there is no locale folder, "
        "which is how a repack looks"
    )


def test_the_repack_smell_needs_both_halves(tmp_path: Path) -> None:
    """Either half alone is an ordinary client; only together are they the smell.

    The no-locale half is checked with TBC, whose locale rule warns on that
    same folder — so a repack rule that had collapsed into "no locale folder"
    would be caught here rather than agreeing with its neighbour.
    """
    full = client(tmp_path / "full", locale="enUS")
    (full / clientdir.REPACK_FILE).write_text("x", encoding="utf-8")
    with_locale = clientdir.validate(full, VANILLA, free_bytes=lambda _p: PLENTY)
    assert verdicts(with_locale)[clientdir.REPACK_CHECK] == "pass"
    assert details(with_locale)[clientdir.REPACK_CHECK] == "nothing suggests a repack"

    stripped = client(tmp_path / "stripped", locale=None)
    no_realmlist = clientdir.validate(stripped, TBC, free_bytes=lambda _p: PLENTY)
    assert verdicts(no_realmlist)[clientdir.LOCALE_CHECK] == "warn"
    assert verdicts(no_realmlist)[clientdir.REPACK_CHECK] == "pass"
    assert details(no_realmlist)[clientdir.REPACK_CHECK] == (
        f"no {clientdir.REPACK_FILE} at the root of {stripped}"
    )


def test_little_free_space_next_to_the_client_warns_with_both_numbers(tmp_path: Path) -> None:
    """Extraction caches the shared folder on the client's own drive; 3 GB is not enough."""
    folder = client(tmp_path)
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: 3 * GIB)
    assert verdicts(checks)[clientdir.SPACE_CHECK] == "warn"
    assert details(checks)[clientdir.SPACE_CHECK] == (
        "3 GB free on the drive holding the client; 8 GB is the comfortable figure, because "
        "extraction reads the client through Docker's file sharing and that cache lands on "
        "this drive"
    )


def test_a_drive_that_will_not_answer_is_unchecked_and_a_full_one_is_a_warning(
    tmp_path: Path,
) -> None:
    """`None` is "could not ask" — the third answer, and never the smallest number.

    Zero and `None` are asserted side by side because that is the collapse this
    guard exists to stop. Rounding `None` down invents "0 GB free" on a machine
    with room; rounding it up hides the one warning worth printing. A full
    drive is a bad answer that was actually obtained, and it warns.
    """
    folder = client(tmp_path)
    empty = clientdir.validate(folder, TBC, free_bytes=lambda _p: 0)
    assert verdicts(empty)[clientdir.SPACE_CHECK] == "warn"
    assert details(empty)[clientdir.SPACE_CHECK].startswith("0 GB free on the drive")

    unknown = clientdir.validate(folder, TBC, free_bytes=lambda _p: None)
    assert verdicts(unknown)[clientdir.SPACE_CHECK] == "unchecked"
    assert details(unknown)[clientdir.SPACE_CHECK] == (
        f"the free space on the drive holding {folder} could not be measured — "
        "that is not a pass"
    )
    assert "GB free" not in details(unknown)[clientdir.SPACE_CHECK]
    assert not [check for check in unknown if check.verdict == "refuse"]


def test_the_space_rule_asks_about_the_client_drive_once(tmp_path: Path) -> None:
    """The client can sit on a different drive from the server folder, which preflight owns."""
    folder = client(tmp_path)
    asked: list[Path] = []

    def spy(path: Path) -> int:
        asked.append(path)
        return PLENTY

    clientdir.validate(folder, TBC, free_bytes=spy)
    assert asked == [folder]


def test_an_unreadable_data_folder_is_unchecked_not_too_few_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder that would not be listed is not a folder with two archives in it.

    Both a bare `OSError` and the `PermissionError` a real refusal raises are
    tried, so the rule cannot be catching the one exception this test happens
    to throw. The detail is asserted to be the "could not be listed" sentence
    and NOT the count sentence: telling someone to re-download an intact client
    because its permissions are wrong is the wrong three hours.
    """
    folder = client(tmp_path)
    for error in (OSError("I/O error"), PermissionError("permission denied")):

        def boom(_data: Path, _depth: object, _error: OSError = error) -> tuple[Path, ...]:
            raise _error

        monkeypatch.setattr(clientdir, "mpq_files", boom)
        checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
        assert verdicts(checks)[clientdir.MPQ_CHECK] == "unchecked", error
        assert details(checks)[clientdir.MPQ_CHECK] == (
            f"the archives anywhere under {folder / clientdir.DATA_DIR} could not be listed "
            "— that is not a pass"
        )
        assert "a complete client has at least" not in details(checks)[clientdir.MPQ_CHECK]
        assert not [check for check in checks if check.verdict == "refuse"]


def test_an_unlistable_data_folder_leaves_locale_and_repack_unchecked_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two rules read the same listing, so they must not disagree about whether it was read.

    Without the shared answer the repack rule sees an empty tuple where the
    locale rule saw a failure, and a client whose `Data/` merely would not open
    is accused of being a repack.
    """
    folder = client(tmp_path)
    (folder / clientdir.REPACK_FILE).write_text("x", encoding="utf-8")

    def boom(_data: Path) -> tuple[Path, ...]:
        raise PermissionError("permission denied")

    monkeypatch.setattr(clientdir, "locale_dirs", boom)
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert verdicts(checks)[clientdir.LOCALE_CHECK] == "unchecked"
    assert verdicts(checks)[clientdir.REPACK_CHECK] == "unchecked"
    assert "could not be listed" in details(checks)[clientdir.LOCALE_CHECK]
    assert "could not be listed" in details(checks)[clientdir.REPACK_CHECK]


def test_no_warning_rule_ever_refuses(tmp_path: Path) -> None:
    """The worst client that still has `Data/` and its expansion MPQ: four warnings, no wall.

    That is the whole shape of this group. Extraction's own `produces` counts
    are the real gate, and a refusal here would stop an install the scripts let
    through after one "Continue anyway?".
    """
    folder = client(tmp_path, mpqs=0, locale=None)
    (folder / clientdir.REPACK_FILE).write_text("x", encoding="utf-8")
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: 0)
    assert not [check for check in checks if check.verdict == "refuse"]
    warned = [check.name for check in checks if check.verdict == "warn"]
    assert warned == [
        clientdir.MPQ_CHECK,
        clientdir.LOCALE_CHECK,
        clientdir.REPACK_CHECK,
        clientdir.SPACE_CHECK,
    ]


def test_every_warning_and_unchecked_row_says_what_to_do_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warning nobody can act on is noise, and noise is what buries the refusals."""
    bad = client(tmp_path / "bad", mpqs=0, locale=None)
    (bad / clientdir.REPACK_FILE).write_text("x", encoding="utf-8")
    cases = [
        clientdir.validate(bad, TBC, free_bytes=lambda _p: 0),
        clientdir.validate(bad, TBC, free_bytes=lambda _p: None),
    ]

    def boom(*_args: object) -> tuple[Path, ...]:
        raise PermissionError("permission denied")

    monkeypatch.setattr(clientdir, "mpq_files", boom)
    monkeypatch.setattr(clientdir, "locale_dirs", boom)
    cases.append(clientdir.validate(bad, TBC, free_bytes=lambda _p: None))
    seen = set()
    for checks in cases:
        for check in checks:
            if check.verdict in {"warn", "unchecked"}:
                seen.add((check.name, check.verdict))
                assert check.detail.strip(), check
                assert check.remedy.strip(), check
    assert len(seen) == 8, seen  # four rules, each seen warning and each seen unchecked


def test_the_warn_table_is_the_four_rules_in_reading_order(tmp_path: Path) -> None:
    """The names are the user-visible column, so they are pinned as text, once.

    Reading order matters: the count is the rule that decides an install, the
    locale and repack rules explain a count that looks fine but is not, and
    free space is about the machine rather than the folder.
    """
    folder = client(tmp_path)
    tbc = [check.name for check in clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)]
    assert tbc == [
        "the client folder",
        "the client's expansion data",
        "the client's archives",
        "the client's locale",
        "the client's origin",
        "free space next to the client",
    ]
    vanilla = [
        check.name for check in clientdir.validate(folder, VANILLA, free_bytes=lambda _p: PLENTY)
    ]
    assert vanilla == [
        "the client folder",
        "the client's expansion data",
        "the client's archives",
        "the client's origin",
        "free space next to the client",
    ]


# --- the locale a server's packs are made for (T179 Task 7) ---------------------------------

CENTURION = ClientSpec(
    required_file=None,
    min_mpq=5,
    mpq_depth="recursive",
    locale_mpq_required=True,
    locales=("enUS",),
)


def test_a_client_of_another_locale_is_refused_when_the_spec_names_its_locales(
    tmp_path: Path,
) -> None:
    """Centurion's patches are enUS only (`patch-enUS-6/7/A`): a deDE client would extract
    stock maps and play without its locale patch, so it is refused, saying which it is."""
    folder = client(tmp_path, locale="deDE")
    checks = clientdir.validate(folder, CENTURION, free_bytes=lambda _p: PLENTY)
    refused = [check for check in checks if check.verdict == "refuse"]
    assert [check.name for check in refused] == [clientdir.LOCALE_CHECK]
    assert "deDE" in refused[0].detail and "enUS" in refused[0].detail
    assert "enUS" in refused[0].remedy
    assert checks[-1] is refused[0], "a refusal ends the table"


def test_a_client_holding_a_named_locale_passes_the_locale_rule(tmp_path: Path) -> None:
    folder = client(tmp_path, locale="enUS")
    (folder / "Data" / "deDE").mkdir()
    (folder / "Data" / "deDE" / "locale-deDE.MPQ").write_bytes(b"MPQ")
    checks = clientdir.validate(folder, CENTURION, free_bytes=lambda _p: PLENTY)
    assert not [check for check in checks if check.verdict == "refuse"]


def test_the_locale_name_is_matched_as_windows_spells_it_any_case(tmp_path: Path) -> None:
    folder = client(tmp_path, locale="ENUS")
    checks = clientdir.validate(folder, CENTURION, free_bytes=lambda _p: PLENTY)
    assert not [check for check in checks if check.verdict == "refuse"]


def test_a_client_with_no_locale_folder_is_refused_when_a_locale_is_named(tmp_path: Path) -> None:
    folder = client(tmp_path, locale=None)
    checks = clientdir.validate(folder, CENTURION, free_bytes=lambda _p: PLENTY)
    refused = [check for check in checks if check.verdict == "refuse"]
    assert [check.name for check in refused] == [clientdir.LOCALE_CHECK]
    assert "no locale folder" in refused[0].detail


def test_no_locales_named_leaves_every_locale_to_the_old_rules(tmp_path: Path) -> None:
    folder = client(tmp_path, locale="deDE")
    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)
    assert not [check for check in checks if check.verdict == "refuse"]


def test_an_unreadable_data_folder_is_unchecked_not_a_refusal_for_the_locale_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = client(tmp_path)

    def refuse(_data: Path) -> tuple[Path, ...]:
        raise PermissionError("denied")

    monkeypatch.setattr(clientdir, "locale_dirs", refuse)
    checks = clientdir.validate(folder, CENTURION, free_bytes=lambda _p: PLENTY)
    assert verdicts(checks)[clientdir.LOCALE_CHECK] == "unchecked"
    assert not [check for check in checks if check.verdict == "refuse"]


def test_a_spec_naming_locales_must_also_require_the_locale_folder() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="needs locale_mpq_required"):
        ClientSpec(required_file=None, locales=("enUS",))
    with pytest.raises(ValidationError, match="String should match pattern"):
        ClientSpec(required_file=None, locale_mpq_required=True, locales=("en/US",))


# -- T227: a client whose archives are named in lower case -------------------


def _centurion_spec() -> ClientSpec:
    """The real catalog's Centurion client rule: `Data/lichking.MPQ`, enUS archives."""
    from yulon.catalog.catalog import load_catalog

    native = load_catalog().get("wow-centurion").install.native
    assert native is not None and native.trinitycore is not None
    return native.trinitycore.client


def _lowercase_335a(root: Path) -> Path:
    """A complete 3.3.5a enUS client whose archive names are all lower case.

    How the Centurion proof's client looked (yulon-ubuntu2, 2026-10-04), and how
    clients copied from Windows or unpacked by some tools arrive.
    """
    data = root / "client" / "Data"
    (data / "enus").mkdir(parents=True)
    for name in ("common", "common-2", "expansion", "lichking", "patch", "patch-2", "patch-3"):
        (data / f"{name}.mpq").write_bytes(b"MPQ")
    for name in ("locale-enus", "speech-enus", "patch-enus"):
        (data / "enus" / f"{name}.mpq").write_bytes(b"MPQ")
    return root / "client"


@needs_case_sensitive_disk
def test_a_lowercase_335a_client_passes_on_a_case_sensitive_disk(tmp_path: Path) -> None:
    """T227: refused on Linux as 'Data/lichking.MPQ is missing' while it was right there."""
    folder = _lowercase_335a(tmp_path)

    checks = clientdir.validate(folder, _centurion_spec(), free_bytes=lambda _p: PLENTY)

    assert [check for check in checks if check.verdict == "refuse"] == []
    required = next(check for check in checks if check.name == clientdir.REQUIRED_CHECK)
    assert required.verdict == "pass"
    assert "Data/lichking.mpq" in required.detail, "the name said is the one on disk"
    assert sorted(p.name for p in (folder / "Data").iterdir() if p.is_file())[3] == (
        "lichking.mpq"
    ), "nothing in the player's client was renamed"
    assert not (folder / "Data" / "lichking.MPQ").exists()


def test_a_folder_wearing_the_required_file_s_name_in_another_case_is_still_refused(
    tmp_path: Path,
) -> None:
    """The match is on the name; a folder is not the archive whatever it is called."""
    folder = _lowercase_335a(tmp_path)
    (folder / "Data" / "lichking.mpq").unlink()
    (folder / "Data" / "LICHKING.mpq").mkdir()

    checks = clientdir.validate(folder, _centurion_spec(), free_bytes=lambda _p: PLENTY)

    refused = [check for check in checks if check.verdict == "refuse"]
    assert [check.name for check in refused] == [clientdir.REQUIRED_CHECK]
    assert "Data/lichking.MPQ is missing" in refused[0].detail


def _native_client(game: str) -> ClientSpec:
    """The real catalog's client rule for a CMaNGOS game."""
    from yulon.catalog.catalog import load_catalog

    native = load_catalog().get(game).install.native
    assert native is not None and native.cmangos is not None
    return native.cmangos.client


@needs_case_sensitive_disk
@pytest.mark.parametrize(
    ("game", "lower"),
    [
        ("wow-tbc", "Data/expansion.mpq"),
        ("wow-vanilla", "Data/dbc.mpq"),
    ],
)
def test_a_cmangos_game_accepts_a_lowercase_client_its_map_tools_read_through_links(
    tmp_path: Path, game: str, lower: str
) -> None:
    """T260: was the T227 refusal "named in lower case; this server's map tools open ... only".

    The extraction now reads such a client through links carrying the names the
    tools open (`extract.lay_case_view()`), so the check passes it and names the
    file as the disk names it.
    """
    folder = client(tmp_path, required=False)
    (folder / lower).write_bytes(b"MPQ")

    checks = clientdir.validate(folder, _native_client(game), free_bytes=lambda _p: PLENTY)

    assert [check for check in checks if check.verdict == "refuse"] == []
    required = next(check for check in checks if check.name == clientdir.REQUIRED_CHECK)
    assert (required.verdict, required.detail) == ("pass", f"{lower} is there")
    assert (folder / lower).is_file(), "nothing in the player's client was renamed"


@needs_case_sensitive_disk
def test_a_spec_whose_tools_read_exact_names_still_refuses_another_case(tmp_path: Path) -> None:
    """`archives_any_case` false: the refusal T227 wrote stays for a family that needs it."""
    folder = client(tmp_path, required=False)
    (folder / "Data" / "Expansion.mpq").write_bytes(b"MPQ")

    checks = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)

    (refused,) = [check for check in checks if check.verdict == "refuse"]
    assert refused.detail == (
        "Data/Expansion.mpq is named in another case; this server's map tools open "
        "Data/expansion.MPQ only"
    )


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_every_cmangos_client_spec_accepts_archives_in_any_case(game: str) -> None:
    """T260: each CMaNGOS extraction reads another case through links, so each spec says so."""
    assert _native_client(game).archives_any_case is True


# -- T261: a client whose Data folder itself is named in lower case --------------------


def _lowercase_data_folder(root: Path) -> Path:
    """`client(root)` with its `Data/` renamed `data/`, as `unzip -LL` leaves a client."""
    folder = client(root)
    (folder / "Data").rename(folder / "data")
    return folder


@needs_case_sensitive_disk
@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"])
def test_a_lowercase_data_folder_is_a_client_for_every_game_that_extracts(
    tmp_path: Path, game: str
) -> None:
    """T261: refused as "has no Data directory, so it is not a game client" while it held one."""
    folder = _lowercase_data_folder(tmp_path)
    (folder / "data" / "lichking.mpq").write_bytes(b"MPQ")
    spec = _centurion_spec() if game == "wow-centurion" else _native_client(game)

    checks = clientdir.validate(folder, spec, free_bytes=lambda _p: PLENTY)

    assert [check for check in checks if check.verdict == "refuse"] == []
    first = checks[0]
    assert first.name == clientdir.CLIENT_CHECK
    assert first.detail == f"{folder} has a data directory", "named as the disk names it"
    assert sorted(p.name for p in folder.iterdir()) == ["data"], "nothing was renamed"


@needs_case_sensitive_disk
def test_the_archives_of_a_lowercase_data_folder_are_counted(tmp_path: Path) -> None:
    """The count and locale rules read the folder that is there, not an empty `Data/`."""
    folder = _lowercase_data_folder(tmp_path)

    checks = clientdir.validate(folder, _native_client("wow-tbc"), free_bytes=lambda _p: PLENTY)

    found = {check.name: check for check in checks}
    assert found[clientdir.MPQ_CHECK].verdict == "pass"
    assert found[clientdir.MPQ_CHECK].detail.startswith("11 MPQ archives anywhere under ")
    assert found[clientdir.MPQ_CHECK].detail.endswith(f"{folder / 'data'}")
    assert found[clientdir.LOCALE_CHECK].detail == "locale archives in enUS"


def test_a_folder_named_like_data_that_is_a_file_is_still_not_a_client(tmp_path: Path) -> None:
    """Found whatever its case, and still has to be a folder."""
    folder = tmp_path / "client"
    folder.mkdir()
    (folder / "data").write_bytes(b"not a folder")

    (check,) = clientdir.validate(folder, TBC, free_bytes=lambda _p: PLENTY)

    assert check.verdict == "refuse"
    assert check.detail == (
        f"{folder} has no {clientdir.DATA_DIR} directory, so it is not a game client"
    )
