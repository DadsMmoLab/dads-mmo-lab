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
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest

from yulon import docker, platform
from yulon.catalog.catalog import ExtractPlan, ExtractTool, RetrySpec
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
        f"{VMAP.name}: retrying /opt/bin/vmap_extractor -d /client/Data",
        f"{VMAP.name}: done (Buildings: 2 files)",
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


def test_the_stage_script_copies_out_by_content_so_a_resume_cannot_nest_a_folder() -> None:
    """`cp -r Buildings /out/` a second time makes `/out/Buildings/Buildings`.

    The counts would still pass — the files are all there, one level down — and
    the server would find no vmaps at all. Copying the folder's CONTENT into a
    folder we make ourselves is the shape that survives a second pass.
    """
    assert 'cp -r "$name/." "/out/$name/"' in extract.STAGE_SCRIPT
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
