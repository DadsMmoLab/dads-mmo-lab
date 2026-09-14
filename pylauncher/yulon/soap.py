"""One `executeCommand` envelope over the standard library (Phase 8.2a).

The wire and nothing else. This module does not build command text, does not
hold a lock, does not know a game, and never lets a password reach a log, a
`repr` or an exception.

## Where the envelope comes from

Not derived: ported from prior art that runs. The Rust launcher's client
(`rust-main:crates/dml-wow/src/soap.rs`, itself a port of `cli/src/20-soap.sh`)
posts exactly this shape to `http://127.0.0.1:7878/`, and the worldserver's own
symbol is `int ns1__executeCommand(soap*, char* command, char** result)`
(`.notes/phase8-reads/cmangos.md:122` names the same symbol on the CMaNGOS
family). The namespace is `urn:AC`.

**Fault before result.** The prior art checks `<faultstring>` first and
`<result>` second, and the order is load-bearing rather than incidental: the
server sends the same print buffer in both directions (`ACSoap.cpp:133-140`), so
a body carrying a fault also carries text a result-first reader would report as
success.

## Two places this deliberately differs from the prior art, and why

* **A 403 is its own outcome.** The Rust client knows only 401, so a
  gmlevel-too-low answer falls through to "the command failed" — and the two
  need different repairs: one rewrites a password, the other raises a level.
  AzerothCore returns 403 when the account is below `SEC_ADMINISTRATOR`
  (`ACSoap.cpp:103-107`), so that is answered as `forbidden`.
* **A body that is neither fault nor result is `unreadable`, not a failure.**
  The prior art reports the whole body as a fault, which for an HTML error page
  from something in the way reads as "your command failed: <html>". This phase
  types every answer as yes, no, or could-not-ask, and a reply nobody can parse
  is the third of those.

What is kept from the prior art without change: the escape order (`&` before
`<` and `>`, so the entities' own ampersand is not re-escaped) and extracting
the reply text **raw**, without entity decoding — the text is evidence, and a
decoder is another thing that can be wrong between the server and the capture.

## Why `http.client` and not `urllib.request`

Two reasons, and the first was found by this package's own guard.
`test_download.py` insists every `urlopen` in the package is handed a verified
TLS context, because three bare ones once inherited OpenSSL's snapshot of the
Windows root store and failed on a fresh box. That rule is right and this call
cannot satisfy it honestly: it is `http://` to a loopback port, where there is
no certificate to verify, so passing a context would be pantomime.

The second reason is better. `urllib.request` honours `http_proxy` and friends,
so a user with a proxy configured would have a **loopback** SOAP call routed
through it. `http.client.HTTPConnection` talks to the host it is given.
"""

from __future__ import annotations

import base64
import http.client
import re
from dataclasses import dataclass, field
from typing import Literal

from yulon.log import get_logger

logger = get_logger(__name__)

Outcome = Literal[
    "answered",
    "refused",
    "unauthorised",
    "forbidden",
    "unreadable",
    "unreachable",
    "silent",
    "timeout",
]
"""What one round trip came back as.

`unreachable` and `silent` are two different machines. Nobody is listening on
the first; the second accepted the connection, read the whole request and hung
up without a byte of HTTP -- which on CMaNGOS is what EVERY refused command
looks like (measured at the wire on the live TBC server, 2026-09-07:
`server info` 951 bytes, `account set` and `blargh` 0 bytes).
"""

DEFAULT_TIMEOUT = 20.0
"""Seconds for one round trip, and it is a bound rather than a guess.

The command runs on the world thread and this call blocks until it finishes
(`ACSoap.cpp:120-130`), so a busy world can be slow — but the failure this
protects against is not slowness. A listener bound to the container's own
loopback leaves Docker accepting the connection and relaying nothing
(`.notes/gates/8-spikes/published-port-vs-container-loopback/`), so a client
without a timeout waits forever against a server that is running perfectly.
"""


class SoapError(RuntimeError):
    """Something went wrong on the wire. Never carries a password."""


@dataclass(frozen=True)
class Endpoint:
    """Where the listener is and who we are to it."""

    host: str
    port: int
    account: str
    password: str = field(repr=False)
    """Kept out of the repr, like `apply.DockerSql.root_password`.

    A frozen dataclass reprs every field by default, and this object is handed
    to worker threads and closed over by the tab's seams: a pytest assertion
    diff, a logged object or a traceback frame dump would each print it.
    """

    namespace: str = "urn:AC"
    """Which XML namespace this listener answers to; see `envelope()`.

    Defaulted here and NOWHERE else. This is a transport-level struct that
    predates the second family, and every construction site inside the app now
    passes the entry's own value; the default keeps a credential file written
    before this field existed readable, which is the same reason
    `Verified.at` is optional.
    """

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


@dataclass(frozen=True)
class Reply:
    """What came back, typed. `text` is raw — see the module docstring."""

    outcome: Outcome
    text: str = ""
    http_status: int | None = None

    @property
    def answered(self) -> bool:
        """True only for a command the server ran and reported as succeeding."""
        return self.outcome == "answered"


_ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
    ' xmlns:ns1="{namespace}">\n'
    "  <SOAP-ENV:Body>\n"
    "    <ns1:executeCommand><command>{command}</command></ns1:executeCommand>\n"
    "  </SOAP-ENV:Body>\n"
    "</SOAP-ENV:Envelope>\n"
)


def envelope(command: str, *, namespace: str) -> str:
    """The request body for `command`, escaped, in this tree's own namespace.

    The namespace is an argument and not a constant because it is not the same
    on both families -- `urn:AC` on AzerothCore, `urn:MaNGOS` on CMaNGOS -- and
    getting it wrong is invisible from here: the service checks the namespace
    before the credential, so the wrong one answers HTTP 500 `method name or
    namespace not recognized` even for a bad password, which is indistinguishable
    from a world that has not finished loading (measured on m910q, 2026-09-07).
    """
    return _ENVELOPE.format(command=escape(command), namespace=namespace)


def escape(text: str) -> str:
    """XML-escape for the outbound `<command>`; `&` first, then `<` and `>`."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_ENTITY = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|lt|gt|amp|quot|apos);")

_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def unescape(text: str) -> str:
    """Undo XML escaping in text the server sent, because a person reads it.

    Measured on `yulon-ubuntu`, 2026-09-07: a real `server info` came back with
    every line ending `&#xD;`, and that text goes straight into a gate capture
    and, later, the console this channel will carry.

    XML's five named entities and numeric character references, and nothing
    else. Not `html.unescape`, which also decodes several hundred HTML-only
    names -- a server printing a literal `&nbsp;` in somebody's guild name
    would have it silently turned into a space. An `&` that begins no entity is
    left exactly as it is: it is text, not a broken entity to be guessed at.

    `&amp;` last is not a concern here the way it is when escaping, because a
    single pass over the source never revisits what it has already written.
    """

    def one(match: re.Match[str]) -> str:
        body = match.group(1)
        if body.startswith("#"):
            digits = body[1:]
            base = 16 if digits[:1] in ("x", "X") else 10
            return chr(int(digits.lstrip("xX") if base == 16 else digits, base))
        return _NAMED[body]

    return _ENTITY.sub(one, text)


def execute(endpoint: Endpoint, command: str, *, timeout: float = DEFAULT_TIMEOUT) -> Reply:
    """POST one command and classify the answer. Never raises.

    Every failure is an outcome rather than an exception, because the caller is
    a seam that has to say something to a person either way, and because the
    interesting failures here — a socket that accepts and stays silent, an
    account whose level is too low — are answers about the server rather than
    faults in this code.
    """
    connection = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=timeout)
    try:
        connection.request(
            "POST",
            "/",
            body=envelope(command, namespace=endpoint.namespace).encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "Authorization": _basic(endpoint),
                "SOAPAction": '""',
            },
        )
        response = connection.getresponse()
        return _classify(response.status, response.read().decode("utf-8", errors="replace"))
    except TimeoutError:
        # `socket.timeout` is this same class since 3.10, so one arm covers both.
        return Reply("timeout", f"no answer from {endpoint.url} within {timeout:g}s")
    except http.client.RemoteDisconnected:
        # Raised only by `getresponse`, so the request is already out: the
        # server took it and answered with nothing. On CMaNGOS that is a
        # refusal (`MaNGOSsoap.cpp:133`); it is never a channel nobody is on,
        # because that one fails at the connect instead. This arm has to come
        # before the one below -- `RemoteDisconnected` is both an
        # `HTTPException` and an `OSError`, so the broad arm would swallow it.
        return Reply(
            "silent",
            f"{endpoint.url} took the command and closed the connection without answering",
        )
    except (http.client.HTTPException, OSError) as exc:
        return Reply("unreachable", f"could not reach {endpoint.url}: {exc}")
    finally:
        connection.close()


def _basic(endpoint: Endpoint) -> str:
    """The Basic header. Built here so no caller has to hold the pair as text."""
    pair = f"{endpoint.account}:{endpoint.password}".encode()
    return "Basic " + base64.b64encode(pair).decode("ascii")


_FAULT = re.compile(r"<faultstring>(.*?)</faultstring>", re.DOTALL)
_RESULT = re.compile(r"<result>(.*?)</result>", re.DOTALL)


def _classify(status: int, body: str) -> Reply:
    """Turn one HTTP answer into one outcome. Fault is checked before result."""
    if status == 401:
        return Reply("unauthorised", "the server did not accept this account and password", status)
    if status == 403:
        return Reply(
            "forbidden",
            "the account exists but its GM level is below administrator, which SOAP requires",
            status,
        )
    fault = _FAULT.search(body)
    if fault:
        return Reply("refused", unescape(fault.group(1)), status)
    result = _RESULT.search(body)
    if result:
        return Reply("answered", unescape(result.group(1)), status)
    logger.warning(f"a SOAP reply carried neither a result nor a fault (HTTP {status})")
    return Reply("unreadable", body.strip()[:400], status)
