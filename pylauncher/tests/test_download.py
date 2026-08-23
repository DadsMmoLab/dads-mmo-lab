"""Tests for the verified installer download in `yulon.platform`, and for the TLS
rules the whole package has to keep.

Almost nothing here opens a socket: the curl transport is exercised through the
`run` seam, the in-process transport through an `open_url` seam that returns
canned responses, and the three small GETs through a stand-in `urlopen`. The
assertions are about the thing that actually broke on a real Windows 11 box
(2026-08-22) — `urllib` could not verify desktop.docker.com because OpenSSL
cannot see the roots Windows fetches on demand — and about the two rules that
came out of it: a transfer that cannot be verified fails loudly rather than
retrying with verification off, and no module in the package calls `urlopen`
without a verifying context. The second rule is here rather than in a file of its
own because it is the same measurement that motivates the first.

The exception is the last section, which stands up a real self-signed HTTPS
server on 127.0.0.1 (review finding, 2026-08-23). Building the failure by hand is
what let a defect through: every "verification" test constructed the exception
itself, so nobody noticed that `urlopen` never raises the one the code was
looking for. One test has to earn its answer from the real stack.
"""

from __future__ import annotations

import ast
import hashlib
import http.server
import ssl
import subprocess
import sys
import threading
import types
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import manifest_store, platform, update

# Verbatim from the failing run on the fresh Windows 11 install.
MEASURED_TLS_FAILURE = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
    "local issuer certificate (_ssl.c:1000)"
)

# A throwaway certificate + key for 127.0.0.1, pre-generated and committed rather
# than built at test time: there is no X.509 builder in the standard library and
# nothing in requirements-dev.txt can make one (`cryptography` is not a
# dependency and is not worth becoming one for a single file), while shelling out
# to an `openssl` binary would make the test depend on what happens to be
# installed on the runner. The file's own header says how it was generated. It is
# a fixture, not a secret — the test's whole point is that trusting it FAILS.
SELF_SIGNED_PEM = Path(__file__).parent / "data" / "self-signed-localhost.pem"


def _pem_fingerprints(pem: str) -> set[str]:
    """DER SHA-256 of every CERTIFICATE block in `pem`, ignoring keys and comments."""
    fingerprints: set[str] = set()
    block: list[str] = []
    for line in pem.splitlines(keepends=True):
        if "BEGIN CERTIFICATE" in line:
            block = [line]
        elif block:
            block.append(line)
            if "END CERTIFICATE" in line:
                der = ssl.PEM_cert_to_DER_cert("".join(block))
                fingerprints.add(hashlib.sha256(der).hexdigest())
                block = []
    return fingerprints


def _fingerprints(context: ssl.SSLContext) -> set[str]:
    """DER SHA-256 of every CA certificate a context trusts.

    Identity, not a count: "121 roots" and "58 roots" say nothing about whether
    the 58 are among the 121, which is the question this file has to answer.
    """
    return {hashlib.sha256(der).hexdigest() for der in context.get_ca_certs(binary_form=True)}


def _certificates_only(pem: str) -> str:
    """`pem` with everything that is not a CERTIFICATE block stripped out.

    The fixture file carries its private key and a header comment; a CA file
    handed to `load_verify_locations()` should be certificates and nothing else.
    """
    out: list[str] = []
    keep = False
    for line in pem.splitlines(keepends=True):
        keep = keep or "BEGIN CERTIFICATE" in line
        if keep:
            out.append(line)
        keep = keep and "END CERTIFICATE" not in line
    return "".join(out)


def _clear_tls_memo() -> None:
    """Drop `verify_context()`'s memo, tolerating there not being one.

    `getattr` rather than a bare call because this fixture must not be what
    reports a lost `@lru_cache` (review finding, 2026-08-23): calling
    `cache_clear()` unconditionally turned that into 25 collection errors in an
    autouse fixture, which points the reader at the fixture instead of at
    `test_the_context_is_built_once_and_shared`, the test that can say what
    sharing is for.
    """
    getattr(platform.verify_context, "cache_clear", lambda: None)()


@pytest.fixture(autouse=True)
def _fresh_tls_context() -> Iterator[None]:
    """`verify_context()` is memoized; these tests swap certifi out from under it.

    Without this, whichever test ran first would decide the context every later
    test sees — including the one asserting certifi's root count.
    """
    _clear_tls_memo()
    yield
    _clear_tls_memo()


class _FakeResponse:
    """A canned `urlopen` result — no socket behind it."""

    def __init__(
        self,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://desktop.docker.com/installer.exe",
    ) -> None:
        self._body = body
        self._headers = headers if headers is not None else {"Content-Length": str(len(body))}
        self._url = url
        self.closed = False

    def geturl(self) -> str:
        return self._url

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)

    def read(self, amt: int = -1) -> bytes:
        chunk = self._body if amt is None or amt < 0 else self._body[:amt]
        self._body = self._body[len(chunk) :]
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeOpener:
    """The `open_url` seam: records requests, answers with queued responses."""

    def __init__(self, *responses: _FakeResponse | Exception) -> None:
        self.queue: list[_FakeResponse | Exception] = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> platform.HttpResponse:
        self.requests.append(request)
        answer = self.queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _FakeCurl:
    """The `run` seam, behaving like curl: writes `--output`, or exits non-zero."""

    def __init__(self, body: bytes = b"", returncode: int = 0, stderr: str = "") -> None:
        self.body = body
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if self.returncode == 0:
            out = Path(argv[argv.index("--output") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("ab") as fh:  # curl --continue-at - appends
                fh.write(self.body)
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


# ------------------------------------------------------------- transport order


def test_the_os_curl_is_tried_first_so_openssl_is_never_the_only_chance(tmp_path: Path) -> None:
    """The transport that can see Windows' on-demand roots goes first.

    The whole defect is that OpenSSL alone could not verify desktop.docker.com on
    a fresh install. curl (schannel) can, so it runs first and the in-process
    opener is not even consulted.
    """
    dest = tmp_path / "downloads" / "Docker Desktop Installer.exe"
    system_curl = Path("C:/Windows/System32/curl.exe")
    curl = _FakeCurl(body=b"installer-bytes")
    opener = _FakeOpener(RuntimeError("urllib must not be reached"))

    result = platform.download_verified(
        "https://desktop.docker.com/installer.exe",
        dest,
        run=curl,
        find_curl=lambda: system_curl,
        open_url=opener,
    )

    assert result == dest and dest.read_bytes() == b"installer-bytes"
    assert opener.requests == []
    argv = curl.calls[0]
    assert argv[0] == str(system_curl)
    assert "--continue-at" in argv and argv[argv.index("--continue-at") + 1] == "-"
    assert "--proto-redir" in argv and "=https" in argv


def test_a_box_without_curl_falls_back_to_the_bundled_root_store(tmp_path: Path) -> None:
    """Windows before 1803 has no System32 curl; the in-process transport carries it."""
    dest = tmp_path / "Docker.dmg"
    opener = _FakeOpener(_FakeResponse(b"dmg-bytes"))

    result = platform.download_verified(
        "https://desktop.docker.com/Docker.dmg",
        dest,
        run=_FakeCurl(),
        find_curl=lambda: None,
        open_url=opener,
    )

    assert result == dest and dest.read_bytes() == b"dmg-bytes"
    assert len(opener.requests) == 1


def test_a_curl_that_cannot_verify_falls_through_to_certifi(tmp_path: Path) -> None:
    """curl exit 60 is 'peer certificate not verifiable' — try the other root set, not `-k`."""
    dest = tmp_path / "installer.exe"
    curl = _FakeCurl(returncode=60, stderr="curl: (60) SSL certificate problem")
    opener = _FakeOpener(_FakeResponse(b"installer-bytes"))

    result = platform.download_verified(
        "https://desktop.docker.com/installer.exe",
        dest,
        run=curl,
        find_curl=lambda: Path("curl.exe"),
        open_url=opener,
    )

    assert result == dest and dest.read_bytes() == b"installer-bytes"
    assert len(curl.calls) == 1 and len(opener.requests) == 1


# ------------------------------------------------------------- honest failure


def test_when_nothing_can_verify_the_download_fails_instead_of_downgrading(
    tmp_path: Path,
) -> None:
    """Both transports fail to verify: no file, no unverified retry, and `verification` set.

    This is the exact shape of the measured run — `urllib` raising
    CERTIFICATE_VERIFY_FAILED — with curl also unable to help. The installer is
    run elevated afterwards, so 'fetch it anyway' is not an option that exists.
    """
    dest = tmp_path / "installer.exe"
    curl = _FakeCurl(returncode=60, stderr="curl: (60) SSL certificate problem")
    opener = _FakeOpener(ssl.SSLCertVerificationError(MEASURED_TLS_FAILURE))

    with pytest.raises(platform.DownloadError) as caught:
        platform.download_verified(
            "https://desktop.docker.com/installer.exe",
            dest,
            run=curl,
            find_curl=lambda: Path("curl.exe"),
            open_url=opener,
        )

    assert caught.value.verification is True
    assert "curl: (60)" in str(caught.value)
    assert "unable to get local issuer certificate" in str(caught.value)
    assert not dest.exists()


def test_a_network_failure_is_not_reported_as_a_certificate_problem(tmp_path: Path) -> None:
    """A dead link and a missing root need different words; only one is the user's to fix."""
    dest = tmp_path / "installer.exe"
    opener = _FakeOpener(TimeoutError("timed out"))

    with pytest.raises(platform.DownloadError) as caught:
        platform.download_verified(
            "https://desktop.docker.com/installer.exe",
            dest,
            run=_FakeCurl(),
            find_curl=lambda: None,
            open_url=opener,
        )

    assert caught.value.verification is False


def test_a_redirect_off_https_is_refused(tmp_path: Path) -> None:
    """`urllib` follows https -> http redirects by default; an installer must not."""
    dest = tmp_path / "installer.exe"
    opener = _FakeOpener(_FakeResponse(b"x", url="http://cdn.example.invalid/installer.exe"))

    with pytest.raises(platform.DownloadError, match="not HTTPS"):
        platform.download_verified(
            "https://desktop.docker.com/installer.exe",
            dest,
            run=_FakeCurl(),
            find_curl=lambda: None,
            open_url=opener,
        )
    assert not dest.exists()


def test_a_short_transfer_is_never_renamed_into_place(tmp_path: Path) -> None:
    """A cut connection leaves a `.part` to resume, not a truncated .exe to run."""
    dest = tmp_path / "installer.exe"
    opener = _FakeOpener(_FakeResponse(b"only-ten", headers={"Content-Length": "1000"}))

    with pytest.raises(platform.DownloadError, match="transfer ended at 8 of 1000"):
        platform.download_verified(
            "https://desktop.docker.com/installer.exe",
            dest,
            run=_FakeCurl(),
            find_curl=lambda: None,
            open_url=opener,
        )

    assert not dest.exists()
    assert (tmp_path / "installer.exe.part").read_bytes() == b"only-ten"


# ------------------------------------------------------------- cache + resume


def test_a_completed_download_is_reused_rather_than_fetched_again(tmp_path: Path) -> None:
    """629 MB is not something to fetch twice; a finished file short-circuits both transports."""
    dest = tmp_path / "installer.exe"
    dest.write_bytes(b"already-here")
    curl = _FakeCurl()
    opener = _FakeOpener(RuntimeError("nothing should be fetched"))

    result = platform.download_verified(
        "https://desktop.docker.com/installer.exe",
        dest,
        run=curl,
        find_curl=lambda: Path("curl.exe"),
        open_url=opener,
    )

    assert result == dest and dest.read_bytes() == b"already-here"
    assert curl.calls == [] and opener.requests == []


def test_an_interrupted_download_resumes_where_it_stopped(tmp_path: Path) -> None:
    """A `.part` becomes a Range request, and the answered range is appended."""
    dest = tmp_path / "installer.exe"
    (tmp_path / "installer.exe.part").write_bytes(b"first-half-")
    opener = _FakeOpener(
        _FakeResponse(
            b"second-half",
            headers={"Content-Range": "bytes 11-21/22", "Content-Length": "11"},
        )
    )

    platform.download_verified(
        "https://desktop.docker.com/installer.exe",
        dest,
        run=_FakeCurl(),
        find_curl=lambda: None,
        open_url=opener,
    )

    assert opener.requests[0].get_header("Range") == "bytes=11-"
    assert dest.read_bytes() == b"first-half-second-half"


def test_a_server_that_ignores_the_range_restarts_instead_of_concatenating(
    tmp_path: Path,
) -> None:
    """No `Content-Range` in the answer means a 200 with the whole body: overwrite, don't append."""
    dest = tmp_path / "installer.exe"
    (tmp_path / "installer.exe.part").write_bytes(b"stale-")
    opener = _FakeOpener(_FakeResponse(b"whole-body"))

    platform.download_verified(
        "https://desktop.docker.com/installer.exe",
        dest,
        run=_FakeCurl(),
        find_curl=lambda: None,
        open_url=opener,
    )

    assert dest.read_bytes() == b"whole-body"


def test_curl_drops_a_partial_the_server_will_not_range(tmp_path: Path) -> None:
    """Exit 33 = 'no byte ranges here'; keeping the partial would fail forever."""
    dest = tmp_path / "installer.exe"
    part = tmp_path / "installer.exe.part"
    part.write_bytes(b"partial")

    class _NoRangeThenOk(_FakeCurl):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            if not self.calls:
                self.calls.append(argv)
                return subprocess.CompletedProcess(argv, 33, "", "no byte ranges")
            return super().__call__(argv)

    curl = _NoRangeThenOk(body=b"whole-body")
    platform.download_verified(
        "https://desktop.docker.com/installer.exe",
        dest,
        run=curl,
        find_curl=lambda: Path("curl.exe"),
    )

    assert len(curl.calls) == 2
    assert dest.read_bytes() == b"whole-body"


# ------------------------------------------------------------- the TLS context


def test_the_context_is_always_verifying_with_and_without_certifi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither branch of `verify_context()` may relax verification."""
    for masked in (False, True):
        with monkeypatch.context() as patch:
            if masked:
                patch.setitem(sys.modules, "certifi", None)
            context = platform.verify_context()
        assert context.verify_mode is ssl.CERT_REQUIRED
        assert context.check_hostname is True


def test_the_context_loads_certifis_bundle_on_top_of_the_os_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """certifi's file is handed to the context, and it ADDS roots rather than replacing them.

    A stand-in certifi pointing at a one-certificate PEM: the context has to come
    back with the OS store's CA count plus that one. Only a context that actually
    reads `certifi.where()` gets the +1, and only one that loaded the OS roots
    first keeps the rest.
    """
    single = tmp_path / "one-root.pem"
    single.write_text(
        _certificates_only(SELF_SIGNED_PEM.read_text(encoding="ascii")), encoding="ascii"
    )
    fake = types.ModuleType("certifi")
    fake.where = lambda: str(single)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "certifi", fake)

    os_only = ssl.create_default_context().cert_store_stats()["x509_ca"]
    context = platform.verify_context()

    assert context.cert_store_stats()["x509_ca"] == os_only + 1


def test_an_unreadable_certifi_bundle_falls_back_to_the_os_store_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packaging fault must not surface to the user as "you are offline".

    `verify_context()` used to raise when certifi imported but its bundle did
    not open, and `detect_public_ip()` calls it inside a `except (OSError,
    ValueError)` loop — so a PyInstaller build that failed to collect
    `cacert.pem` reported itself as having no network (review finding,
    2026-08-23). The OS store alone is a narrower root set but still a fully
    verifying one, so degrading to it keeps the app working and truthful.
    """
    fake = types.ModuleType("certifi")
    fake.where = lambda: str(tmp_path / "no-such-cacert.pem")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "certifi", fake)

    context = platform.verify_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.cert_store_stats() == ssl.create_default_context().cert_store_stats()


def test_the_os_roots_survive_certifi_being_added_to_them() -> None:
    """The regression test for a swap that read like a widening.

    `create_default_context(cafile=...)` takes the cafile arm and never calls
    `load_default_certs()`, so passing certifi there REPLACED the OS store. On
    this box that dropped 33 of its 58 roots — DigiCert Global Root CA and
    Baltimore CyberTrust Root among them — and, structurally, every root an
    administrator installed: the corporate TLS-inspecting proxy and internal-CA
    cases, both of which fail silently (a manifest refresh falls back to the
    bundled tree, the update banner just never appears). Compared by DER
    SHA-256 rather than by count, because a count going up says nothing about
    which certificates went away (review finding, 2026-08-23).
    """
    os_roots = _fingerprints(ssl.create_default_context())
    ours = _fingerprints(platform.verify_context())

    assert os_roots, "no OS roots to compare against; this box cannot answer the question"
    assert os_roots <= ours, (
        f"{len(os_roots - ours)} OS root(s) are no longer trusted: certifi replaced the OS "
        "store instead of widening it"
    )


def test_the_real_certifi_bundle_has_the_roots_the_os_store_was_missing() -> None:
    """certifi is a declared requirement; the point of shipping it is the root count.

    The fresh Windows box had 18 CA certs and could not build a chain to Amazon
    Root CA 1. Mozilla's bundle carries well over a hundred, including that one,
    and every one of them is in the context on top of whatever the OS already
    trusted.
    """
    import certifi

    assert Path(certifi.where()).exists()
    ours = _fingerprints(platform.verify_context())

    assert platform.verify_context().cert_store_stats()["x509_ca"] > 100
    assert _pem_fingerprints(Path(certifi.where()).read_text(encoding="ascii")) <= ours


def test_adding_certifi_relaxes_nothing_the_default_context_had_set() -> None:
    """The widening must not be a downgrade wearing a wider hat.

    `load_verify_locations()` is documented to touch the store only, so every
    other knob has to still read exactly as `create_default_context()` left it —
    asserted rather than assumed, because this is the function that decides
    whether an elevated installer is trusted.
    """
    default = ssl.create_default_context()
    ours = platform.verify_context()

    assert ours.verify_mode is default.verify_mode is ssl.CERT_REQUIRED
    assert ours.check_hostname is default.check_hostname is True
    assert ours.verify_flags == default.verify_flags
    assert ours.options == default.options
    assert ours.minimum_version == default.minimum_version


def test_the_context_is_built_once_and_shared() -> None:
    """Not a micro-optimization: a refresh GETs 45 manifest files, one `urlopen` each.

    Assembling the union was measured at 211 ms per context on this dev box
    (14 ms for the OS store alone, 193 ms for certifi alone), so building one per
    call would put ~9.5 s of certificate parsing into a manifest refresh. An
    `ssl.SSLContext` holds no per-connection state, so one is shared.

    The memo is asserted before the identity check so that removing the
    `@lru_cache` fails HERE, saying what it cost, rather than as an error inside
    the autouse fixture that clears it (review finding, 2026-08-23).
    """
    assert hasattr(platform.verify_context, "cache_clear"), (
        "verify_context() lost its @lru_cache: every urlopen now reparses the root set, "
        "~211 ms each, 45 of them in one manifest refresh"
    )
    assert platform.verify_context() is platform.verify_context()


PACKAGE_ROOT = Path(platform.__file__).parent

# Every module in the package that opens an HTTPS connection of its own.
HTTPS_MODULES = (platform, manifest_store, update)


@pytest.mark.parametrize("module", HTTPS_MODULES, ids=lambda m: m.__name__)
def test_nothing_in_a_module_that_talks_https_can_switch_verification_off(
    module: types.ModuleType,
) -> None:
    """A guardrail, not a nicety: one of these downloads a file that is then run elevated.

    Widened from platform.py alone once `manifest_store` and `update` started
    verifying too — a guardrail that covers one of three callers is an invitation
    to fix a TLS error in whichever of them it is not watching.
    """
    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    name = Path(module.__file__ or "").name
    for forbidden in (
        "_create_unverified_context",
        "CERT_NONE",
        "check_hostname = False",
        "check_hostname=False",
        "--insecure",
        "verify=False",
    ):
        assert forbidden not in source, f"{forbidden} must never appear in {name}"


def test_every_urlopen_in_the_package_is_handed_a_verified_context() -> None:
    """The regression test for the actual defect: three `urlopen` calls with no context.

    `platform.py` grew `verify_context()` and `download_verified()`, and the
    public-IP probe, the manifest refresh and the update check kept calling
    `urlopen` bare — inheriting OpenSSL's snapshot of the Windows root store,
    which is the thing that was measured failing. Grepping for the callers we
    know about would not catch the fourth one someone adds next, so this walks
    the AST of every module in the package instead and insists that each call
    passes `context=`.
    """
    bare: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "urlopen":
                continue
            if not any(kw.arg == "context" for kw in node.keywords):
                bare.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}")
    assert not bare, "urlopen without context= (unverified on a fresh Windows box): " + ", ".join(
        bare
    )


# ------------------------------------------------- the small verified GETs


class _FakeUrlopen:
    """`urllib.request.urlopen` with no socket behind it, recording the TLS context.

    The three small GETs use `urlopen` as a context manager and read `.status` /
    `.headers`, which the downloader's `_FakeResponse` has no reason to carry.
    Patching the stdlib function is the only seam these three have — that they
    had no injectable one is part of why they were missed.
    """

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.status = 200
        self.headers: dict[str, str] = {}
        self.contexts: list[ssl.SSLContext] = []
        self.urls: list[str] = []

    def __call__(
        self,
        request: urllib.request.Request,
        timeout: float = 0.0,
        context: ssl.SSLContext | None = None,
    ) -> _FakeUrlopen:
        assert context is not None, f"{request.full_url} was opened without a TLS context"
        self.contexts.append(context)
        self.urls.append(request.full_url)
        return self

    def __enter__(self) -> _FakeUrlopen:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, amt: int = -1) -> bytes:
        return self.body


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, body: bytes) -> _FakeUrlopen:
    """Install a `_FakeUrlopen`; all three modules call the one stdlib function."""
    fake = _FakeUrlopen(body)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def _assert_verifying(fake: _FakeUrlopen) -> None:
    """Exactly one call, and the context it carried verifies the peer and the hostname."""
    assert len(fake.contexts) == 1
    assert fake.contexts[0].verify_mode is ssl.CERT_REQUIRED
    assert fake.contexts[0].check_hostname is True


def test_the_public_ip_probe_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_http_get_text` is the worst of the three: its TLS failure is reported as 'offline'."""
    fake = _patch_urlopen(monkeypatch, b"98.24.105.7\n")

    assert platform.detect_public_ip(services=("https://icanhazip.com",)).address == "98.24.105.7"

    assert fake.urls == ["https://icanhazip.com"]
    _assert_verifying(fake)


def test_the_manifest_refresh_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manifest names the repo to clone and the SQL to apply; it is not inert data."""
    fake = _patch_urlopen(monkeypatch, b'{"ok": true}')

    resp = manifest_store.urllib_get("https://raw.githubusercontent.com/o/r/b/x.json", None)

    assert resp.body == b'{"ok": true}'
    _assert_verifying(fake)


def test_the_update_check_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """It decides which version the user is told to install, from a response it must trust."""
    fake = _patch_urlopen(monkeypatch, b'{"tag_name": "v9.9.9"}')

    assert update.check_for_update("0.1.4").latest == "v9.9.9"

    _assert_verifying(fake)


# ------------------------------------------------------ what the user is told


def test_a_windows_install_that_cannot_verify_names_the_certificate_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The user gets the fixable cause, not just 'go download it yourself'."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)

    def refuse(url: str, dest: Path) -> Path:
        raise platform.DownloadError(f"{url}: {MEASURED_TLS_FAILURE}", verification=True)

    class _WinRun:
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            rc = 1 if argv[:2] == ["docker", "info"] else 0
            return subprocess.CompletedProcess(argv, rc, "", "")

    report = platform.ensure_docker(
        run=_WinRun(), which=lambda n: None, download=refuse, wait_seconds=0.0
    )

    assert any("root certificate" in step for step in report.manual_steps)
    assert any("docker-desktop" in step for step in report.manual_steps)
    assert any("CERTIFICATE_VERIFY_FAILED" in s for s in report.skipped)
    assert report.docker_ready is False


def test_a_download_that_merely_failed_does_not_blame_certificates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)

    def refuse(url: str, dest: Path) -> Path:
        raise TimeoutError("timed out")

    class _WinRun:
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            rc = 1 if argv[:2] == ["docker", "info"] else 0
            return subprocess.CompletedProcess(argv, rc, "", "")

    report = platform.ensure_docker(
        run=_WinRun(), which=lambda n: None, download=refuse, wait_seconds=0.0
    )

    assert not any("root certificate" in step for step in report.manual_steps)
    assert report.manual_steps == (platform._MANUAL_DOCKER_DESKTOP,)


def test_the_os_curl_is_taken_by_absolute_path_not_from_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A curl.exe earlier on PATH must not get to decide how an installer is verified."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert platform._os_curl() is None  # nothing there yet

    system32 = tmp_path / "System32"
    system32.mkdir()
    (system32 / "curl.exe").write_bytes(b"")
    assert platform._os_curl() == system32 / "curl.exe"


# ------------------------------------------- against a real, untrusted handshake


class _SelfSignedHandler(http.server.BaseHTTPRequestHandler):
    """Answers any GET with a plausible public IP. No client should ever read it."""

    def do_GET(self) -> None:  # noqa: N802 - the stdlib chooses this name
        body = b"203.0.113.7\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence the per-request line the stdlib prints to stderr."""


@pytest.fixture
def self_signed_https() -> Iterator[str]:
    """A real HTTPS server on 127.0.0.1 with a certificate no root store trusts.

    Loopback and a committed certificate, so this needs no network and reaches
    nothing outside the machine. Port 0 with the assigned port read back, because
    a hardcoded one collides with whatever else the runner is doing; a daemon
    thread, so a server that somehow refuses to stop cannot wedge the session;
    and the socket is listening before the fixture yields, so a client connects
    immediately and the handshake fails in milliseconds rather than sitting on a
    timeout.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(SELF_SIGNED_PEM))
    server = http.server.HTTPServer(("127.0.0.1", 0), _SelfSignedHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def test_urlopen_reports_a_refused_certificate_as_a_wrapped_url_error(
    self_signed_https: str,
) -> None:
    """The mechanism the rest of this section depends on, pinned to the real stack.

    `AbstractHTTPHandler.do_open` catches the `OSError` the handshake raises and
    re-raises it as `urllib.error.URLError(err)`, so an
    `ssl.SSLCertVerificationError` NEVER escapes `urlopen` — it arrives one level
    down, in `.reason`. Asserting the shape here means that if a future CPython
    stops wrapping, this test says so instead of the predicate quietly going back
    to being dead code (review finding, 2026-08-23).
    """
    with pytest.raises(urllib.error.URLError) as caught:
        platform._http_get_text(self_signed_https)

    assert isinstance(caught.value.reason, ssl.SSLCertVerificationError)
    assert "CERTIFICATE_VERIFY_FAILED" in str(caught.value)


def test_the_predicate_answers_true_for_what_a_real_handshake_actually_raises(
    self_signed_https: str,
) -> None:
    """The test whose absence let a dead fix pass two RED runs.

    Every other verification assertion in this suite and in test_networking.py
    constructs the exception by hand — `DownloadError(..., verification=True)`,
    or a bare `ssl.SSLCertVerificationError` — so all of them stayed green
    against a predicate that nothing in production could satisfy. This one hands
    `_is_verification_failure()` whatever the real `urllib` stack produced and
    accepts no substitute.
    """
    try:
        platform._http_get_text(self_signed_https)
    except OSError as exc:
        assert platform._is_verification_failure(exc) is True
    else:
        pytest.fail("the self-signed certificate was trusted; the server is not what it claims")


def test_the_public_ip_probe_calls_a_real_bad_certificate_a_certificate_problem(
    self_signed_https: str,
) -> None:
    """End to end over a socket: reached a server, refused to trust it, said so.

    The user-visible half is `networking.plan()`, which reads this flag to choose
    between the certificate sentence and "could not determine the public IP
    (offline?)" — a branch that measured as unreachable until the predicate was
    fixed.
    """
    probe = platform.detect_public_ip(services=(self_signed_https,))

    assert probe == platform.PublicIpResult(None, True)


def test_a_box_with_no_os_curl_still_gets_told_about_root_certificates(
    self_signed_https: str, tmp_path: Path
) -> None:
    """The corollary of the same defect: `download_verified()`'s urllib branch was silent.

    `verification` was only ever set from `_download_curl`'s exit code, because
    the `URLError` the urllib branch raises did not satisfy the predicate either.
    On a box with no OS curl — Windows before 1803, and the case that branch
    exists for — the installer failure therefore never mentioned the root store,
    which is the one thing the user could act on (review finding, 2026-08-23).
    """
    dest = tmp_path / "installer.exe"

    with pytest.raises(platform.DownloadError) as caught:
        platform.download_verified(self_signed_https, dest, find_curl=lambda: None)

    assert caught.value.verification is True
    assert platform._MANUAL_ROOT_CERTS in platform._download_manual_steps(caught.value)
    assert not dest.exists()
