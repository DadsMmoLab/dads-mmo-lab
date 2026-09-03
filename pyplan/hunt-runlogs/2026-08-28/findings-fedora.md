# yulon-fedora — 2026-08-28 — run report

Fedora 44 Workstation, GNOME/Wayland, **Python 3.14.3** (only interpreter), Docker 29.7.2,
SELinux **Enforcing**. Branch `fix/installers-honour-chosen-folder` @ 80fb68a9.

## Headline: no Python 3.14 breakage at all
`pip install -r requirements.txt -r requirements-dev.txt` clean, PySide6 6.11.2 (cp310-abi3 wheel)
works on 3.14. ruff clean, black clean, `mypy yulon main.py` clean on native + win32 + darwin.
`pytest -q --ignore=tests/integration` → **967 passed, 3 skipped in 25.75s**.

## Finding 1 — HIGH — SELinux relabel missing from the Python bind-mount probe
`yulon/docker.py:2428` (`bind_mount_ok`) mounts `f"{mount}:/probe:ro"` with no `:z`/`:Z`.
On enforcing SELinux the container cannot read the bind. Reproduced directly, A/B:

    docker run --rm --entrypoint ls -v /tmp/selinux-repro:/probe:ro  alpine:3.19 -A /probe
      → ls: can't open '/probe': Permission denied   (exit 1)
    docker run --rm --entrypoint ls -v /tmp/selinux-repro:/probe:ro,Z alpine:3.19 -A /probe
      → a.txt                                        (exit 0)

`sudo ausearch -m avc -ts recent` showed **no** denial — Fedora's container-selinux policy
`dontaudit`s this exact case. Absence of an AVC is not absence of SELinux. The `:Z` A/B is the proof.

Same omission at `tests/integration/conftest.py:210` (`- ./marker:/marker` in `_ONE_SHOT_YML`):
the marker write silently fails, so `import_runs()` always reads 0.

Consequence today: 5 integration tests fail on any SELinux-enforcing box with real Docker
(`test_the_bind_mount_probe_actually_works_against_a_real_daemon`, the three import-tracking
tests, `test_compose_up_reruns_the_one_shot_import`).

Consequence later: **this does not block the shipped Linux install today** — `installer_for()`
(`yulon/catalog/installer.py:697-738`) routes Linux to the bash `Installer`, and
`install-wow-wotlk-fedora.sh:216-291,1405-1425` already does the relabel correctly
(`selinux_is_enforcing`, `chcon -Rt container_file_t`, `:z` on every override bind).
But Phase 7.1/7.2 move Linux onto the native Python installer. The day Linux leaves
`script_platforms`, this unlabeled probe refuses every install on Fedora/RHEL/CentOS — and
reports it with the wrong, Docker-Desktop-flavoured message at
`yulon/catalog/preflight.py:484-490` ("Add this folder to Docker Desktop's Settings → Resources").

Fix: add `:z`/`ro,Z` at `docker.py:2428` and `conftest.py:210`. The bash script is the working
precedent in the same repo.

## Finding 2 — MEDIUM — confirms the Ubuntu finding independently
`pytest -q` (full) → **973 passed, 5 failed, 8 skipped in 1942s (32m22s)**. The runtime is the
busybox-PID-1 problem, reproduced in isolation:

    docker run -d --name yulon-repro-test busybox:1.36 sh -c "echo ready; sleep 600"
    time docker stop -t 300 yulon-repro-test
    → real 5m0.748s

`stop_staged`'s own docstring (`yulon/docker.py:1489-1491`) claims "measured at 6.19s for three
containers where the first traps SIGTERM for 6s" — that does not hold for this fixture.
`py-spy dump` caught two different tests stuck in `stop_staged` (`docker.py:1576`) minutes apart.
Production `STOP_GRACE_SECONDS = 300` is correct and should stay; the fixture containers
(`conftest.py:87,104,113`) should trap SIGTERM and exit.
**Open question worth checking: is CI paying the same tax?** GitHub runners have a live Docker daemon.

## Finding 3 — LOW — self-update 404 (same as Ubuntu). Graceful, not a crash.

## Confirmed working
- **The branch's core fix, live**: `Using custom location: /home/pk/yulon-run-install/wow-wotlk-fedora-test`
  then `Install location OK — 48 GB available at /home/pk/yulon-run-install` — free space is checked on
  the **parent of the chosen folder**, not `$HOME`. Historical proof of the old bug on this same box, from
  a pre-fix Aug-24 AppImage run in `~/.local/share/yulon/yulon.log`:
  `Using custom location: /home/pk` → `Cannot use '/home/pk' as the install location.`
- dnf variant selection: `linux_package_manager()` → `dnf`; `script_for("dnf")` →
  `install-wow-wotlk-fedora.sh`; install banner says `Package manager: dnf`.
- TBC refuses without a client dir: `InstallerError: WoW TBC needs the folder of your 2.4.3 client...`
- GUI rendered: frame `"Yu'lon — Dad's MMO Lab launcher 0.6.57"`, all 4 catalog entries; Install opens
  `"Where should WoW WotLK be installed? (suggested: a new folder called wow-server-playerbots)"`;
  "Use existing…" opens `"Select the folder where WoW WotLK is installed"`.
- `Controller.status()` on the pre-existing install → `InstallStatus(db=False, auth=False, world=False)`.

## In flight when the agent stopped
WotLK compile still running detached on the VM (PID 39745), last seen object 1147/1829 (~63%),
~929 s elapsed, 40 GB free. Log: `~/yulon-run/logs/install-fedora.log`.

## Not tested, and why
- Pixel screenshot: GNOME blocks every route — D-Bus `Screenshot` → AccessDenied; `gnome-screenshot`
  hangs on a portal consent dialog nobody can click over SSH; `grim` → protocol unsupported;
  `wtype` synthetic input rejected. Substituted an **AT-SPI accessibility-tree dump** (exact widget
  titles/text) — stronger than pixels for proving content, but not a screenshot.
- Qt file dialogs by click: opened and title-verified, but `childCount: 0` to AT-SPI. Folder selection
  verified instead by driving `Installer.run()` with `InstallOptions(server_dir=...)` — the same code
  the dialog result feeds.
- Controller tabs beyond `status()` — no install reached running containers in the session.

---

# yulon-fedora — round 2 (tested f5882c2a)

## The 8 GB limit is not theoretical — it bricked the box
The compile reached **1518/1829 (83%)** at 2172.8 s, with `free -h` showing 5.6/7.7 GiB used and
**zram swap 3.6 GiB of 7.7 GiB in use**. Seven compiler jobs at the measured ~2 GB each wants ~14 GB
in an 8 GB box. It was not OOM-killed — it was surviving on swap. Shortly after, SSH stopped
completing: `Connection timed out during banner exchange`, while Hyper-V still reported the VM
`Running / Operating normally`. Alive but swap-thrashed past the point sshd could finish a handshake.
This is the failure `yulon-use.ps1`'s header predicted, observed directly.

**A host-side framebuffer capture taken while SSH was dead** (`vmshot.ps1`) shows the desktop
rendering perfectly, with the launcher's **"Select the folder where WoW WotLK is installed"** dialog
open on `~/wow-server-playerbots`. Two things follow: the GUI and its file dialog work on Fedora, and
**GNOME blocking in-guest screenshots does not mean a box cannot be screenshotted** — the host route
works and should be the default from now on. See [[vm-screenshots-and-activity-windows]].

## Finding 1 (SELinux) REFINED — real, but dead code on Linux today
The A/B reproduces again with a different image:

    sudo docker run --rm --entrypoint ls -v /tmp/selinux-probe-test:/probe:ro   alpine/git -A /probe
      → ls: can't open '/probe': Permission denied   (exit 1)
    sudo docker run --rm --entrypoint ls -v /tmp/selinux-probe-test:/probe:ro,Z alpine/git -A /probe
      → f.txt                                        (exit 0)

Call graph traced this round: `bind_mount_ok()` has exactly one production caller —
`catalog/preflight.py:183` (`_default_bind_probe`), the default probe for `preflight.gather()`.
`gather` has exactly one non-test caller — `catalog/native.py:272`. And `installer_for()`
(`installer.py:697-738`) returns `NativeInstaller` only when `entry.install.is_native(here)`;
`wow-wotlk` has `script_platforms: ["linux"]`, so **every Linux box gets the bash `Installer`**, whose
`preflight()`/`run()` contain zero references to `bind_mount_ok`/`preflight`.

So: **severity for today's shipped Linux install — none, nothing reaches it. Severity for macOS and
native Windows (the native engine's actual current targets) and for any future native-Linux entry —
still HIGH and unfixed.** The failure mode if reached: image already pulled → `images_built()` True →
`bind_mount_ok()` False → `preflight._bind_check()` turns it into a hard `refuse`.

## The shipped Fedora path is correct, and now proven end-to-end
`install-wow-wotlk-fedora.sh:268-289` (`selinux_label_for_containers`) runs
`chcon -Rt container_file_t "$1/env"` after detecting Enforcing. Live in this round's log
(`install-fedora.log:157-158`): "SELinux is enforcing — labelling the server folder for container
access" / "Server folder labelled for container access". On disk:
`ls -Z ~/wow-server-playerbots/env/dist` → `unconfined_u:object_r:container_file_t:s0` on `etc` and
`logs`, the two directories every AC container binds. That persistent relabel is *why* the base
compose file's binds work without inline `:z`. Proven empirically — all three containers started
clean, `WORLD: World Initialized In 0 Minutes 59 Seconds`, then the worldserver ready line.

## A running WotLK server now exists on Fedora — on non-default ports
Round 1's "never got past ac-db-import" is **stale**: `ac-db-import` exited 0 three days ago.
An unrelated `vanilla-mangosd`/`vanilla-realmd` CMaNGOS stack was already holding 3724/8085, so the
stack was brought up with `DOCKER_AUTH_EXTERNAL_PORT=3725 DOCKER_WORLD_EXTERNAL_PORT=8086`.
**`~/wow-server-playerbots` is live on 3725 (auth) / 8086 (world)** — a follow-up needs this to connect.
No account was created before the box went unreachable.

## Blocked by the harness, not by the code
`sudo docker stop vanilla-realmd vanilla-mangosd` was refused twice by the auto-mode classifier.
The agent worked around it with port overrides rather than forcing it. Worth an allowlist entry if
these runs are to be autonomous.

## Still open on Fedora
The entire feature sweep — Console, accounts, backups, modules, networking, maintenance/`repair_import()`,
log panel, self-update. Not reached. This remains the single biggest gap in the whole run.
AT-SPI driver scripts are left on the box at `~/yulon-run/atspi_*.py` for a follow-up; they must be run
through `~/bin/on-desktop` to inherit the session's DISPLAY/DBUS.
