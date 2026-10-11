"""A proving try is spent only when the world answered (T723).

Measured on yulon-ubuntu2: a WotLK install leaves SOAP off, the post-install
settles asked a port nothing listened on and each refused or reset ask spent
one of `MAX_VERIFY_TRIES`; "Turn on the command channel" did not give them
back, and the Start's first ask (before the world listened) gave up.

Everything here goes through the real `InstallChannel`, the real `SoapChannel`
and the real `soap.execute` over a real local socket: a closed port (refused),
a listener that accepts and resets, and a tiny HTTP server that answers 401 or
200 under `urn:AC`. The only fakes are the auth table the account is made in
and the port the endpoint is pointed at (the entry's own is 7878).
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import socket
import struct
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from yulon import channel as channel_module
from yulon import channel_setup as setup
from yulon import docker, resources, soap
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
INSTALL = "ab12cd34"
ACCOUNT = setup.account_name(INSTALL)

_OK = (
    '<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
    ' xmlns:ns1="urn:AC"><SOAP-ENV:Body><ns1:executeCommandResponse><result>Connected players: 0.'
    "</result></ns1:executeCommandResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
)


class _Auth:
    """The auth table: `create` keeps the first password of a row, as the app's does."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.creates: list[str] = []

    def create(self, name: str, password: str, _level: int) -> None:
        self.creates.append(name)
        self.rows.setdefault(name, password)

    def reset(self, name: str, password: str) -> None:
        if name in self.rows:
            self.rows[name] = password


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def _reset_listener(port: int) -> Iterator[None]:
    """Accepts the connection and resets it: docker-proxy in front of a world with SOAP off."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(8)
    stop = threading.Event()

    def run() -> None:
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conn.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        server.close()


@contextlib.contextmanager
def _http_world(port: int, auth: _Auth, *, accept: bool, blank: bool = False) -> Iterator[None]:
    """A SOAP listener: 401 for a wrong pair (or always, `accept=False`), else 200 under urn:AC.

    `blank` answers 200 with no result: the world replied, and it is not a proof.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's name
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            header = self.headers.get("Authorization", "")
            pair = base64.b64decode(header.removeprefix("Basic ")).decode()
            name, _, password = pair.partition(":")
            if blank:
                body = b"<x/>"
                self.send_response(200)
            elif accept and auth.rows.get(name) == password:
                body = _OK.encode()
                self.send_response(200)
            else:
                body = b"<x/>"
                self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def _launch(tmp_path: Path, auth: _Auth, port: int) -> setup.InstallChannel:
    server_dir = tmp_path / "server"
    if not server_dir.is_dir():
        server_dir.mkdir()
        plan = composegen.render(WOTLK, server_dir, templates_root=resources.installers_dir())
        composegen.write_plan(plan, server_dir)
    return setup.InstallChannel(
        WOTLK,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=auth.create,
        reset=auth.reset,
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=dataclasses.replace(endpoint, port=port),
            state_of=lambda: docker.ContainerState(status="running"),
            send=lambda ep, cmd, *, timeout: soap.execute(ep, cmd, timeout=2.0),
        ),
        config_dir=tmp_path / "config",
    )


def _tries(state: setup.State) -> int:
    assert isinstance(state, setup.Pending), state
    return state.tries


def test_asks_into_a_closed_port_spend_no_try(tmp_path: Path) -> None:
    port = _free_port()
    channel = _launch(tmp_path, _Auth(), port)
    for _ in range(setup.MAX_VERIFY_TRIES + 2):
        assert _tries(channel.settle()) == 0


def test_asks_a_listener_resets_spend_no_try(tmp_path: Path) -> None:
    port = _free_port()
    channel = _launch(tmp_path, _Auth(), port)
    with _reset_listener(port):
        for _ in range(setup.MAX_VERIFY_TRIES + 2):
            assert _tries(channel.settle()) == 0


def test_an_http_reply_that_is_not_a_proof_still_spends_its_try(tmp_path: Path) -> None:
    port = _free_port()
    channel = _launch(tmp_path, _Auth(), port)
    with _http_world(port, _Auth(), accept=False, blank=True):
        assert _tries(channel.settle()) == 1
        assert _tries(channel.settle()) == 2
        assert isinstance(channel.settle(), setup.GaveUp)


def test_a_401_is_the_world_answering_so_it_is_refused_not_silence(tmp_path: Path) -> None:
    port = _free_port()
    channel = _launch(tmp_path, _Auth(), port)
    with _http_world(port, _Auth(), accept=False):
        assert isinstance(channel.settle(), setup.Refused)


def test_turn_on_gives_a_waiting_row_its_tries_back(tmp_path: Path) -> None:
    port = _free_port()
    channel = _launch(tmp_path, _Auth(), port)
    with _http_world(port, _Auth(), accept=False, blank=True):
        channel.settle()
        assert _tries(channel.settle()) == 2
    channel.enable(world_running=False)
    assert _tries(channel.setup_state()) == 0


def test_install_off_then_turn_on_then_a_late_start_is_verified(tmp_path: Path) -> None:
    port = _free_port()
    auth = _Auth()
    channel = _launch(tmp_path, auth, port)
    # the post-install settles, SOAP off: refused, then reset
    assert _tries(channel.settle()) == 0
    with _reset_listener(port):
        assert _tries(channel.settle()) == 0
    channel.enable(world_running=False)
    # the Start's first asks land before the world listens
    assert _tries(channel.settle()) == 0
    assert _tries(channel.settle()) == 0
    with _http_world(port, auth, accept=True):
        state = channel.settle()
    assert isinstance(state, setup.Verified), state
    assert auth.creates == [ACCOUNT]
