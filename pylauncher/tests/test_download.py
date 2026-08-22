"""Tests for the verified installer download in `yulon.platform`.

Nothing here opens a socket: the curl transport is exercised through the `run`
seam and the in-process transport through an `open_url` seam that returns canned
responses. The assertions are about the thing that actually broke on a real
Windows 11 box (2026-08-22) — `urllib` could not verify desktop.docker.com
because OpenSSL cannot see the roots Windows fetches on demand — and about the
rule that came out of it: a download that cannot be verified fails loudly, it is
never retried with verification switched off.
"""

from __future__ import annotations

import ssl
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

import pytest

from yulon import platform

# Verbatim from the failing run on the fresh Windows 11 install.
MEASURED_TLS_FAILURE = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
    "local issuer certificate (_ssl.c:1000)"
)


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
    """Neither branch of `_verify_context()` may relax verification."""
    for masked in (False, True):
        with monkeypatch.context() as patch:
            if masked:
                patch.setitem(sys.modules, "certifi", None)
            context = platform._verify_context()
        assert context.verify_mode is ssl.CERT_REQUIRED
        assert context.check_hostname is True


def test_the_context_loads_certifis_bundle_when_it_is_importable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """certifi's path is handed to the context, not merely imported and ignored.

    Proven by pointing a stand-in certifi at a file that does not exist: only a
    context that actually loads `certifi.where()` can fail on it.
    """
    fake = types.ModuleType("certifi")
    missing = tmp_path / "no-such-cacert.pem"
    fake.where = lambda: str(missing)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "certifi", fake)

    with pytest.raises(OSError):
        platform._verify_context()


def test_the_real_certifi_bundle_has_the_roots_the_os_store_was_missing() -> None:
    """certifi is a declared requirement; the point of shipping it is the root count.

    The fresh Windows box had 18 CA certs and could not build a chain to Amazon
    Root CA 1. Mozilla's bundle carries well over a hundred, including that one.
    """
    import certifi

    context = platform._verify_context()
    assert Path(certifi.where()).exists()
    assert context.cert_store_stats()["x509_ca"] > 100


def test_nothing_in_the_module_can_switch_verification_off() -> None:
    """A guardrail, not a nicety: this module downloads a file that is then run elevated."""
    source = Path(platform.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "_create_unverified_context",
        "CERT_NONE",
        "check_hostname = False",
        "check_hostname=False",
        "--insecure",
        "verify=False",
    ):
        assert forbidden not in source, f"{forbidden} must never appear in platform.py"


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
