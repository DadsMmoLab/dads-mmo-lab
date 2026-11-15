"""WoW Forever on SteamOS and Linux: GE-Proton through Steam Linux Runtime 4, in its own prefix.

What Steam Deck #1 proved by hand (its kit v5, 2026-11-14), done by Play:

* **GE-Proton11-12**, the build the Deck plays on, in Steam's `compatibilitytools.d`: used
  when it is there, else downloaded once from its release at a pinned size and sha256.
* **Steam Linux Runtime 4.0** from the player's Steam library. Proton is always started
  through its `_v2-entry-point`; a bare `proton run` gave false results on the Deck.
* **A prefix of its own** in the install folder, never a Battle.net prefix: Battle.net would
  move the client past the build the realm accepts. Its folder name has digits, without
  which Proton's protonfixes does nothing and the launch silently fails.
* **The install's CA, trusted inside that prefix only** (`LocalMachine\\Root` of the prefix,
  through Windows' own crypt32 run by a Windows Python in the prefix), so the system's trust
  store is never touched. Proton forwards no Windows stdout, so the script writes a report.
* **The hosts line** for the portal, which needs root: `sudo -n` when that works, else the
  one command for the player to run, as everywhere else in Yu'lon on Linux.

The login key is `forever_keywatch`'s job, from Linux, beside the game.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import forever_trust, platform, play_launch, runner
from yulon.catalog.verified_download import sha256_of
from yulon.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PinnedFile:
    """A download Play needs, at the size and sha256 measured when it was pinned."""

    url: str
    name: str
    size: int
    sha256: str


GE_PROTON = PinnedFile(
    url=(
        "https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton11-12/"
        "GE-Proton11-12-x86_64.tar.gz"
    ),
    name="GE-Proton11-12-x86_64.tar.gz",
    size=566859228,
    sha256="0595159e417c3dbbfb7cc3ae0d3ed15a617cc607aa23ef756ea79029d7af5d70",
)
"""The Proton Steam Deck #1 plays WoW Forever on (GitHub's recorded digest, 2026-11-14)."""

GE_PROTON_FOLDER = "GE-Proton11-12-x86_64"
"""The folder the tarball unpacks to, and the one Steam lists it by."""

WINDOWS_PYTHON = PinnedFile(
    url="https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip",
    name="python-3.13.17-embed-amd64.zip",
    size=11441267,
    sha256="9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38",
)
"""The Windows Python that trusts the CA inside the prefix (Deck #1's, measured 2026-11-14)."""

PREFIX_NAME = "wowforever-offline-pfx-1"
"""The prefix folder in the install folder. The digits are required (protonfixes)."""

SLR_FOLDER = "SteamLinuxRuntime_4"
"""Steam Linux Runtime 4.0's folder in a Steam library."""

SLR_MISSING = (
    "Steam Linux Runtime 4.0 is not installed. In Steam: Library, search for "
    '"Steam Linux Runtime 4.0", Install. Then press Play again.'
)

HOSTS_MISSING = (
    "This computer does not send {portal} to 127.0.0.1 yet, and that needs root once. "
    "In Konsole (or any terminal) run:\n\n"
    "    echo '127.0.0.1 {portal}' | sudo tee -a /etc/hosts\n\n"
    "then press Play again."
)

REPORT_TRUSTED = "CA in LocalMachine\\Root: yes"

CA_THUMBPRINT_FILE = ".yulon-ca-thumbprint"
"""In the prefix: the CA's thumbprint as the prefix last imported it (T888).

Play writes it after a verified import, and the Gaming Mode tile writes it after its own
re-import; a tile that finds a different thumbprint on `certs/ca.pem` imports the new CA
before the game starts, because a remade CA is a root the prefix does not have."""

IMPORT_CA = r'''"""Written by Yu'lon: trust one CA in LocalMachine\\Root of THIS prefix only."""
import base64, ctypes, hashlib, re, sys
import ctypes.wintypes as wt

CA_NAME = "Yulon WoW Forever Local CA"
CERT_SIMPLE_NAME = 4
CERT_SHA1_HASH_PROP_ID = 3
ROOT_STORE = ctypes.c_void_p(10)
LOCAL_MACHINE = 2 << 16


def main():
    pem, report = sys.argv[1], sys.argv[2]
    stale = {t.upper() for t in sys.argv[3:]}
    def say(line):
        with open(report, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    m = re.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----",
                  open(pem, encoding="ascii").read(), re.S)
    if not m:
        say("no certificate in " + pem); return 2
    der = base64.b64decode("".join(m.group(1).split()))
    new = hashlib.sha1(der).hexdigest().upper()
    c = ctypes.WinDLL("crypt32", use_last_error=True)
    c.CertOpenStore.restype = ctypes.c_void_p
    c.CertOpenStore.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                ctypes.c_void_p]
    c.CertAddEncodedCertificateToStore.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_char_p,
                                                   wt.DWORD, wt.DWORD, ctypes.c_void_p]
    c.CertCloseStore.argtypes = [ctypes.c_void_p, wt.DWORD]
    c.CertEnumCertificatesInStore.restype = ctypes.c_void_p
    c.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    c.CertDuplicateCertificateContext.restype = ctypes.c_void_p
    c.CertDuplicateCertificateContext.argtypes = [ctypes.c_void_p]
    c.CertGetNameStringW.restype = wt.DWORD
    c.CertGetNameStringW.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                     ctypes.c_wchar_p, wt.DWORD]
    c.CertGetCertificateContextProperty.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                                    ctypes.POINTER(wt.DWORD)]
    c.CertDeleteCertificateFromStore.argtypes = [ctypes.c_void_p]
    c.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]

    def name_of(ctx):
        size = c.CertGetNameStringW(ctx, CERT_SIMPLE_NAME, 0, None, None, 0)
        if size <= 1:
            return ""
        buf = ctypes.create_unicode_buffer(size)
        if c.CertGetNameStringW(ctx, CERT_SIMPLE_NAME, 0, None, buf, size):
            return buf.value
        return ""

    def thumb_of(ctx):
        buf = ctypes.create_string_buffer(20)
        size = wt.DWORD(20)
        ok = c.CertGetCertificateContextProperty(ctx, CERT_SHA1_HASH_PROP_ID, buf,
                                                 ctypes.byref(size))
        return buf.raw[:size.value].hex().upper() if ok else ""

    def scan(store):
        # Every certificate in the store this script opened. Each call frees the context
        # the last one returned, so nothing keeps it past the next step.
        found = []
        ctx = c.CertEnumCertificatesInStore(store, None)
        while ctx:
            found.append((thumb_of(ctx), name_of(ctx)))
            ctx = c.CertEnumCertificatesInStore(store, ctx)
        return found

    store = c.CertOpenStore(ROOT_STORE, 0, None, LOCAL_MACHINE, ctypes.c_wchar_p("Root"))
    if not store:
        say("CertOpenStore failed, error %d" % ctypes.get_last_error()); return 1
    # A remade CA is a DIFFERENT root: the old ones must leave, or this prefix keeps
    # trusting a certificate whose key is gone (T888). Everything of ours goes -- by its
    # subject name, and by a thumbprint the install record calls stale -- except the one
    # being installed. Walk the store the ADD below uses: a context made from bytes is
    # in no store at all, and deleting it deletes nothing.
    doomed, targets = [], {}
    ctx = c.CertEnumCertificatesInStore(store, None)
    while ctx:
        thumb, name = thumb_of(ctx), name_of(ctx)
        if thumb and thumb != new and (name == CA_NAME or thumb in stale):
            dup = c.CertDuplicateCertificateContext(ctx)  # the next call frees ctx
            if dup:
                doomed.append(dup)
                targets[thumb] = name
        ctx = c.CertEnumCertificatesInStore(store, ctx)
    for dup in doomed:
        c.CertDeleteCertificateFromStore(dup)  # frees the context too
    # A delete only ASKED the store: a second walk says which ones are really gone, and
    # only those are reported as removed.
    left = {thumb for thumb, _ in scan(store)}
    removed = sum(1 for thumb in targets if thumb not in left)
    for thumb, name in targets.items():
        if thumb in left:
            say("could not delete %s (%s)" % (name, thumb))
    ok = c.CertAddEncodedCertificateToStore(store, 1, der, len(der), 3, None)
    err = ctypes.get_last_error()
    present = new in {thumb for thumb, _ in scan(store)}
    c.CertCloseStore(store, 0)
    if not ok:
        say("CertAddEncodedCertificateToStore failed, error %d" % err); return 1
    say("removed %d superseded certificate(s)" % removed)
    say("CA in LocalMachine\\Root: " + ("yes" if present else "NO"))
    return 0 if present else 1

sys.exit(main())
'''
"""Deck #1's `import_ca.py`, cut to what Play needs."""


class ProtonError(OSError):
    """Play cannot start the game under Proton; the message says what to do."""


RunCmd = Callable[..., object]


@dataclass(frozen=True)
class ProtonSeams:
    """What this module reaches outside itself through. Real by default."""

    home: Callable[[], Path] = Path.home
    download: Callable[[str, Path], Path] = lambda url, dest: platform.download_verified(url, dest)
    run: Callable[..., object] = runner.run
    launch: Callable[[play_launch.LaunchSpec], None] = play_launch.launch
    read_hosts: Callable[[], str] = lambda: Path("/etc/hosts").read_text(errors="replace")
    which: Callable[[str], str | None] = shutil.which


def winpath(path: Path) -> str:
    """`path` as the prefix sees it: Wine's Z: drive is the Linux root."""
    return "Z:" + str(path).replace("/", "\\")


def steam_root(home: Path) -> Path:
    """The Steam install Proton reads its libraries from (`STEAM_COMPAT_CLIENT_INSTALL_PATH`)."""
    for root in (home / ".local/share/Steam", home / ".steam/steam"):
        if (root / "steamapps").is_dir():
            return root
    return home / ".local/share/Steam"


def library_folders(home: Path, media: Path = Path("/run/media")) -> list[Path]:
    """Every `steamapps` folder: Steam's own, then each SD card or drive's."""
    found = [steam_root(home) / "steamapps"]
    if media.is_dir():
        found += sorted(media.glob("*/*/steamapps")) + sorted(media.glob("*/steamapps"))
    return [p for p in found if p.is_dir()]


def find_slr(home: Path, media: Path = Path("/run/media")) -> Path | None:
    """Steam Linux Runtime 4.0's `_v2-entry-point`, in whichever library holds it."""
    for library in library_folders(home, media):
        entry = library / "common" / SLR_FOLDER / "_v2-entry-point"
        if entry.is_file():
            return entry
    return None


def compat_tools(home: Path) -> Path:
    """Steam's folder for compatibility tools it did not ship (GE-Proton goes here)."""
    return steam_root(home) / "compatibilitytools.d"


def matches(path: Path, pinned: PinnedFile) -> bool:
    """`path` is the pinned file: its size first (cheap), then its sha256."""
    return (
        path.is_file()
        and path.stat().st_size == pinned.size
        and sha256_of(path).lower() == pinned.sha256
    )


def fetch(pinned: PinnedFile, dest: Path, say: Callable[[str], None], seams: ProtonSeams) -> Path:
    """`dest` holding `pinned`, downloaded if it is not there already. Raises ProtonError."""
    if matches(dest, pinned):
        return dest
    dest.unlink(missing_ok=True)
    say(f"Downloading {pinned.name} ({pinned.size / 1e6:.0f} MB) once...")
    try:
        seams.download(pinned.url, dest)
    except OSError as exc:
        raise ProtonError(f"{pinned.name} could not be downloaded ({exc}).") from exc
    if not matches(dest, pinned):
        dest.unlink(missing_ok=True)
        raise ProtonError(
            f"{pinned.name} did not match its pinned size and sha256, so it was deleted. "
            "Press Play again to download it again."
        )
    return dest


def safe_members(members: Iterable[tarfile.TarInfo], top: str) -> list[tarfile.TarInfo]:
    """The tarball's members, refused whole if one would land outside `top/`."""
    kept: list[tarfile.TarInfo] = []
    for member in members:
        name = member.name
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts or not parts or parts[0] != top:
            raise ProtonError(f"{GE_PROTON.name} holds {name!r}, outside {top}/; not unpacked.")
        if member.islnk() and (
            ".." in Path(member.linkname).parts or member.linkname.startswith("/")
        ):
            raise ProtonError(f"{GE_PROTON.name} links {name!r} outside itself; not unpacked.")
        kept.append(member)
    return kept


def ensure_proton(say: Callable[[str], None], seams: ProtonSeams) -> Path:
    """GE-Proton11-12's folder, unpacked into `compatibilitytools.d` first if need be."""
    tools = compat_tools(seams.home())
    folder = tools / GE_PROTON_FOLDER
    if (folder / "proton").is_file():
        return folder
    downloads = tools / ".yulon-downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    tarball = fetch(GE_PROTON, downloads / GE_PROTON.name, say, seams)
    say(f"Unpacking {GE_PROTON_FOLDER} into {tools}...")
    staging = tools / f".{GE_PROTON_FOLDER}.unpacking"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(tarball, "r:gz") as tar:
        members = safe_members(tar.getmembers(), GE_PROTON_FOLDER)
        tar.extractall(staging, members=members, filter="tar")
    if not (staging / GE_PROTON_FOLDER / "proton").is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise ProtonError(f"{GE_PROTON.name} unpacked without a proton script; not used.")
    (staging / GE_PROTON_FOLDER).rename(folder)
    shutil.rmtree(staging, ignore_errors=True)
    tarball.unlink(missing_ok=True)
    return folder


def compat_env(
    home: Path, prefix: Path, client_dir: Path, server_dir: Path, extra: Mapping[str, str] = {}
) -> dict[str, str]:
    """What Proton and the runtime read: the prefix, Steam, and the folders it must see."""
    steam = steam_root(home)
    mounts = sorted({str(client_dir), str(server_dir)})
    return {
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam),
        "STEAM_COMPAT_DATA_PATH": str(prefix),
        "STEAM_COMPAT_INSTALL_PATH": str(client_dir),
        "STEAM_COMPAT_LIBRARY_PATHS": ":".join([str(steam), *mounts]),
        "STEAM_COMPAT_MOUNTS": ":".join(mounts),
        "WINE_SIMULATE_WRITECOPY": "1",
        **extra,
    }


def proton_argv(slr: Path, proton: Path, program: str, args: Iterable[str]) -> list[str]:
    """Run `program` under `proton` inside the runtime, waiting for it (Deck #1's command)."""
    return [
        str(slr),
        "--verb=waitforexitandrun",
        "--",
        str(proton / "proton"),
        "waitforexitandrun",
        program,
        *args,
    ]


def _env(base: Mapping[str, str]) -> dict[str, str]:
    """The runtime's environment: the frozen build's libraries and Steam's overlay left out."""
    env = dict(runner.child_env() or os.environ)
    env.pop("LD_PRELOAD", None)
    env.update(base)
    return env


@dataclass(frozen=True)
class Runtime:
    """Everything a Proton run needs, found or made once per Play."""

    slr: Path
    proton: Path
    prefix: Path
    compat: dict[str, str]
    """What Proton reads (`compat_env`): all a launch spec adds to the environment."""
    env: dict[str, str]
    """`compat` over a clean copy of this process's environment, for the runs Play waits for."""


def prepare(
    server_dir: Path,
    client_dir: Path,
    ca: Path,
    say: Callable[[str], None],
    *,
    portal: str,
    stale: Sequence[str] = (),
    seams: ProtonSeams | None = None,
) -> Runtime:
    """Runtime, Proton, prefix, Windows Python and the trusted CA. Raises ProtonError.

    The CA is checked first (`forever_trust.verified_ca`): a prefix is a trust store too,
    and a CA that may vouch for any name must not go into one. Raises `TrustError` for
    that, before anything is written into the prefix.
    """
    s = seams if seams is not None else ProtonSeams()
    home = s.home()
    slr = find_slr(home)
    if slr is None:
        raise ProtonError(SLR_MISSING)
    proton = ensure_proton(say, s)
    prefix = server_dir / PREFIX_NAME
    prefix.mkdir(parents=True, exist_ok=True)
    compat = compat_env(home, prefix, client_dir, server_dir)
    runtime = Runtime(slr=slr, proton=proton, prefix=prefix, compat=compat, env=_env(compat))
    if not (prefix / "pfx" / "drive_c" / "windows").is_dir():
        say("Making the game's own Wine prefix (the first time only, about a minute)...")
        _run(runtime, "C:\\windows\\system32\\cmd.exe", ["/c", "exit", "0"], s, timeout=600)
        if not (prefix / "pfx" / "drive_c" / "windows").is_dir():
            raise ProtonError(f"Proton did not make its prefix in {prefix}.")
    checked = forever_trust.verified_ca(ca, portal)
    try:
        trust_ca(runtime, checked, server_dir, say, s, stale=stale)
    finally:
        checked.unlink(missing_ok=True)
    return runtime


def _run(runtime: Runtime, program: str, args: list[str], s: ProtonSeams, timeout: float) -> None:
    argv = proton_argv(runtime.slr, runtime.proton, program, args)
    try:
        s.run(argv, cwd=runtime.prefix, env=runtime.env, timeout=timeout)
    except (OSError, TimeoutError) as exc:
        raise ProtonError(f"Proton could not run {program} ({exc}).") from exc


def trust_ca(
    runtime: Runtime,
    ca: Path,
    server_dir: Path,
    say: Callable[[str], None],
    s: ProtonSeams,
    *,
    stale: Sequence[str] = (),
) -> None:
    """The install's CA in the prefix's `LocalMachine\\Root`, checked by its report. Every Play.

    The script deletes every superseded CA of ours from that store first (its subject name,
    or a thumbprint `stale` names), so a remade CA does not leave the old root -- whose key
    is gone -- trusted inside the game's prefix (T888). Its argv carries the certificate,
    the report, and those thumbprints; the prefix records the thumbprint it imported.
    """
    python_dir = runtime.prefix / "pfx" / "drive_c" / "python313"
    if not (python_dir / "python.exe").is_file():
        zipped = fetch(WINDOWS_PYTHON, server_dir / "downloads" / WINDOWS_PYTHON.name, say, s)
        with zipfile.ZipFile(zipped) as archive:
            archive.extractall(python_dir)
    script = runtime.prefix / "import_ca.py"
    report = runtime.prefix / "import_ca-report.txt"
    script.write_text(IMPORT_CA, encoding="utf-8")
    report.unlink(missing_ok=True)
    _run(
        runtime,
        str(python_dir / "python.exe"),
        ["-I", winpath(script), winpath(ca), winpath(report), *stale],
        s,
        timeout=300,
    )
    said = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
    if REPORT_TRUSTED not in said:
        raise ProtonError(
            "The game's prefix did not take the install's certificate"
            + (f": {said.strip()}" if said.strip() else " (no report was written).")
        )
    try:
        (runtime.prefix / CA_THUMBPRINT_FILE).write_text(
            forever_trust.thumbprint(ca) + "\n", encoding="ascii"
        )
    except OSError as exc:
        logger.warning(f"could not record the prefix's CA thumbprint: {exc}")


def hosts_ready(portal: str, s: ProtonSeams | None = None) -> bool:
    """`/etc/hosts` sends `portal` to 127.0.0.1, adding the line with `sudo -n` if that works."""
    seams = s if s is not None else ProtonSeams()
    try:
        if forever_trust.hosts_maps(seams.read_hosts(), portal):
            return True
    except OSError:
        pass
    if seams.which("sudo") is None:
        return False
    line = f"127.0.0.1 {portal} # yulon-wow-forever"
    try:
        seams.run(
            ["sudo", "-n", "sh", "-c", f"printf '\\n%s\\n' '{line}' >> /etc/hosts"], timeout=10
        )
    except (OSError, TimeoutError):
        return False
    try:
        return forever_trust.hosts_maps(seams.read_hosts(), portal)
    except OSError:
        return False


def start_game(
    runtime: Runtime, exe: Path, args: Iterable[str], s: ProtonSeams | None = None
) -> None:
    """Start the game under Proton, detached: closing Yu'lon never closes it."""
    seams = s if s is not None else ProtonSeams()
    argv = proton_argv(runtime.slr, runtime.proton, str(exe), args)
    seams.launch(play_launch.LaunchSpec(argv=tuple(argv), cwd=exe.parent, env=runtime.compat))
