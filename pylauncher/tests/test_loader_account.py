"""Tests for the accounts Play makes on a LandSandBoat server (`yulon.loader_account`).

The server is an `ask` fake answering what LandSandBoat answers (`login_result`), and
the config dir a folder under `tmp_path`, so every case is a request and a file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yulon import loader_account
from yulon.catalog.catalog import LoaderLogin, SingleEntry, load_catalog
from yulon.loader_account import AccountRefusal, ensure

FFXI: SingleEntry = load_catalog().get_single("ffxi")
assert FFXI.play is not None and FFXI.play.loader is not None
assert FFXI.play.loader.login is not None
LOGIN: LoaderLogin = FFXI.play.loader.login
HOST, PORT = "127.0.0.1", 54231


class Server:
    """A fake auth port: records each request, answers with the next result."""

    def __init__(self, *results: int | Exception, config_dir: Path | None = None) -> None:
        self.results = list(results)
        self.requests: list[dict[str, Any]] = []
        self.kept_when_asked: list[str | None] = []
        self.config_dir = config_dir

    def __call__(self, host: str, port: int, payload: bytes) -> bytes:
        assert (host, port) == (HOST, PORT)
        request = json.loads(payload)
        self.requests.append(request)
        if self.config_dir is not None:
            self.kept_when_asked.append(
                loader_account.recall("ffxi", HOST, request["username"], config_dir=self.config_dir)
            )
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        # The server pads its answer with NULs (`sendJsonAsBuffer`).
        return json.dumps({"result": result}).encode() + b"\0" * 32


def _ensure(tmp_path: Path, server: Server, name: str = "Shantotto") -> str:
    server.config_dir = tmp_path
    return ensure(
        "ffxi",
        LOGIN,
        host=HOST,
        port=PORT,
        name=name,
        loader="xiloader",
        config_dir=tmp_path,
        ask=server,
        choice=lambda chars: chars[0],
    )


def _kept(tmp_path: Path, name: str = "Shantotto", server: str = HOST) -> str | None:
    return loader_account.recall("ffxi", server, name, config_dir=tmp_path)


def test_a_name_is_three_to_fifteen_letters_or_digits() -> None:
    assert loader_account.check_name(LOGIN, "Shantotto") is None
    assert loader_account.check_name(LOGIN, "abc") is None
    for bad in ("ab", "a" * 16, "two words", "under_score", "naïve", ""):
        assert loader_account.check_name(LOGIN, bad) is not None, bad


def test_a_password_is_letters_and_digits_at_the_catalogs_length() -> None:
    password = loader_account.make_password(LOGIN.password_length)
    assert len(password) == 24 and password.isascii() and password.isalnum()


def test_the_request_is_xiloaders_own_create(tmp_path: Path) -> None:
    server = Server(loader_account.CREATED)
    password = _ensure(tmp_path, server)
    assert server.requests == [
        {"command": 0x20, "username": "Shantotto", "password": password, "version": [2, 2, 0]}
    ]


def test_a_new_account_is_kept_before_it_is_asked_for(tmp_path: Path) -> None:
    server = Server(loader_account.CREATED)
    password = _ensure(tmp_path, server)
    assert password == "a" * 24
    assert server.kept_when_asked == [password], "kept before the server could make it"
    assert _kept(tmp_path) == password


def test_a_kept_account_is_asked_again_and_taken_means_it_is_there(tmp_path: Path) -> None:
    loader_account.remember("ffxi", HOST, "Shantotto", "kept-one", config_dir=tmp_path)
    server = Server(loader_account.TAKEN)
    assert _ensure(tmp_path, server) == "kept-one"
    assert server.requests[0]["password"] == "kept-one"


def test_a_database_made_again_gets_the_kept_account_back(tmp_path: Path) -> None:
    loader_account.remember("ffxi", HOST, "Shantotto", "kept-one", config_dir=tmp_path)
    assert _ensure(tmp_path, Server(loader_account.CREATED)) == "kept-one"


def test_a_name_someone_else_made_is_refused_and_nothing_is_kept(tmp_path: Path) -> None:
    with pytest.raises(AccountRefusal, match="Yu'lon did not make") as caught:
        _ensure(tmp_path, Server(loader_account.TAKEN))
    assert "xiloader's own prompt" in str(caught.value)
    assert _kept(tmp_path) is None


def test_a_server_that_does_not_answer_says_press_start(tmp_path: Path) -> None:
    with pytest.raises(AccountRefusal, match="Press Start"):
        _ensure(tmp_path, Server(ConnectionRefusedError(10061, "refused")))
    assert _kept(tmp_path) is None


def test_a_kept_account_survives_a_server_that_does_not_answer(tmp_path: Path) -> None:
    loader_account.remember("ffxi", HOST, "Shantotto", "kept-one", config_dir=tmp_path)
    with pytest.raises(AccountRefusal):
        _ensure(tmp_path, Server(TimeoutError("timed out")))
    assert _kept(tmp_path) == "kept-one"


@pytest.mark.parametrize(
    ("result", "said"),
    [
        (loader_account.CREATE_DISABLED, "does not make new accounts"),
        (loader_account.VERSION_UNSUPPORTED, "another xiloader than 2.2.0"),
        (loader_account.CREATE_FAILED, "it answered 9"),
    ],
)
def test_every_other_refusal_says_what_the_server_said(
    tmp_path: Path, result: int, said: str
) -> None:
    with pytest.raises(AccountRefusal, match=said):
        _ensure(tmp_path, Server(result))
    assert _kept(tmp_path) is None


def test_an_answer_that_is_not_a_result_is_no_answer(tmp_path: Path) -> None:
    def garbage(_host: str, _port: int, _payload: bytes) -> bytes:
        return b"\x02"

    with pytest.raises(AccountRefusal, match="Nothing answered"):
        ensure(
            "ffxi",
            LOGIN,
            host=HOST,
            port=PORT,
            name="Shantotto",
            loader="xiloader",
            config_dir=tmp_path,
            ask=garbage,
        )


def test_names_are_one_account_in_any_case_and_per_server(tmp_path: Path) -> None:
    loader_account.remember("ffxi", HOST, "Shantotto", "one", config_dir=tmp_path)
    loader_account.remember("ffxi", "192.168.0.60", "Shantotto", "two", config_dir=tmp_path)
    assert _kept(tmp_path, "SHANTOTTO") == "one"
    assert _kept(tmp_path, "shantotto", server="192.168.0.60") == "two"
    loader_account.remember("ffxi", HOST, "shantotto", "three", config_dir=tmp_path)
    path = loader_account.accounts_path("ffxi", config_dir=tmp_path)
    assert len(json.loads(path.read_text(encoding="utf-8"))["accounts"]) == 2
    assert _kept(tmp_path) == "three"


def test_an_unreadable_kept_file_is_nothing_kept(tmp_path: Path) -> None:
    path = loader_account.accounts_path("ffxi", config_dir=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert _kept(tmp_path) is None


def test_the_login_file_is_the_catalogs_with_the_account_filled_in(tmp_path: Path) -> None:
    path = loader_account.write_login_file(
        "ffxi", LOGIN, name="Shantotto", password="s3cret", config_dir=tmp_path
    )
    assert path == tmp_path / "accounts" / "ffxi-login.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "username": "Shantotto",
        "password": "s3cret",
        "hide": True,
    }
    assert not path.with_name(path.name + ".partial").exists()


def test_secrets_are_created_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: list[int] = []
    real_open = loader_account.os.open

    def recording_open(path: Any, flags: int, mode: int = 0o777) -> int:
        modes.append(mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(loader_account.os, "open", recording_open)
    loader_account.remember("ffxi", HOST, "Shantotto", "one", config_dir=tmp_path)
    loader_account.write_login_file(
        "ffxi", LOGIN, name="Shantotto", password="one", config_dir=tmp_path
    )
    assert modes == [0o600, 0o600]
