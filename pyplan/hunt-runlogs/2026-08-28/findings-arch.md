# yulon-arch — 2026-08-28 — round 1 report

Arch Linux, XFCE/X11, **Python 3.14.7** (the only python3 on the box — `pacman -Ss '^python3'`
returns only `core/python 3.14.7-1`), Docker 29.7.2. Branch @ 80fb68a9.

## Finding 1 — HIGH — the launcher will not start from source on a stock Arch desktop
`~/bin/on-desktop python3 main.py` core-dumps immediately:

    qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin.
    qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.

The message names one library. It is wrong — installing only that one still fails. `ldd` on
`PySide6/Qt/plugins/platforms/libqxcb.so` showed **five** missing objects:
`libxcb-cursor`, `libxcb-icccm.so.4`, `libxcb-keysyms.so.1`, `libxcb-shape.so.0`, `libglib-2.0.so.0`.
Only after all five did the app render. Arch package for the first is `extra/xcb-util-cursor`, not
anything named `libxcb-cursor0`.

This is the same class as the shipped-artifact breakage `build/check-bundle-closure.sh` was written
for, but it hits **running from source via pip**, which that gate does not cover. The pylauncher
README lists no system-package prerequisite for Linux. Real onboarding blocker for any Arch user.

## Finding 2 — MEDIUM — the installer CLI has no way to answer a sudo prompt, and it bypasses `installer_for()`
The box does **not** have passwordless sudo (`sudo -n true` → `sudo: a password is required`;
`pk` is in `docker wheel autologin`). Verified independently by the orchestrator, so this is not an
agent misreport — and it contradicts what [[yulon-test-vms]] implies.

Two things follow, both confirmed by reading `yulon/catalog/installer.py`:
1. `_main()` calls `installer.run(options)` with **no `ask` callback**, though `run()` accepts one.
   On any box that needs a sudo password the CLI hangs forever at the script's
   `[sudo via Yu'lon <token>] password:` prompt (`installer.py:464`, `:503`).
2. `_main()` constructs `Installer(entry, ...)` **directly**, not via `installer_for()`. So the CLI
   harness can never exercise `NativeInstaller`, on any platform. That matters for how much a
   "I ran the install through the CLI" claim actually proves — on Linux today the two agree, but the
   equivalence is incidental, not designed.

Also: `install-wow-wotlk.sh` primes sudo up front unconditionally, even when `pk` is already in the
`docker` group and Docker is installed and running — no fast path that skips the prompt when nothing
privileged needs doing.

## Finding 3 — pacman fallback is correct (a pass, not a finding)
`host_package_manager()` → `pacman`; `wotlk.install.script_variants` has only `apt` and `dnf` keys;
the base `wow-wotlk/install-wow-wotlk.sh` is selected. Not an apt or dnf script on Arch.

## Finding 4 — the branch fix verified again on real hardware
    ✅ Disk space OK on /home/pk (84GB available)
    ✅ Disk space OK on /var/lib/docker (84GB available, Docker's images)
Both disks checked separately — third independent confirmation of `258b7a1e`.

## Finding 5 — third independent confirmation of the STOP_GRACE tax
`pytest -q` → 983 passed, 3 skipped, 0 failed in **42m43s**. Same root cause as Ubuntu and Fedora
reported: `STOP_GRACE_SECONDS = 300` (`yulon/docker.py:791`) spent in full against busybox stand-ins
that do not handle SIGTERM.

## Finding 6 — `mypy .` 375 errors / CI's `mypy yulon main.py` clean. Fourth box, identical numbers.
ruff and black clean.

## GUI, driven around finding 1
Round 1 could not install system libraries (no sudo), so it ran the same checkout in a throwaway
`python:3.12-slim` container with `DISPLAY=:0` and `.Xauthority` bind-mounted — real launcher source
rendering on the real screen. Title `Yu'lon — Dad's MMO Lab launcher 0.6.57`, Catalog tab correct.
`xdotool` clicks did not register in that nested setup (keyboard nav did), so Install was driven by
Tab+Space; it opened the real `QFileDialog` with the title from `catalog_view.py:430-432`.
Container removed afterwards; the pre-existing `~/wow-server-playerbots` stack was never touched.

## Not completed in round 1
Install (blocked on sudo), controller tabs, packaged-artifact launch.
Both blockers are solved in round 2: the sudo password is `yulon`, and the five packages are known.

---

# yulon-arch — round 2 (tested f5882c2a)

## Finding 1 CLOSED — the exact package set
`ldd libqxcb.so | grep 'not found'` needed **two** rounds, so the round-1 list of five was itself short:

    pacman -S --needed xcb-util-cursor xcb-util-wm xcb-util-keysyms glib2 libxcb
      (glib2 and libxcb were already present; this pulled in xcb-util-image + xcb-util-renderutil)
    still missing: libxkbcommon-x11.so.0   ← from two separate DSOs
    pacman -S --needed libxkbcommon-x11

**Prerequisites list for the README:**
`xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil libxkbcommon-x11`
(plus `glib2` and `libxcb`, which stock Arch already has). After all of them,
`ldd libqxcb.so | grep 'not found'` returns nothing.

`libxkbcommon-x11` is one of the five sonames `build/check-bundle-closure.sh` found missing from the
shipped tarball — the same library, hit again on a different path (pip-from-source, which that gate
does not cover). Independent corroboration of the cross-cutting note in `checklist.md`.

Also not preinstalled: `imagemagick` (only needed to take the screenshot, not to run the app).

## Launcher window CONFIRMED on the real Arch desktop
`~/bin/on-desktop ~/yulon-run/.venv/bin/python3 main.py`, screenshot at
`~/yulon-run/logs/launcher-round2-desktop.png`. Title `Yu'lon — Dad's MMO Lab launcher 0.6.57`,
Catalog tab, WotLK (stable) + Vanilla (beta) with Install / Use existing. Only console noise is a
benign `qt.qpa.theme.gnome: dbus reply error ... ServiceUnknown` (GNOME theme portal probe on XFCE).
Round 1's Docker/X11 workaround is no longer needed.

## Finding 2 sharpened — the CLI hang is by design, and undocumented as a trap
`_main()` (`installer.py:741-780`) calls `installer.run(options)` with no `ask=`. `run()`'s docstring
(line 594) says `ask` is "consulted for exactly one thing: sudo asking for a password", and the
comment at line 603 records that sudo reads from `/dev/tty` on a pty the script opens itself — so
piping a password to stdin cannot work either. The CLI parks forever at the `SUDO_PROMPT` marker with
no timeout and no error. Confirmed live: process alive, log tail frozen, across repeated checks.
The CLI's own docstring calls it "the roadmap 3.2 test harness", not an unattended install path —
but nothing warns the caller. Worth a `--sudo-password` / env var / `getpass()` or at least a timeout.

**No compile ever started on Arch** — the install never got past the sudo prompt, so there is no
partial build state. A follow-up starts clean. A driver `~/yulon-run/run_install.py` that wires
`ask=` was written but never executed.

## Correction to round 1
The `~/wow-server-playerbots` stack is **running**, not stopped — it auto-starts on boot via Docker's
restart policy (box booted 11:39:56; containers "Up about an hour" one minute later). `worldserver`
alone holds ~5.2 GB RSS, which is why the box had ~137 MB free and swap 99% full at 8 GB.

## Still open on Arch
The whole feature sweep (Console, accounts, backups, modules, networking, maintenance, log panel,
self-update) and the install itself. Neither was reached before the resize stop.
