# 7.10 gaps, closed against the live server — Lane C, 2026-09-04, `yulon-ubuntu`

The 2026-09-04 sweep (`pyplan/gates/7.10-ubuntu-2026-09-04/`) left two things its own
report is explicit about: it drove the **feature** surface of the 6.5 gate and not its
**install** half, and **nothing it did travelled through a widget** — every call went to
`ControllerServices`, the object behind the tiles. This lane closes what can be closed on
a box that has a finished install and a running stack.

Target: the same live server, `/home/pk/wowserver`, code at `81d7311e` in
`/home/pk/gate0904/checkout`. Nothing was installed, removed, restored or applied.

## What is CITED, not re-run

Three of the four install-half items were already earned by the 7.1 lane on this same
box, in this same install. Re-running them would have proved nothing new and would have
cost the box its state, so they are cited by file and line:

| 6.5 install-half item | Already evidenced by | The line |
|---|---|---|
| **preflight floors refusing, not warning** | `7.1-ubuntu-2026-09-04/press1.log:15-26` | a tri-state in one run: `[pass]` on Docker/memory/folder/SELinux/ports, `[warn]` on compiler-jobs-vs-memory ("a build this far ahead of its memory has finished before, so this is a caution"), and `[refuse]` on space — "41 GB free, and the install needs 48 GB" — after which `install failed` and nothing ran |
| **staged / resumable install** | `7.1-ubuntu-2026-09-04/press2.log`, `press3.log`, `kill-record.txt`, `ccache-stats.txt` | press 2 SIGKILLed at compile edge 896/1829, press 3 resumed to `ready` with ccache going 881 misses → 881 hits |
| **`keep_awake()`** | `7.1-ubuntu-2026-09-04/press3.log:28` and `kill-record.txt:41` | the inhibitor is taken — `systemd-inhibit --what=idle:sleep --who=Yu'lon --why=installing a server sleep infinity` — and after the kill, "NONE: no install, no builder, no inhibitor left behind", so both halves (taken, and released) are on record |

The fourth, **honest cancel copy**, was not evidenced anywhere, and is below.

## What this lane RAN: `widget_driver.py`

One driver, `/home/pk/gate710c_widget_driver.py`, under
`systemd-run --user --unit=dml-710c --collect`, `QT_QPA_PLATFORM=offscreen`, PySide6 6.11.2.
It builds the **real `ControllerView`**, the **real `CatalogView`** over a **real `LogPanel`**
on a real `QApplication`, and presses their real `QPushButton`s with `QTest.mouseClick`,
which delivers a press/release to the widget the way a finger does. `QTest.keyClicks`
types into the line edits one character at a time; `QTest.keyClick(Key_Up)` drives the GM
spin box. No signal is called by hand anywhere.

**Result: 32 checks, 32 OK, 0 FAIL** (`widget-run.log`, second run).

### Two choices that make the clicks load-bearing

* **`status_poll_ms=0`.** `ControllerView` normally re-reads status on a 5 s `QTimer`. A
  test that clicked Refresh and then waited would pass with the button unwired. With the
  timer off, `status_label` starts at `'status: unknown'` — asserted before anything is
  clicked — and only the click can change it. It became `status: db up, auth up, world up`.
* **`Apply` on the Networking tab is asserted DISABLED and never clicked.** That is the
  button whose `ufw --force enable` locked the 7.1 lane out of this box. The driver
  asserts it is dead before a plan exists, clicks **Show plan**, asserts it went live —
  and stops there.

### The six widget round-trips, and how each was corroborated

| Tab | Real button pressed | What the widget then showed | Checked against |
|---|---|---|---|
| Server | `Refresh` | `status: db up, auth up, world up` | `docker ps` read separately: all three containers |
| Console | typed `server info`, then `Send` | the reply carrying the core revision; the line edit cleared itself | the panel's own text |
| Accounts | typed `WIDGET0904`, a password, two `Up` keys on the GM box, then `Create` | `WIDGET0904: created (id 103), GM level 2.` | `docker exec mysql` → `103  WIDGET0904  32  32  2` — salt 32, verifier 32 (a real SRP6 row), and the GM level the spin box showed is the GM level the DB got |
| Networking | `Show plan` | LAN IP `172.30.55.119`, ports 3724/8085, the three ufw commands, the realmlist `UPDATE` | it names the address the realm actually advertises |
| Maintenance | `Refresh` (backups) | 5 rows, `problem_label` empty | — |
| Catalog | `Install` on the WotLK tile | see below | — |

### The install half, through `CatalogView`'s own Install button

`CatalogView` was built with the real catalog and the real `installer_for_app` factory —
the one `main.py` passes — with only the folder picker injected, so the folder under test
is a throwaway (`/home/pk/gate710c-doomed-install`) instead of a modal file dialog.
Clicking the tile's `Install` produced, **as a modal dialog titled "Install failed"** that
the driver dismissed by clicking its real `OK` button:

```
InstallerError: This machine cannot install the server yet:
free space on Docker's disk and the server folder: 39 GB free, and the install needs 48 GB
  (the server folder and Docker's disk share one drive, so both needs add up)
  Free some space, or install to a drive that has room, then try again.
the server's ports: ac-worldserver, ac-authserver already publish the ports this server needs
  Stop that server first (or remove its containers), then try again.
```

So the install half's **refuse-don't-warn preflight was reached through the widget**, it
listed *two* refusals rather than the first one it met, and afterwards:

* the chosen folder was **empty** — asserted by listing it, not assumed;
* the tile still reads `Install`, so nothing was remembered as installed.

### The cancel machinery and the cancel copy

* **The machinery, through the real button.** `LogPanel`'s `Stop` was asserted dead while
  idle, a worldserver log follow was started, `Stop` went live, 11 lines arrived, the
  button was clicked, the follow stopped and the panel reported **`cancelled=True`** — not
  "finished". That distinction is the whole point of the property: a stopped job arrives
  with `ok=True` and the message "done".
* **The copy.** `cancelled_install_message()` — what `catalog_view.py:753` shows on a
  cancelled install — was rendered for two folders in the same run, and it is genuinely
  different for each. For an empty folder it says the installer "had not got as far as
  writing a compose file … Press Install again and choose <folder> to carry on". For
  `/home/pk/wowserver`, which holds a complete install, it says "The source is there …
  press *Use existing…* … nothing is lost". Both say the honest thing first — *"Stopping
  undoes nothing and tidies nothing away"* — and both warn against clearing Docker's build
  cache to tidy up. Full text in `widget-run.log`.

**Still not exercised, and reported as such:** that copy arriving from a *cancelled
install driven through the widget*. It cannot be produced on this box — preflight refuses
every install here (39 GB free against a 48 GB floor, plus the port conflict with the
running server) **before** an install starts, so there is nothing to cancel. It needs the
clean checkpoint that 7.1 clause 1 is blocked on. Not claimed.

## Two traps this run fired, and what they cost

1. **A wait that the placeholder satisfied.** The first run reported 5 FAILs — the account
   row and the network plan. Neither was a product fault: `account_report` is set to
   `'Creating WIDGET0904…'` the instant the button is pressed, and `network_text` to
   `'working out the plan… (this can take a few seconds)'`, so a predicate of *"the field
   is no longer empty"* was satisfied by the placeholder and the wait ended before the
   work began. The fix waits for the placeholder to be **replaced**. Both runs are kept in
   `widget-run.log`, the failing one first; the account the first run created was deleted
   before the second so its "does not exist before the click" check stayed honest.
2. **`--collect` erases the exit status you meant to read.** `systemctl --user show
   dml-710c -p ExecMainStatus` returned `0` for the run that printed `27 OK, 5 FAIL`,
   because `--collect` had already garbage-collected the unit and `show` answers with
   defaults for a unit that no longer exists. A driver under `--collect` must print its own
   verdict; the status read afterwards is not evidence.

## The box was left as it was found

`final-state.txt`, taken after cleanup:

* `acore_auth.account` back to **101 rows** — the `WIDGET0904` account this run created
  (id 103) was deleted, with its `account_access` and `realmcharacters` rows; the count is
  shown at 102 before and 101 after;
* the 7.1 gate account `101 GATE0904` untouched;
* both throwaway folders listed (empty, which is itself the proof the install click wrote
  nothing) and then removed;
* realmlist `1 AzerothCore 172.30.55.119 172.30.55.119 8085`;
* all three containers up; ufw active with 3724, 8085 and 22.

## Files

| File | What it is |
|---|---|
| `widget_driver.py` | the driver, exactly as it ran (copied to the box as `/home/pk/gate710c_widget_driver.py`) |
| `widget-run.log` | both runs' full transcripts — the 5-FAIL first run and the 32-OK second — as journald recorded them |
| `final-state.txt` | cleanup and the state the box was left in |
