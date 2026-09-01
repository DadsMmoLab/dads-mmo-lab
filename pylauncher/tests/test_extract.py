"""Tests for `yulon.catalog.families.extract`.

The rule under test is three-part and every part is a real incident shape: a
tool killed after it passed its count threshold (record missing → re-run), a
resume pointed at another client (stage facts differ → everything re-runs),
and an edited argv (hash differs → that tool re-runs).

The fourth thing these tests pin is the shape of the answer, not its value.
"Has this tool already run?" has three states and `tool_satisfied` returns two,
so every way of not knowing — an evidence file that will not open, one that
will not parse, a `stat()` on the client that raises — has to be walked to the
`False` side on purpose and proved to land there. A skip is unrecoverable from
the user's side: they get a half-extracted client that looks finished. A
needless re-run only costs an hour.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yulon.catalog.catalog import ExtractPlan, ExtractTool
from yulon.catalog.families import extract

AD = ExtractTool(
    name="dbc and maps",
    argv=("/opt/bin/ad", "-i", "/client", "-o", "/out"),
    produces={"dbc": 3, "maps": 2},
)
VMAP = ExtractTool(
    name="vmap extract",
    argv=("/opt/bin/vmap_extractor", "-d", "/client/Data"),
    produces={"Buildings": 2},
)
ASSEMBLE = ExtractTool(
    name="vmap assemble",
    argv=("/opt/bin/vmap_assembler", "Buildings", "vmaps"),
    produces={"vmaps": 2},
)
PLAN = ExtractPlan(image="server", tools=(AD, VMAP, ASSEMBLE))
REQUIRED = "Data/expansion.MPQ"


def client(root: Path) -> Path:
    folder = root / "client"
    (folder / "Data").mkdir(parents=True)
    (folder / "Data" / "expansion.MPQ").write_bytes(b"MPQ" * 100)
    return folder


def fill(data_dir: Path, folder: str, count: int) -> None:
    (data_dir / folder).mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (data_dir / folder / f"f{index}").write_bytes(b"x")


def satisfied_data(tmp_path: Path) -> tuple[Path, Path]:
    """A client and a `data/` in which `AD`'s counts pass — the setup five tests share."""
    folder = client(tmp_path)
    data = tmp_path / "data"
    fill(data, "dbc", 3)
    fill(data, "maps", 2)
    return folder, data


def test_evidence_round_trips_and_a_missing_or_broken_file_reads_as_none(tmp_path: Path) -> None:
    assert extract.read_evidence(tmp_path) is None
    evidence = extract.Evidence("abc", "/c", 300, 1700000000, (extract.ToolRecord("ad", "h", 1),))
    extract.write_evidence(tmp_path, evidence)
    assert (tmp_path / extract.EVIDENCE_FILE).is_file()
    assert extract.read_evidence(tmp_path) == evidence
    (tmp_path / extract.EVIDENCE_FILE).write_text("{not json", encoding="utf-8")
    assert extract.read_evidence(tmp_path) is None


def test_expected_evidence_reads_the_required_file_facts_and_none_without_one(
    tmp_path: Path,
) -> None:
    folder = client(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    assert expected.client_path == str(folder.resolve())
    assert expected.required_file_size == 300
    assert expected.required_file_mtime == int((folder / "Data" / "expansion.MPQ").stat().st_mtime)
    assert expected.plan_hash == extract.plan_hash(PLAN)
    bare = extract.expected_evidence(PLAN, folder, None)
    assert bare.required_file_size is None and bare.required_file_mtime is None


def test_plan_hash_is_stable_and_changes_with_the_plan() -> None:
    assert extract.plan_hash(PLAN) == extract.plan_hash(PLAN)
    assert len(extract.plan_hash(PLAN)) == 16
    other = ExtractPlan(image="server", tools=(AD, VMAP, ASSEMBLE), ulimit_stack_unlimited=True)
    assert extract.plan_hash(other) != extract.plan_hash(PLAN)


def test_a_tool_with_passing_counts_but_no_record_is_not_satisfied(tmp_path: Path) -> None:
    folder, data = satisfied_data(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    assert extract.tool_satisfied(AD, data, None, expected) is False
    assert extract.tool_satisfied(AD, data, expected, expected) is False  # no ToolRecord yet
    recorded = extract.with_record(
        expected, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    assert extract.tool_satisfied(AD, data, recorded, expected) is True


def test_a_record_with_passing_counts_but_other_stage_facts_is_not_satisfied(
    tmp_path: Path,
) -> None:
    folder, data = satisfied_data(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    record = extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    other_client = extract.with_record(
        extract.Evidence(
            expected.plan_hash, "/somewhere/else", 300, expected.required_file_mtime, ()
        ),
        record,
    )
    assert extract.tool_satisfied(AD, data, other_client, expected) is False
    other_argv = extract.with_record(expected, extract.ToolRecord(AD.name, "0000000000000000", 5))
    assert extract.tool_satisfied(AD, data, other_argv, expected) is False


def test_a_record_whose_counts_fell_short_is_not_satisfied(tmp_path: Path) -> None:
    folder = client(tmp_path)
    data = tmp_path / "data"
    fill(data, "dbc", 3)
    fill(data, "maps", 1)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    recorded = extract.with_record(
        expected, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    assert extract.tool_satisfied(AD, data, recorded, expected) is False
    assert extract.shortfall(AD.produces, data) == {"maps": (1, 2)}


def test_with_record_replaces_a_record_of_the_same_name() -> None:
    evidence = extract.Evidence("p", "/c", None, None, (extract.ToolRecord("a", "1", 1),))
    again = extract.with_record(evidence, extract.ToolRecord("a", "2", 2))
    assert again.tools == (extract.ToolRecord("a", "2", 2),)
    assert extract.with_record(again, extract.ToolRecord("b", "3", 3)).tools[1].name == "b"


# --- the three parts, one at a time, each with its neighbours held right ------------------


def test_each_part_of_the_rule_alone_is_enough_to_refuse_the_skip(tmp_path: Path) -> None:
    """Every part is load-bearing: break one, hold the other two, and the skip is gone.

    Asserted as a table rather than three separate `False`s so that a rule that
    collapsed into "any two of three" cannot pass by having a neighbour answer
    for the part that was broken.
    """
    folder, data = satisfied_data(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    good_record = extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    whole = extract.with_record(expected, good_record)
    assert extract.tool_satisfied(AD, data, whole, expected) is True

    no_record = expected
    stale_stage = extract.with_record(
        extract.Evidence(
            expected.plan_hash, expected.client_path, 999, expected.required_file_mtime, ()
        ),
        good_record,
    )
    assert extract.tool_satisfied(AD, data, no_record, expected) is False, "part 2: the record"
    assert (
        extract.tool_satisfied(AD, data, stale_stage, expected) is False
    ), "part 1: the stage facts"
    (data / "maps" / "f1").unlink()
    assert extract.tool_satisfied(AD, data, whole, expected) is False, "part 3: the counts"


def test_a_record_for_another_tool_never_satisfies_this_one(tmp_path: Path) -> None:
    """The record is looked up BY NAME; any record at all would make the gate a count gate."""
    folder, data = satisfied_data(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    someone_else = extract.with_record(
        expected, extract.ToolRecord(VMAP.name, extract.argv_hash(VMAP.argv), 5)
    )
    assert extract.tool_satisfied(AD, data, someone_else, expected) is False
    assert someone_else.record_for(AD.name) is None
    assert someone_else.record_for(VMAP.name) is not None


def test_every_stage_fact_is_compared_and_not_just_the_client_path(tmp_path: Path) -> None:
    folder, data = satisfied_data(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    record = extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    for field, value in (
        ("plan_hash", "0" * 16),
        ("client_path", "/somewhere/else"),
        ("required_file_size", 4096),
        ("required_file_mtime", 1),
    ):
        differs = extract.with_record(
            extract.Evidence(
                **{
                    "plan_hash": expected.plan_hash,
                    "client_path": expected.client_path,
                    "required_file_size": expected.required_file_size,
                    "required_file_mtime": expected.required_file_mtime,
                    "tools": (),
                    field: value,
                }
            ),
            record,
        )
        assert extract.tool_satisfied(AD, data, differs, expected) is False, field
        assert extract.same_stage(differs, expected) is False, field


def test_satisfied_answers_the_same_question_without_an_extract_tool(tmp_path: Path) -> None:
    """`run_mmaps` has an `MmapPlan`, not an `ExtractTool`; both go through one rule."""
    folder, data = satisfied_data(tmp_path)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    recorded = extract.with_record(
        expected, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    assert extract.satisfied(AD.name, AD.argv, AD.produces, data, recorded, expected) is True
    assert extract.satisfied(AD.name, ("other",), AD.produces, data, recorded, expected) is False
    assert extract.satisfied("mmaps", AD.argv, {}, data, recorded, expected) is False


# --- "we could not tell" lands on False, three ways ---------------------------------------


def test_an_evidence_file_that_will_not_open_is_no_evidence_and_never_a_skip(
    tmp_path: Path,
) -> None:
    """A directory where the file should be: a real OSError from the real filesystem.

    The claim "that tool already ran" may only come from evidence somebody read.
    An unreadable file proves nothing, so it reads as `None` — the same answer
    as no file at all — and the tool runs again.
    """
    folder, data = satisfied_data(tmp_path)
    (data / extract.EVIDENCE_FILE).mkdir()
    assert extract.read_evidence(data) is None
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    assert extract.tool_satisfied(AD, data, extract.read_evidence(data), expected) is False


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        '"a string"',
        json.dumps(
            {
                "client_path": "/c",
                "required_file_size": None,
                "required_file_mtime": None,
                "tools": [],
            }
        ),
        json.dumps(
            {
                "plan_hash": "p",
                "client_path": "/c",
                "required_file_size": None,
                "required_file_mtime": None,
                "tools": [{"name": "a", "argv_hash": "h"}],
                "client_facts_complete": True,
            }
        ),
        json.dumps(
            {
                "plan_hash": "p",
                "client_path": "/c",
                "required_file_size": None,
                "required_file_mtime": None,
                "tools": {"a": 1},
                "client_facts_complete": True,
            }
        ),
        json.dumps(
            {
                "plan_hash": "p",
                "client_path": "/c",
                "required_file_size": "not a number",
                "required_file_mtime": None,
                "tools": [],
                "client_facts_complete": True,
            }
        ),
        json.dumps(
            {
                "plan_hash": "p",
                "client_path": "/c",
                "required_file_size": None,
                "required_file_mtime": None,
                "tools": [],
                "client_facts_complete": "yes",
            }
        ),
    ],
    ids=[
        "broken",
        "list",
        "scalar",
        "missing-key",
        "short-record",
        "tools-not-a-list",
        "size-not-a-number",
        "flag-not-a-bool",
    ],
)
def test_evidence_that_will_not_parse_reads_as_none(tmp_path: Path, text: str) -> None:
    """Every malformed shape is one answer — `None` — and never a half-built `Evidence`."""
    (tmp_path / extract.EVIDENCE_FILE).write_text(text, encoding="utf-8")
    assert extract.read_evidence(tmp_path) is None


def test_read_evidence_opens_the_file_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two reads is two answers, and the disagreement always accuses somebody.

    `is_file()` then `read_text()` is the shape that made a transient failure
    read as "Yu'lon did not write this" in I.3. Both halves of that shape are
    pinned: one `open()`, and no second question about the same path — an
    `is_file()` that says "yes" to a file the following read cannot open is the
    disagreement, and it costs an hour of extraction either way it falls.
    """
    extract.write_evidence(tmp_path, extract.Evidence("p", "/c", None, None, ()))
    opens: list[Path] = []
    asked: list[Path] = []
    real_open, real_is_file = Path.open, Path.is_file

    def counting_open(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == extract.EVIDENCE_FILE:
            opens.append(self)
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    def counting_is_file(self: Path, *args: object, **kwargs: object) -> bool:
        if self.name == extract.EVIDENCE_FILE:
            asked.append(self)
        return real_is_file(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", counting_open)
    monkeypatch.setattr(Path, "is_file", counting_is_file)
    assert extract.read_evidence(tmp_path) is not None
    assert len(opens) == 1, f"the evidence file was opened {len(opens)} times"
    assert asked == [], "the evidence file was asked about as well as read"


def test_a_stat_that_raises_marks_the_facts_incomplete_and_can_never_skip(tmp_path: Path) -> None:
    """A required file we could not measure is not a client we can identify.

    A real OSError, from a real path whose parent is a file — no monkeypatched
    exception that only happens to be the one the code catches. The evidence
    that comes out says so, and even compared with ITSELF it refuses the skip:
    "I do not know which client this was" cannot be made true by asking twice.
    """
    folder, data = satisfied_data(tmp_path)
    through_a_file = "Data/expansion.MPQ/inner"
    with pytest.raises(OSError):
        folder.joinpath("Data", "expansion.MPQ", "inner").stat()

    expected = extract.expected_evidence(PLAN, folder, through_a_file)
    assert expected.client_facts_complete is False
    assert expected.required_file_size is None and expected.required_file_mtime is None
    recorded = extract.with_record(
        expected, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    assert extract.tool_satisfied(AD, data, recorded, expected) is False
    assert extract.satisfied(AD.name, AD.argv, AD.produces, data, recorded, recorded) is False


def test_incomplete_facts_stay_incomplete_across_the_file_so_a_resume_cannot_use_them(
    tmp_path: Path,
) -> None:
    """The doubt is written down, not lost in the round trip.

    Without the flag on disk, a run whose `stat()` failed writes size `null`
    and mtime `null`; the next run's `stat()` fails the same way, the two
    `null`s agree, and the skip happens on a client nobody ever identified.
    """
    folder, data = satisfied_data(tmp_path)
    unknown = extract.expected_evidence(PLAN, folder, "Data/expansion.MPQ/inner")
    recorded = extract.with_record(
        unknown, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    extract.write_evidence(data, recorded)

    back = extract.read_evidence(data)
    assert back == recorded
    assert back is not None and back.client_facts_complete is False
    assert extract.tool_satisfied(AD, data, back, unknown) is False
    assert (
        extract.tool_satisfied(AD, data, back, extract.expected_evidence(PLAN, folder, REQUIRED))
        is False
    )


def test_a_client_dir_that_will_not_resolve_is_incomplete_rather_than_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve()` is a filesystem call and can fail; it must not escape as a raw OSError."""
    folder, data = satisfied_data(tmp_path)

    def refuse(self: Path, strict: bool = False) -> Path:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(Path, "resolve", refuse)
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    assert expected.client_facts_complete is False
    assert expected.client_path == str(folder)
    recorded = extract.with_record(
        expected, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    assert extract.tool_satisfied(AD, data, recorded, expected) is False


def test_no_required_file_is_complete_facts_and_still_skips(tmp_path: Path) -> None:
    """Tortoise names no required file: that is an answer, not a failure to get one.

    The distinction the flag exists for. Both shapes write two `null`s; only one
    of them is allowed to license a skip.
    """
    folder, data = satisfied_data(tmp_path)
    bare = extract.expected_evidence(PLAN, folder, None)
    assert bare.client_facts_complete is True
    recorded = extract.with_record(bare, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5))
    assert extract.tool_satisfied(AD, data, recorded, bare) is True


@pytest.mark.parametrize("error", [PermissionError(13, "denied"), OSError(5, "I/O error")])
def test_an_output_folder_that_will_not_list_counts_short_and_never_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    """The I.1 incident, in the other module: `rglob()` answers an unreadable
    folder with a short list and no sound. Short is the safe side here — it
    re-runs the tool — but it has to be reached deliberately and logged, never
    by a swallowed error, and the count must never be rounded UP to the
    threshold, which is the shape that would skip a folder nobody could read.
    """
    folder, data = satisfied_data(tmp_path)
    real_iterdir = Path.iterdir

    def refuse(self: Path) -> object:
        if self.name == "maps":
            raise error
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert extract.file_count(data / "maps") == 0
    assert extract.shortfall(AD.produces, data) == {"maps": (0, 2)}
    expected = extract.expected_evidence(PLAN, folder, REQUIRED)
    recorded = extract.with_record(
        expected, extract.ToolRecord(AD.name, extract.argv_hash(AD.argv), 5)
    )
    assert extract.tool_satisfied(AD, data, recorded, expected) is False


# --- counting ------------------------------------------------------------------------------


def test_file_count_walks_to_any_depth_and_answers_zero_for_what_is_not_a_folder(
    tmp_path: Path,
) -> None:
    fill(tmp_path, "maps", 2)
    nested = tmp_path / "maps" / "deep" / "deeper"
    nested.mkdir(parents=True)
    (nested / "x").write_bytes(b"x")
    assert extract.file_count(tmp_path / "maps") == 3
    assert extract.file_count(tmp_path / "never-made") == 0
    (tmp_path / "a-file").write_bytes(b"x")
    assert extract.file_count(tmp_path / "a-file") == 0
    assert extract.file_count(tmp_path / "a-file" / "under-a-file") == 0


def test_shortfall_names_every_short_folder_and_is_empty_when_all_pass(tmp_path: Path) -> None:
    fill(tmp_path, "dbc", 1)
    assert extract.shortfall({"dbc": 3, "maps": 2}, tmp_path) == {"dbc": (1, 3), "maps": (0, 2)}
    fill(tmp_path, "dbc", 3)
    fill(tmp_path, "maps", 5)
    assert extract.shortfall({"dbc": 3, "maps": 2}, tmp_path) == {}
    assert extract.shortfall({}, tmp_path) == {}


# --- hashes --------------------------------------------------------------------------------


def test_argv_hash_is_short_hex_and_separates_argv_that_flatten_to_one_string() -> None:
    """`("a b",)` and `("a", "b")` are different command lines and must hash apart.

    A hash over `" ".join(argv)` collapses them, and the argv part of the rule
    then cannot see an edited catalog block that merely re-split an argument.
    """
    assert extract.argv_hash(("a", "b")) != extract.argv_hash(("a b",))
    assert extract.argv_hash(("a", "b")) == extract.argv_hash(["a", "b"])
    digest = extract.argv_hash(("a", "b"))
    assert len(digest) == 16 and int(digest, 16) >= 0


def test_plan_hash_moves_with_a_tools_argv_its_counts_and_their_order() -> None:
    edited_argv = ExtractTool(name=AD.name, argv=(*AD.argv, "--verbose"), produces=AD.produces)
    edited_counts = ExtractTool(name=AD.name, argv=AD.argv, produces={"dbc": 3, "maps": 9})
    assert extract.plan_hash(
        ExtractPlan(image="server", tools=(edited_argv, VMAP, ASSEMBLE))
    ) != extract.plan_hash(PLAN)
    assert extract.plan_hash(
        ExtractPlan(image="server", tools=(edited_counts, VMAP, ASSEMBLE))
    ) != extract.plan_hash(PLAN)
    assert extract.plan_hash(
        ExtractPlan(image="server", tools=(VMAP, AD, ASSEMBLE))
    ) != extract.plan_hash(PLAN)
    assert extract.plan_hash(
        ExtractPlan(image="other", tools=(AD, VMAP, ASSEMBLE))
    ) != extract.plan_hash(PLAN)
    assert int(extract.plan_hash(PLAN), 16) >= 0


# --- writing -------------------------------------------------------------------------------


def test_write_evidence_makes_the_data_dir_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    data = tmp_path / "server" / "data"
    first = extract.Evidence("p", "/c", 1, 2, (extract.ToolRecord("a", "h", 3),))
    extract.write_evidence(data, first)
    extract.write_evidence(data, extract.with_record(first, extract.ToolRecord("b", "h2", 4)))
    assert sorted(path.name for path in data.iterdir()) == [extract.EVIDENCE_FILE]
    read = extract.read_evidence(data)
    assert read is not None and [record.name for record in read.tools] == ["a", "b"]


def test_the_evidence_file_is_written_lf_whatever_the_platform(tmp_path: Path) -> None:
    """A CRLF copy of our own JSON parses fine, but the file is ours and stays LF —
    the same rule the Dockerfile writer keeps, for the same reason (I.3)."""
    extract.write_evidence(tmp_path, extract.Evidence("p", "/c", None, None, ()))
    raw = (tmp_path / extract.EVIDENCE_FILE).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_a_write_that_fails_leaves_no_temporary_and_does_not_pretend_it_worked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written claim that reads as a whole one is the skip we cannot afford."""
    monkeypatch.setattr(extract.os, "replace", _raise_oserror)
    with pytest.raises(OSError):
        extract.write_evidence(tmp_path, extract.Evidence("p", "/c", None, None, ()))
    assert list(tmp_path.iterdir()) == []
    assert extract.read_evidence(tmp_path) is None


def _raise_oserror(*args: object, **kwargs: object) -> None:
    raise OSError(28, "no space left on device")


def test_with_record_leaves_the_original_alone_and_keeps_the_others(tmp_path: Path) -> None:
    evidence = extract.Evidence(
        "p", "/c", None, None, (extract.ToolRecord("a", "1", 1), extract.ToolRecord("b", "2", 2))
    )
    again = extract.with_record(evidence, extract.ToolRecord("a", "9", 9))
    assert evidence.tools == (extract.ToolRecord("a", "1", 1), extract.ToolRecord("b", "2", 2))
    assert [record.name for record in again.tools] == ["b", "a"]
    assert again.record_for("a") == extract.ToolRecord("a", "9", 9)
    assert again.record_for("b") == extract.ToolRecord("b", "2", 2)
    assert again.plan_hash == "p" and again.client_path == "/c"


def test_the_evidence_file_lives_under_the_data_dir_it_vouches_for() -> None:
    """Deleting `data/` deletes the claim; a marker elsewhere would outlive its subject."""
    assert extract.EVIDENCE_FILE == ".yulon-extract.json"
