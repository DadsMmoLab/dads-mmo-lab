"""Add to Steam Deck (Gaming Mode)…: the Lab's Gaming Mode launchers for one single server.

Linux and Steam Deck only, and nothing is drawn anywhere else: `main.py` hands the server's
tab no seam off Linux, as `controller_view._steam_seam()` does for 8.8's Add to Steam…. The
label says Steam Deck so nobody reads it as a Windows feature.

One press, for one install:

1. Renders the game's three scripts from `catalog/installers/<templates>/`: the server
   launcher, the client and the stop. They go into `<config_dir>/gaming-mode/<game>/`, a
   stable folder outside the AppImage's temporary mount. The Lab's shared
   `~/dml-launcher-lib.sh`, which the launcher sources, is installed when it is missing or
   older than ours, and never replaced by an older one: DadsMmoLab.sh and every Lab
   launcher source the same file.
2. Adds three non-Steam shortcuts through `steam.py`'s codec, with every one of its
   refusals and checks: "<title> Server" (`konsole --hold -e bash <launcher>`, the Lab's
   pattern, so its output shows in Gaming Mode), "<title>" (the client script) and
   "<title> Server Stop". A shortcut of the same name is replaced in place and keeps its
   appid. None of the three gets a compatibility tool: the client script runs Proton
   itself, inside the Steam Linux Runtime, and a compatibility tool would wrap that
   Proton in another.

The scripts were written and measured on Deck #2 on 2026-10-16 by the Steam Deck session,
and are shipped as written. The values filled per machine are `@NAME@` markers, because
the scripts hold Docker's own `{{.State.Running}}`, which `composegen.fill()` rightly
refuses: the home folder, this server's folder, the account, Yu'lon's data folder, the
scripts' own folder, and (DDON's) the Proton the client falls back to when Yu'lon's prefix
does not exist yet. Only the markers a game's scripts use are filled, and only those are
checked: DFO's client finds Proton 11 in Steam's libraries itself. With Deck #2's values
DDON's rendered scripts are byte-identical to the ones measured there, which a test holds.

**Play on the desktop runs the same client script** for a game whose `gaming_mode` says
`desktop_play` (DFO): its game has to join the server's private network, which only the
script does (`process_controller.py`). `play_spec()` hands it to `bash -c`, so nothing is
written for a Play.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from yulon import platform, play_launch, resources, single_play, steam
from yulon.catalog.catalog import SingleEntry, SingleGamingMode
from yulon.log import get_logger

logger = get_logger(__name__)

BUTTON = "Add to Steam Deck (Gaming Mode)…"
"""The button's label, and the name every refusal tells the player to press again."""

LIB_FILE = "dml-launcher-lib.sh"
LIB_TEMPLATE = Path("steam-deck") / LIB_FILE
"""The Lab's shared launcher library, shipped as the Lab wrote it (no markers in it)."""

KONSOLE = steam.KONSOLE
KONSOLE_DIR = steam.KONSOLE_DIR
SNIPER = Path(".steam/steam/steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point")
"""Where the client script looks for the Steam Linux Runtime, under the home folder."""

SCRIPT_MODE = 0o755
LIB_MODE = 0o644

_MARKER = re.compile(r"@([A-Z][A-Z0-9_]*)@")
_UNSAFE = re.compile(r'["$`\n]|\\(?:\\|$)')
"""What a filled value may not hold: the scripts put every value inside double quotes.

Inside them a backslash is literal except before `$ ` " \\` or a line break; with the
others refused, only a doubled backslash or one at the end (which would escape the
closing quote) is left to refuse.
"""
_LIB_VERSION = re.compile(r'^DML_LIB_VERSION="([0-9]+(?:\.[0-9]+)*)"', re.MULTILINE)

NOT_LINUX = (
    "Gaming Mode shortcuts are a Steam Deck and Linux feature ({platform_id} was "
    "detected). Nothing was written."
)
NO_GAMING_MODE = "{name} has no Steam Deck Gaming Mode scripts yet. Nothing was written."
NO_CLIENT = (
    "Choose your {name} client folder first: the Gaming Mode client starts the game "
    "from it. Nothing was written."
)
NO_CLIENT_EXE = (
    "There is no {exe} in {folder}, so the Gaming Mode client would have nothing to "
    "start. Choose {client}. Nothing was written."
)
NO_SERVER = (
    "{folder} is not there any more, so there is no server for these shortcuts to "
    "start. Nothing was written."
)
NO_PROTON = (
    "The Gaming Mode client starts {name} with the Proton that made Yu'lon's {name} "
    "prefix, or else a Proton in {tools}. {problem} Install GE-Proton there, or press "
    "Play once so Yu'lon makes the prefix, then press " + BUTTON + " again. Nothing was "
    "written."
)


def _for_this_button(message: str) -> str:
    """One of `steam.py`'s refusals, telling the player to press this button again."""
    return message.replace("Add to Steam…", BUTTON)


def render(template: str, values: Mapping[str, str]) -> str:
    """`@NAME@` markers filled from `values`; a marker with no value is an error."""

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"the template asks for @{name}@, which nothing fills")
        return values[name]

    return _MARKER.sub(fill, template)


def markers(*templates: str) -> set[str]:
    """Every `@NAME@` marker the templates ask for."""
    return {name for template in templates for name in _MARKER.findall(template)}


def check_values(values: Mapping[str, str]) -> str | None:
    """Why a value cannot go into a script, or None: each lands inside double quotes."""
    for name, value in values.items():
        if _UNSAFE.search(value):
            return (
                f"{value!r} ({name.lower().replace('_', ' ')}) holds a character a shell "
                'script cannot quote (" $ ` a line break, a doubled backslash or one at '
                "the end)."
            )
    return None


def lib_version(text: str) -> tuple[int, ...] | None:
    """The `DML_LIB_VERSION` a copy of the library declares, or None if it declares none."""
    match = _LIB_VERSION.search(text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _dotted(version: tuple[int, ...] | None) -> str:
    return ".".join(str(part) for part in version) if version else "no version"


@dataclass(frozen=True)
class GamingModeReport:
    """What one press did, for the sentence the tab shows."""

    entries: tuple[str, ...]
    scripts: tuple[Path, ...]
    lib: str
    path: Path
    backup: Path | None
    replaced: bool
    notes: tuple[str, ...] = ()


def confirmation(report: GamingModeReport) -> str:
    """The tab's message after a press that worked."""
    server, game, stop = report.entries
    lines = [
        f"{'Updated' if report.replaced else 'Added'} three shortcuts in Steam:",
        f"• {server}: starts the server. Leave its window open while you play.",
        f"• {game}: the game. Launch it once the server says it is ready.",
        f"• {stop}: stops the server if it is still running.",
        "",
        "Start Steam, or switch to Gaming Mode, to see them.",
        f"The scripts are in {report.scripts[0].parent}. {report.lib}",
    ]
    if report.backup is not None:
        lines.append(f"Your shortcut list from before is kept at {report.backup}.")
    lines.extend(report.notes)
    return "\n".join(lines)


def lib_plan(home: Path, installers_root: Path) -> tuple[Path, bytes, bool, str]:
    """The Lab's launcher library: where it goes, ours, whether to write it, and what was done.

    Installed when it is missing or older than ours, and never replaced by an older one:
    DadsMmoLab.sh and every Lab launcher source the same `~/dml-launcher-lib.sh`.
    """
    lib_path = home / LIB_FILE
    try:
        ours = (installers_root / LIB_TEMPLATE).read_bytes()
    except OSError as exc:
        raise steam.SteamRefusal(
            f"The Lab's launcher library could not be read: {exc}. Nothing was written."
        ) from exc
    our_version = lib_version(ours.decode("utf-8", errors="replace"))
    their_version: tuple[int, ...] | None = None
    if lib_path.is_file():
        try:
            theirs = lib_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise steam.SteamRefusal(
                f"{lib_path} cannot be read ({exc.strerror or exc}). Nothing was written."
            ) from exc
        their_version = lib_version(theirs) or ()
        install_lib = their_version < (our_version or ())
    else:
        install_lib = True
    if not install_lib:
        sentence = (
            f"{lib_path} is version {_dotted(their_version)}, at least as new as "
            f"Yu'lon's {_dotted(our_version)}, so it was kept."
        )
    elif their_version is None:
        sentence = f"{lib_path} was installed ({_dotted(our_version)})."
    else:
        sentence = (
            f"{lib_path} was updated from {_dotted(their_version)} to {_dotted(our_version)}."
        )
    return lib_path, ours, install_lib, sentence


def _write_file(path: Path, data: bytes, mode: int) -> None:
    """Write `path` whole, through a temp file beside it, with `mode`."""
    temporary = path.with_name(path.name + steam.TMP_SUFFIX)
    try:
        temporary.write_bytes(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


@dataclass
class GamingMode:
    """One install's Gaming Mode scripts and shortcuts. Every seam is injectable.

    As in `steam.SteamShortcuts`: the tests run on machines with a Steam profile of their
    own, so nothing here reaches `Path.home()` or the real config folder unless asked to.
    """

    entry: SingleEntry
    server_dir: Path
    home: Path = field(default_factory=Path.home)
    config_dir: Path = field(default_factory=platform.config_dir)
    installers_root: Path = field(default_factory=resources.installers_dir)
    is_running: Callable[[], bool] = steam.steam_running
    now: Callable[[], datetime] = datetime.now
    platform_id: Callable[[], str] = platform.detect

    def _gaming_mode(self) -> SingleGamingMode:
        play = self.entry.play
        if play is None or play.gaming_mode is None:
            raise steam.SteamRefusal(NO_GAMING_MODE.format(name=self.entry.name))
        return play.gaming_mode

    def scripts_dir(self) -> Path:
        """Where this game's three scripts live: stable, and Yu'lon's own."""
        return self.config_dir / "gaming-mode" / self.entry.id

    def values(self, account: str) -> dict[str, str]:
        """The markers every game's scripts may use; `PROTON_NAME` is added by `add()`."""
        return {
            "HOME": str(self.home),
            "SERVER_DIR": str(self.server_dir),
            "ACCOUNT": account,
            "CONFIG_DIR": str(self.config_dir),
            "SCRIPTS_DIR": str(self.scripts_dir()),
        }

    def _proton_name(self, steam_root: Path) -> str:
        """The Proton folder name the client script will open, checked to exist.

        The script reads the name off Yu'lon's prefix (`version`, written by the Proton
        that made it) and looks for it in `compatibilitytools.d`; without a prefix it
        uses the default rendered here, the best tool `steam.py` finds in that folder.
        """
        tools = steam_root / "compatibilitytools.d"
        prefix = play_launch.prefix_path(self.config_dir, self.entry.id, self.server_dir)
        version = prefix / "version"
        if version.is_file():
            try:
                name = version.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as exc:
                raise steam.SteamRefusal(
                    NO_PROTON.format(
                        name=self.entry.name, tools=tools, problem=f"{version} cannot be read."
                    )
                ) from exc
            if not (tools / name / "proton").is_file():
                raise steam.SteamRefusal(
                    NO_PROTON.format(
                        name=self.entry.name,
                        tools=tools,
                        problem=f"The prefix was made by {name!r}, which is not there.",
                    )
                )
            return name
        for _name, folder in steam._proton_candidates(steam_root):
            if folder.parent == tools and (folder / "proton").is_file():
                return folder.name
        raise steam.SteamRefusal(
            NO_PROTON.format(
                name=self.entry.name,
                tools=tools,
                problem="There is no prefix yet, and no Proton there either.",
            )
        )

    def add(self, client_dir: Path | None, account: str) -> GamingModeReport:
        """Write the three scripts, the library if ours is newer, and the three shortcuts.

        Every refusal comes before the first byte; the shortcut list is backed up before
        it is written, and the scripts are written before the shortcuts that point at them.
        """
        detected = self.platform_id()
        if detected != "linux":
            raise steam.SteamRefusal(NOT_LINUX.format(platform_id=detected))
        gaming = self._gaming_mode()
        play = self.entry.play
        assert play is not None  # `_gaming_mode()` refused otherwise
        if client_dir is None:
            raise steam.SteamRefusal(NO_CLIENT.format(name=self.entry.name))
        if not (client_dir / play.exe).is_file():
            raise steam.SteamRefusal(
                NO_CLIENT_EXE.format(exe=play.exe, folder=client_dir, client=play.client)
            )
        problem = single_play.check_account(account)
        if problem is not None:
            raise steam.SteamRefusal(f"{problem} Nothing was written.")
        if not self.server_dir.is_dir():
            raise steam.SteamRefusal(NO_SERVER.format(folder=self.server_dir))
        if self.is_running():
            raise steam.SteamRefusal(_for_this_button(steam.RUNNING))
        try:
            config = steam.find_profile(self.home)
        except steam.SteamRefusal as exc:
            raise steam.SteamRefusal(_for_this_button(str(exc))) from exc
        steam_root = config.parent.parent.parent

        folder = self.installers_root / gaming.templates
        scripts_dir = self.scripts_dir()
        names = (gaming.launcher_file, gaming.client_file, gaming.stop_file)
        templates: list[str] = []
        for name in names:
            try:
                templates.append((folder / name).read_text(encoding="utf-8"))
            except OSError as exc:
                raise steam.SteamRefusal(
                    f"The Gaming Mode script {name} could not be read: {exc}. Nothing was "
                    "written."
                ) from exc
        values = self.values(account)
        if "PROTON_NAME" in markers(*templates):
            values["PROTON_NAME"] = self._proton_name(steam_root)
        problem = check_values(values)
        if problem is not None:
            raise steam.SteamRefusal(f"{problem} Nothing was written.")
        rendered: list[tuple[Path, bytes]] = []
        for name, template in zip(names, templates, strict=True):
            try:
                text = render(template, values)
            except ValueError as exc:
                raise steam.SteamRefusal(
                    f"The Gaming Mode script {name} could not be prepared: {exc}. Nothing "
                    "was written."
                ) from exc
            rendered.append((scripts_dir / name, text.encode("utf-8")))
        launcher_path, client_path, stop_path = (path for path, _ in rendered)

        lib_path, ours, install_lib, lib_sentence = lib_plan(self.home, self.installers_root)

        title = gaming.title
        server_entry = steam.new_entry(
            name=f"{title} Server",
            exe=KONSOLE,
            startdir=KONSOLE_DIR,
            launchopts=f"--hold -e bash {shlex.quote(str(launcher_path))}",
        )
        client_entry = steam.new_entry(name=title, exe=str(client_path), startdir=str(scripts_dir))
        stop_entry = steam.new_entry(
            name=f"{title} Server Stop", exe=str(stop_path), startdir=str(scripts_dir)
        )

        path = config / "shortcuts.vdf"
        root: steam.VdfMap = {"shortcuts": {}}
        if path.exists():
            try:
                root, _ = steam.vdf_parse(path.read_bytes())
            except ValueError as exc:
                raise steam.SteamRefusal(
                    _for_this_button(steam.UNREADABLE.format(path=path, problem=exc))
                ) from exc
            root.setdefault("shortcuts", {})
        shortcuts = root["shortcuts"]
        before = len(shortcuts)
        replaced_server, server_entry = steam.upsert(shortcuts, server_entry)
        replaced_client, client_entry = steam.upsert(shortcuts, client_entry)
        replaced_stop, stop_entry = steam.upsert(shortcuts, stop_entry)
        replaced = replaced_server | replaced_client | replaced_stop
        expected = before + (not replaced_server) + (not replaced_client) + (not replaced_stop)
        payload = steam.vdf_dump(root)
        reparsed, consumed = steam.vdf_parse(payload)
        if consumed != len(payload) or not steam.terminated(payload):
            raise steam.SteamRefusal(
                "The shortcuts file this would write does not end the way Steam's own does, "
                "and a bad terminator makes Steam drop every non-Steam shortcut in the "
                "library. Nothing was written."
            )
        if len(reparsed["shortcuts"]) != expected:
            raise steam.SteamRefusal(
                "The shortcuts file this would write does not round-trip through its own "
                "parser. Nothing was written."
            )

        notes: list[str] = []
        compat_path = steam_root / "config" / "config.vdf"
        if compat_path.is_file():
            with compat_path.open(
                "r", encoding="utf-8", errors="surrogateescape", newline=""
            ) as fh:
                compat_lines = fh.read().splitlines(keepends=True)
            mapped = [
                str(entry["AppName"])
                for entry in (server_entry, client_entry, stop_entry)
                if steam._find_mapped_appid(
                    compat_lines, f'"{steam.to_unsigned(int(entry["appid"]))}"'
                )
                is not None
            ]
            if mapped:
                notes.append(
                    "Steam has a compatibility tool set for "
                    + ", ".join(mapped)
                    + ". Turn it off in each one's Properties → Compatibility: these "
                    "shortcuts run Proton themselves."
                )
        if "PROTON_NAME" in values and not (self.home / SNIPER).exists():
            notes.append(
                f"The Steam Linux Runtime (sniper) is not installed yet ({self.home / SNIPER}). "
                "The game needs it: install any Proton game, or 'Steam Linux Runtime 3.0 "
                "(sniper)' from the Library's Tools."
            )

        stamp = self.now().strftime("%Y%m%d-%H%M%S")
        try:
            scripts_dir.mkdir(parents=True, exist_ok=True)
            for script, data in rendered:
                _write_file(script, data, SCRIPT_MODE)
            if install_lib:
                _write_file(lib_path, ours, LIB_MODE)
            backup = steam._backup(path, stamp)
            grid = config / "grid"
            grid.mkdir(parents=True, exist_ok=True)
            steam._write_artwork(
                steam.artwork_paths(grid, int(client_entry["appid"])),
                steam.icon_png(steam.CLIENT_ACCENT),
            )
            for entry in (server_entry, stop_entry):
                steam._write_artwork(
                    steam.artwork_paths(grid, int(entry["appid"])),
                    steam.icon_png(steam.SERVER_ACCENT),
                )
            steam._write_shortcuts(path, payload)
        except OSError as exc:
            raise steam.SteamRefusal(
                steam.UNWRITABLE.format(path=path.parent, reason=exc.strerror or exc)
            ) from exc

        logger.info(
            f"gaming mode: {self.entry.id} scripts in {scripts_dir}, "
            f"{'replaced' if replaced else 'added'} 3 shortcut(s) in {path}; {lib_sentence}"
        )
        return GamingModeReport(
            entries=(str(server_entry["AppName"]), title, str(stop_entry["AppName"])),
            scripts=(launcher_path, client_path, stop_path),
            lib=lib_sentence,
            path=path,
            backup=backup,
            replaced=replaced,
            notes=tuple(notes),
        )


GamingModeSeam = Callable[[Path | None, str], GamingModeReport]
"""What the server's tab calls: (its client folder, the typed account) -> what was done."""


def seam_for(
    entry: SingleEntry,
    server_dir: Path,
    *,
    platform_id: Callable[[], str] = platform.detect,
) -> GamingModeSeam | None:
    """The tab's Gaming Mode seam, or None, so the tab draws no button.

    None off Linux, as `controller_view._steam_seam()` answers for 8.8, and None for a
    game whose catalog entry carries no `play.gaming_mode` scripts.
    """
    if platform_id() != "linux":
        return None
    if entry.play is None or entry.play.gaming_mode is None:
        return None
    return GamingMode(entry, server_dir).add


def play_spec(
    entry: SingleEntry,
    client_dir: Path | None,
    *,
    account: str,
    server_dir: Path,
    home: Path | None = None,
    config_dir: Path | None = None,
    installers_root: Path | None = None,
) -> play_launch.LaunchSpec:
    """Play on a Linux desktop, for a `desktop_play` game: its Gaming Mode client script.

    The same refusals as `single_play.launch_spec()`, then the client template rendered
    and handed to `bash -c`. The tab's own client folder and account travel as
    `YULON_*` variables, so the game starts as the tab says even before `state.json` has
    caught up; the script reads Yu'lon's files only for what they do not say.
    """
    play = entry.play
    gaming = play.gaming_mode if play is not None else None
    if play is None or gaming is None or not gaming.desktop_play:
        raise single_play.PlayRefusal(f"{entry.name} has no client Yu'lon knows how to start.")
    if client_dir is None:
        raise single_play.PlayRefusal(
            f"Choose {play.client} first. Yu'lon never downloads a client; it starts the one "
            "you have."
        )
    if not (client_dir / play.exe).is_file():
        raise single_play.PlayRefusal(
            f"There is no {play.exe} in {client_dir}, so nothing was started. Choose "
            f"{play.client}."
        )
    problem = single_play.check_account(account)
    if problem is not None:
        raise single_play.PlayRefusal(problem)
    mode = GamingMode(
        entry,
        server_dir,
        home=home if home is not None else Path.home(),
        config_dir=config_dir if config_dir is not None else platform.config_dir(),
        installers_root=(
            installers_root if installers_root is not None else resources.installers_dir()
        ),
    )
    values = mode.values(account)
    extra = {
        "YULON_ACCOUNT": account,
        "YULON_CLIENT_DIR": str(client_dir),
        "YULON_SERVER_DIR": str(server_dir),
    }
    problem = check_values({**values, **extra})
    if problem is not None:
        raise single_play.PlayRefusal(f"{problem} Nothing was started.")
    path = mode.installers_root / gaming.templates / gaming.client_file
    try:
        text = render(path.read_text(encoding="utf-8"), values)
    except (OSError, ValueError) as exc:
        raise single_play.PlayRefusal(
            f"{entry.name}'s client script could not be prepared ({exc}), so nothing was "
            "started."
        ) from exc
    return play_launch.LaunchSpec(
        argv=("/bin/bash", "-c", text, gaming.client_file), env=extra, cwd=Path("/")
    )


# -- WoW: the same three tiles, with Yu'lon doing the server's work -------------------------

WOW_TEMPLATES = Path("wow") / "gaming-mode"
WOW_LAUNCHER = "wow-launcher.sh"
WOW_STOP = "wow-stop.sh"


def yulon_argv(launcher: Callable[[], tuple[str, str]] = steam.launcher_command) -> list[str]:
    """How a script starts THIS Yu'lon: the AppImage file, the binary, or the interpreter
    and `main.py`, as 8.8's Server entry did (`steam.launcher_command()`)."""
    exe, opts = launcher()
    return [exe, opts] if opts else [exe]


def wow_scripts(
    game_id: str,
    title: str,
    server_dir: Path,
    *,
    home: Path,
    config_dir: Path,
    installers_root: Path,
    yulon: Sequence[str],
) -> steam.GamingScripts:
    """A WoW server's launcher and stop scripts, rendered; written by `steam.SteamShortcuts`.

    WoW's game tile stays 8.8's (the client's own executable, through the compatibility tool
    Steam is told to use); the Server tile's launcher and the Stop tile's script call
    `yulon --server` (`server_cli`), because a WoW Start cannot be done from a script. The
    scripts go into `<config_dir>/gaming-mode/<game>/`, as DDON's and DFO's do.
    """
    scripts_dir = config_dir / "gaming-mode" / game_id
    values = {
        "HOME": str(home),
        "GAME": game_id,
        "TITLE": title,
        "SERVER_DIR": str(server_dir),
        "SCRIPTS_DIR": str(scripts_dir),
    }
    problem = check_values(values)
    if problem is not None:
        raise steam.SteamRefusal(f"{problem} Nothing was written.")
    # Unquoted in `YULON=(...)`: `shlex.join` quotes each word for the shell itself.
    command = {**values, "YULON": shlex.join(yulon)}
    rendered: list[tuple[Path, bytes]] = []
    for name in (WOW_LAUNCHER, WOW_STOP):
        try:
            template = (installers_root / WOW_TEMPLATES / name).read_text(encoding="utf-8")
            rendered.append((scripts_dir / name, render(template, command).encode("utf-8")))
        except (OSError, ValueError) as exc:
            raise steam.SteamRefusal(
                f"The Gaming Mode script {name} could not be prepared: {exc}. Nothing was "
                "written."
            ) from exc
    lib_path, ours, install_lib, lib_sentence = lib_plan(home, installers_root)

    def write() -> None:
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for script, data in rendered:
            _write_file(script, data, SCRIPT_MODE)
        if install_lib:
            _write_file(lib_path, ours, LIB_MODE)

    return steam.GamingScripts(
        launcher=scripts_dir / WOW_LAUNCHER,
        stop=scripts_dir / WOW_STOP,
        write=write,
        lib=lib_sentence,
    )
