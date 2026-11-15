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


def _len(n: int) -> bytes:
    """A DER length: short form under 128, else the minimal long form."""
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, body: bytes) -> bytes:
    """One DER tag-length-value."""
    return bytes([tag]) + _len(len(body)) + body


def _oid(dotted: str) -> bytes:
    """An OBJECT IDENTIFIER's DER body and tag, from its dotted form."""
    first, *rest = (int(part) for part in dotted.split("."))
    body = bytearray([first * 40 + rest[0]])
    for value in rest[1:]:
        chunk = [value & 0x7F]
        value >>= 7
        while value:
            chunk.append(0x80 | (value & 0x7F))
            value >>= 7
        body.extend(reversed(chunk))
    return _tlv(0x06, bytes(body))


def _extension(oid: str, value: bytes, *, critical: bool = True) -> bytes:
    """One `Extension` sequence, DER."""
    body = _oid(oid) + (_tlv(0x01, b"\xff") if critical else b"") + _tlv(0x04, value)
    return _tlv(0x30, body)


def _certificate(*extensions: bytes) -> bytes:
    """A certificate-shaped DER holding exactly `extensions` (nothing is signed)."""
    wrapper = _tlv(0x30, b"".join(extensions))
    tbs = _tlv(0x30, _tlv(0xA3, wrapper))
    return _tlv(0x30, tbs)


BASIC = _extension(x509_names.BASIC_CONSTRAINTS, _tlv(0x30, _tlv(0x01, b"\xff")))


def _constraints(*fields: bytes) -> bytes:
    """A `nameConstraints` extension over `fields` (the `[0]`/`[1]` wrappers given)."""
    return _extension(x509_names.NAME_CONSTRAINTS, _tlv(0x30, b"".join(fields)))


def _permitted(*subtrees: bytes) -> bytes:
    """One `[0] permittedSubtrees` holding `subtrees`, each a GeneralSubtree sequence."""
    return _tlv(0xA0, b"".join(subtrees))


def _dns(name: str) -> bytes:
    """One `GeneralSubtree` whose base is `dNSName` `name`."""
    return _tlv(0x30, _tlv(0x82, name.encode("ascii")))


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


# -- strict DER (T888 item 5) ---------------------------------------------------------


def test_a_hand_built_certificate_with_the_right_shape_is_read() -> None:
    """The builder above makes what the reader accepts: one CA, one name, nothing else."""
    der = _certificate(BASIC, _constraints(_permitted(_dns(PORTAL))))
    assert problem(der, PORTAL) is None
    assert x509_names.permitted_dns_names(
        x509_names.extensions(der)[x509_names.NAME_CONSTRAINTS]
    ) == (PORTAL,)


def test_a_repeated_extension_oid_is_refused() -> None:
    """T888 item 5 (Claude #4): last-wins let a client read the first, this reader the second."""
    der = _certificate(BASIC, _constraints(_permitted(_dns(PORTAL))), BASIC)
    with pytest.raises(CertificateError, match="repeats"):
        x509_names.extensions(der)
    assert (problem(der, PORTAL) or "").startswith("it is not a certificate")


def test_excluded_subtrees_are_refused() -> None:
    """T888 item 5: a CA that permits one name and excludes another is not this CA."""
    excluded = _tlv(0xA1, _dns("us.actual.battle.net"))
    der = _certificate(BASIC, _constraints(_permitted(_dns(PORTAL)), excluded))
    assert "excludes names" in (problem(der, PORTAL) or "")


def test_a_permitted_subtree_that_is_not_a_dns_name_is_refused() -> None:
    """T888 item 5: an IP or URI subtree is a name this app's one sentence does not cover."""
    other = _tlv(0x30, _tlv(0x87, bytes(4)))  # iPAddress
    der = _certificate(BASIC, _constraints(_permitted(_dns(PORTAL), other)))
    assert "not a DNS name" in (problem(der, PORTAL) or "")


def test_a_permitted_subtree_with_a_distance_field_is_refused() -> None:
    """T888 item 5: `minimum`/`maximum` narrow a subtree; reading it as plain is not reading."""
    distant = _tlv(0x30, _tlv(0x82, PORTAL.encode("ascii")) + _tlv(0x02, b"\x80"))
    der = _certificate(BASIC, _constraints(_permitted(distant)))
    assert "distance field" in (problem(der, PORTAL) or "")


def test_bytes_after_a_value_are_refused_at_every_level() -> None:
    """T888 item 5 (Codex #3): a trailing byte is a value this reader did not see."""
    good = _certificate(BASIC, _constraints(_permitted(_dns(PORTAL))))
    with pytest.raises(CertificateError, match="bytes after a value"):
        x509_names.extensions(good + b"\x00")
    assert "bytes after a value" in (problem(good + b"\x00", PORTAL) or "")
    padded = _certificate(
        BASIC,
        _extension(x509_names.NAME_CONSTRAINTS, _tlv(0x30, _permitted(_dns(PORTAL))) + b"\x05"),
    )
    with pytest.raises(CertificateError, match="bytes after a value"):
        x509_names.permitted_dns_names(
            x509_names.extensions(padded)[x509_names.NAME_CONSTRAINTS]
        )
    assert "bytes after a value" in (problem(padded, PORTAL) or "")
