"""Start Yu'lon in the tray when the player signs in (T540). Off unless they turn it on.

One entry per system, each naming the INSTALLED Yu'lon with `--tray`, so it opens
hidden in the tray rather than as a window:

- **Windows:** the value "Yu'lon" under `HKCU\\Software\\Microsoft\\Windows\\
  CurrentVersion\\Run`. The path is QUOTED: an unquoted path with a space sends
  Windows' `CreateProcess` hunting `C:\\Program.exe` first (rust-main's
  `autostart.rs`, where this rule comes from).
- **Linux:** `$XDG_CONFIG_HOME/autostart/org.dadsmmolab.yulon.desktop`
  (`~/.config` without the variable), the XDG autostart spec that GNOME, KDE and
  the rest read. Written to a temporary name and renamed over, as `ui.json` is.
- **macOS:** `~/Library/LaunchAgents/org.dadsmmolab.yulon.plist` with
  `RunAtLoad`, read by launchd at the next sign-in. Unit-tested; not proved on a
  Mac (none to prove it on).

**"On" means it would work** (rust-main's rule): the entry exists AND the
program it names exists. An entry left by a build that was moved or deleted
starts nothing, and a switch that said "on" over it would be lying.

What is launched comes from `selfupdate.detect.detect_install()`, the same
answer the self-update acts on: the AppImage's own file, the folder install's
executable, the app bundle's executable. A source checkout has nothing stable to
name, so it is not offered there.
"""

from __future__ import annotations

import importlib
import os
import plistlib
import sys
import tempfile
from pathlib import Path
from typing import Any

from yulon import platform
from yulon.log import get_logger
from yulon.selfupdate.detect import Install, InstallKind, detect_install

logger = get_logger(__name__)

APP_ID = "org.dadsmmolab.yulon"
TRAY_ARG = "--tray"
"""What the entry passes: open hidden in the tray (`main.main`)."""
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Yu'lon"
DESKTOP_NAME = f"{APP_ID}.desktop"
AGENT_NAME = f"{APP_ID}.plist"

NOT_INSTALLED = "Only an installed Yu'lon can start when you sign in."
UNSUPPORTED = "Starting at sign-in is not available for this build of Yu'lon."


def _winreg() -> Any:
    """`winreg`, imported at the call: it exists only on Windows, and a test fakes it."""
    return importlib.import_module("winreg")


def launch_program(install: Install | None = None) -> Path | None:
    """The file an entry starts, or None where there is none (a source checkout)."""
    install = install if install is not None else detect_install()
    if install.kind is InstallKind.APPIMAGE:
        return install.target
    if install.kind in (InstallKind.WINDOWS_ZIP, InstallKind.TARBALL):
        return install.target / install.executable if install.target is not None else None
    if install.kind is InstallKind.MACOS_APP:
        return Path(sys.executable)
    return None


def why_not(install: Install | None = None, *, platform_id: str | None = None) -> str | None:
    """Why the switch is greyed here, or None when it can be turned on."""
    install = install if install is not None else detect_install()
    if install.kind is InstallKind.SOURCE:
        return NOT_INSTALLED
    if launch_program(install) is None:
        return UNSUPPORTED
    return None


def is_enabled(install: Install | None = None, *, platform_id: str | None = None) -> bool:
    """Whether this system will start Yu'lon at sign-in: the entry is there and names a file."""
    which = platform_id or platform.detect()
    try:
        if which == "windows":
            program = _windows_program()
        elif which == "macos":
            program = _agent_program()
        else:
            program = _desktop_program()
    except OSError as exc:
        logger.info(f"autostart: could not read the sign-in entry: {exc}")
        return False
    return program is not None and program.is_file()


def set_enabled(
    on: bool, install: Install | None = None, *, platform_id: str | None = None
) -> None:
    """Write or remove the sign-in entry. Raises OSError with what went wrong."""
    which = platform_id or platform.detect()
    program = launch_program(install)
    if on and program is None:
        raise OSError(NOT_INSTALLED)
    if which == "windows":
        _set_windows(program if on else None)
    elif which == "macos":
        _set_agent(program if on else None)
    else:
        _set_desktop(program if on else None)
    logger.info(f"autostart: start at sign-in turned {'on' if on else 'off'} ({which})")


# ------------------------------------------------------------------- Windows


def _windows_program() -> Path | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return None
    text = str(value).strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return Path(text[1:end]) if end > 0 else None
    return Path(text.split(" --", 1)[0])


def _set_windows(program: Path | None) -> None:
    winreg = _winreg()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if program is None:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass  # already off
            return
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{program}" {TRAY_ARG}')


# --------------------------------------------------------------------- Linux


def desktop_entry_path() -> Path:
    """`$XDG_CONFIG_HOME/autostart/<id>.desktop`, `~/.config` when the variable is unset."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if os.path.isabs(base) else Path.home() / ".config"
    return root / "autostart" / DESKTOP_NAME


def desktop_exec(program: Path) -> str:
    """The `Exec=` value: the program quoted as the Desktop Entry spec says, then `--tray`.

    Inside quotes `"`, `` ` ``, `$` and `\\` are backslash-escaped, and `%` is a
    field code everywhere, so a literal one is `%%`.
    """
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in str(program)).replace("%", "%%")
    return f'"{quoted}" {TRAY_ARG}'


def _desktop_text(program: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Yu'lon\n"
        "Comment=Dad's MMO Lab: keeps your servers in the tray\n"
        f"Exec={desktop_exec(program)}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _desktop_program() -> Path | None:
    path = desktop_entry_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        if line.strip().lower() in ("hidden=true", "x-gnome-autostart-enabled=false"):
            return None
    for line in text.splitlines():
        if line.startswith("Exec="):
            value = line[len("Exec=") :].strip()
            if value.startswith('"'):
                out, i = [], 1
                while i < len(value) and value[i] != '"':
                    if value[i] == "\\" and i + 1 < len(value):
                        i += 1
                    out.append(value[i])
                    i += 1
                return Path("".join(out).replace("%%", "%"))
            return Path(value.split(" ", 1)[0])
    return None


def _set_desktop(program: Path | None) -> None:
    path = desktop_entry_path()
    if program is None:
        path.unlink(missing_ok=True)
        return
    _write_file(path, _desktop_text(program).encode("utf-8"))


# --------------------------------------------------------------------- macOS


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / AGENT_NAME


def _agent_program() -> Path | None:
    path = launch_agent_path()
    try:
        data = plistlib.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (ValueError, plistlib.InvalidFileException) as exc:
        raise OSError(f"{path} is not a property list: {exc}") from exc
    arguments = data.get("ProgramArguments") if isinstance(data, dict) else None
    if not isinstance(arguments, list) or not arguments:
        return None
    return Path(str(arguments[0]))


def _set_agent(program: Path | None) -> None:
    path = launch_agent_path()
    if program is None:
        path.unlink(missing_ok=True)
        return
    agent = {
        "Label": APP_ID,
        "ProgramArguments": [str(program), TRAY_ARG],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }
    _write_file(path, plistlib.dumps(agent))


# --------------------------------------------------------------------- files


def _write_file(target: Path, content: bytes) -> None:
    """Write `target` through a unique temporary name and a rename, never half a file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    os.close(handle)
    tmp = Path(name)
    try:
        tmp.write_bytes(content)
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ------------------------------------------------- Windows notification identity

APP_ID_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"
APP_NAME = "Yu'lon"


def register_app_id(*, platform_id: str | None = None) -> bool:
    """Name Yu'lon's AppUserModelID for Windows, so its tray notifications are shown. Never raises.

    `main()` gives the process the explicit ID `org.dadsmmolab.yulon` (the
    taskbar groups by it). Windows shows a notification from an app with an
    explicit ID only when it can name that ID: a Start-menu shortcut carrying it,
    or this registry key with a `DisplayName`. A zip install has no shortcut,
    and on yulon-win11 every tray notification was dropped without a word while a
    plain balloon from another process showed (T540, 2026-10-07). Off Windows
    there is nothing to do. True when the name is in place.
    """
    which = platform_id or platform.detect()
    if which != "windows":
        return False
    try:
        winreg = _winreg()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, APP_ID_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
    except OSError as exc:
        logger.info(f"notifications: could not name the app ID {APP_ID}: {exc}")
        return False
    return True
