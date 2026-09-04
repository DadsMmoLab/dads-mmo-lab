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

import inspect
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from yulon import docker, platform
from yulon.catalog.catalog import ExtractPlan, ExtractTool, MmapPlan, RetrySpec, load_catalog
from yulon.catalog.families import extract
from yulon.catalog.installer import InstallerError

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


def test_a_hand_built_evidence_defaults_to_having_identified_its_client() -> None:
    """The default is the safe end of `client_facts_complete`, and nothing else pins it.

    Every caller inside this module sets the flag explicitly — `expected_evidence`
    from whether the `stat()` landed, `_parse` from the file — so a mutation that
    flipped the DEFAULT survived the whole suite. The direction it would fail in is
    the harmless one (a spurious re-run, never a wrong skip), which is exactly why
    nothing noticed: a default nobody exercises is a default nobody is told about.

    `True` is right because the flag means "the client facts are complete", and a
    five-field construction — the shape the plan's own tests and I.5's use — is one
    that supplied every client fact it had. A `False` default would make those
    constructions silently unskippable forever.
    """
    assert extract.Evidence("p", "/c", 1, 2, ()).client_facts_complete is True


# --- running the plan: one container per tool -----------------------------------------------
#
# `run_plan` is where the evidence rule above meets a real `docker run`, and
# where three questions that each have three answers have to stay apart:
#
#   * how a tool ended — exit 0, non-zero, stopped by the user, or never started
#     at all because there was no docker CLI to start it;
#   * what its output folders say — enough files, too few, or a folder nobody
#     could list (I.4 walks that last one to "too few" on purpose, and the
#     refusal has to say so rather than blame the user's client for it);
#   * whether a tool has run — recorded, recorded for another argv, or not yet.
#
# The container is described by field, so every one of those is asserted by
# field too: which mount is `:ro`, which security option is on the RUN and not
# on a mount, and how many times the machine was asked about SELinux.


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """Every value that follows `flag` — the same by-field audit `test_docker.py` uses."""
    return [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == flag]


def tool_program(spec: docker.ContainerRun) -> str:
    """The extractor's own program, whether it ran directly or through the staging wrapper.

    `stage_client` wraps the argv in `sh -c <script> sh <the tool...>`, so
    `argv[0]` stops naming the tool and starts naming a shell. A double that
    keyed its behaviour on `argv[0]` would answer every staged run with "I know
    nothing about `sh`" — no fabricated output, no configured failure — and a
    staging test would then pass or fail on the count gate rather than on the
    staging. This reads the tool back out of the wrapper, so the same `Runner`
    means the same thing in both modes.
    """
    return spec.argv[4] if spec.argv[:2] == ("sh", "-c") else spec.argv[0]


class Runner:
    """A `run_container` double: records every spec by field and fabricates the tool's output.

    Its fabricated output goes straight to the `/out` mount even for a staged
    run, where the real tool writes into the farm and `STAGE_SCRIPT` copies the
    result across. That is the same net effect, and the copy itself is shell —
    proved against a real `cp -rs`, not against this class.
    """

    def __init__(
        self,
        writes: Mapping[str, Mapping[str, int]],
        *,
        fail: Mapping[str, tuple[int, str]] | None = None,
    ) -> None:
        self.writes = writes
        self.fail = dict(fail or {})
        self.specs: list[docker.ContainerRun] = []
        self.cancel_after: int | None = None

    def __call__(
        self, spec: docker.ContainerRun, *, sink: docker.OutputSink, cancel: threading.Event | None
    ) -> docker.AttachedRun:
        self.specs.append(spec)
        program = tool_program(spec)
        sink(f"ran {program}")
        out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
        for folder, count in self.writes.get(program, {}).items():
            fill(out, folder, count)
        if program in self.fail:
            code, words = self.fail.pop(program)
            return docker.AttachedRun(code, (words,))
        if self.cancel_after is not None and len(self.specs) >= self.cancel_after:
            if cancel is not None:
                cancel.set()
            return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ())
        return docker.AttachedRun(0, ())

    def names(self) -> list[str]:
        return [Path(tool_program(spec)).name for spec in self.specs]


FULL = {
    "/opt/bin/ad": {"dbc": 3, "maps": 2},
    "/opt/bin/vmap_extractor": {"Buildings": 2},
    "/opt/bin/vmap_assembler": {"vmaps": 2},
}


def run(
    plan: ExtractPlan,
    runner: Runner,
    tmp_path: Path,
    *,
    cancel: threading.Event | None = None,
    enforcing: bool | None = None,
) -> list[str]:
    """Drive `run_plan` to exhaustion, with the SELinux answer stated rather than measured.

    `enforcing` goes through the seam on every call, including its `None`
    default. A test that let the real probe answer would assert whatever THIS
    machine is — on Windows always "could not tell" — and would pass for a
    reason that has nothing to do with the code.
    """
    folder = client(tmp_path) if not (tmp_path / "client").exists() else tmp_path / "client"
    return list(
        extract.run_plan(
            plan,
            image_ref="yulon.local/x-server:1",
            client_dir=folder,
            data_dir=tmp_path / "server" / "data",
            run_container=runner,
            user_args=("--user", "1000:1000"),
            sink=lambda _line: None,
            cancel=cancel,
            required_file=REQUIRED,
            client_build=8606,
            selinux_enforcing=lambda: enforcing,
        )
    )


def test_each_tool_runs_once_with_the_client_read_only_and_data_at_out(tmp_path: Path) -> None:
    runner = Runner(FULL)
    run(PLAN, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor", "vmap_assembler"]
    for spec in runner.specs:
        assert spec.image == "yulon.local/x-server:1"
        assert spec.workdir == "/out"
        assert spec.user_args == ("--user", "1000:1000")
        assert spec.ulimits == ()
        by_guest = {mount.guest: mount for mount in spec.mounts}
        assert by_guest["/client"].read_only is True
        assert by_guest["/client"].host == tmp_path / "client"
        assert by_guest["/out"].read_only is False
        assert by_guest["/out"].host == tmp_path / "server" / "data"
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None
    assert [record.name for record in evidence.tools] == [AD.name, VMAP.name, ASSEMBLE.name]


def test_ulimit_stack_unlimited_is_passed_by_field(tmp_path: Path) -> None:
    runner = Runner(FULL)
    run(ExtractPlan(image="server", tools=(AD,), ulimit_stack_unlimited=True), runner, tmp_path)
    assert runner.specs[0].ulimits == ("stack=-1",)


def test_a_second_run_skips_every_recorded_tool(tmp_path: Path) -> None:
    run(PLAN, Runner(FULL), tmp_path)
    again = Runner(FULL)
    said = run(PLAN, again, tmp_path)
    assert again.specs == []
    assert sum("already extracted" in line for line in said) == 3
    assert f"{AD.name}: already extracted (dbc: 3 files, maps: 2 files)" in said


def test_a_tool_with_passing_counts_but_no_record_runs_again_and_finished_tools_do_not(
    tmp_path: Path,
) -> None:
    """The kill-after-threshold case: counts pass, no record, so ONLY that tool re-runs.

    The first run is the SAME plan object as the second on purpose. A one-tool
    `ExtractPlan` hashes differently, so the stage facts would not match and
    every tool would re-run — which proves nothing this test is about.
    """
    with pytest.raises(InstallerError):
        run(PLAN, Runner({"/opt/bin/ad": {"dbc": 3, "maps": 2}}), tmp_path)
    data = tmp_path / "server" / "data"
    fill(data, "Buildings", 2)  # as a killed extractor leaves it: past its threshold, unfinished
    assert extract.shortfall(VMAP.produces, data) == {}, "the count gate alone would skip this"
    runner = Runner(FULL)
    run(PLAN, runner, tmp_path)
    assert runner.names() == ["vmap_extractor", "vmap_assembler"]


def test_evidence_for_another_client_forces_a_full_re_extract(tmp_path: Path) -> None:
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    old = extract.read_evidence(data)
    assert old is not None
    extract.write_evidence(
        data,
        extract.Evidence(old.plan_hash, "/another/client", 300, old.required_file_mtime, old.tools),
    )
    runner = Runner(FULL)
    said = run(PLAN, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor", "vmap_assembler"]
    assert any("another client" in line for line in said)
    fresh = extract.read_evidence(data)
    assert fresh is not None and fresh.client_path == str((tmp_path / "client").resolve())


def test_an_edited_plan_forces_a_full_re_extract_too(tmp_path: Path) -> None:
    """The plan hash is one of the four stage facts, and the whole point of hashing it."""
    run(PLAN, Runner(FULL), tmp_path)
    edited = ExtractPlan(image="server", tools=(AD, VMAP, ASSEMBLE), ulimit_stack_unlimited=True)
    runner = Runner(FULL)
    said = run(edited, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor", "vmap_assembler"]
    assert any("another client or plan" in line for line in said)


def test_a_tool_that_exits_zero_but_falls_short_is_refused_naming_counts_and_build(
    tmp_path: Path,
) -> None:
    runner = Runner({**FULL, "/opt/bin/ad": {"dbc": 3, "maps": 1}})
    with pytest.raises(InstallerError) as caught:
        run(PLAN, runner, tmp_path)
    message = str(caught.value)
    assert "maps" in message and "1" in message and "2" in message
    assert "8606" in message
    assert "fail to load maps" in message
    assert runner.names() == ["ad"]
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and evidence.tools == ()


def test_the_shortfall_refusal_does_not_blame_the_client_for_a_folder_nobody_could_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I.4 walks "could not list it" to "too few files"; the sentence has to say so.

    The count gate has three inputs and two answers — enough, too few, and a
    folder that would not open — and the third is deliberately folded into the
    second so nothing is skipped on evidence nobody read. That is right, and it
    leaves the refusal's *cause* unknown: told only to check the client, a user
    whose `data/` is unreadable is sent to look at the one thing that is fine.
    So the refusal names both, and names the folder whose files it counted.
    """
    real_iterdir = Path.iterdir

    def refuse(self: Path) -> object:
        if self.name == "maps":
            raise PermissionError(13, "denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    with pytest.raises(InstallerError) as caught:
        run(PLAN, Runner(FULL), tmp_path)
    message = str(caught.value)
    assert str(tmp_path / "server" / "data") in message
    assert "could not be listed" in message
    assert "maps: 0 files, at least 2 expected" in message


def test_the_shortfall_refusal_quotes_the_number_the_gate_read_and_walks_the_folder_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fifth ending is the one that counts, and it counts once. Nothing said so.

    `counts()` and `short_of()` are split apart precisely so the threshold and
    the sentence come from ONE walk, and `counts()`'s docstring names the
    incident: a folder that stops listing between two walks makes the refusal
    and the log disagree about the number both are made of. The walked-once
    tests only ever exercise a tool that succeeded, so rebuilding the refusal's
    numbers with a second `shortfall(tool.produces, data_dir)` inside the `if
    short:` block survived the whole file — the message is identical while the
    filesystem holds still.

    So the fixture does not hold still. `maps` answers 1 on its first walk and 0
    on any later one, which is the only thing that distinguishes the two
    versions: one walk quotes the 1 the gate decided on, two walks quote a 0 the
    gate never saw. `dbc` is left alone and passes, so the run reaches ending
    five and no other.
    """
    walked: list[Path] = []
    real_count = extract.file_count

    def counting(folder: Path) -> int:
        walked.append(folder)
        if folder.name == "maps":
            return 1 if walked.count(folder) == 1 else 0
        return real_count(folder)

    monkeypatch.setattr(extract, "file_count", counting)
    with pytest.raises(InstallerError) as caught:
        run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    assert walked == [data / "dbc", data / "maps"]
    assert "maps: 1 files, at least 2 expected" in str(caught.value)


def test_a_failing_tool_stops_the_plan_with_its_last_words(tmp_path: Path) -> None:
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": (1, "cannot open Data")})
    with pytest.raises(InstallerError, match="cannot open Data"):
        run(PLAN, runner, tmp_path)
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and [r.name for r in evidence.tools] == [AD.name]


def test_a_container_that_never_started_is_not_the_tool_failing(tmp_path: Path) -> None:
    """The fourth thing `run_container` can answer: there was no docker CLI to run.

    "The tool failed (exit 127)" is a sentence about a tool that ran. Nothing
    ran, no client was ever opened, and the help text is the whole answer — so
    it is said as its own thing, exactly as `docker._cli_missing()` tells the
    same two shapes apart for the unstreamed calls.
    """
    runner = Runner(FULL, fail={"/opt/bin/ad": (127, platform.DOCKER_CLI_MISSING_HELP)})
    with pytest.raises(InstallerError) as caught:
        run(PLAN, runner, tmp_path)
    message = str(caught.value)
    assert "could not be started" in message
    assert platform.DOCKER_CLI_MISSING_HELP in message
    assert "exit 127" not in message
    assert "last words" not in message


def test_a_tool_that_really_exits_127_is_still_the_tool_failing(tmp_path: Path) -> None:
    """Both halves of the sentinel are checked. `docker run` returns the CONTAINER's
    status, so a command missing inside the image genuinely exits 127, and reading
    that as "docker is not installed" would send the user to reinstall Docker."""
    runner = Runner(FULL, fail={"/opt/bin/ad": (127, "exec /opt/bin/ad: no such file")})
    with pytest.raises(InstallerError) as caught:
        run(PLAN, runner, tmp_path)
    message = str(caught.value)
    assert "exit 127" in message and "no such file" in message
    assert "could not be started" not in message


def test_a_cancel_keeps_finished_tools_and_says_so(tmp_path: Path) -> None:
    runner = Runner(FULL)
    runner.cancel_after = 2
    cancel = threading.Event()
    with pytest.raises(InstallerError, match=extract.EXTRACT_CANCEL_NOTE):
        run(PLAN, runner, tmp_path, cancel=cancel)
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and [r.name for r in evidence.tools] == [AD.name]
    again = Runner(FULL)
    run(PLAN, again, tmp_path)
    assert again.names() == ["vmap_extractor", "vmap_assembler"]


def test_a_cancel_between_tools_is_a_stop_and_not_a_success(tmp_path: Path) -> None:
    """Stop pressed while a tool was exiting: it returns 0 and the token is set.

    `run_attached()` reports `CANCELLED_RETURNCODE` only while it is still
    reading; a tool that finished in the same instant comes back 0. Recording
    it and marching on would start the NEXT tool after the user said stop.
    """
    cancel = threading.Event()

    class StopsQuietly(Runner):
        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            result = super().__call__(spec, sink=sink, cancel=cancel)
            if cancel is not None:
                cancel.set()
            return result

    runner = StopsQuietly(FULL)
    with pytest.raises(InstallerError, match=extract.EXTRACT_CANCEL_NOTE):
        run(PLAN, runner, tmp_path, cancel=cancel)
    assert runner.names() == ["ad"]
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and evidence.tools == ()


def test_the_cancel_note_promises_exactly_what_the_per_tool_record_delivers() -> None:
    """The words, pinned once, because the refusal tests cannot pin them.

    A4 has the spine yield this note straight after `--- extract`, so it is the
    user's whole picture of what pressing Stop costs. What it CLAIMS is proved
    by the cancel tests — the resume really does run only the unfinished tools —
    but they match the refusal against the constant itself, which would accept
    any sentence at all, including one that told the user the opposite.
    """
    assert extract.EXTRACT_CANCEL_NOTE == (
        "Finished tools are kept; only the tool that was interrupted runs again."
    )


def test_the_cancel_sentinel_alone_is_a_stop_even_with_no_token(tmp_path: Path) -> None:
    """The run's own answer, not only the caller's token.

    `CANCELLED_RETURNCODE` is negative on purpose — no container can exit with
    it — so it means "stopped" all by itself. Read only through the token, a
    stop would be missed by any caller that passed no token, or one whose token
    is not the object the docker layer was watching, and the tool after it would
    start.
    """
    runner = Runner(FULL)
    runner.cancel_after = 1
    with pytest.raises(InstallerError, match=extract.EXTRACT_CANCEL_NOTE):
        run(PLAN, runner, tmp_path, cancel=None)
    assert runner.names() == ["ad"]
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and evidence.tools == ()


def test_the_evidence_is_rewritten_after_every_tool_and_not_at_the_end(tmp_path: Path) -> None:
    """The cancel note's whole claim, asserted from inside the run.

    Written once at the end instead, a Stop after five hours of `ad` would find
    an evidence file with no records in it and start again from the first tool,
    and `EXTRACT_CANCEL_NOTE` would be a promise the code does not keep. The
    first entry also pins that the stage facts are on disk BEFORE any tool runs.
    """
    data = tmp_path / "server" / "data"
    seen: list[list[str] | None] = []

    class Watching(Runner):
        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            found = extract.read_evidence(data)
            seen.append(None if found is None else [record.name for record in found.tools])
            return super().__call__(spec, sink=sink, cancel=cancel)

    run(PLAN, Watching(FULL), tmp_path)
    assert seen == [[], [AD.name], [AD.name, VMAP.name]]


def test_each_output_folder_is_walked_once_per_tool_and_not_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's numbers and the user's numbers come from the same walk.

    Counted separately for the threshold and for the line, a folder that stops
    listing between the two — the case `file_count()` is written around — makes
    the refusal and the log disagree about the one number both are made of.
    Four walks for this plan: `ad`'s two folders, `vmap`'s one, `assemble`'s one.
    """
    walked: list[Path] = []
    real_count = extract.file_count

    def counting(folder: Path) -> int:
        walked.append(folder)
        return real_count(folder)

    monkeypatch.setattr(extract, "file_count", counting)
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    assert walked == [data / "dbc", data / "maps", data / "Buildings", data / "vmaps"]


# --- the container-level security decision --------------------------------------------------


def test_tool_run_puts_the_security_decision_on_the_run_and_never_a_label_on_a_mount(
    tmp_path: Path,
) -> None:
    """`:z`/`:Z` recursively relabel their mount source, and one of these mounts is the
    user's own game install. The flag is container-level for exactly that reason."""
    spec = extract.tool_run(
        PLAN,
        AD,
        image_ref="yulon.local/x:1",
        client_dir=tmp_path / "client",
        data_dir=tmp_path / "data",
        user_args=(),
        security_args=("--security-opt", "label:disable"),
    )
    assert spec.security_args == ("--security-opt", "label:disable")
    argv = spec.to_argv()
    assert _flag_values(argv, "-v") == [
        f"{tmp_path / 'client'}:/client:ro",
        f"{tmp_path / 'data'}:/out",
    ]
    for value in _flag_values(argv, "-v"):
        assert not value.endswith((":z", ":Z")), value
    assert extract.CLIENT_MOUNT == "/client" and extract.OUT_MOUNT == "/out"


@pytest.mark.parametrize(
    ("enforcing", "labelled"),
    [(True, True), (False, False), (None, False)],
    ids=["enforcing", "not-enforcing", "could-not-ask"],
)
def test_label_disable_is_added_only_when_selinux_is_known_to_be_enforcing(
    tmp_path: Path, enforcing: bool | None, labelled: bool
) -> None:
    """Three answers, not two. Turning a container's confinement off on "could not
    ask" would be a security decision taken on no evidence — the mistake
    `platform.selinux_enforcing()` exists to keep visible."""
    runner = Runner(FULL)
    run(PLAN, runner, tmp_path, enforcing=enforcing)
    assert runner.specs
    for spec in runner.specs:
        assert ("label:disable" in spec.security_args) is labelled
        assert ("label:disable" in _flag_values(spec.to_argv(), "--security-opt")) is labelled


def test_the_selinux_question_is_asked_once_for_the_whole_plan(tmp_path: Path) -> None:
    """One fallible probe, one answer, handed to every tool.

    Asked per tool, three containers could get three different answers out of
    one `getenforce` that flickered — and the tool that heard "not enforcing"
    would be denied the client with nothing to explain it.
    """
    asked: list[int] = []

    def ask() -> bool:
        asked.append(1)
        return True

    runner = Runner(FULL)
    list(
        extract.run_plan(
            PLAN,
            image_ref="yulon.local/x-server:1",
            client_dir=client(tmp_path),
            data_dir=tmp_path / "server" / "data",
            run_container=runner,
            user_args=(),
            sink=lambda _line: None,
            cancel=None,
            required_file=REQUIRED,
            selinux_enforcing=ask,
        )
    )
    assert len(asked) == 1
    assert len(runner.specs) == 3
    assert all("label:disable" in spec.security_args for spec in runner.specs)


def test_the_selinux_seam_defaults_to_the_real_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left unset, the machine is actually asked — and resolved at call time, so a
    monkeypatch on `platform` is seen (a default bound at import would not be)."""
    monkeypatch.setattr(platform, "selinux_enforcing", lambda: True)
    runner = Runner(FULL)
    list(
        extract.run_plan(
            PLAN,
            image_ref="yulon.local/x-server:1",
            client_dir=client(tmp_path),
            data_dir=tmp_path / "server" / "data",
            run_container=runner,
            user_args=(),
            sink=lambda _line: None,
            cancel=None,
        )
    )
    assert runner.specs
    assert all("label:disable" in spec.security_args for spec in runner.specs)


def test_every_extraction_container_gives_up_the_network_and_new_privileges(
    tmp_path: Path,
) -> None:
    """Defence in depth for a tool that parses an untrusted binary client, and half of
    what `container_t` was providing for free until `label:disable` turned it off.

    `--cap-drop ALL` and `--read-only` are deliberately NOT here: they change
    what the process may do to the folder it must write, on the platform this
    suite cannot measure (Docker Desktop, where the tool runs as the image's
    root), and `docker.ContainerRun` records that neither has been measured
    against these tools.
    """
    runner = Runner(FULL)
    run(PLAN, runner, tmp_path, enforcing=True)
    assert runner.specs
    for spec in runner.specs:
        argv = spec.to_argv()
        assert _flag_values(argv, "--network") == ["none"]
        assert "no-new-privileges" in _flag_values(argv, "--security-opt")
        assert "--cap-drop" not in argv and "--read-only" not in argv
        assert argv.index("--network") < argv.index("yulon.local/x-server:1")


def test_the_security_args_are_the_platform_rule_and_not_a_second_spelling_of_it() -> None:
    """One home for "how does a container reach a host folder on an enforcing box"."""
    assert extract.container_security_args(enforcing=True) == (
        *extract.EXTRACT_HARDENING,
        *platform.label_disable_args(enforcing=True),
    )
    assert extract.container_security_args(enforcing=False) == extract.EXTRACT_HARDENING
    assert extract.container_security_args(enforcing=None) == extract.EXTRACT_HARDENING


# --- the retry recipe and the staged-client fallback ------------------------------------------
#
# Two fallbacks that live in the catalog as data, and each has THREE outcomes
# rather than two, because a user who cannot tell them apart is told the wrong
# thing about their own machine.
#
# The retry: no rule matched (a plain failure, and nothing in the log mentions a
# retry at all), the rule matched and the second attempt worked (the log names
# which tools ran again and why), or the rule matched and the second attempt
# failed too (a refusal that says it was ALREADY the retry, so the same crash
# twice does not read as the first one).
#
# The farm: it was not needed, it was built and the tool ran inside it, or it
# could not be built — which is emphatically not "the tool failed", because the
# tool never started. `STAGE_SCRIPT` says so in its own words and with its own
# status, and both halves are demanded before that sentence is used, exactly as
# `docker.cli_missing_run()` demands both halves of its sentinel.

RETRY = RetrySpec(
    when_log_matches="Segmentation fault|core dumped", tools=(VMAP.name, ASSEMBLE.name)
)
RETRY_PLAN = ExtractPlan(
    image="server", tools=(AD, VMAP, ASSEMBLE), ulimit_stack_unlimited=True, retry=RETRY
)
STAGED_PLAN = ExtractPlan(image="server", tools=(VMAP,), stage_client=True)
SEGFAULT = (139, "Segmentation fault (core dumped)")


def test_a_matching_crash_re_runs_the_named_tools_once_and_continues(tmp_path: Path) -> None:
    """Outcome two: the recipe matched, the second attempt worked, and the log says so.

    The whole transcript, in order, rather than four `any()`s over it. A retry
    that ran the right containers and then said the wrong thing about them
    satisfies every loose assertion here — including the one that only asks
    whether the word "retry" appears somewhere — and the transcript IS the
    user's only account of why a tool ran twice. The last line is load-bearing
    too: the outer loop reaches `vmap assemble` after the recipe already ran it,
    and finds a record rather than running it a third time.
    """
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    said = run(RETRY_PLAN, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor", "vmap_extractor", "vmap_assembler"]
    assert said == [
        f"{AD.name}: running /opt/bin/ad -i /client -o /out",
        f"{AD.name}: done (dbc: 3 files, maps: 2 files)",
        f"{VMAP.name}: running /opt/bin/vmap_extractor -d /client/Data",
        f"{VMAP.name} crashed the way the retry recipe expects; "
        f"running {VMAP.name}, {ASSEMBLE.name} again once",
        f"{VMAP.name}: emptying Buildings before the retry, so it regenerates what the crashed "
        "attempt left rather than adding to it",
        f"{VMAP.name}: retrying /opt/bin/vmap_extractor -d /client/Data",
        f"{VMAP.name}: done (Buildings: 2 files)",
        f"{ASSEMBLE.name}: emptying vmaps before the retry, so it regenerates what the crashed "
        "attempt left rather than adding to it",
        f"{ASSEMBLE.name}: retrying /opt/bin/vmap_assembler Buildings vmaps",
        f"{ASSEMBLE.name}: done (vmaps: 2 files)",
        f"{ASSEMBLE.name}: already extracted (vmaps: 2 files)",
    ]
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and len(evidence.tools) == 3


def test_a_crash_that_does_not_match_the_recipe_is_a_plain_failure(tmp_path: Path) -> None:
    """Outcome one: no rule matched, so nothing is re-run and nothing is said about a retry."""
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": (1, "no such archive")})
    said: list[str] = []
    with pytest.raises(InstallerError, match="no such archive") as caught:
        said.extend(run(RETRY_PLAN, runner, tmp_path))
    assert runner.names() == ["ad", "vmap_extractor"]
    assert not any("retry" in line.lower() for line in said)
    assert "retry" not in str(caught.value).lower()


def test_a_second_matching_crash_is_a_failure_not_a_loop_and_says_it_was_the_retry(
    tmp_path: Path,
) -> None:
    """Outcome three, and the sentence that keeps it apart from outcome one.

    "vmap extract failed (exit 139)" is true of the first crash and of the
    second, and a user reading it after the retry silently ran would go looking
    for a first attempt the message says nothing about. The refusal names the
    retry, and the tool runs exactly twice — the recipe is one more attempt, not
    a loop.
    """

    class AlwaysCrash(Runner):
        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            self.specs.append(spec)
            out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
            if tool_program(spec) == "/opt/bin/ad":
                fill(out, "dbc", 3)
                fill(out, "maps", 2)
                return docker.AttachedRun(0, ())
            return docker.AttachedRun(139, ("core dumped",))

    runner = AlwaysCrash(FULL)
    with pytest.raises(InstallerError, match="core dumped") as caught:
        run(RETRY_PLAN, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor", "vmap_extractor"]
    message = str(caught.value)
    assert "exit 139" in message
    assert "already the one retry" in message


def test_a_first_failure_is_never_told_as_though_it_had_been_retried(tmp_path: Path) -> None:
    """The other side of the same sentence: outcome one must not borrow outcome three's words."""
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": (1, "no such archive")})
    with pytest.raises(InstallerError) as caught:
        run(RETRY_PLAN, runner, tmp_path)
    assert "already the one retry" not in str(caught.value)


@pytest.mark.parametrize(
    ("returncode", "with_token"),
    [(docker.CANCELLED_RETURNCODE, False), (139, True)],
    ids=["the-sentinel", "the-token"],
)
def test_a_stop_whose_last_words_match_the_recipe_is_a_stop_and_not_a_retry(
    tmp_path: Path, returncode: int, with_token: bool
) -> None:
    """A stopped tool's last lines can say anything, including the recipe's own words.

    Both shapes of a stop are tried with a tail the recipe matches: the docker
    layer's sentinel, and a tool that exited while the token was being set. Read
    only as "a non-zero exit whose log matches", either one starts a fresh
    container after the user pressed Stop — the one thing the cancel path exists
    to prevent — and `runner.names()` is what tells the two apart, because the
    refusal is the same sentence either way.
    """
    token = threading.Event() if with_token else None

    class CrashesOnTheWayOut(Runner):
        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            self.specs.append(spec)
            out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
            if tool_program(spec) == "/opt/bin/ad":
                fill(out, "dbc", 3)
                fill(out, "maps", 2)
                return docker.AttachedRun(0, ())
            if cancel is not None:
                cancel.set()
            return docker.AttachedRun(returncode, ("Segmentation fault (core dumped)",))

    runner = CrashesOnTheWayOut(FULL)
    with pytest.raises(InstallerError, match=extract.EXTRACT_CANCEL_NOTE):
        run(RETRY_PLAN, runner, tmp_path, cancel=token)
    assert runner.names() == ["ad", "vmap_extractor"]


def test_a_container_that_never_started_is_not_retried(tmp_path: Path) -> None:
    """Running a missing docker CLI a second time is not a recipe for anything."""
    missing = RetrySpec(when_log_matches=".", tools=(VMAP.name,))
    plan = ExtractPlan(image="server", tools=(AD, VMAP), retry=missing)
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": (127, platform.DOCKER_CLI_MISSING_HELP)})
    with pytest.raises(InstallerError, match="could not be started"):
        run(plan, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor"]


def test_a_recipe_that_does_not_name_the_tool_that_failed_does_not_apply_to_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A matching log is not enough: the recipe has to be able to cover the failure.

    Re-running only OTHER tools and then stepping past the crash leaves the tool
    that failed with no record and no refusal, every later tool satisfied, and a
    stage that ends by saying it worked — the extraction reported as finished by
    the one thing that did not finish. Nothing is re-run, the tool's own failure
    stands, and the mismatch is logged because it is a bug in our catalog.
    """
    elsewhere = RetrySpec(when_log_matches="core dumped", tools=(ASSEMBLE.name,))
    plan = ExtractPlan(image="server", tools=(AD, VMAP, ASSEMBLE), retry=elsewhere)
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    with caplog.at_level("WARNING"):
        with pytest.raises(InstallerError, match="core dumped") as caught:
            run(plan, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor"]
    assert "already the one retry" not in str(caught.value)
    assert any(ASSEMBLE.name in record.message for record in caplog.records)
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and [record.name for record in evidence.tools] == [AD.name]


def test_a_recipe_whose_pattern_is_not_a_regex_is_a_plain_failure_and_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A catalog bug must not reach the user as a `re.error` out of a stage of yields.

    The safe answer is "no retry": what the user then reads is the tool's own
    failure, which is true, instead of a traceback about a bracket.
    """
    broken = RetrySpec(when_log_matches="core dumped[", tools=(VMAP.name,))
    plan = ExtractPlan(image="server", tools=(AD, VMAP), retry=broken)
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    with caplog.at_level("WARNING"):
        with pytest.raises(InstallerError, match="core dumped"):
            run(plan, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor"]
    assert any("core dumped[" in record.message for record in caplog.records)


def test_retry_matches_answers_every_ending_and_not_just_the_regex() -> None:
    """The decision as a table, so no single ending can answer for another."""
    matching = docker.AttachedRun(139, ("Segmentation fault (core dumped)",))
    assert extract._retry_matches(RETRY, matching, None) is True
    assert extract._retry_matches(RETRY, docker.AttachedRun(0, ("core dumped",)), None) is False
    assert extract._retry_matches(RETRY, docker.AttachedRun(1, ("nope",)), None) is False
    cancelled = docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("core dumped",))
    assert extract._retry_matches(RETRY, cancelled, None) is False
    stopped = threading.Event()
    stopped.set()
    assert extract._retry_matches(RETRY, matching, stopped) is False
    assert extract._retry_matches(RETRY, matching, threading.Event()) is True
    never_started = docker.AttachedRun(127, (platform.DOCKER_CLI_MISSING_HELP,))
    anything = RetrySpec(when_log_matches=".", tools=(VMAP.name,))
    assert extract._retry_matches(anything, never_started, None) is False


def test_the_retried_containers_are_built_the_same_way_as_the_first_attempt(
    tmp_path: Path,
) -> None:
    """The retry is another `tool_run`, not a second spelling of one.

    The SELinux answer in particular: a retried container that lost
    `label:disable` would be denied the client on an enforcing box, and the
    "retry" in the log would be the last thing the user saw work.
    """
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    run(RETRY_PLAN, runner, tmp_path, enforcing=True)
    first, retried = runner.specs[1], runner.specs[2]
    assert tool_program(first) == tool_program(retried) == "/opt/bin/vmap_extractor"
    assert retried.security_args == first.security_args
    assert "label:disable" in retried.security_args
    assert retried.user_args == ("--user", "1000:1000")
    assert retried.ulimits == ("stack=-1",)
    assert retried.argv == first.argv and retried.mounts == first.mounts


def test_the_retry_walks_each_output_folder_once_and_never_the_crashed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One walk per attempt that finished, and none for the attempt that crashed.

    The crashed run is refused before any counting — counting it would put a
    number in the log that describes half a tool's work — and the retried run's
    numbers come from the same single walk the gate used.
    """
    walked: list[Path] = []
    real_count = extract.file_count

    def counting(folder: Path) -> int:
        walked.append(folder)
        return real_count(folder)

    monkeypatch.setattr(extract, "file_count", counting)
    run(RETRY_PLAN, Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT}), tmp_path)
    data = tmp_path / "server" / "data"
    assert walked[:4] == [data / "dbc", data / "maps", data / "Buildings", data / "vmaps"]
    assert walked.count(data / "Buildings") == 1


# --- what the retry removes, and what it must not -----------------------------------------
#
# The 7.5 gate on m910q, 2026-09-04, forced the crash the recipe is written for
# and proved the recipe reachable; the retry then died on its first breath with
# `Your output directory seems to be polluted, please use an empty directory!`
# and `data/Buildings` holding the crashed attempt's 5,076 files
# (`pyplan/gates/7.5-m910q/vmap75-full.log`). So the retry has to empty what it
# is about to regenerate — and that is a deletion of somebody's extracted data on
# a path that fires with no question, which is why three of the four tests below
# are about what is NOT removed.


def _files_under(data_dir: Path) -> set[str]:
    """Every file under `data/`, by posix-relative name — one snapshot of the folder."""
    return {path.relative_to(data_dir).as_posix() for path in data_dir.rglob("*") if path.is_file()}


def _emptying(tool: ExtractTool) -> str:
    """The line the engine yields before it removes anything, spelled once for four tests."""
    return (
        f"{tool.name}: emptying {', '.join(tool.produces)} before the retry, so it regenerates "
        "what the crashed attempt left rather than adding to it"
    )


def _stale(data_dir: Path, folder: str, name: str) -> None:
    """A file no tool in these tests ever writes, so its survival answers "was this cleared?"."""
    (data_dir / folder).mkdir(parents=True, exist_ok=True)
    (data_dir / folder / name).write_bytes(b"x")


class Watching(Runner):
    """A `Runner` that snapshots `data/` as each container starts, before that container writes.

    What `data/` held at the MOMENT a tool re-ran is the only thing that tells
    "emptied just before the tool that regenerates it" apart from "emptied the
    whole recipe's output up front". Both end with the same folders on disk, and
    only the first leaves a folder alone when the pass dies before reaching the
    tool that would rewrite it.
    """

    def __init__(
        self,
        writes: Mapping[str, Mapping[str, int]],
        *,
        fail: Mapping[str, tuple[int, str]] | None = None,
    ) -> None:
        super().__init__(writes, fail=fail)
        self.before: list[set[str]] = []

    def __call__(
        self, spec: docker.ContainerRun, *, sink: docker.OutputSink, cancel: threading.Event | None
    ) -> docker.AttachedRun:
        out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
        self.before.append(_files_under(out))
        return super().__call__(spec, sink=sink, cancel=cancel)


def test_the_retry_empties_the_folder_it_is_about_to_regenerate_and_says_so_first(
    tmp_path: Path,
) -> None:
    """The defect the 7.5 gate found: the recipe fired and then could not possibly succeed.

    `stale.wmo` stands for the 5,076 files that were actually there — the tool
    asks for an empty directory, not a complete one, so one file is the same
    refusal as five thousand.

    The order of the two log lines is asserted rather than their presence. The
    sentence is the only warning a user gets that a folder of theirs is about to
    go, and a warning printed after the removal is a receipt.
    """
    data = tmp_path / "server" / "data"
    _stale(data, "Buildings", "stale.wmo")
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    said = run(RETRY_PLAN, runner, tmp_path)
    assert "Buildings/stale.wmo" not in _files_under(data)
    assert said.index(_emptying(VMAP)) < said.index(
        f"{VMAP.name}: retrying /opt/bin/vmap_extractor -d /client/Data"
    )


def test_a_first_run_empties_nothing_and_never_says_it_did(tmp_path: Path) -> None:
    """Only the retry path removes. A plan that HAS a recipe, and no crash to fire it.

    The plan is `RETRY_PLAN` on purpose: "there is no recipe" would pass this
    test with a clear that ran on every install. What must be left alone is
    `data/` as the user handed it over — an extraction copied from another
    machine, or one this installer wrote before somebody edited the catalog —
    and no crash means no licence to touch it.
    """
    data = tmp_path / "server" / "data"
    _stale(data, "Buildings", "stale.wmo")
    said = run(RETRY_PLAN, Runner(FULL), tmp_path)
    assert "Buildings/stale.wmo" in _files_under(data)
    assert not any("emptying" in line for line in said)


def test_when_the_assembler_crashed_the_extractor_is_left_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """The case that decides the shape: the crash is in the tool that CONSUMES the other's output.

    `wow-vanilla`'s recipe names `vmap extract` and `vmap assemble`, and the
    assembler reads `Buildings` and writes `vmaps`. So a pass that simply "runs
    the named tools again" reaches the EXTRACTOR first, and would empty a
    `Buildings` that is complete and correct and spend half an hour rebuilding
    it byte for byte.

    It is not a free trade, and an earlier draft of this test said it was. That
    draft argued the recipe re-ran the extractor anyway so the removal cost
    nothing extra -- which is false, and the run on m910q is what shows it: the
    re-run did not spend half an hour, it died in seconds on
    `Your output directory seems to be polluted`. Before any of this existed the
    assembler case failed FAST and healed on the next launch, because a fresh
    press finds `Buildings` satisfied and skips straight to the assembler.

    So the retry pass skips a tool the evidence already vouches for. That keeps
    the fast path fast and still empties what a genuine crash left, because the
    two are told apart by a RECORD: `_conclude()` writes one, and the retry
    branch is taken before it -- so a crashed tool never has one and is always
    re-run, while a tool that finished earlier in the outer loop does.
    """
    data = tmp_path / "server" / "data"
    _stale(data, "vmaps", "stale.vmtree")
    runner = Watching(FULL, fail={"/opt/bin/vmap_assembler": SEGFAULT})
    said = run(RETRY_PLAN, runner, tmp_path)
    # Four runs, not five: the extractor is not re-run at all.
    assert runner.names() == ["ad", "vmap_extractor", "vmap_assembler", "vmap_assembler"]
    # What the extractor wrote is still there, and was never emptied.
    assert "Buildings/f0" in runner.before[3]
    # Only the assembler's own output went, and only as the assembler re-ran.
    assert "vmaps/stale.vmtree" not in runner.before[3]
    assert _files_under(data) >= {"Buildings/f0", "vmaps/f0"}
    assert _emptying(ASSEMBLE) in said
    assert _emptying(VMAP) not in said
    left_alone = [line for line in said if "leaving it alone" in line]
    assert len(left_alone) == 1 and left_alone[0].startswith(VMAP.name)


def test_a_folder_that_will_not_empty_refuses_and_the_tool_is_never_re_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two answers kept apart: "the removal did not happen" is not "there was nothing there".

    `_remove_tree()` keeps them apart already; this is the caller honouring it.
    Carrying on would run the tool over exactly what the removal exists to take
    away, which is the gate's own failure with an extra hour spent reaching it —
    and the install would end on the tool's sentence rather than on the folder's.
    """

    def refuse(path: Path) -> bool:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(extract, "_remove_tree", refuse)
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    with pytest.raises(InstallerError, match="could not be emptied") as caught:
        run(RETRY_PLAN, runner, tmp_path)
    assert "Nothing was re-run" in str(caught.value)
    assert runner.names() == ["ad", "vmap_extractor"]


def _stopped_on_the_first_tool() -> Runner:
    """Ending 1, and only ending 1: the cancel sentinel, with no configured failure."""
    runner = Runner(FULL)
    runner.cancel_after = 1
    return runner


def _first_tool_never_started() -> Runner:
    """Ending 2: both halves of `cli_missing_run()`'s sentinel and nothing else."""
    return Runner(FULL, fail={"/opt/bin/ad": (127, platform.DOCKER_CLI_MISSING_HELP)})


def _the_farm_could_not_be_laid() -> Runner:
    """Ending 3: both halves of the staging sentinel, on the one plan that stages."""
    return Runner(
        FULL,
        fail={
            "/opt/bin/vmap_extractor": (
                extract.STAGE_FAILED_RETURNCODE,
                extract.STAGE_FAILED_MARKER,
            )
        },
    )


def _first_tool_crashed_plainly() -> Runner:
    """Ending 4: an ordinary non-zero status with ordinary last words, on a plan with
    no retry recipe — so it is refused where it stands rather than run again."""
    return Runner(FULL, fail={"/opt/bin/ad": SEGFAULT})


@pytest.mark.parametrize(
    ("plan", "make_runner", "says"),
    [
        pytest.param(PLAN, _stopped_on_the_first_tool, "was stopped", id="stopped"),
        pytest.param(PLAN, _first_tool_never_started, "could not be started", id="never-started"),
        pytest.param(STAGED_PLAN, _the_farm_could_not_be_laid, "never ran", id="farm-not-laid"),
        pytest.param(PLAN, _first_tool_crashed_plainly, "failed (exit 139)", id="failed"),
    ],
)
def test_none_of_the_four_refusing_endings_walks_an_output_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: ExtractPlan,
    make_runner: Callable[[], Runner],
    says: str,
) -> None:
    """`_conclude` says only the fifth ending counts the folders. Four say it here.

    The two "walked once" tests above only ever exercise a tool that succeeded
    and a crash the retry recipe rescues, so the plain crash — ending 4, which
    reaches `_conclude` and raises from it — had nothing asserting it counts
    nothing. Moving `counts()` above `if run.returncode != 0` survived the whole
    file. The cost is only wasted I/O over a half-written `data/` on a run that
    is already refused, but the invariant is claimed in the docstring, and a
    claimed invariant with no test is the one that quietly stops being true.

    Three of these four also satisfy `returncode != 0`, so "it raised" would not
    say which rule caught the run; each case asserts the sentence its own ending
    produces, and each fixture trips exactly one — the cancel sentinel with no
    configured failure, both halves of the CLI-missing sentinel, both halves of
    the staging sentinel on the only plan that stages, and a 139 that is none of
    them. Every case fails on the FIRST tool of its plan, so an empty `walked`
    means nothing was walked at all rather than nothing since the last success.
    """
    walked: list[Path] = []
    real_count = extract.file_count

    def counting(folder: Path) -> int:
        walked.append(folder)
        return real_count(folder)

    monkeypatch.setattr(extract, "file_count", counting)
    with pytest.raises(InstallerError) as caught:
        run(plan, make_runner(), tmp_path)
    assert says in str(caught.value)
    assert walked == []


def test_a_recipe_naming_a_tool_the_plan_does_not_have_is_said_not_raised_blank() -> None:
    """The model validator makes this unreachable from `catalog.json`; a plan built in
    code can still name a stranger, and a sentence beats a `StopIteration`."""
    assert extract._tool_named(PLAN, VMAP.name) is VMAP
    with pytest.raises(InstallerError, match="not a tool of this plan"):
        extract._tool_named(PLAN, "no such tool")


def test_a_plan_with_no_recipe_runs_no_retry_at_all(tmp_path: Path) -> None:
    """The default is nothing: `retry=None` and a crash that would have matched."""
    assert PLAN.retry is None
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": SEGFAULT})
    with pytest.raises(InstallerError, match="core dumped"):
        run(PLAN, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor"]


# --- the staged client: a symlink farm in the container's own writable layer -------------------


def test_stage_client_wraps_the_tool_in_a_symlink_farm_and_never_mounts_the_client_writable(
    tmp_path: Path,
) -> None:
    runner = Runner(FULL)
    run(STAGED_PLAN, runner, tmp_path)
    (spec,) = runner.specs
    assert spec.workdir == extract.STAGE_MOUNT
    assert spec.argv[:2] == ("sh", "-c")
    assert "cp -rs /client/. /work" in spec.argv[2]
    assert spec.argv[3:] == ("sh", "/opt/bin/vmap_extractor", "-d", "/work/Data")
    assert spec.env == {"YULON_OUT_DIRS": "Buildings"}
    by_guest = {mount.guest: mount for mount in spec.mounts}
    assert by_guest["/client"].read_only is True
    assert set(by_guest) == {"/client", "/out"}


def test_a_plan_that_does_not_stage_the_client_is_left_exactly_as_it_was(tmp_path: Path) -> None:
    """The fallback that was not needed: no wrapper, no environment, cwd still `/out`."""
    assert PLAN.stage_client is False
    spec = extract.tool_run(
        PLAN,
        VMAP,
        image_ref="yulon.local/x:1",
        client_dir=tmp_path / "client",
        data_dir=tmp_path / "data",
        user_args=(),
    )
    assert spec.argv == VMAP.argv
    assert spec.workdir == extract.OUT_MOUNT
    assert spec.env == {}


def test_the_staged_farm_is_not_a_third_mount_and_keeps_the_container_wide_label_decision(
    tmp_path: Path,
) -> None:
    """A third bind would be a third place to get the SELinux answer right; `--rm` takes
    the container's own layer with it, so the farm needs neither a mount nor a label."""
    runner = Runner(FULL)
    run(STAGED_PLAN, runner, tmp_path, enforcing=True)
    (spec,) = runner.specs
    assert [mount.guest for mount in spec.mounts] == ["/client", "/out"]
    assert "label:disable" in spec.security_args
    argv = spec.to_argv()
    assert _flag_values(argv, "-w") == ["/work"]
    for value in _flag_values(argv, "-v"):
        assert not value.endswith((":z", ":Z")), value
    assert _flag_values(argv, "-e") == ["YULON_OUT_DIRS=Buildings"]


def test_only_client_paths_are_rewritten_into_the_farm() -> None:
    """`/client` and things under it move; a longer name that merely starts the same does not."""
    assert extract._staged_argv(("/opt/bin/x", "/client", "/client/Data", "-o", "/out")) == (
        "/opt/bin/x",
        "/work",
        "/work/Data",
        "-o",
        "/out",
    )
    assert extract._staged_argv(("/clientele", "client", "x/client")) == (
        "/clientele",
        "client",
        "x/client",
    )


def test_the_stage_script_names_the_same_mount_points_the_module_does() -> None:
    """One home per path: a script that hard-coded a fourth spelling would drift silently."""
    assert extract.STAGE_MOUNT == "/work"
    assert f"cp -rs {extract.CLIENT_MOUNT}/. {extract.STAGE_MOUNT}" in extract.STAGE_SCRIPT
    assert f"cd {extract.STAGE_MOUNT} ||" in extract.STAGE_SCRIPT
    assert f'"{extract.OUT_MOUNT}/$name/"' in extract.STAGE_SCRIPT
    assert extract.STAGE_FAILED_MARKER in extract.STAGE_SCRIPT
    assert f"exit {extract.STAGE_FAILED_RETURNCODE}" in extract.STAGE_SCRIPT


def test_the_stage_script_copies_out_by_content_into_a_folder_it_makes_itself() -> None:
    """Not because the plan's `cp -r "$name" /out/` nests — re-measured, it does not.

    That was this test's original reason and it was wrong: run twice against one
    persistent `/out` on `debian:stable-slim` and `alpine:3.20` (2026-09-01),
    the plan's spelling merged flat into `/out/Buildings/` on both. The reason
    that survived is the one this assertion is really about — `mkdir -p
    "/out/$name"` plus a copy by content is the only spelling that puts a
    `produces` name with a slash in it where `counts()` looks. `Cameras/
    Buildings` lands at `/out/Buildings` under the plan's form, which the count
    gate reads as nothing produced; here it lands at `/out/Cameras/Buildings`.
    Nothing in `ExtractTool` forbids such a name.

    The negative half stays for the same reason, not the retired one, and is
    a single-rule fixture: the plan's spelling is the one that mislays a
    slashed name.
    """
    assert 'cp -r "$name/." "/out/$name/"' in extract.STAGE_SCRIPT
    assert 'mkdir -p "/out/$name"' in extract.STAGE_SCRIPT
    assert 'cp -r "$name" /out/' not in extract.STAGE_SCRIPT


def test_the_stage_script_exits_with_the_tools_status_and_not_the_copy_loops() -> None:
    """The wrapper must not launder the tool's exit status.

    `$?` is saved before the copy-back and restored at the end. Left to fall out
    of the loop, the container's status would be the last `cp`'s — or the last
    `[ -e ]`'s, which is 1 for a tool that produced nothing — and `_conclude`
    would read a crashed extraction as a success, or a clean one as a failure,
    depending on which folder happened to exist. Measured against a real daemon
    too: a tool exiting 3 inside the farm gives a container that exits 3, with
    its output still copied out.
    """
    assert "status=$?; " in extract.STAGE_SCRIPT
    assert extract.STAGE_SCRIPT.endswith("exit $status")


def test_a_staged_farm_that_could_not_be_built_is_not_the_tool_failing(tmp_path: Path) -> None:
    """The third outcome of the fallback, and it is not one of the tool's four.

    "vmap extract failed (exit 91)" is a sentence about a tool that ran; the
    script gave up before `"$@"` and the tool never started, so the refusal says
    that instead — and no client was read either way.
    """
    gave_up = (extract.STAGE_FAILED_RETURNCODE, extract.STAGE_FAILED_MARKER)
    runner = Runner(FULL, fail={"/opt/bin/vmap_extractor": gave_up})
    with pytest.raises(InstallerError) as caught:
        run(STAGED_PLAN, runner, tmp_path)
    message = str(caught.value)
    assert "never ran" in message
    assert extract.STAGE_FAILED_MARKER in message
    assert "exit 91" not in message
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and evidence.tools == ()


def test_a_tool_that_really_exits_ninety_one_is_still_the_tool_failing(tmp_path: Path) -> None:
    """Both halves of the marker, the way `cli_missing_run()` demands both of its own.

    91 is an ordinary status a C++ extractor may exit with, and the same words
    could appear in a tool's log; neither alone means the farm failed, and a plan
    that stages nothing has no farm to fail in the first place — so even both
    halves together are not enough without `stage_client`.
    """
    gave_up = (extract.STAGE_FAILED_RETURNCODE, extract.STAGE_FAILED_MARKER)
    runner = Runner(FULL, fail={"/opt/bin/ad": gave_up})
    with pytest.raises(InstallerError, match="exit 91") as unstaged:
        run(PLAN, runner, tmp_path)
    assert "never ran" not in str(unstaged.value)
    words_only = Runner(FULL, fail={"/opt/bin/vmap_extractor": (1, extract.STAGE_FAILED_MARKER)})
    with pytest.raises(InstallerError, match="exit 1") as caught:
        run(STAGED_PLAN, words_only, tmp_path)
    assert "never ran" not in str(caught.value)
    status_only = Runner(
        FULL, fail={"/opt/bin/vmap_extractor": (extract.STAGE_FAILED_RETURNCODE, "aborting")}
    )
    with pytest.raises(InstallerError, match="exit 91") as status_caught:
        run(STAGED_PLAN, status_only, tmp_path)
    assert "never ran" not in str(status_caught.value)


def test_an_output_folder_named_with_whitespace_is_refused_rather_than_silently_lost(
    tmp_path: Path,
) -> None:
    """`for name in $YULON_OUT_DIRS` splits on whitespace: two halves, neither of which
    exists, nothing copied, and a count gate that then blames the user's client."""
    spaced = ExtractTool(name="odd", argv=("/opt/bin/x",), produces={"two words": 1})
    plan = ExtractPlan(image="server", tools=(spaced,), stage_client=True)
    with pytest.raises(InstallerError, match="whitespace"):
        extract.tool_run(
            plan,
            spaced,
            image_ref="yulon.local/x:1",
            client_dir=tmp_path / "client",
            data_dir=tmp_path / "data",
            user_args=(),
        )
    extract.tool_run(
        ExtractPlan(image="server", tools=(spaced,)),
        spaced,
        image_ref="yulon.local/x:1",
        client_dir=tmp_path / "client",
        data_dir=tmp_path / "data",
        user_args=(),
    )


# --- the mmaps stage: one tool, and the only folder this installer deletes ---------------------
#
# Everything above re-runs a tool that did not finish. This stage REMOVES what
# is there first, because MoveMapGen skips every map it already finds output
# for, so generating over a folder left behind by an interrupted run would
# produce a permanently partial set that passes every later gate.
#
# That makes three questions worth asking of every test here, and none of them
# is asked of `run_plan`:
#
#   * WHAT is removed — the answer has to be a folder this app made, under the
#     `data/` it owns, and it has to stay that whatever a catalog says;
#   * WHETHER the removal is followed by a run — a wipe and then a cancel, a
#     missing docker CLI or a crash leaves the user with less than they had,
#     and every one of those endings has to say so;
#   * WHICH part of the skip rule can actually refuse. `run_mmaps` hands
#     `satisfied()` the SAME evidence on both sides, so `same_stage()`'s four
#     fields agree by construction; the parts that can disagree are the
#     `client_facts_complete` veto, the record for this argv, and the counts.

MMAPS = MmapPlan(
    argv=("/opt/bin/MoveMapGen", "--silent", "--threads", "2"), min_files=3, required=True
)
MMAPS_WRITES = {"/opt/bin/MoveMapGen": {"mmaps": 3}}


def mmaps(
    plan: MmapPlan, runner: Runner, tmp_path: Path, *, cancel: threading.Event | None = None
) -> list[str]:
    return list(
        extract.run_mmaps(
            plan,
            image_ref="yulon.local/x-server:1",
            data_dir=tmp_path / "server" / "data",
            run_container=runner,
            user_args=("--user", "1000:1000"),
            sink=lambda _line: None,
            cancel=cancel,
        )
    )


def test_mmaps_runs_in_data_only_with_the_plan_argv_and_records_itself(tmp_path: Path) -> None:
    run(PLAN, Runner(FULL), tmp_path)
    runner = Runner(MMAPS_WRITES)
    mmaps(MMAPS, runner, tmp_path)
    (spec,) = runner.specs
    assert spec.argv == MMAPS.argv
    assert spec.workdir == "/out"
    assert [mount.guest for mount in spec.mounts] == ["/out"]
    assert spec.user_args == ("--user", "1000:1000")
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert evidence is not None and evidence.record_for(extract.MMAPS_TOOL) is not None
    again = Runner(MMAPS_WRITES)
    assert any("already" in line for line in mmaps(MMAPS, again, tmp_path))
    assert again.specs == []


def test_mmaps_without_a_record_wipes_the_partial_folder_and_regenerates(tmp_path: Path) -> None:
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    fill(data, "mmaps", 3)
    (data / "mmaps" / "stale").write_bytes(b"x")
    runner = Runner(MMAPS_WRITES)
    mmaps(MMAPS, runner, tmp_path)
    assert len(runner.specs) == 1
    assert not (data / "mmaps" / "stale").exists()


def test_mmaps_shortfall_refuses_when_required_and_warns_when_not(tmp_path: Path) -> None:
    run(PLAN, Runner(FULL), tmp_path)
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}}), tmp_path)
    assert "mmaps holds 1 files where at least 3 were expected" in str(caught.value)
    assert "Creatures would not move properly, so nothing was recorded" in str(caught.value)
    refused = extract.read_evidence(tmp_path / "server" / "data")
    assert refused is not None and refused.record_for(extract.MMAPS_TOOL) is None
    optional = MmapPlan(argv=MMAPS.argv, min_files=3, required=False)
    said = mmaps(optional, Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}}), tmp_path)
    assert any("warning" in line for line in said)
    again = Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}})
    mmaps(optional, again, tmp_path)
    assert again.specs == [], "an optional shortfall is recorded, not regenerated on every resume"


def test_the_exit_status_that_means_finished_comes_from_the_plan_in_both_directions(
    tmp_path: Path,
) -> None:
    """Zero is a convention, not a promise, and MoveMapGen breaks it.

    The Tortoise fork's `main()` ends `return silent ? 1 : finish("Movemap
    build is complete!", 1)` (tools/mmap/src/generator.cpp:352, read on the box
    that built it), so a Tortoise build that wrote every map exits 1. CMaNGOS
    mangos-classic ends `return 0`. The version of this stage that hard-coded
    `!= 0` threw away a finished 2.5 GB Tortoise run -- 58 maps, 2075 tiles,
    about four hours of CPU -- and printed "map generation failed" while
    quoting the tool's own last line saying it had just written a file
    (yulon-ubuntu, 2026-09-03).

    Both directions are asserted, and the second is the one that carries the
    weight. A stage that accepted `0 or plan.success_codes` would pass the
    first half and be wrong in exactly the way that matters: it would still be
    reading its own opinion rather than the catalog's fact. So the second half
    hands a plan that declares ONLY 1 a generator that exited 0 -- which for
    that tree means it stopped somewhere in argument handling -- and requires a
    refusal. There is no arrangement of a hard-coded zero that passes both.
    """
    run(PLAN, Runner(FULL), tmp_path)
    finished = MmapPlan(argv=MMAPS.argv, min_files=3, required=True, success_codes=(1,))
    runner = Runner(MMAPS_WRITES, fail={"/opt/bin/MoveMapGen": (1, "Movemap build is complete!")})
    said = mmaps(finished, runner, tmp_path)
    assert any("mmaps: done" in line for line in said), said
    evidence = extract.read_evidence(tmp_path / "server" / "data")
    assert (
        evidence is not None and evidence.record_for(extract.MMAPS_TOOL) is not None
    ), "a generator that reported the status its own source calls success was not recorded"

    other = tmp_path / "zero"
    run(PLAN, Runner(FULL), other)
    with pytest.raises(InstallerError) as caught:
        mmaps(finished, Runner(MMAPS_WRITES), other)
    assert "exit 0" in str(caught.value)
    assert "reports 1 when it finishes" in str(caught.value), str(caught.value)
    assert extract.read_evidence(other / "server" / "data").record_for(extract.MMAPS_TOOL) is None


def test_the_shipped_catalog_says_which_status_each_generator_reports() -> None:
    """The declaration is worth nothing unless the shipped entries carry the value.

    Asserting that `MmapPlan` HAS a `success_codes` field would pass while every
    entry defaulted to zero and Tortoise stayed broken. So the three entries are
    read out of the real catalog and their values pinned to what their own
    upstream source returns -- checked by reading the C, not by watching a run
    and writing down whatever it happened to print.
    """
    plans = {
        game.id: game.install.native.cmangos.mmaps
        for game in load_catalog().games
        if getattr(getattr(game.install, "native", None), "cmangos", None) is not None
    }
    assert plans["wow-tortoise"].success_codes == (1,), (
        "the Tortoise generator returns 1 when it finishes; a catalog that says 0 throws the "
        "whole build away"
    )
    assert plans["wow-tbc"].success_codes == (0,)
    assert plans["wow-vanilla"].success_codes == (0,)


def test_a_sentinel_can_never_be_declared_a_success() -> None:
    """`success_codes` is exit statuses only, because two negatives already mean something.

    `docker.CANCELLED_RETURNCODE` is -1 and a signal death is spelled -N. If an
    entry could name either, a Stop would be read as a finished build, the stage
    would be recorded, and every later resume would skip it -- the permanently
    partial set this whole stage is arranged around.
    """
    for bad in (-1, -9, 256):
        with pytest.raises(ValidationError):
            MmapPlan(argv=MMAPS.argv, min_files=3, success_codes=(bad,))
    with pytest.raises(ValidationError):
        MmapPlan(argv=MMAPS.argv, min_files=3, success_codes=())


def test_mmaps_before_extraction_is_refused(tmp_path: Path) -> None:
    """A12: no evidence file means no maps to read, and the tool is not started to find out.

    Asserted on the refusal's own words rather than on `match="extract"`, which
    is the loose assertion a neighbour answers for: with the check deleted the
    run reaches the shortfall refusal instead, whose "Check the extracted maps
    and vmaps" contains "extract" and passes a test that was meant to prove the
    stage never ran. `runner.specs` is the other half — a container that was
    started has already been given the `data/` mount.
    """
    runner = Runner({})
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, runner, tmp_path)
    assert "holds no extraction evidence" in str(caught.value)
    assert "The extract stage has to finish first." in str(caught.value)
    assert runner.specs == []


def test_mmaps_cancel_names_its_own_note(tmp_path: Path) -> None:
    run(PLAN, Runner(FULL), tmp_path)
    runner = Runner(MMAPS_WRITES)
    runner.cancel_after = 1
    with pytest.raises(InstallerError, match=extract.MMAPS_CANCEL_NOTE):
        mmaps(MMAPS, runner, tmp_path, cancel=threading.Event())


# --- what is removed, and the two places that decide it ----------------------------------------


def test_the_wipe_target_is_built_from_the_data_dir_and_a_constant_no_catalog_names() -> None:
    """The wipe's inputs, audited where they are CHOSEN rather than where they meet.

    PR #142's shape was a uniqueness claim that protected nothing because the
    caller reaching `rmtree` never asked it. The equivalent here is asserting
    "it removed `data/mmaps`" on one happy path and calling the question
    settled — a later `MmapPlan` field naming its own output folder would pass
    that assertion on the day it was written and point the removal somewhere
    else on the day it shipped.

    So the two inputs are pinned instead of the one result. `data_dir` is the
    only path `run_mmaps` is given — there is no client parameter to confuse it
    with — and `MMAPS_DIR` is a bare relative component with no anchor and no
    `..`, so `data_dir / MMAPS_DIR` cannot leave `data_dir` however either is
    spelled. `MmapPlan` carrying no folder field is the third leg: no catalog
    entry has anywhere to write a different name.

    The field set is pinned rather than a `"folder" not in` check on purpose:
    the question is not whether today's spelling of a folder field is absent,
    it is whether ANY new field slipped in without someone asking it.
    `success_codes` was added on 2026-09-03 and answers it -- a tuple of exit
    statuses, which `run_mmaps` puts in an `in` test and never in a path.
    """
    parameters = inspect.signature(extract.run_mmaps).parameters
    assert set(parameters) == {
        "plan",
        "image_ref",
        "data_dir",
        "run_container",
        "user_args",
        "sink",
        "cancel",
    }
    assert "client" not in " ".join(parameters)
    folder = Path(extract.MMAPS_DIR)
    assert folder.parts == (extract.MMAPS_DIR,)
    assert not folder.is_absolute() and folder.anchor == "" and ".." not in folder.parts
    assert set(MmapPlan.model_fields) == {"argv", "min_files", "required", "success_codes"}
    # NOT `all(isinstance(code, int) ...)`, which was here until a review
    # pointed out that `isinstance(True, int)` is True and the assertion pinned
    # nothing about the value. The default itself is what matters, and it is
    # also pinned indirectly by the shipped-catalog test: TBC and Vanilla
    # declare no `success_codes` at all, so a default of `(0, 1)` would fail
    # there.
    assert MmapPlan(argv=("x",)).success_codes == (0,)


def test_the_run_hands_rmtree_the_mmaps_folder_and_a_skip_hands_it_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the audit: what the run actually handed `rmtree`.

    The client sits beside `server/` in this fixture, holding the file
    `expected_evidence()` measures, and the sibling extract output (`dbc`,
    `maps`, `vmaps`) sits inside the same `data/` the wipe happens in — so a
    removal that took `data_dir` itself, or anything derived from the client,
    would be visible here rather than inferred.

    The second half is the ending the first half cannot see: once the evidence
    vouches for the folder, nothing is removed at all. A wipe placed before the
    skip test would still pass every assertion above.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    fill(data, "mmaps", 1)
    removed: list[Path] = []
    real_rmtree = extract.shutil.rmtree

    def recording(path: Path, *args: object, **kwargs: object) -> None:
        removed.append(Path(path))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(extract.shutil, "rmtree", recording)
    mmaps(MMAPS, Runner(MMAPS_WRITES), tmp_path)
    assert removed == [data / "mmaps"]
    assert (tmp_path / "client" / "Data" / "expansion.MPQ").is_file()
    assert extract.file_count(data / "dbc") == 3
    assert extract.file_count(data / "maps") == 2
    assert extract.file_count(data / "vmaps") == 2

    skipping = Runner(MMAPS_WRITES)
    mmaps(MMAPS, skipping, tmp_path)
    assert skipping.specs == []
    assert removed == [data / "mmaps"], "a folder the evidence vouches for is not wiped"


# --- a wipe that is not followed by a run ------------------------------------------------------


def test_a_stop_that_arrived_before_the_stage_started_removes_nothing(tmp_path: Path) -> None:
    """The one ending where "do not wipe" is free, so it is taken.

    A cancel token that is already set when the stage begins means the user
    pressed Stop and this stage has done nothing yet. Wiping and then reading
    the same token back off the container would cost them a finished mmaps
    folder for a decision they made before it started.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    fill(data, "mmaps", 7)
    already = threading.Event()
    already.set()
    runner = Runner(MMAPS_WRITES)
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, runner, tmp_path, cancel=already)
    assert "stopped" in str(caught.value)
    assert extract.MMAPS_CLEARED_NOTE not in str(caught.value)
    assert runner.specs == []
    assert extract.file_count(data / "mmaps") == 7


def test_a_folder_that_could_not_be_removed_refuses_before_the_tool_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Third outcome: the removal neither happened nor was assumed to have happened.

    Generating over a folder that is still there is the one thing the wipe
    exists to prevent, so a removal that failed cannot be shrugged off and
    followed by a run. Nothing ran and nothing was lost — the folder is still
    exactly as full as it was.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    fill(data, "mmaps", 7)

    def refuse(path: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "in use by another process")

    monkeypatch.setattr(extract.shutil, "rmtree", refuse)
    runner = Runner(MMAPS_WRITES)
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, runner, tmp_path)
    assert "could not be removed" in str(caught.value)
    assert runner.specs == []
    assert extract.file_count(data / "mmaps") == 7


def _mmaps_stopped() -> Runner:
    """Ending 1, and only ending 1: the cancel sentinel with no configured failure."""
    runner = Runner(MMAPS_WRITES)
    runner.cancel_after = 1
    return runner


def _mmaps_never_started() -> Runner:
    """Ending 2: both halves of `cli_missing_run()`'s sentinel and nothing else."""
    return Runner({}, fail={"/opt/bin/MoveMapGen": (127, platform.DOCKER_CLI_MISSING_HELP)})


def _mmaps_crashed() -> Runner:
    """Ending 3: an ordinary non-zero status with ordinary last words."""
    return Runner({}, fail={"/opt/bin/MoveMapGen": SEGFAULT})


def _mmaps_fell_short() -> Runner:
    """Ending 4: exit 0, having written one file where three were asked for."""
    return Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}})


MMAPS_ENDINGS = [
    pytest.param(_mmaps_stopped, "was stopped", id="stopped"),
    pytest.param(_mmaps_never_started, "could not be started", id="never-started"),
    # The clause after the semicolon is the point: a user reading "exit 139" has
    # no way to know whether that status meant anything, and the answer differs
    # between the generators this app installs.
    pytest.param(
        _mmaps_crashed,
        "failed (exit 139; this server's generator reports 0 when it finishes)",
        id="failed",
    ),
    pytest.param(_mmaps_fell_short, "at least 3 were expected", id="fell-short"),
]


@pytest.mark.parametrize(("make_runner", "says"), MMAPS_ENDINGS)
def test_every_refusal_after_a_wipe_says_the_earlier_mmaps_are_gone(
    tmp_path: Path, make_runner: Callable[[], Runner], says: str
) -> None:
    """A shortfall after a wipe is not the same fact as a shortfall before one.

    All four of these leave the stage with no record, so the next attempt starts
    over — but the user's disk is not where it was. They had a folder; it was
    removed so MoveMapGen would not skip past what was in it; and then nothing
    replaced it. A refusal that says only "failed (exit 139)" describes a
    machine that is one folder poorer than the sentence implies.

    Each fixture trips exactly one of the four endings, so "it raised" cannot
    stand in for "this rule caught it": the cancel sentinel with no configured
    failure, both halves of the CLI-missing sentinel, a 139 that is neither, and
    an exit 0 that wrote too little.
    """
    run(PLAN, Runner(FULL), tmp_path)
    fill(tmp_path / "server" / "data", "mmaps", 5)
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, make_runner(), tmp_path, cancel=threading.Event())
    message = str(caught.value)
    assert says in message
    assert extract.MMAPS_CLEARED_NOTE in message


@pytest.mark.parametrize(("make_runner", "says"), MMAPS_ENDINGS)
def test_a_refusal_with_nothing_to_wipe_does_not_claim_a_folder_was_removed(
    tmp_path: Path, make_runner: Callable[[], Runner], says: str
) -> None:
    """The same four endings on a first install, where there was nothing there.

    Without this the "cleared" clause could be an unconditional string and every
    assertion above would still pass, while a first-time install was told it had
    lost a folder it never had.
    """
    run(PLAN, Runner(FULL), tmp_path)
    assert not (tmp_path / "server" / "data" / "mmaps").exists()
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, make_runner(), tmp_path, cancel=threading.Event())
    message = str(caught.value)
    assert says in message
    assert extract.MMAPS_CLEARED_NOTE not in message


def test_a_generator_that_never_started_is_not_map_generation_failing(tmp_path: Path) -> None:
    """`run_plan`'s ending 2, kept here: "it failed (exit 127)" is about a tool that ran.

    Both halves of the sentinel, for `docker.cli_missing_run()`'s reason —
    `docker run` returns the CONTAINER's status, so a MoveMapGen missing inside
    the image genuinely exits 127, and reading that as "docker is not installed"
    sends the user to reinstall Docker.
    """
    run(PLAN, Runner(FULL), tmp_path)
    with pytest.raises(InstallerError) as missing:
        mmaps(MMAPS, _mmaps_never_started(), tmp_path)
    assert "could not be started" in str(missing.value)
    assert platform.DOCKER_CLI_MISSING_HELP in str(missing.value)
    assert "exit 127" not in str(missing.value)
    real = Runner({}, fail={"/opt/bin/MoveMapGen": (127, "exec /opt/bin/MoveMapGen: not found")})
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, real, tmp_path)
    assert "exit 127" in str(caught.value) and "not found" in str(caught.value)
    assert "could not be started" not in str(caught.value)


# --- the counts, walked once ------------------------------------------------------------------


def test_the_shortfall_refusal_quotes_the_number_the_gate_read_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One walk decides the refusal, and the refusal is made of that walk's number.

    `mmaps` is a single folder, so a second `shortfall()` inside the `if short:`
    block produces an identical sentence for as long as the filesystem holds
    still — which is exactly the way I.4's version of this survived a whole
    file. So the fixture does not hold still: the folder answers 1 the first
    time it is walked and 0 to anything after that, which is the only thing that
    tells the two versions apart.

    One walk, not two: `satisfied()` answers False on the missing record before
    it reaches its count part, so the skip gate never touches the folder on a
    run that is about to regenerate it.
    """
    run(PLAN, Runner(FULL), tmp_path)
    walked: list[Path] = []
    real_count = extract.file_count

    def counting(folder: Path) -> int:
        walked.append(folder)
        if folder.name == extract.MMAPS_DIR:
            return 1 if walked.count(folder) == 1 else 0
        return real_count(folder)

    monkeypatch.setattr(extract, "file_count", counting)
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}}), tmp_path)
    data = tmp_path / "server" / "data"
    assert walked == [data / "mmaps"]
    assert "mmaps holds 1 files where at least 3 were expected" in str(caught.value)


def test_the_warning_and_the_done_line_quote_that_same_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The optional path says the number twice, and both times it is the gate's.

    A `required: false` plan yields a warning naming the count AND a done line
    naming it again, which is two chances to walk the folder again and tell the
    user two different numbers about one folder. The fixture answers 1 once and
    0 after, so either extra walk shows up as a 0 in a sentence.
    """
    run(PLAN, Runner(FULL), tmp_path)
    walked: list[Path] = []
    real_count = extract.file_count

    def counting(folder: Path) -> int:
        walked.append(folder)
        if folder.name == extract.MMAPS_DIR:
            return 1 if walked.count(folder) == 1 else 0
        return real_count(folder)

    monkeypatch.setattr(extract, "file_count", counting)
    optional = MmapPlan(argv=MMAPS.argv, min_files=3, required=False)
    said = mmaps(optional, Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}}), tmp_path)
    data = tmp_path / "server" / "data"
    assert walked == [data / "mmaps"]
    assert any("mmaps holds 1 files where at least 3 were expected" in line for line in said)
    assert any("done (mmaps: 1 files)" in line for line in said)


# --- the container this stage builds ------------------------------------------------------------


def test_the_mmaps_container_gives_up_the_network_and_new_privileges_and_no_label(
    tmp_path: Path,
) -> None:
    """The hardening every extraction run gets, and the SELinux flag this one does not.

    `container_security_args()` adds `label:disable` because the extraction
    container has to read the user's game client, which is outside the server
    folder and which no `chcon` of ours reaches (measured on
    `yulon-fedora-gate`, Fedora 44 Enforcing, Docker 29.7.2, 2026-09-01, and
    recorded on `docker.ContainerRun.security_args`). This container has no
    client mount at all: its one bind is the `data/` under the server directory,
    which that same measurement found readable and writable while confined. So
    the label flag would be turning a container's confinement off for nothing,
    which is the decision `platform.label_disable_args()` exists to make nobody
    take by default.
    """
    run(PLAN, Runner(FULL), tmp_path)
    runner = Runner(MMAPS_WRITES)
    mmaps(MMAPS, runner, tmp_path)
    (spec,) = runner.specs
    assert spec.security_args == extract.EXTRACT_HARDENING
    assert "label:disable" not in spec.security_args
    assert spec.mounts == (docker.Mount(tmp_path / "server" / "data", "/out"),)
    assert spec.env == {}
    assert spec.ulimits == ()


# --- which part of the skip rule can actually refuse -------------------------------------------


def test_evidence_that_cannot_identify_its_client_never_licenses_a_skip_of_mmaps(
    tmp_path: Path,
) -> None:
    """The veto is the part of the rule that compares two different values here.

    `run_mmaps` hands `satisfied()` the same `Evidence` on both sides, so
    `same_stage()`'s four fields agree by construction and cannot refuse
    anything — asserted below so the claim is not left as a comment. The veto
    can refuse, because the bit it reads was written by whichever run measured
    (or failed to measure) the client, and it survives being written to the file
    and read back. Flipping it on disk turns a skip into a wipe and a re-run.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    mmaps(MMAPS, Runner(MMAPS_WRITES), tmp_path)
    recorded = extract.read_evidence(data)
    assert recorded is not None
    assert extract.same_stage(recorded, recorded) is True

    skipping = Runner(MMAPS_WRITES)
    mmaps(MMAPS, skipping, tmp_path)
    assert skipping.specs == []

    extract.write_evidence(data, replace(recorded, client_facts_complete=False))
    blind = Runner(MMAPS_WRITES)
    said = mmaps(MMAPS, blind, tmp_path)
    assert len(blind.specs) == 1
    assert any("no finished run vouches for" in line for line in said)


def test_an_edited_mmaps_argv_regenerates_rather_than_skipping(tmp_path: Path) -> None:
    """The record is name AND argv: a catalog edit to the thread count is a different run."""
    run(PLAN, Runner(FULL), tmp_path)
    mmaps(MMAPS, Runner(MMAPS_WRITES), tmp_path)
    edited = MmapPlan(
        argv=("/opt/bin/MoveMapGen", "--silent", "--threads", "4"), min_files=3, required=True
    )
    runner = Runner(MMAPS_WRITES)
    said = mmaps(edited, runner, tmp_path)
    assert [spec.argv for spec in runner.specs] == [edited.argv]
    assert any("no finished run vouches for" in line for line in said)


def test_a_re_extract_for_another_client_drops_the_mmaps_record_and_one_tool_re_running_does_not(
    tmp_path: Path,
) -> None:
    """What this stage's own evidence can and cannot notice, both halves.

    `run_mmaps` compares an evidence with itself, so nothing inside it detects a
    changed client; what does is `run_plan`, which replaces the whole file —
    every record, this stage's included — when the stage facts move. That is the
    protection, and it lives one stage away, so it is pinned here rather than
    assumed.

    The half that is NOT protected is pinned with it: a single tool re-running
    against the same client leaves this stage's record in place, and the mmaps
    folder is skipped even though the maps under it were written again. That is
    a deliberate limit — the evidence records which tools ran, not which files
    fed which — and a test saying so is what stops it being read as a promise.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    mmaps(MMAPS, Runner(MMAPS_WRITES), tmp_path)

    (data / "dbc" / "f0").unlink()
    same_client = Runner(FULL)
    run(PLAN, same_client, tmp_path)
    assert same_client.names() == ["ad"]
    after_one_tool = extract.read_evidence(data)
    assert after_one_tool is not None
    assert after_one_tool.record_for(extract.MMAPS_TOOL) is not None
    skipping = Runner(MMAPS_WRITES)
    mmaps(MMAPS, skipping, tmp_path)
    assert skipping.specs == []

    (tmp_path / "client" / "Data" / "expansion.MPQ").write_bytes(b"MPQ" * 200)
    run(PLAN, Runner(FULL), tmp_path)
    after_another_client = extract.read_evidence(data)
    assert after_another_client is not None
    assert after_another_client.record_for(extract.MMAPS_TOOL) is None
    regenerating = Runner(MMAPS_WRITES)
    mmaps(MMAPS, regenerating, tmp_path)
    assert len(regenerating.specs) == 1


def test_the_mmaps_cancel_note_promises_exactly_what_the_stage_delivers(tmp_path: Path) -> None:
    """Restarts from the beginning; the extracted maps it reads are kept — both halves.

    The note is yielded by the spine before this stage does anything (A4), so it
    has to be true of a Stop pressed at any moment inside it. It is true because
    nothing is recorded until the tool exits 0 with enough files, and because
    the only folder this stage touches is its own.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data = tmp_path / "server" / "data"
    before = extract.read_evidence(data)
    runner = Runner(MMAPS_WRITES)
    runner.cancel_after = 1
    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, runner, tmp_path, cancel=threading.Event())
    assert "was stopped" in str(caught.value)
    assert extract.MMAPS_CANCEL_NOTE in str(caught.value)
    after = extract.read_evidence(data)
    assert after == before, "the extract stage's records are untouched by a stopped mmaps run"
    assert after is not None and after.record_for(extract.MMAPS_TOOL) is None
    assert extract.file_count(data / "maps") == 2
    assert extract.file_count(data / "vmaps") == 2
    assert extract.file_count(data / "dbc") == 3
    again = Runner(MMAPS_WRITES)
    mmaps(MMAPS, again, tmp_path)
    assert len(again.specs) == 1, "it starts from the beginning rather than being skipped"


# --------------------------------------------------------------------------
# Output folders, and who creates them. Found on m910q on 2026-09-02, on the
# first WoW TBC install ever run: `vmap_assembler Buildings vmaps` died with
# `Cannot open vmaps/000.vmtree`, then `error converting
# Abandonedorcbarracks.wmo`, exit 1 -- twice, identically. The assembler does
# not create its own output folder. `mkdir vmaps` and re-running the
# byte-identical `docker run` produced 1869 `.vmtile` files.


class _SeesItsFolder(Runner):
    """A `Runner` that also records whether each tool's output folder existed AT START.

    Subclassed rather than replaced so the fabricated output, the recorded
    specs and the failure handling all stay the ones every other test in this
    file exercises -- the only added fact is the one the ordering bug is about.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.folder_existed: list[tuple[str, bool]] = []

    def __call__(
        self, spec: docker.ContainerRun, *, sink: docker.OutputSink, cancel: threading.Event | None
    ) -> docker.AttachedRun:
        program = tool_program(spec)
        out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
        for folder in FULL.get(program, {}):
            self.folder_existed.append((folder, (out / folder).is_dir()))
        return super().__call__(spec, sink=sink, cancel=cancel)


def test_every_tools_output_folder_exists_before_that_tool_runs(tmp_path: Path) -> None:
    """Created by us, because the tool that needs it does not create it.

    Asserts existence AT THE MOMENT THE CONTAINER STARTS, not afterwards: the
    defect is entirely one of ordering, and a check made after the run would
    pass against a version that creates the folder too late, and against no fix
    at all whenever an earlier tool happened to leave the folder behind.

    Every folder in the plan, not just `vmaps`: the assembler was not special,
    it was merely the first tool whose output folder nothing else had made.
    """
    runner = _SeesItsFolder(FULL)

    run(PLAN, runner, tmp_path)

    assert runner.folder_existed, "no tool ran, so the ordering was never observed"
    missing = [folder for folder, existed in runner.folder_existed if not existed]
    assert missing == [], f"these tools ran before their output folder existed: {missing}"


def test_making_the_folder_does_not_make_an_unrun_tool_look_finished(tmp_path: Path) -> None:
    """An empty folder counts zero files, so the shortfall gate still refuses.

    The obvious objection to creating output folders up front, answered rather
    than argued: `counts()` walks FILES. Without this, the fix could have been
    written to create the folder and let the count gate pass, which would report
    a finished extraction over an empty `vmaps/`.
    """
    data_dir = tmp_path / "data"
    (data_dir / "vmaps").mkdir(parents=True)

    assert extract.counts({"vmaps": 100}, data_dir) == {"vmaps": 0}
    assert extract.shortfall({"vmaps": 100}, data_dir) == {"vmaps": (0, 100)}


def test_a_slashed_produces_name_is_created_where_the_count_gate_looks(tmp_path: Path) -> None:
    """`counts()` reads `data_dir / folder`, so the folder is made at that same path.

    `ExtractTool` does not forbid a slash, and `STAGE_SCRIPT`'s `cp` form is
    documented to land a slashed name at exactly this path. A `mkdir` without
    `parents=True` raises on it; one that split the name differently would
    create a folder the gate never reads.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    extract.make_out_dirs(["Cameras/Buildings"], data_dir)

    assert (data_dir / "Cameras" / "Buildings").is_dir()


def test_a_folder_that_cannot_be_created_refuses_and_names_it(tmp_path: Path) -> None:
    """A sentence naming the folder, not an `OSError` escaping into a container run."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # A FILE where the folder must go. `mkdir` raises `FileExistsError`, an
    # `OSError`, without depending on permissions a test cannot arrange on
    # every platform this suite runs on.
    (data_dir / "vmaps").write_text("not a folder", encoding="utf-8")

    with pytest.raises(InstallerError) as caught:
        extract.make_out_dirs(["vmaps"], data_dir)

    assert "vmaps" in str(caught.value), caught.value
    assert "could not be created" in str(caught.value), caught.value


def test_mmaps_output_folder_exists_before_movemapgen_runs(tmp_path: Path) -> None:
    """The stage WIPES `mmaps/` and nothing put it back, so the tool met no folder.

    The same defect as the assembler's, one stage later, and it survived the
    first mutation pass over that fix -- V5 removed the `make_out_dirs` call
    here and every test in this file stayed green. This is the test that was
    missing.

    Driven through the wipe on purpose: `_remove_tree()` runs whenever no
    finished record vouches for the folder, which is exactly the resume a user
    performs after an interrupted generation, so the folder is at its most
    absent on the path most likely to be taken twice.

    Records existence AT THE MOMENT THE CONTAINER STARTS. Checking afterwards
    would pass against a version that creates it too late and against no fix at
    all, since MoveMapGen's fabricated output makes the folder either way.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data_dir = tmp_path / "server" / "data"
    # A folder no finished run vouches for: what an interrupted generation
    # leaves, and what `run_mmaps` removes before it starts.
    (data_dir / "mmaps").mkdir(parents=True, exist_ok=True)
    (data_dir / "mmaps" / "half-written.mmtile").write_text("x", encoding="utf-8")

    seen_at_start: list[bool] = []

    class _Watching(Runner):
        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
            seen_at_start.append((out / "mmaps").is_dir())
            return super().__call__(spec, sink=sink, cancel=cancel)

    mmaps(MMAPS, _Watching(MMAPS_WRITES), tmp_path)

    assert seen_at_start == [True], "MoveMapGen ran with no mmaps/ folder to write into"


def test_a_retried_tool_the_main_loop_never_reached_still_gets_its_output_folder(
    tmp_path: Path,
) -> None:
    """The retry recipe can reach a tool the main loop has not run yet.

    `wow-vanilla` is the only entry that ships a `retry`, and its recipe is
    `["vmap extract", "vmap assemble"]` -- the two tools this fixture mirrors.
    So the ONE crash the recipe exists for, a segfault in `vmap extract`, makes
    the recipe re-run `vmap assemble` BEFORE the main loop has ever reached it,
    and therefore before anything created `vmaps/`.

    That is precisely the `Cannot open vmaps/000.vmtree` failure `make_out_dirs`
    was added to prevent, still reachable after the fix, on the only recipe in
    the catalog. Two independent reviews found it on 2026-09-02; five mutations
    of the original change had all been killed, because none of them ran a
    retry.

    Records existence AT THE MOMENT THE CONTAINER STARTS, for the same reason
    the sibling test does: `Runner` fabricates the output, so checking afterwards
    would pass against no fix at all.
    """
    seen_at_start: dict[str, bool] = {}

    class _WatchingRetry(Runner):
        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            program = tool_program(spec)
            out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
            for folder in FULL.get(program, {}):
                # Last write wins: the retry's observation is the one that
                # matters, and it happens after the main loop's.
                seen_at_start[folder] = (out / folder).is_dir()
            return super().__call__(spec, sink=sink, cancel=cancel)

    runner = _WatchingRetry(FULL, fail={VMAP.argv[0]: SEGFAULT})

    lines = run(RETRY_PLAN, runner, tmp_path)

    assert any("retrying" in line for line in lines), f"no retry happened: {lines}"
    assert (
        seen_at_start.get("vmaps") is True
    ), "vmap assemble ran on the retry path with no vmaps/ folder to write into"


def test_a_wipe_followed_by_an_unmakeable_folder_still_says_the_mmaps_are_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The FIFTH ending after a wipe, and the one that had no test.

    `run_mmaps` removes `mmaps/` when no finished record vouches for it, then
    creates it again for MoveMapGen. If that creation fails -- ENOSPC, a mount
    gone read-only, something else holding the name -- `make_out_dirs`'s own
    sentence ends "Nothing was run", which is true of the TOOL and false of the
    FOLDER that was just deleted.

    Deliberately NOT a fifth entry in `MMAPS_ENDINGS`: those four are each
    tripped by a different `Runner`, and this one is tripped before any runner
    is reached, so it needs a different hook and would have to fake its way into
    that list.

    `make_out_dirs` is stubbed rather than provoked with a real unwritable path:
    it has its own tests for WHEN it raises, and permissions behave differently
    on the three platforms this suite runs on. What is under test here is that
    `run_mmaps` catches it and appends what the wipe did -- which a review added
    on 2026-09-02 and nothing asserted, so deleting `{cleared}` left all 2095
    tests green.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data_dir = tmp_path / "server" / "data"
    fill(data_dir, "mmaps", 5)

    def refuse(produces: object, where: Path) -> None:
        raise InstallerError(f"{where / 'mmaps'} could not be created (boom). Nothing was run.")

    monkeypatch.setattr(extract, "make_out_dirs", refuse)

    with pytest.raises(InstallerError) as caught:
        mmaps(MMAPS, Runner(MMAPS_WRITES), tmp_path)

    message = str(caught.value)
    assert "could not be created" in message, message
    assert extract.MMAPS_CLEARED_NOTE in message, (
        "the folder was wiped and the refusal did not say so: " + message
    )


def test_an_optional_generator_that_wrote_nothing_is_refused_and_never_recorded(
    tmp_path: Path,
) -> None:
    """Two safe settings stacked, and the failure they let through together.

    `required: false` means "fewer maps than we hoped is survivable" -- a solo
    realm does not need every one. `success_codes: [1]` is a measured fact about
    the Tortoise fork, whose MoveMapGen returns 1 when it FINISHES. Neither is
    wrong. Stacked, a Tortoise run that dies exits 1, reads as finished, and its
    empty folder is downgraded to a warning.

    The recording is the damage, not the wording. `produces` for an optional
    plan is `{mmaps: 0}`, so once an empty run is written into the evidence file
    every later resume finds the stage satisfied by zero files and says
    "already extracted" forever. The user's next sight of it is a world server
    that cannot path.

    This shipped in `102e2dd1` with a comment and no test: deleting the branch
    outright left the whole suite green.
    """
    run(PLAN, Runner(FULL), tmp_path)
    data_dir = tmp_path / "server" / "data"
    optional = MmapPlan(
        argv=("/opt/bin/MoveMapGen", "--silent"), min_files=3, required=False, success_codes=(1,)
    )
    runner = Runner({}, fail={"/opt/bin/MoveMapGen": (1, "Movemap build is complete!")})

    with pytest.raises(InstallerError) as caught:
        mmaps(optional, runner, tmp_path)

    message = str(caught.value)
    assert "produced no files at all" in message, message
    assert len(runner.specs) == 1, "the generator should have been run exactly once"

    evidence = extract.read_evidence(data_dir)
    assert evidence is not None
    assert evidence.record_for(extract.MMAPS_TOOL) is None, (
        "an empty run was recorded, so every resume after this one will find the stage "
        "satisfied by zero files and never generate a movement map again"
    )

    # And the sibling case that must NOT be refused: the same optional plan,
    # the same exit 1, but the tool wrote FEWER files than `min_files`. That is
    # the shortfall `required: false` exists for, and it goes through.
    short = Runner({"/opt/bin/MoveMapGen": {"mmaps": 1}}, fail={"/opt/bin/MoveMapGen": (1, "ok")})
    said = mmaps(optional, short, tmp_path)
    assert any("mmaps: 1 file" in line for line in said), said
    after = extract.read_evidence(data_dir)
    assert after is not None and after.record_for(extract.MMAPS_TOOL) is not None


def test_a_crash_with_no_output_at_all_is_still_recognised_as_the_crash() -> None:
    """The shape the recipe was written for, and could not see until 2026-09-03.

    `test_retry_matches_answers_every_ending_and_not_just_the_regex` builds its
    crash as `AttachedRun(139, ("Segmentation fault (core dumped)",))` -- exit
    status AND the words. That is the fixture answering itself: it hands the
    matcher the very text it looks for, so it never asked whether a crashed
    tool produces that text.

    It does not. `Segmentation fault (core dumped)` is printed by a SHELL's job
    control, and these tools are exec'd as the container's PID 1 with no shell
    in between. Probed on yulon-ubuntu (2026-09-03): every signal-killed
    container returned ZERO bytes of output and zero matches for the pattern.
    A recipe with only `when_log_matches` therefore could not fire on the
    failure it names.

    So the ending here carries the status and NOTHING else, which is what a
    crashed extractor really leaves behind.
    """
    silent_crash = docker.AttachedRun(139, ())
    with_codes = RetrySpec(
        when_log_matches="Segmentation fault|core dumped",
        when_returncode_in=(139, 134),
        tools=(VMAP.name,),
    )
    assert extract._retry_matches(with_codes, silent_crash, None) is True

    # And the codes are what did it: the same silent crash against a recipe
    # that names only the text is still no retry. Without this half, a matcher
    # that ignored `when_returncode_in` entirely would pass the first assertion
    # on any implementation that simply retried every non-zero exit.
    text_only = RetrySpec(when_log_matches="Segmentation fault|core dumped", tools=(VMAP.name,))
    assert extract._retry_matches(text_only, silent_crash, None) is False

    # A status the recipe does not name is still not this recipe's failure.
    assert extract._retry_matches(with_codes, docker.AttachedRun(1, ()), None) is False

    # The refusals ahead of the status check still win over it.
    stopped = threading.Event()
    stopped.set()
    assert extract._retry_matches(with_codes, silent_crash, stopped) is False
    cancelled = docker.AttachedRun(docker.CANCELLED_RETURNCODE, ())
    assert extract._retry_matches(with_codes, cancelled, None) is False


def test_a_recipe_cannot_name_success_or_a_sentinel_as_a_reason_to_retry() -> None:
    """0 and the negatives are refused before `_retry_matches` ever looks at them.

    A recipe listing either would be dead text that reads as if it did
    something: `_retry_matches` returns False for exit 0 and for
    `CANCELLED_RETURNCODE` in its first line, whatever the recipe says.
    """
    for bad in (0, -1, -11, 256):
        with pytest.raises(ValidationError):
            RetrySpec(when_log_matches="x", when_returncode_in=(bad,), tools=(VMAP.name,))


def shipped_vanilla_recipe() -> RetrySpec:
    """The recipe `wow-vanilla` actually ships, read from the catalog.

    Both tests below drive THIS object rather than a `RetrySpec` typed into the
    file, so a catalog edit reaches them. `RETRY` above cannot: it names no
    statuses at all, which is why every stage-level retry test in this file was
    blind to the field until now.
    """
    native = load_catalog().get("wow-vanilla").install.native
    assert native is not None and native.cmangos is not None
    recipe = native.cmangos.extract.retry
    assert recipe is not None, "wow-vanilla is the entry this recipe exists for"
    return recipe


def test_the_shipped_vanilla_recipe_names_the_status_a_crash_actually_reports() -> None:
    """The value has to be in the catalog, not merely possible in the model.

    139 is 128+SIGSEGV, the status the recipe's own name is about. Read from the
    shipped entry so a model that grew the field while no entry used it fails
    here.

    **The tuple is asserted whole, and 134 is the reason.** It shipped for a few
    hours beside 139 on the theory that a signal death is a signal death, and
    `37b83d7b` took it out: 128+SIGABRT here is a failed assertion inside the
    extractor over a particular record of the client's data, and the retry runs
    the identical container over the identical bytes, so it can only buy a
    second multi-minute wait before the same failure. A membership check would
    let it -- or anything else -- come back in silence, which is exactly the
    move that argument was written against. 139 stays because a stack overflow
    is resource-dependent and plausibly transient.
    """
    assert shipped_vanilla_recipe().when_returncode_in == (139,), (
        "a crashed extractor reports 139 and prints nothing; without it in the recipe the "
        "retry cannot fire on the failure it was written for -- and nothing else belongs "
        "here without evidence that running the same container again could change it"
    )


def test_the_shipped_recipe_fires_the_stage_retry_on_a_crash_that_printed_nothing(
    tmp_path: Path,
) -> None:
    """The wire, end to end: catalog value -> `_retry_applies` -> containers run again.

    Every other stage-level retry test in this file drives the module-level
    `RETRY`, which carries no `when_returncode_in`, against a fixture whose
    crash already contains the text the recipe matches on. So all of them pass
    with the status check removed -- verified: blanking `when_returncode_in` at
    the call site left the whole suite green while restoring the very defect
    `247b2c68` was written to fix.

    This one closes that gap the way `test_families_cmangos.py` closed it for
    `success_codes`. The recipe is the shipped one, the crash is SILENT (139 and
    a tail of zero lines, which is what a signal-killed PID 1 really leaves),
    and the transcript is asserted whole. Delete the status from the catalog, or
    stop passing it down, and the extractor's crash becomes a plain failure
    hours before anyone finds out the maps are missing.
    """

    class SilentCrash(Runner):
        """Crashes `vmap_extractor` once with no output at all, then behaves."""

        def __init__(self) -> None:
            super().__init__(FULL)
            self.crashed = False

        def __call__(
            self,
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            if tool_program(spec) == "/opt/bin/vmap_extractor" and not self.crashed:
                self.crashed = True
                self.specs.append(spec)
                return docker.AttachedRun(139, ())
            return super().__call__(spec, sink=sink, cancel=cancel)

    recipe = shipped_vanilla_recipe()
    assert recipe.tools == (VMAP.name, ASSEMBLE.name), (
        "this test drives the shipped recipe against the fixture plan; if the entry renames "
        "its tools the fixture has to follow, or the retry would be re-running strangers"
    )
    plan = ExtractPlan(
        image="server", tools=(AD, VMAP, ASSEMBLE), ulimit_stack_unlimited=True, retry=recipe
    )
    runner = SilentCrash()
    said = run(plan, runner, tmp_path)
    assert runner.names() == ["ad", "vmap_extractor", "vmap_extractor", "vmap_assembler"]
    assert said == [
        f"{AD.name}: running /opt/bin/ad -i /client -o /out",
        f"{AD.name}: done (dbc: 3 files, maps: 2 files)",
        f"{VMAP.name}: running /opt/bin/vmap_extractor -d /client/Data",
        f"{VMAP.name} crashed the way the retry recipe expects; "
        f"running {VMAP.name}, {ASSEMBLE.name} again once",
        f"{VMAP.name}: emptying Buildings before the retry, so it regenerates what the crashed "
        "attempt left rather than adding to it",
        f"{VMAP.name}: retrying /opt/bin/vmap_extractor -d /client/Data",
        f"{VMAP.name}: done (Buildings: 2 files)",
        f"{ASSEMBLE.name}: emptying vmaps before the retry, so it regenerates what the crashed "
        "attempt left rather than adding to it",
        f"{ASSEMBLE.name}: retrying /opt/bin/vmap_assembler Buildings vmaps",
        f"{ASSEMBLE.name}: done (vmaps: 2 files)",
        f"{ASSEMBLE.name}: already extracted (vmaps: 2 files)",
    ]


def test_a_produces_name_that_lands_outside_the_data_dir_is_refused_before_anything_is_removed(
    tmp_path: Path,
) -> None:
    """`empty_out_dirs()` is the one call in this module that deletes a tree, so it checks first.

    `ExtractTool.produces` validates the COUNTS and nothing about the names, and
    `Path.__truediv__` lets an absolute segment replace the left side outright --
    `Path("/srv/data") / "/etc"` is `/etc`. `make_out_dirs()` has always joined
    the same keys and was harmless doing it, because the worst a bad key bought
    there was a directory in an odd place. The worst it buys here is an `rmtree`
    of a folder this install does not own.

    Added 2026-09-04 after a review of the removal this function was written for.
    The keys that ship are all plain relative names, which is why this refuses
    rather than sanitising: a key that reaches here and is not under `data_dir`
    is a `catalog.json` defect, and saying so is more use than quietly repairing
    it.
    """
    data_dir = tmp_path / "server" / "data"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "not-ours"
    outside.mkdir()
    (outside / "keep.txt").write_text("somebody else's", encoding="utf-8")

    with pytest.raises(extract.InstallerError) as caught:
        extract.empty_out_dirs({str(outside): 1}, data_dir)

    assert "outside" in str(caught.value)
    assert "Nothing was removed" in str(caught.value)
    assert (outside / "keep.txt").exists(), "the refusal has to come before the removal"

    # The same refusal for a key that climbs out with `..` rather than by being
    # absolute -- the other spelling of the same defect.
    with pytest.raises(extract.InstallerError):
        extract.empty_out_dirs({"../not-ours": 1}, data_dir)
    assert (outside / "keep.txt").exists()


# ------------------------------------------------ the doodad placement check (option C)


def buildings(root: Path, files: tuple[str, ...], placed: tuple[str, ...] | None) -> Path:
    """A `Buildings/` with model files by name and a `dir_bin` naming `placed` (None: no index)."""
    folder = root / "data" / extract.BUILDINGS_DIR
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b"VMAP")
    if placed is not None:
        # The real index is binary records with a length-prefixed name inside each;
        # the check reads names out of the bytes and never parses the records.
        body = b"".join(b"\x00\x01\x02" + n.encode() + b"\xff" * 3 for n in placed)
        (folder / extract.DIR_BIN).write_bytes(body)
    return folder


def test_the_reader_spelling_port_matches_fixnamen_and_fixname2_on_the_shapes_that_matter() -> None:
    """The C the extractor applies before its lookup, ported; the witnesses are §4 and §8's.

    `INNBED.MDX` is the raw MODN name the write-up watched being written; the
    reader asks for `Innbed.mdx` (then `.m2`). `Razorfen Leanto03.m2` is the one
    model whose plain name carries a space, which `fixname2` underscores. And a
    name already in that form is a fixed point — the assumption §8 says a
    maintainer should test first, tested here for the port at least.
    """
    assert extract.reader_spelling("INNBED.MDX") == "Innbed.mdx"
    assert extract.reader_spelling("INNBED.M2") == "Innbed.m2"
    assert extract.reader_spelling("Razorfen Leanto03.m2") == "Razorfen_Leanto03.m2"
    assert extract.reader_spelling("Scholme_Bookshelf.m2") == "Scholme_Bookshelf.m2"
    assert extract.reader_spelling("ahnqirajdoor01.m2") == "Ahnqirajdoor01.m2"
    for name in ("Innbed.m2", "Razorfen_Leanto03.m2", "Wc_Cairn.m2", "40Mancourtyard.wmo"):
        assert extract.reader_spelling(name) == name, name
    assert extract.reader_spelling("ab") == "ab"


def test_the_check_counts_models_placed_unplaced_and_misspelt_case_folded(tmp_path: Path) -> None:
    folder = buildings(
        tmp_path,
        files=("Innbed.m2", "INNBED.M2", "Abbeyshelf01.m2", "SCHOLME_BOOKSHELF.M2", "Abbey.wmo"),
        placed=("Innbed.m2", "Abbey.wmo", "Innbed.m2"),
    )
    check = extract.doodad_placements(folder)
    assert check is not None
    assert check.extracted == 3, "case-folded: the two Innbed spellings are one model"
    assert check.placed == 1
    assert check.unplaced == 2
    assert check.misspelt == 2, "INNBED.M2 and SCHOLME_BOOKSHELF.M2 are not the reader's spelling"
    line = check.line()
    assert line.startswith("warning:")
    assert "2 of the 3" in line and "2 " in line
    assert "case-sensitive" in line


def test_a_clean_extraction_is_one_line_of_counts_and_not_a_warning(tmp_path: Path) -> None:
    """Models with no placement are ordinary — 434 of them on the patched m910q run — so
    that number alone never warns; only a spelling the reader would miss does."""
    folder = buildings(
        tmp_path,
        files=("Innbed.m2", "Auctioneercollision.m2", "Abbey.wmo"),
        placed=("Innbed.m2", "Abbey.wmo"),
    )
    check = extract.doodad_placements(folder)
    assert check is not None
    assert (check.extracted, check.placed, check.unplaced, check.misspelt) == (2, 1, 1, 0)
    line = check.line()
    assert not line.startswith("warning")
    assert "2 models" in line and "1 placed" in line and "1 with no placement" in line


def test_no_index_or_no_folder_is_no_check_rather_than_a_warning_about_nothing(
    tmp_path: Path,
) -> None:
    assert extract.doodad_placements(tmp_path / "data" / "Buildings") is None
    folder = buildings(tmp_path, files=("Innbed.m2",), placed=None)
    assert extract.doodad_placements(folder) is None


def test_an_index_that_will_not_read_is_no_check_and_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = buildings(tmp_path, files=("Innbed.m2",), placed=("Innbed.m2",))
    real = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self.name == extract.DIR_BIN:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert extract.doodad_placements(folder) is None
