"""`yulon.x509_names`: the CA checks `forever_trust` makes before a trust store is touched.

The five fixtures in `tests/data/forever-certs/` were made with the local `openssl` CLI and
differ from each other in exactly one fact each, so a green here means the reader tells them
apart rather than that it likes every certificate:

| file | basicConstraints | nameConstraints |
|---|---|---|
| `narrow-critical.pem` | CA:TRUE | critical, one dNSName `trinity.actual.battle.net` |
| `broad.pem` | CA:TRUE | critical, one dNSName `actual.battle.net` |
| `two-names.pem` | CA:TRUE | critical, two dNSNames |
| `non-critical.pem` | CA:TRUE | not critical, one dNSName `trinity.actual.battle.net` |
| `no-constraint.pem` | CA:TRUE | none at all |
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from yulon import x509_names
from yulon.x509_names import CertificateError, problem

DATA = Path(__file__).parent / "data" / "forever-certs"
PORTAL = "trinity.actual.battle.net"

_PEM = re.compile(r"-----BEGIN CERTIFICATE-----\s*(.+?)\s*-----END CERTIFICATE-----", re.DOTALL)


def _der(name: str) -> bytes:
    text = (DATA / name).read_text(encoding="ascii")
    found = _PEM.search(text)
    assert found is not None, name
    return base64.b64decode("".join(found.group(1).split()))


def test_the_narrow_critical_constraint_is_the_one_this_app_may_trust() -> None:
    assert problem(_der("narrow-critical.pem"), PORTAL) is None
    assert x509_names.permitted_dns_names(
        x509_names.extensions(_der("narrow-critical.pem"))[x509_names.NAME_CONSTRAINTS]
    ) == (PORTAL,)


@pytest.mark.parametrize(
    ("fixture", "said"),
    [
        ("broad.pem", "permits actual.battle.net, not trinity.actual.battle.net alone"),
        ("two-names.pem", "permits 2 names"),
        ("non-critical.pem", "its name constraint is not critical"),
        ("no-constraint.pem", "it has no name constraint"),
    ],
)
def test_every_other_constraint_is_refused_with_what_is_wrong(fixture: str, said: str) -> None:
    """The four ways a CA is wider than this server: the player's Details sentence names it."""
    refused = problem(_der(fixture), PORTAL)
    assert refused is not None and said in refused


def test_a_name_other_than_the_one_this_server_answers_for_is_refused() -> None:
    """The check is against THIS server's name, not against the string in the file."""
    assert "permits trinity.actual.battle.net, not trinity.actual.battle.net.evil" in (
        problem(_der("narrow-critical.pem"), "trinity.actual.battle.net.evil") or ""
    )


def test_bytes_that_are_not_a_certificate_are_refused_not_guessed_at() -> None:
    for junk in (b"", b"\x30", b"\x30\x03\x02\x01", b"not a certificate", b"\x30\x82\xff\xff"):
        refused = problem(junk, PORTAL)
        assert refused is not None and refused.startswith("it is not a certificate")
    with pytest.raises(CertificateError):
        x509_names.extensions(b"\x30\x03\x02\x01\x00")
