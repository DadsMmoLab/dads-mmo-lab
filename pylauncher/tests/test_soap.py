"""Tests for `yulon.soap` — one command envelope over the standard library (8.2a).

Driven against a real `http.server` on 127.0.0.1 rather than a mock: this module
exists to speak HTTP to something, and the failures worth catching are wire
failures — a 401, a fault body, a socket that accepts and says nothing. A mock
would confirm the code calls the functions it calls.

The envelope shape is `ns1__executeCommand(soap*, char* command, char** result)`
(`cmangos.md:122` names the same symbol on that family) in namespace `urn:AC`,
which is what the Hypeer launcher's working client posts to
`http://127.0.0.1:7878/` (`hypeer.md:48`, `soap.rs:127-132`). That is prior art
that runs, not a derivation, and 8.2a's gate is where this client's own
round-trip confirms it against a real worldserver.
"""

from __future__ import annotations

import http.server
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from yulon import soap

ACCOUNT = "YULON_ab12"
PASSWORD = "a-password-that-must-never-be-logged"

_OK = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ns1="urn:AC">
 <SOAP-ENV:Body>
  <ns1:executeCommandResponse><result>{body}</result></ns1:executeCommandResponse>
 </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
"""

_FAULT = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
 <SOAP-ENV:Body>
  <SOAP-ENV:Fault>
   <faultcode>SOAP-ENV:Client</faultcode>
   <faultstring>{body}</faultstring>
   <detail>{body}</detail>
  </SOAP-ENV:Fault>
 </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
"""


@contextmanager
def _server(handler_for: object) -> Iterator[soap.Endpoint]:
    """A one-request-at-a-time HTTP server on a port the OS picks."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib spells it this way
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            handler_for(self, body)  # type: ignore[operator]

        def log_message(self, *_args: object) -> None:
            """Silence the stdlib's stderr logging during tests."""

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield soap.Endpoint(
            host="127.0.0.1", port=httpd.server_address[1], account=ACCOUNT, password=PASSWORD
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


def _respond(handler: object, status: int, body: str) -> None:
    handler.send_response(status)  # type: ignore[attr-defined]
    handler.send_header("Content-Type", "text/xml; charset=utf-8")  # type: ignore[attr-defined]
    handler.send_header("Content-Length", str(len(body.encode())))  # type: ignore[attr-defined]
    handler.end_headers()  # type: ignore[attr-defined]
    handler.wfile.write(body.encode())  # type: ignore[attr-defined]


# -- what the server says ---------------------------------------------------


def test_a_command_that_succeeds_comes_back_as_its_print_buffer() -> None:
    """The accumulated console output IS the result string (`ACSoap.cpp:133-137`)."""
    seen: list[str] = []

    def handle(handler: object, body: str) -> None:
        seen.append(body)
        _respond(handler, 200, _OK.format(body="Account created."))

    with _server(handle) as endpoint:
        reply = soap.execute(endpoint, "account create bob hunter2")

    assert reply.outcome == "answered"
    assert reply.text == "Account created."
    assert "executeCommand" in seen[0]
    assert "urn:AC" in seen[0]
    assert "account create bob hunter2" in seen[0]


def test_a_command_the_server_refused_comes_back_as_a_fault_not_as_success() -> None:
    """Failure is a SOAP sender fault carrying the same buffer (`ACSoap.cpp:139-140`)."""

    def handle(handler: object, body: str) -> None:
        _respond(handler, 500, _FAULT.format(body="Account cannot be created."))

    with _server(handle) as endpoint:
        reply = soap.execute(endpoint, "account create bob hunter2")

    assert reply.outcome == "refused"
    assert reply.text == "Account cannot be created."


def test_wrong_credentials_are_told_apart_from_a_command_that_failed() -> None:
    """401 is about US, not about the command — and the repair path keys off it."""

    def handle(handler: object, body: str) -> None:
        _respond(handler, 401, "")

    with _server(handle) as endpoint:
        reply = soap.execute(endpoint, "server info")

    assert reply.outcome == "unauthorised"


def test_an_account_below_administrator_is_its_own_outcome() -> None:
    """403 means the account exists and its level is too low (`ACSoap.cpp:103-107`).

    Distinct from 401 because the repair is different: one rewrites a password,
    the other raises a GM level.
    """

    def handle(handler: object, body: str) -> None:
        _respond(handler, 403, "")

    with _server(handle) as endpoint:
        reply = soap.execute(endpoint, "server info")

    assert reply.outcome == "forbidden"


def test_a_reply_that_is_not_xml_is_reported_rather_than_parsed_optimistically() -> None:
    def handle(handler: object, body: str) -> None:
        _respond(handler, 200, "<html>a proxy got in the way</html>")

    with _server(handle) as endpoint:
        reply = soap.execute(endpoint, "server info")

    assert reply.outcome == "unreadable"


# -- what the wire does -----------------------------------------------------


def test_nothing_listening_is_could_not_ask_rather_than_a_failed_command() -> None:
    """The three outcomes the spike measured: refused, silent, answering."""
    with _server(lambda *_: None) as endpoint:
        dead = soap.Endpoint(
            host=endpoint.host, port=endpoint.port, account=ACCOUNT, password=PASSWORD
        )
    # the server is closed now, so this port refuses

    reply = soap.execute(dead, "server info")

    assert reply.outcome == "unreachable"
    assert reply.text


def test_a_socket_that_accepts_and_says_nothing_times_out_instead_of_hanging() -> None:
    """The published-port misconfiguration answers exactly like this.

    `gates/8-spikes/published-port-vs-container-loopback/` measured it: a
    listener on the container's own loopback leaves Docker accepting the
    connection and relaying nothing. A client without a timeout waits forever on
    a server that is running perfectly.
    """
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    endpoint = soap.Endpoint(
        host="127.0.0.1", port=listener.getsockname()[1], account=ACCOUNT, password=PASSWORD
    )

    try:
        reply = soap.execute(endpoint, "server info", timeout=0.5)
    finally:
        listener.close()

    assert reply.outcome == "timeout"


# -- the password ------------------------------------------------------------


def test_the_password_never_reaches_a_repr_a_log_or_an_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One test for all three, because one leak is enough.

    The endpoint is handed to worker threads and closed over by the tab's seams;
    a pytest assertion diff, a logged object or a traceback frame dump would each
    print it. `DockerSql.root_password` closed this same channel on 2026-08-23
    and its sibling was missed, which is why it is asserted rather than reviewed.
    """

    def handle(handler: object, body: str) -> None:
        _respond(handler, 500, _FAULT.format(body="no"))

    with caplog.at_level(logging.DEBUG), _server(handle) as endpoint:
        assert PASSWORD not in repr(endpoint)
        reply = soap.execute(endpoint, "server info")
        assert PASSWORD not in repr(reply)

    assert PASSWORD not in caplog.text
    try:
        raise soap.SoapError(f"something went wrong talking to {endpoint}")
    except soap.SoapError as exc:
        assert PASSWORD not in str(exc)


def test_the_command_is_escaped_so_a_bracket_cannot_change_the_envelope() -> None:
    """Command text arrives from a template and a user; XML is not a place to trust it."""
    seen: list[str] = []

    def handle(handler: object, body: str) -> None:
        seen.append(body)
        _respond(handler, 200, _OK.format(body="ok"))

    with _server(handle) as endpoint:
        soap.execute(endpoint, "account create <bob> & 'friends'")

    assert "<bob>" not in seen[0]
    assert "&lt;bob&gt;" in seen[0]
    assert seen[0].count("<ns1:executeCommand>") == 1


def test_the_reply_text_is_unescaped_because_a_person_reads_it() -> None:
    """Measured on `yulon-ubuntu`, 2026-09-07: the server escapes its own output.

    A real `server info` came back as

        AzerothCore rev. 413bea61a85e+ ... (Static)&#xD;
        Connected players: 0. Characters in world: 500.&#xD;

    and that text goes straight to a person -- the gate capture, and the
    console this channel will carry later. `&#xD;` is a carriage return the
    server put there, not something to show.
    """
    body = (
        "<SOAP-ENV:Envelope><SOAP-ENV:Body><ns1:executeCommandResponse>"
        "<result>Connected players: 0.&#xD;&#10;Bots &amp; players &lt;here&gt;</result>"
        "</ns1:executeCommandResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )

    reply = soap._classify(200, body)

    assert reply.outcome == "answered"
    assert "&#xD;" not in reply.text
    assert "&amp;" not in reply.text
    assert reply.text == "Connected players: 0.\r\nBots & players <here>"


def test_a_fault_is_unescaped_for_the_same_reason() -> None:
    body = (
        "<SOAP-ENV:Fault><faultstring>There is no such command &apos;foo&apos;"
        "</faultstring></SOAP-ENV:Fault>"
    )

    reply = soap._classify(200, body)

    assert reply.outcome == "refused"
    assert reply.text == "There is no such command 'foo'"


def test_unescaping_leaves_a_bare_ampersand_alone_rather_than_guessing() -> None:
    """Text the server did not escape is text, not a broken entity."""
    assert soap.unescape("bots & players") == "bots & players"
    assert soap.unescape("100% &amp; rising") == "100% & rising"


def test_the_envelope_carries_this_tree_s_own_namespace() -> None:
    """One wire format, two namespaces, and the server checks it before the password.

    Measured on m910q, 2026-09-07, against a live CMaNGOS TBC worldserver: an
    `urn:AC` envelope comes back HTTP 500 with `method name or namespace not
    recognized` **whether the credential is right or wrong**, because the
    namespace is checked first. So sending the wrong one does not read as a
    rejected credential -- it reads as a server that has not answered, which is
    exactly what a world still loading looks like too.
    """
    for namespace in ("urn:AC", "urn:MaNGOS"):
        body = soap.envelope("server info", namespace=namespace)
        assert f'xmlns:ns1="{namespace}"' in body, body
        assert "<ns1:executeCommand>" in body
        assert "<command>server info</command>" in body


# -- 8.3b: a refusal that arrives as silence --------------------------------


def test_a_server_that_takes_the_command_and_closes_is_not_a_dead_channel() -> None:
    """Measured on the live TBC server, 2026-09-07, at the wire.

    CMaNGOS hands a failed command to `soap_sender_fault` (`MaNGOSsoap.cpp:133`)
    and the client gets NOTHING -- zero bytes, connection closed:

        'server info'                 -> 951 bytes, HTTP 200
        'account set'                 -> 0 bytes
        'blargh'                      -> 0 bytes
        'account characters GATE83B'  -> 0 bytes

    So on that lineage every refusal looks like an outage. Calling it
    `unreachable` makes this app tell a person to turn the command channel on
    while the channel is answering other commands a second later.

    The two are separable, and it is the connect that separates them: a channel
    nobody is listening on refuses the connection, and this one accepts it,
    takes the whole request, and then hangs up.
    """

    def handle(handler: object, _body: str) -> None:
        handler.connection.close()  # type: ignore[attr-defined]

    with _server(handle) as endpoint:
        reply = soap.execute(endpoint, "account set")

    assert reply.outcome == "silent", reply.outcome
    assert "closed" in reply.text


# -- T226: a command that ran and printed nothing ----------------------------

_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ns1="urn:AC">
 <SOAP-ENV:Body>
  <ns1:executeCommandResponse>{result}</ns1:executeCommandResponse>
 </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
"""


def test_a_command_that_ran_and_printed_nothing_is_answered_with_empty_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T226, measured on the Centurion proof (yulon-ubuntu2, 2026-10-04).

    `revive Joradan` for an offline character came back HTTP 200 with
    `<ns1:executeCommandResponse><result/></ns1:executeCommandResponse>`: the
    offline branch prints nothing, and the server had run it (at_login=256, the
    corpse gone). An empty print buffer is still a result.
    """

    def handle(handler: object, body: str) -> None:
        _respond(handler, 200, _EMPTY.format(result="<result/>"))

    with caplog.at_level(logging.WARNING), _server(handle) as endpoint:
        reply = soap.execute(endpoint, "revive Joradan")

    assert reply.outcome == "answered", reply
    assert reply.text == ""
    assert reply.http_status == 200
    assert "neither a result nor a fault" not in caplog.text


@pytest.mark.parametrize("result", ["<result />", '<result xsi:nil="true"/>'])
def test_every_spelling_of_an_empty_result_element_is_an_answer(result: str) -> None:
    """gSOAP may put a space before the slash or an attribute on the element."""
    reply = soap._classify(200, _EMPTY.format(result=result))

    assert reply.outcome == "answered", reply
    assert reply.text == ""


def test_a_result_element_with_an_attribute_still_carries_its_text() -> None:
    reply = soap._classify(
        200, _EMPTY.format(result='<result xsi:type="xsd:string">Done.</result>')
    )

    assert reply.outcome == "answered", reply
    assert reply.text == "Done."


def test_a_fault_beside_an_empty_result_is_still_a_refusal() -> None:
    """Fault before result holds for the empty element too."""
    body = (
        "<SOAP-ENV:Fault><faultstring>Character not found</faultstring></SOAP-ENV:Fault>"
        "<result/>"
    )

    reply = soap._classify(500, body)

    assert reply.outcome == "refused"
    assert reply.text == "Character not found"


@pytest.mark.parametrize("element", ["<results/>", "<resultset/>", "<result-code/>"])
def test_an_element_that_only_starts_with_result_is_not_a_result(element: str) -> None:
    reply = soap._classify(200, _EMPTY.format(result=element))

    assert reply.outcome == "unreadable", reply
