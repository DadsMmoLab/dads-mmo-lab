"""The credential file, and what has to be true before it exists (8.2a).

Two rules, and the first is the architecture's own for this module: **nothing is
persisted before a round trip has answered.** The second is that the file is
created private, proved on the flags `os.open` was given rather than on a
`chmod` afterwards — because a chmod is a second step, and the window before it
is exactly when a file is world-readable.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from yulon import channel
from yulon import channel_setup as setup

PASSWORD = "a-password-nobody-should-see"


def _verified() -> setup.Verified:
    return setup.Verified(account="YULON_AB12CD34", password=PASSWORD)


# -- where it goes ----------------------------------------------------------


def test_the_file_is_named_for_the_game_and_the_install(tmp_path: Path) -> None:
    """Two installs of one game keep two credentials, not one that overwrites."""
    first = setup.credential_path("wow-wotlk", "ab12cd34", config_dir=tmp_path)
    second = setup.credential_path("wow-wotlk", "99999999", config_dir=tmp_path)

    assert first != second
    assert first.parent.name == "credentials"
    assert "wow-wotlk" in first.name and "ab12cd34" in first.name


# -- when it is written -----------------------------------------------------


def test_a_verified_account_can_be_saved_and_read_back(tmp_path: Path) -> None:
    saved = setup.save_credential(
        _verified(),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
    )

    endpoint = setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path)

    assert saved.exists()
    assert endpoint is not None
    assert endpoint.account == "YULON_AB12CD34"
    assert endpoint.password == PASSWORD
    assert (endpoint.host, endpoint.port) == ("127.0.0.1", 7878)


def test_nothing_is_there_before_anything_is_saved(tmp_path: Path) -> None:
    assert setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is None


def test_a_file_that_is_not_readable_json_answers_none_rather_than_raising(
    tmp_path: Path,
) -> None:
    """A stale or hand-edited file must not stop the app opening."""
    path = setup.credential_path("wow-wotlk", "ab12cd34", config_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    assert setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is None


# -- how it is written ------------------------------------------------------


def test_the_file_is_created_private_by_its_open_flags_not_by_a_later_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chmod afterwards leaves a window; the mode has to be in the creation.

    Asserted on the flags because on Windows the mode argument is ignored at
    runtime — so a test that read the mode back would pass there for the wrong
    reason and prove nothing about the POSIX boxes where it matters.
    """
    seen: list[tuple[int, int]] = []
    real_open = os.open

    def watched(path, flags, mode=0o777, **kwargs):  # noqa: ANN001, ANN202
        seen.append((flags, mode))
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(setup.os, "open", watched)

    setup.save_credential(
        _verified(),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
    )

    # The creating open: since T138 the writer also opens the folder read-only
    # to sync the rename, and that open creates nothing.
    created = [(flags, mode) for flags, mode in seen if flags & os.O_CREAT]
    assert created, "the file was not created through os.open, so its mode is not in its creation"
    flags, mode = created[-1]
    assert mode == 0o600, f"created with mode {oct(mode)}"
    assert flags & os.O_CREAT
    assert flags & os.O_WRONLY or flags & os.O_RDWR


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not enforced on Windows")
def test_and_on_a_posix_box_the_mode_really_is_private(tmp_path: Path) -> None:
    """The other half: the flags are only worth anything if they land."""
    path = setup.save_credential(
        _verified(),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
    )

    mode = stat.S_IMODE(path.stat().st_mode)

    assert mode == 0o600, oct(mode)


def test_saving_twice_replaces_rather_than_refusing(tmp_path: Path) -> None:
    """A rotated password has to be able to land on top of the old one."""
    for password in ("first-password", "second-password"):
        setup.save_credential(
            setup.Verified(account="YULON_AB12CD34", password=password),
            game="wow-wotlk",
            install_id="ab12cd34",
            host="127.0.0.1",
            port=7878,
            namespace="urn:AC",
            config_dir=tmp_path,
        )

    endpoint = setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path)
    assert endpoint is not None and endpoint.password == "second-password"


def test_the_password_never_reaches_a_log_while_being_saved(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        setup.save_credential(
            _verified(),
            game="wow-wotlk",
            install_id="ab12cd34",
            host="127.0.0.1",
            port=7878,
            namespace="urn:AC",
            config_dir=tmp_path,
        )
        setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path)

    assert PASSWORD not in caplog.text


def test_the_file_carries_no_more_than_it_must(tmp_path: Path) -> None:
    """A credential file is not a place to accumulate install facts.

    `verified_at` earns its place by being unavailable anywhere else: the file
    IS the record that a round trip answered, so the moment it answered has no
    other home, and without it the tab cannot tell a channel proved a minute
    ago from one proved in March.
    """
    path = setup.save_credential(
        _verified(),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
    )

    written = json.loads(path.read_text(encoding="utf-8"))

    assert set(written) == {"account", "password", "namespace", "host", "port", "verified_at"}
    # `namespace` joined the file in 8.2c: the app overrides it from the entry
    # on the way out, but a file that records a fact should record the true
    # one -- the gate on m910q read this file, sent `urn:AC` and got HTTP 500
    # from a channel that had just verified.


# -- the whole path ---------------------------------------------------------


class _Channel:
    """A channel that answers from a script, recording what it was asked."""

    def __init__(self, *answers: channel.Answer) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def send(self, command: str) -> channel.Answer:
        self.asked.append(command)
        return self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]


def test_a_failed_verification_writes_no_credential_file(tmp_path: Path) -> None:
    """The architecture's rule for this module, end to end rather than by type.

    An account row may well exist by now — that is what `Pending` is for — but
    nothing about it is written down until a round trip has answered.
    """
    created: list[tuple[str, str, int]] = []

    state = setup.ensure(
        account="YULON_AB12CD34",
        password="p@ssw0rd12345678",
        create=lambda name, pw, level: created.append((name, pw, level)),
        channel=_Channel(channel.Answer("unknown", reason="nothing is listening")),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
    )

    assert created, "the account was never created, so this proves nothing about persisting"
    assert isinstance(state, setup.Pending)
    assert setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is None


def test_a_verified_round_trip_creates_the_account_once_and_saves_it(tmp_path: Path) -> None:
    created: list[tuple[str, str, int]] = []
    talker = _Channel(channel.Answer("yes", "Players online: 0."))

    state = setup.ensure(
        account="YULON_AB12CD34",
        password="p@ssw0rd12345678",
        create=lambda name, pw, level: created.append((name, pw, level)),
        channel=talker,
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
    )

    assert isinstance(state, setup.Verified)
    assert len(created) == 1
    assert created[0][2] == 3, "the account must be made at the level SOAP requires"
    assert talker.asked == ["server info"], "verification must be a real round trip"
    assert setup.load_credential("wow-wotlk", "ab12cd34", config_dir=tmp_path) is not None


def test_resuming_from_pending_re_verifies_and_never_creates_again(tmp_path: Path) -> None:
    """The latch, proved through the door a caller actually uses."""
    created: list[tuple[str, str, int]] = []
    pending = setup.Pending(account="YULON_AB12CD34", password="p@ssw0rd12345678", tries=1)

    state = setup.ensure(
        account="YULON_AB12CD34",
        password="p@ssw0rd12345678",
        create=lambda name, pw, level: created.append((name, pw, level)),
        channel=_Channel(channel.Answer("yes", "ok")),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path,
        state=pending,
    )

    assert isinstance(state, setup.Verified)
    assert created == [], "a resume created a second account"
