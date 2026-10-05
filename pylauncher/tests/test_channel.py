"""Tests for `yulon.channel` — one command, one typed answer (8.2a).

The seam every feature above it calls. It holds no command text and no UI; what
it owns is the three-outcome answer, the per-install lock, and turning a
transport failure into a reason a person can act on.
"""

from __future__ import annotations

import threading
import time

from yulon import channel, docker, soap


def _state(status: str = "running", restarts: int = 0) -> docker.ContainerState:
    return docker.ContainerState(status, "2026-09-07T10:00:00.000000000Z", restarts)


class _Wire:
    """A stand-in for `soap.execute`, recording what it was asked."""

    def __init__(self, *replies: soap.Reply) -> None:
        self.replies = list(replies)
        self.asked: list[str] = []

    def __call__(self, endpoint: soap.Endpoint, command: str, *, timeout: float = 0) -> soap.Reply:
        self.asked.append(command)
        return self.replies[min(len(self.asked) - 1, len(self.replies) - 1)]


def _channel(wire: _Wire, state: docker.ContainerState | None = None) -> channel.SoapChannel:
    return channel.SoapChannel(
        endpoint=soap.Endpoint(host="127.0.0.1", port=7878, account="YULON_AB", password="pw"),
        state_of=lambda: state if state is not None else _state(),
        send=wire,
    )


# -- the three outcomes -----------------------------------------------------


def test_a_command_the_server_ran_is_a_yes_carrying_what_it_printed() -> None:
    answer = _channel(_Wire(soap.Reply("answered", "Players online: 3."))).send("server info")

    assert answer.outcome == "yes"
    assert answer.text == "Players online: 3."
    assert answer.indeterminate is False


def test_a_command_the_server_refused_is_a_no_and_not_a_could_not_ask() -> None:
    """The server was reached and answered; the command is what failed."""
    answer = _channel(_Wire(soap.Reply("refused", "Account cannot be created."))).send("x")

    assert answer.outcome == "no"
    assert answer.text == "Account cannot be created."


def test_a_timeout_is_could_not_ask_and_is_marked_indeterminate() -> None:
    """It may have run. That is the whole difficulty and it is carried, not hidden."""
    answer = _channel(_Wire(soap.Reply("timeout", "no answer within 20s"))).send("account create x")

    assert answer.outcome == "unknown"
    assert answer.indeterminate is True


def test_a_timeout_is_never_retried_because_the_command_may_have_run() -> None:
    """Proved with a wire that answers differently the second time.

    A retry that succeeded would report "done" for a command that ran twice —
    two accounts, two mails, two of whatever it was. The seam asks once.
    """
    wire = _Wire(soap.Reply("timeout", "no answer"), soap.Reply("answered", "Account created."))

    answer = _channel(wire).send("account create bob pw")

    assert wire.asked == ["account create bob pw"], "it asked a second time"
    assert answer.outcome == "unknown"


# -- why, when it could not ask ---------------------------------------------


def test_an_unreachable_channel_says_the_world_is_not_running_when_it_is_not() -> None:
    """The reason comes from container state, so the user is told what to do."""
    wire = _Wire(soap.Reply("unreachable", "connection refused"))

    answer = _channel(wire, _state(status="exited")).send("server info")

    assert answer.outcome == "unknown"
    assert "not running" in answer.reason


def test_an_unreachable_channel_says_restart_loop_when_that_is_what_it_is() -> None:
    wire = _Wire(soap.Reply("unreachable", "connection refused"))

    answer = _channel(wire, _state(status="restarting", restarts=9)).send("server info")

    assert "restart" in answer.reason


def test_a_silent_port_on_a_running_world_names_the_misconfiguration() -> None:
    """The spike's finding, turned into a sentence.

    A listener on the container's own loopback leaves Docker accepting and
    relaying nothing, so a timeout against a world that IS running and settled
    is not a slow command — it is a bind nobody can reach.
    """
    wire = _Wire(soap.Reply("timeout", "no answer within 20s"))

    answer = _channel(wire, _state(status="running")).send("server info")

    assert answer.outcome == "unknown"
    assert "listening" in answer.reason or "bound" in answer.reason


def test_bad_credentials_say_so_rather_than_blaming_the_server() -> None:
    answer = _channel(_Wire(soap.Reply("unauthorised", "no"))).send("server info")

    assert answer.outcome == "unknown"
    assert "password" in answer.reason or "account" in answer.reason


def test_an_account_below_administrator_gets_its_own_sentence() -> None:
    """Different repair from a wrong password, so a different sentence."""
    answer = _channel(_Wire(soap.Reply("forbidden", "no"))).send("server info")

    assert "level" in answer.reason


# -- the lock ---------------------------------------------------------------


def test_two_commands_on_one_install_do_not_overlap() -> None:
    """The listener runs on the single world thread.

    The prior art serialises for exactly this reason, and even the bash CLI took
    a lock file (`launcher/src-tauri/src/lib.rs:117-123`).
    """
    overlaps: list[bool] = []
    inside = threading.Event()

    class _Slow(_Wire):
        def __call__(self, endpoint, command, *, timeout=0):  # noqa: ANN001, ANN204
            overlaps.append(inside.is_set())
            inside.set()
            time.sleep(0.05)
            inside.clear()
            return soap.Reply("answered", "ok")

    one = _channel(_Slow())
    threads = [threading.Thread(target=lambda: one.send("server info")) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert overlaps == [False, False, False, False], "two commands were in flight at once"


def test_two_installs_do_not_wait_on_each_other() -> None:
    """The lock is per install, not global: two servers are two world threads."""
    first, second = _channel(_Wire(soap.Reply("answered", "a"))), _channel(
        _Wire(soap.Reply("answered", "b"))
    )

    assert first.lock is not second.lock


# -- what "the server said no to this credential" means (review, 2026-09-07) --


def test_only_a_rejected_credential_is_marked_denied() -> None:
    """The one outcome that justifies resetting an account's password.

    Every transport failure and every refusal arrives as `unknown`, so a caller
    reading the outcome alone cannot tell "your password is wrong" from "the
    server is not there" -- and 8.2a's repair, offered on the strength of that
    distinction, rotates a GM account's password. An adversarial review found
    it: opening the tab against a stopped server offered to reset a working
    credential.
    """
    for outcome, denied in (
        ("unauthorised", True),
        ("forbidden", False),
        ("unreachable", False),
        ("timeout", False),
        ("unreadable", False),
        ("answered", False),
        ("refused", False),
    ):
        answer = _channel(_Wire(soap.Reply(outcome, "text", 0))).send("server info")
        assert answer.denied is denied, f"{outcome} should be denied={denied}"


def test_a_level_too_low_is_not_denied_because_a_new_password_would_not_fix_it() -> None:
    """`forbidden` is a real refusal of this credential, and repair is wrong for it.

    The account exists and its password is right; what is wrong is its GM
    level. Rotating the password would burn a rotation and change nothing.
    """
    answer = _channel(_Wire(soap.Reply("forbidden", "", 403))).send("server info")

    assert answer.denied is False
    assert "GM level" in answer.reason


# -- 8.2e: the transport for a core with no SOAP -----------------------------


class _Console:
    """Stands in for a game package's `console.send`."""

    def __init__(self, reply: object = None, raises: Exception | None = None) -> None:
        self.reply = reply
        self.raises = raises
        self.sent: list[str] = []

    def __call__(self, command: str, **_kwargs: object) -> object:
        self.sent.append(command)
        if self.raises is not None:
            raise self.raises
        return self.reply


def _reply(lines: tuple[str, ...], *, prompted: bool = True) -> object:
    return type(
        "ConsoleReply", (), {"command": "server info", "lines": lines, "prompted": prompted}
    )()


def test_an_answer_delimited_by_the_prompt_is_an_answer() -> None:
    """The ordinary case: the console printed a prompt, so what is between them is the reply."""
    console = _Console(_reply(("Tortoise 1.18.1", "Online players: 0")))

    answer = channel.AttachChannel(send=console).send("server info")

    assert answer.outcome == "yes"
    assert "Online players: 0" in answer.text
    assert answer.indeterminate is False
    assert console.sent == ["server info"]


def test_a_window_with_no_prompt_is_could_not_ask_and_never_failure() -> None:
    """The clause this box is built on, and the reason this core gets no mutations yet.

    `prompted=False` means nothing in the window was delimited, and the
    transport cannot tell WHY: `docker attach` failing before it reached a
    console looks the same as a worldserver that is up and still loading maps.
    Either way the command may have been typed and may have run, so the honest
    answer is "I could not ask", carrying `indeterminate` -- never "it failed",
    which would invite a caller to do it again.
    """
    console = _Console(_reply(("Loading maps...",), prompted=False))

    answer = channel.AttachChannel(send=console).send("server info")

    assert answer.outcome == "unknown"
    assert answer.indeterminate is True, "an undelimited window may still have run the command"
    assert answer.denied is False, "there is no credential on this transport to deny"
    assert "prompt" in answer.reason.lower(), answer.reason


def test_a_console_that_could_not_be_reached_did_not_run_anything() -> None:
    """The other half, and it is the half that is NOT indeterminate.

    A `ConsoleError` is raised before anything is typed -- no pty on this host,
    no docker CLI, `docker attach` refusing to start. The command did not run,
    so saying it might have would be as wrong as saying it failed.
    """
    from yulon.controller_wow_wotlk.console import ConsoleError

    console = _Console(raises=ConsoleError("this host cannot type at a console"))

    answer = channel.AttachChannel(send=console).send("server info")

    assert answer.outcome == "unknown"
    assert answer.indeterminate is False, "nothing was typed, so nothing may have run"
    assert "cannot type at a console" in answer.reason


# -- 8.3b: a refusal that arrives as silence --------------------------------


def test_a_server_that_hangs_up_may_have_run_the_command_and_says_so() -> None:
    """CMaNGOS refuses by hanging up, so silence cannot mean "not received".

    Measured at the wire on the live TBC server (2026-09-07): `server info`
    came back as 951 bytes of HTTP 200, and `account set`, `blargh` and
    `account characters GATE83B` each came back as zero bytes with the
    connection closed. The core hands a failed command to `soap_sender_fault`
    (`MaNGOSsoap.cpp:133`) and nothing reaches the client.

    Two things follow, and they pull in opposite directions:

    it is NOT a dead channel -- the old sentence, "nothing is listening on the
    command channel, it may not be turned on for this install yet", is simply
    false, and sends a person to configure something that is working;

    it is NOT a plain no either -- 8.3b measured a password change that the
    server performed and then reported as a failure, so a command that hung up
    may well have run. That is exactly `indeterminate`.
    """
    answer = _channel(_Wire(soap.Reply("silent", "127.0.0.1:7878 took the command"))).send(
        "account set password bob a b"
    )

    assert answer.outcome == "unknown"
    assert answer.indeterminate is True, "a hung-up command may still have run"
    assert "may have" in answer.reason, answer.reason
    assert "not be turned on" not in answer.reason, "the channel answered a moment ago"


def test_the_reason_for_silence_does_not_depend_on_docker_being_askable() -> None:
    """The connection is the evidence, and it is better evidence than the state.

    Whatever docker says about the container, THIS app just completed a TCP
    connection to the command channel and sent a whole request into it. A reason
    built from the container state would answer "nothing is known about it" for
    a machine we had just spoken to.
    """
    answer = _channel(_Wire(soap.Reply("silent", "took the command")), _state(status="")).send(
        "account set password bob a b"
    )

    assert answer.outcome == "unknown"
    assert answer.indeterminate is True
    assert "nothing is known" not in answer.reason, answer.reason


# -- T226: a command that ran and printed nothing ----------------------------


def test_a_command_that_ran_and_printed_nothing_is_a_yes_end_to_end() -> None:
    """The real wire, the real channel and the real verb answer, one empty `<result/>`.

    Measured on the Centurion proof (yulon-ubuntu2, 2026-10-04): `revive Joradan`
    came back HTTP 200 with an empty result, the server had run it, and Yu'lon
    said "nothing is listening on the command channel".
    """
    import http.server

    from yulon import actions

    body = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        b'xmlns:ns1="urn:AC"><SOAP-ENV:Body>'
        b"<ns1:executeCommandResponse><result/></ns1:executeCommandResponse>"
        b"</SOAP-ENV:Body></SOAP-ENV:Envelope>\n"
    )

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib spells it this way
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            """Quiet."""

    def _no_state() -> docker.ContainerState:
        raise AssertionError("the server answered; docker has nothing to add")

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        live = channel.SoapChannel(
            endpoint=soap.Endpoint(
                host="127.0.0.1", port=httpd.server_address[1], account="YULON_AB", password="pw"
            ),
            state_of=_no_state,
        )
        answer = live.send("revive Joradan")
        outcome = actions.send(live, "revive Joradan")
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert answer.outcome == "yes", answer
    assert answer.text == "" and answer.reason == ""
    assert outcome.done is True, outcome
    assert "listening" not in outcome.problem


def test_a_reply_nobody_can_read_never_says_nothing_is_listening() -> None:
    """Something answered over HTTP, so the channel is not off (T226).

    The sentence for a closed port ("nothing is listening ... it may not be
    turned on") is false for a server that sent a whole reply back; and since
    it answered, the command may have run, so it must not be retried blindly.
    """

    def _no_state() -> docker.ContainerState:
        raise AssertionError("the server answered; docker has nothing to add")

    live = channel.SoapChannel(
        endpoint=soap.Endpoint(host="127.0.0.1", port=7878, account="YULON_AB", password="pw"),
        state_of=_no_state,
        send=_Wire(soap.Reply("unreadable", "<html>a proxy got in the way</html>", 200)),
    )

    answer = live.send("revive Joradan")

    assert answer.outcome == "unknown"
    assert "listening" not in answer.reason, answer.reason
    assert "could not be read" in answer.reason, answer.reason
    assert answer.indeterminate is True


def test_a_closed_port_on_a_running_server_still_says_nothing_is_listening() -> None:
    """The neighbour of the rule above: the sentence stays where it is true."""
    answer = _channel(_Wire(soap.Reply("unreachable", "connection refused")), _state()).send("x")

    assert "nothing is listening" in answer.reason, answer.reason
    assert answer.indeterminate is False
