"""The two X.509 extensions Yu'lon must read for itself, with no new dependency (T888).

WoW Forever's Play trusts the install's own CA in the machine's trust store (Windows) or in
the game's Wine prefix (SteamOS), and a CA in a trust store may vouch for ANY name it likes.
The only thing that keeps this one to the login name it was made for is its `nameConstraints`
extension — and a CA that is trusted *because of* an extension has to have that extension
checked by the trusting side, not by the side that made the file.

`cryptography` is not a dependency (`requirements.txt`), and neither is OpenSSL's Python
binding, so this reads the certificate's DER itself: a tag-length-value walk, the extension
OIDs, and just the two extensions that matter. It is a reader, not a validator: no signature
chain, no expiry, no key usage. What it answers is exactly the question `forever_trust`
asks before it touches a trust store:

* is this a certificate authority at all (`basicConstraints` with `cA` TRUE)?
* does it carry a `nameConstraints` extension, and is that extension **critical**, so a
  client that does not understand it must reject the certificate rather than ignore it?
* are its permitted subtrees exactly one `dNSName`, and is that name the one this server
  answers for?

Every answer that is not "yes, all three" comes back as a sentence for Details
(`problem()`), and `forever_trust` refuses with its one player sentence.
"""

from __future__ import annotations

from dataclasses import dataclass

BASIC_CONSTRAINTS = "2.5.29.19"
NAME_CONSTRAINTS = "2.5.29.30"

_TAG_BOOLEAN = 0x01
_TAG_INTEGER = 0x02
_TAG_OID = 0x06
_TAG_OCTET_STRING = 0x04
_TAG_SEQUENCE = 0x30
_TAG_EXTENSIONS = 0xA3
_TAG_PERMITTED = 0xA0
_TAG_EXCLUDED = 0xA1
_TAG_DNS_NAME = 0x82
"""`GeneralName`'s dNSName: context-specific tag 2, primitive."""


class CertificateError(ValueError):
    """The bytes are not a certificate this reader can walk; the message says where."""


@dataclass(frozen=True)
class Tlv:
    """One DER tag-length-value: its tag byte and its contents (not the length bytes)."""

    tag: int
    body: bytes

    def items(self) -> tuple[Tlv, ...]:
        """The children of a constructed value, in order."""
        return _items(self.body)


@dataclass(frozen=True)
class Extension:
    """One `Extension` of the certificate: its OID, its criticality, its value bytes."""

    oid: str
    critical: bool
    value: bytes


def _read(data: bytes, at: int = 0) -> tuple[Tlv, int]:
    """The value starting at `at`, and where the next one starts."""
    if at >= len(data):
        raise CertificateError("it ends where a value was expected")
    tag = data[at]
    if tag & 0x1F == 0x1F:
        raise CertificateError("it uses a multi-byte tag")
    length, body_at = _length(data, at + 1)
    end = body_at + length
    if end > len(data):
        raise CertificateError("a value runs past the end of its parent")
    return Tlv(tag, data[body_at:end]), end


def _length(data: bytes, at: int) -> tuple[int, int]:
    """A DER length's value and where the contents start."""
    if at >= len(data):
        raise CertificateError("it ends where a length was expected")
    first = data[at]
    at += 1
    if first < 0x80:
        return first, at
    count = first & 0x7F
    if not 1 <= count <= 4:
        raise CertificateError(f"it spells a length with {count} bytes")
    if at + count > len(data):
        raise CertificateError("a length runs past the end")
    return int.from_bytes(data[at : at + count], "big"), at + count


def _only(data: bytes) -> Tlv:
    """The one value `data` holds, whole: bytes after it are refused, not skipped.

    DER's whole point is that a reader knows where every value ends. A certificate that
    carries something after a value it names -- one more byte after the outer sequence, an
    extra field after a name constraint -- is not the certificate this app read, and a
    Windows client may well read the part this reader did not.
    """
    value, end = _read(data, 0)
    if end != len(data):
        raise CertificateError("it has bytes after a value")
    return value


def _items(body: bytes) -> tuple[Tlv, ...]:
    """Every value inside `body`, in order. Refuses a truncated or overlong one."""
    found: list[Tlv] = []
    at = 0
    while at < len(body):
        item, at = _read(body, at)
        found.append(item)
    return tuple(found)


def _oid(body: bytes) -> str:
    """An OBJECT IDENTIFIER's dotted form."""
    if not body:
        raise CertificateError("it holds an empty object identifier")
    parts = [body[0] // 40, body[0] % 40]
    value = 0
    for byte in body[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(part) for part in parts)


def extensions(der: bytes) -> dict[str, Extension]:
    """Every extension of the certificate `der` holds, by OID. Raises `CertificateError`.

    Walks `Certificate` -> `TBSCertificate` and takes the `[3] extensions` element where it
    is; a certificate with none answers an empty mapping.
    """
    certificate = _only(der)
    if certificate.tag != _TAG_SEQUENCE:
        raise CertificateError("it does not start with a certificate's sequence")
    parts = certificate.items()
    if not parts or parts[0].tag != _TAG_SEQUENCE:
        raise CertificateError("it has no certificate body")
    found: dict[str, Extension] = {}
    for field in parts[0].items():
        if field.tag != _TAG_EXTENSIONS:
            continue
        wrapper = _only(field.body)
        if wrapper.tag != _TAG_SEQUENCE:
            raise CertificateError("its extensions are not a sequence")
        for entry in wrapper.items():
            extension = _extension(entry)
            if extension.oid in found:
                raise CertificateError(
                    f"it repeats the extension {extension.oid}: a client may read the first "
                    "one where this app would read the second"
                )
            found[extension.oid] = extension
    return found


def _extension(entry: Tlv) -> Extension:
    """One `Extension ::= SEQUENCE { extnID, critical DEFAULT FALSE, extnValue }`."""
    if entry.tag != _TAG_SEQUENCE:
        raise CertificateError("an extension is not a sequence")
    parts = entry.items()
    if len(parts) < 2 or parts[0].tag != _TAG_OID:
        raise CertificateError("an extension names no object identifier")
    critical = False
    value = b""
    for part in parts[1:]:
        if part.tag == _TAG_BOOLEAN:
            critical = part.body not in (b"", b"\x00")
        elif part.tag == _TAG_OCTET_STRING:
            value = part.body
    if not value:
        raise CertificateError("an extension holds no value")
    return Extension(oid=_oid(parts[0].body), critical=critical, value=value)


def is_certificate_authority(extension: Extension) -> bool:
    """`basicConstraints`'s `cA` flag: True only when it is present and not FALSE."""
    sequence = _only(extension.value)
    if sequence.tag != _TAG_SEQUENCE:
        raise CertificateError("basicConstraints is not a sequence")
    parts = sequence.items()
    return bool(parts) and parts[0].tag == _TAG_BOOLEAN and parts[0].body != b"\x00"


def permitted_dns_names(extension: Extension) -> tuple[str, ...]:
    """The `dNSName`s `nameConstraints` permits; every other shape is refused.

    Exactly one `[0] permittedSubtrees` (no `[1] excludedSubtrees`, nothing beside it),
    holding only `dNSName` subtrees with no `minimum`/`maximum` distance. Refused rather
    than ignored: RFC 5280 says a name constraint this reader does not understand must
    fail closed, and this CA is trusted BECAUSE of this extension (T888). A subtree with a
    distance bound, or a permitted IP/URI/other name, is a wider CA than the one sentence
    this app promises the player.
    """
    sequence = _only(extension.value)
    if sequence.tag != _TAG_SEQUENCE:
        raise CertificateError("nameConstraints is not a sequence")
    fields = sequence.items()
    if any(field.tag == _TAG_EXCLUDED for field in fields):
        raise CertificateError("it excludes names as well as permitting them")
    if len(fields) != 1 or fields[0].tag != _TAG_PERMITTED:
        raise CertificateError("its name constraint is not one permittedSubtrees")
    names: list[str] = []
    # `[0]` is IMPLICIT (RFC 5280's module): its contents ARE GeneralSubtrees, so the
    # values in it are the `GeneralSubtree` sequences, each holding its `base` first.
    for subtree in _items(fields[0].body):
        if subtree.tag != _TAG_SEQUENCE:
            raise CertificateError("a permitted subtree is not a sequence")
        parts = subtree.items()
        if not parts or parts[0].tag != _TAG_DNS_NAME:
            raise CertificateError("it permits a name that is not a DNS name")
        if len(parts) != 1:
            raise CertificateError("a permitted subtree carries a distance field")
        names.append(parts[0].body.decode("ascii", "replace"))
    return tuple(names)


def problem(der: bytes, allowed: str) -> str | None:
    """Why a trust store must not take this CA for `allowed`; None when it may.

    The sentence is for Details: `forever_trust` refuses the whole trust with one player
    sentence and logs this one, so a player who wants to know reads what is missing rather
    than a bare "no".
    """
    try:
        found = extensions(der)
    except CertificateError as exc:
        return f"it is not a certificate this app can read ({exc})"
    try:
        basic = found.get(BASIC_CONSTRAINTS)
        if basic is None:
            return "it has no basicConstraints extension, so it does not say it is a CA"
        if not is_certificate_authority(basic):
            return "its basicConstraints does not say CA:TRUE"
        names = found.get(NAME_CONSTRAINTS)
        if names is None:
            return "it has no name constraint, so it could vouch for any name"
        if not names.critical:
            return "its name constraint is not critical, so a client may ignore it"
        permitted = permitted_dns_names(names)
    except CertificateError as exc:
        return f"one of its extensions cannot be read ({exc})"
    if permitted == (allowed,):
        return None
    if not permitted:
        return "its name constraint permits no DNS name at all"
    if len(permitted) > 1:
        return (
            f"its name constraint permits {len(permitted)} names "
            f"({', '.join(permitted)}), not {allowed} alone"
        )
    return f"its name constraint permits {permitted[0]}, not {allowed} alone"
