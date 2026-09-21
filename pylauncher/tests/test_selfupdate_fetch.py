"""Downloading the artifact, and proving it against the release's SHA256SUMS (T90 plan 3).

Offline throughout: the opener is a seam and every response here is a fake that
hands out chunks from a list. The one thing measured against a real clock is
`_Deadline.restart()`, which is what keeps a stalled connection from holding
the download for ever — and even that is measured rather than asserted tightly
(no bound here is inside 10x of what the same work took).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from yulon.selfupdate import fetch
from yulon.selfupdate.fetch import (
    Cancelled,
    UpdateError,
    artifact_url,
    download,
    expected_digest,
    fetch_text,
    parse_checksums,
    sha256_of,
    verify,
)
from yulon.update import _Deadline


class _Response:
    """What the opener seam hands back: chunks, headers, and a close that is asserted.

    `read1` is what the real code prefers (it answers with whatever has arrived
    rather than waiting for a whole chunk), so the fake implements it and
    records which reader was used.
    """

    def __init__(
        self,
        chunks: list[bytes],
        *,
        headers: dict[str, str] | None = None,
        raise_at: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self.headers_map = headers or {}
        self._raise_at = raise_at
        self._error = error or OSError("the connection went away")
        self.reads = 0
        self.asked_for: list[int] = []
        self.closed = False

    def read1(self, amount: int) -> bytes:
        if self._raise_at is not None and self.reads == self._raise_at:
            raise self._error
        self.reads += 1
        self.asked_for.append(amount)
        if not self._chunks:
            return b""
        # A real `read1` never answers with more than it was asked for, and the
        # bound the caller puts on `amount` is the whole size defence.
        head = self._chunks[0]
        if len(head) > amount:
            self._chunks[0] = head[amount:]
            return head[:amount]
        return self._chunks.pop(0)

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers_map.get(name, default)

    def close(self) -> None:
        self.closed = True


def _opener(response: _Response) -> fetch.Opener:
    """The seam, ignoring the watchdog the real one hands its connection to."""

    def open_url(_url: str, _watcher: object) -> _Response:
        return response

    return open_url


def _never() -> bool:
    return False


def _nothing(_done: int, _total: int) -> None:
    return None


# -- parse_checksums ---------------------------------------------------------

A = "a" * 64
B = "b" * 64


def test_the_two_space_form_and_the_binary_star_form_are_both_read() -> None:
    text = f"{A}  Yulon-v1.2.3-Public-x86_64.tar.gz\n{B} *Yulon-v1.2.3-Public-windows-x64.zip\n"
    assert parse_checksums(text) == {
        "Yulon-v1.2.3-Public-x86_64.tar.gz": A,
        "Yulon-v1.2.3-Public-windows-x64.zip": B,
    }


def test_crlf_blank_lines_and_garbage_do_not_stop_the_file_being_read() -> None:
    text = f"\r\n{A}  one.tar.gz\r\n\r\nnot a checksum line at all\r\n{B}  two.zip\r\n"
    assert parse_checksums(text) == {"one.tar.gz": A, "two.zip": B}


def test_uppercase_hex_is_normalised_because_a_digest_is_compared_as_text() -> None:
    assert parse_checksums(f"{'A' * 64}  one.zip") == {"one.zip": A}


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "/etc/x"])
def test_a_name_with_a_path_separator_in_it_is_dropped(name: str) -> None:
    """`SHA256SUMS` is remote text, and the names in it index a dict this app looks up.

    Nothing here ever joins one onto a path, so this is belt and braces — and it
    is cheap belt and braces: `sha256sum` never writes a name with a separator
    in it for files in one flat directory, so a line that has one is not a line
    this project's own workflow produced.
    """
    assert parse_checksums(f"{A}  {name}") == {}


def test_a_digest_that_is_not_64_hex_characters_is_not_a_digest() -> None:
    assert parse_checksums(f"{'a' * 63}  one.zip\n{'a' * 65}  two.zip\n{'z' * 64}  three.zip") == {}


# -- expected_digest ---------------------------------------------------------


def test_expected_digest_finds_the_line_for_this_machines_artifact() -> None:
    assert expected_digest(f"{A}  one.zip\n{B}  two.zip\n", "two.zip") == B


def test_a_release_that_does_not_list_this_artifact_is_refused_by_name() -> None:
    with pytest.raises(UpdateError) as caught:
        expected_digest(f"{A}  one.zip\n", "two.zip")
    said = str(caught.value)
    assert "two.zip" in said
    assert "does not list" in said


# -- download ----------------------------------------------------------------


def test_three_chunks_land_as_one_file_and_progress_ends_at_the_total(tmp_path: Path) -> None:
    body = [b"one", b"two!", b"three"]
    payload = b"".join(body)
    response = _Response(body, headers={"Content-Length": str(len(payload))})
    seen: list[tuple[int, int]] = []
    dest = tmp_path / "Yulon.tar.gz"

    out = download(
        "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
        dest,
        expected_size=len(payload),
        progress=lambda done, total: seen.append((done, total)),
        cancelled=_never,
        open_url=_opener(response),
    )

    assert out == dest
    assert dest.read_bytes() == payload
    assert [done for done, _ in seen] == sorted(done for done, _ in seen), "progress went backwards"
    assert seen[-1] == (len(payload), len(payload))
    assert {total for _, total in seen} == {len(payload)}
    assert response.closed, "the response was left open"
    assert not list(tmp_path.glob("*.part")), "a .part file was left behind"


def test_cancel_mid_way_raises_and_leaves_neither_the_file_nor_its_part(tmp_path: Path) -> None:
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    dest = tmp_path / "Yulon.tar.gz"
    with pytest.raises(Cancelled):
        download(
            "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
            dest,
            expected_size=12,
            progress=_nothing,
            cancelled=cancelled,
            open_url=_opener(_Response([b"aaaa", b"bbbb", b"cccc"])),
        )
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_body_shorter_than_the_release_says_is_refused_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "Yulon.tar.gz"
    with pytest.raises(UpdateError) as caught:
        download(
            "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
            dest,
            expected_size=30,
            progress=_nothing,
            cancelled=_never,
            open_url=_opener(_Response([b"x" * 20], headers={"Content-Length": "30"})),
        )
    assert "20" in str(caught.value) and "30" in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_a_body_longer_than_the_release_says_stops_instead_of_filling_the_disk(
    tmp_path: Path,
) -> None:
    """The size bound is a bound on what is READ, not a check made afterwards.

    A server that answers a 70 MB asset with an endless body would otherwise
    fill the user's disk before anything looked at the total. What is ASKED for
    is bounded at one byte past what the release declared, which is enough to
    know it is too long and is the smallest amount that can prove it — and the
    fake here refuses to answer with more than it was asked for, exactly as a
    real `read1` does, so the assertion below is about the caller's bound.
    """
    dest = tmp_path / "Yulon.tar.gz"
    endless = _Response([b"x" * 4096] * 64)
    with pytest.raises(UpdateError) as caught:
        download(
            "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
            dest,
            expected_size=10,
            progress=_nothing,
            cancelled=_never,
            open_url=_opener(endless),
        )
    assert "10" in str(caught.value)
    assert endless.asked_for == [11], f"it asked for more than the bound: {endless.asked_for}"
    assert list(tmp_path.iterdir()) == []


def test_a_read_that_fails_is_reported_in_the_apps_own_words(tmp_path: Path) -> None:
    dest = tmp_path / "Yulon.tar.gz"
    with pytest.raises(UpdateError) as caught:
        download(
            "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
            dest,
            expected_size=8,
            progress=_nothing,
            cancelled=_never,
            open_url=_opener(_Response([b"aaaa"], raise_at=1)),
        )
    assert "the connection went away" in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_an_opener_that_cannot_connect_is_reported_rather_than_raised_raw(tmp_path: Path) -> None:
    def refuse(_url: str, _watcher: object) -> _Response:
        raise OSError("no route to host")

    with pytest.raises(UpdateError, match="no route to host"):
        download(
            "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
            tmp_path / "Yulon.tar.gz",
            expected_size=8,
            progress=_nothing,
            cancelled=_never,
            open_url=refuse,
        )
    assert list(tmp_path.iterdir()) == []


def test_a_stale_part_file_from_an_earlier_attempt_is_overwritten(tmp_path: Path) -> None:
    dest = tmp_path / "Yulon.tar.gz"
    part = tmp_path / "Yulon.tar.gz.part"
    part.write_bytes(b"rubbish from last time")
    download(
        "https://github.com/x/y/releases/download/v1/Yulon.tar.gz",
        dest,
        expected_size=3,
        progress=_nothing,
        cancelled=_never,
        open_url=_opener(_Response([b"new"])),
    )
    assert dest.read_bytes() == b"new"
    assert not part.exists()


# -- sha256 and verify -------------------------------------------------------


def test_sha256_of_a_file_is_the_digest_hashlib_gives_for_its_bytes(tmp_path: Path) -> None:
    blob = b"a" * (fetch.CHUNK_BYTES * 2 + 17)
    path = tmp_path / "blob"
    path.write_bytes(blob)
    assert sha256_of(path) == hashlib.sha256(blob).hexdigest()


def test_a_matching_digest_is_silent_and_keeps_the_file(tmp_path: Path) -> None:
    path = tmp_path / "blob"
    path.write_bytes(b"hello")
    verify(path, hashlib.sha256(b"hello").hexdigest())
    assert path.exists()


def test_a_mismatch_refuses_and_deletes_what_it_refused(tmp_path: Path) -> None:
    path = tmp_path / "blob"
    path.write_bytes(b"hello")
    with pytest.raises(UpdateError) as caught:
        verify(path, A)
    assert "checksum" in str(caught.value).lower()
    assert not path.exists(), "a file that failed its checksum was left on disk"


def test_the_case_of_the_expected_digest_does_not_decide_a_refusal(tmp_path: Path) -> None:
    path = tmp_path / "blob"
    path.write_bytes(b"hello")
    verify(path, hashlib.sha256(b"hello").hexdigest().upper())
    assert path.exists()


# -- fetch_text --------------------------------------------------------------


def test_fetch_text_reads_a_small_document(tmp_path: Path) -> None:
    text = fetch_text(
        "https://github.com/x/y/releases/download/v1/SHA256SUMS",
        open_url=_opener(_Response([b"one ", b"two"])),
    )
    assert text == "one two"


def test_fetch_text_refuses_a_body_over_its_limit() -> None:
    with pytest.raises(UpdateError, match="too large"):
        fetch_text(
            "https://github.com/x/y/releases/download/v1/SHA256SUMS",
            open_url=_opener(_Response([b"x" * 200] * 10)),
            limit=100,
        )


def test_fetch_text_turns_a_broken_connection_into_a_sentence() -> None:
    with pytest.raises(UpdateError, match="could not be downloaded"):
        fetch_text(
            "https://github.com/x/y/releases/download/v1/SHA256SUMS",
            open_url=_opener(_Response([b"x"], raise_at=0)),
        )


# -- the URL the app asks for ------------------------------------------------


def test_the_download_url_is_built_from_the_repo_the_app_names() -> None:
    assert artifact_url("v1.2.3-Public", "Yulon-v1.2.3-Public-x86_64.tar.gz") == (
        "https://github.com/DadsMmoLab/dads-mmo-lab/releases/download/"
        "v1.2.3-Public/Yulon-v1.2.3-Public-x86_64.tar.gz"
    )


@pytest.mark.parametrize(
    ("tag", "name"),
    [
        ("../../other", "x.tar.gz"),
        ("v1.2.3-Public", "../../../etc/passwd"),
        ("v1.2.3-Public", "a b.tar.gz"),
        ("v1.2.3-Public", "x%2e%2e.tar.gz"),
        ("v1.2.3-Public", "x\\y.tar.gz"),
        ("v1.2.3-Public", "x\ny.tar.gz"),
    ],
)
def test_a_tag_or_a_name_that_would_re_point_the_url_is_refused(tag: str, name: str) -> None:
    with pytest.raises(UpdateError, match="download address"):
        artifact_url(tag, name)


def test_no_setting_and_no_environment_variable_names_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run-time switch repointing where updates come from is the attack this avoids.

    Asserted by BUILDING the url with those variables set to a host of the
    test's choosing, rather than by grepping: a variable this module never
    reads cannot change its answer, and that is what "no override" means.
    """
    for name in ("YULON_RELEASES_API", "YULON_RELEASES_REPO", "YULON_UPDATE_URL", "GITHUB_SERVER"):
        monkeypatch.setenv(name, "https://evil.invalid/owner/repo")
    assert artifact_url("v1-Public", "a.zip").startswith(
        "https://github.com/DadsMmoLab/dads-mmo-lab/releases/download/"
    )


# -- the watchdog that makes the bound true ----------------------------------


def test_a_deadline_that_is_ticked_does_not_fire_and_one_that_is_not_does() -> None:
    """A download may take as long as it takes; a SILENT connection may not.

    `_Deadline` (plan 2's, shared rather than written again) is a one-shot
    timer, so `download()` restarts it on every chunk that arrives. Both halves
    are here because the restart is the half that can be broken silently — a
    `restart()` that did nothing would leave every download bounded at one
    stall interval, which looks like a flaky network rather than like a bug.

    Measured on this dev box: the ticking loop below takes 0.11 s, and the
    bound asserted on it is 0.05 s of quiet per tick with 10 ticks — a full
    second of clock for work measured at a tenth of that.
    """
    ticked = _Deadline(0.05)
    with ticked:
        for _ in range(10):
            time.sleep(0.01)
            assert ticked.restart(0.05) is True
        assert not ticked.fired, "a connection that kept sending was cut off"

    starved = _Deadline(0.05)
    with starved:
        deadline = time.monotonic() + 5.0
        while not starved.fired and time.monotonic() < deadline:
            time.sleep(0.01)
        assert starved.fired, "a connection that sent nothing was never cut off"
    assert starved.restart(0.05) is False, "a fired deadline cannot be restarted"
