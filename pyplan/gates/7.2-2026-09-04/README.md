# 7.2 gate, 2026-09-04 — one half earned, one half not run

`pyplan/checklist.md:259` — *"Gate: full checks green; 7.1's Ubuntu gate re-run from the same
checkpoint with no other change."*

Two clauses. **Clause A is met and now has an artifact for the first time. Clause B was not run,
for three reasons that are recorded below rather than worked around.**

---

## Clause A — "full checks green": MET, with an artifact

`AUDIT-2026-09-04.md` §3 said this clause *"has no committed artifact either — the report's
'1320 passed, 2 skipped, 16 deselected in 35.01s' is nowhere in the evidence."* It has one now.

Run on **`yulon-win11-gate`** (Windows 11 Pro 26200) through the prepared environment's
interpreter, `C:/Users/pk/co/dads-mmo-lab-yulon-phase7/pylauncher/.venv/Scripts/python.exe`
(Python 3.12.10), against a `git archive` of **`badee6255965f2ba8e05ad2306dcdc6126e5880b`**
extracted to `C:/gate/checks-badee625`. Every command is the one `.github/workflows/ci.yml` runs,
copied out of the workflow rather than invented here.

| Check | CI spells it | exit | elapsed | result |
|---|---|---|---|---|
| ruff | `python -m ruff check .` | **0** | 0.3 s | `All checks passed!` |
| black | `python -m black --check .` | **0** | 1.3 s | `136 files would be left unchanged.` |
| mypy (this platform) | `python -m mypy yulon main.py` | **0** | 0.9 s | `Success: no issues found in 71 source files` |
| mypy (as Windows) | `python -m mypy --platform win32 yulon main.py` | **0** | 0.9 s | same |
| mypy (as macOS) | `python -m mypy --platform darwin yulon main.py` | **0** | 17.5 s | same |
| pytest | `python -m pytest -q -m "not integration"` | **0** | 208.4 s | **2291 passed, 26 skipped, 23 deselected in 206.44 s** |

`full-checks/SUMMARY.txt` is the table as the run wrote it; each row's full output is the file
named beside it. `full-checks/run-checks-as-run.ps1` is the script that produced them.

### The code these checks ran is the code the 7.1 gate ran

`git diff 81d7311e badee625 -- pylauncher` is **empty**. `81d7311e` is the commit the 7.1 Ubuntu
gate ran on the box; the three commits since add gate evidence and docs and touch no product file.
So this artifact is not "the checks at some later code" — it is the checks at the gate's code.

### Two things that went wrong first, kept because they would otherwise be repeated

1. **The first attempt was invalid and said `E` 700 times.** Under `schtasks /ru pk /it` the
   default pytest basetemp `C:\Users\pk\AppData\Local\Temp\pytest-of-pk` is not readable by the
   task — `PermissionError: [WinError 5]` out of `os.scandir()` — so **every test taking
   `tmp_path` errored**. The same file passes 68/68 in an interactive ssh session on the same box.
   `attempt1-diagnosis-traceback.txt` is the isolating run; `attempt1-basetemp-denied.txt` is a
   trimmed excerpt of the 250,966-line original. The fix is `--basetemp` and it is a harness
   accommodation, labelled as such inside `full-checks/pytest.txt`.
2. **The second attempt failed 7 tests, and both causes were mine.** Five
   `tests/integration/test_docker_live.py` tests, which CI runs as a *separate* job and the
   default run deselects; and two `tests/test_linux_artifact_prereqs.py` tests, which read
   `.github/workflows/release.yml` — a file my first archive had not copied to the box. The run
   above copies `.github` and uses CI's marker filter.

### The integration job on Windows — an observation, not part of this clause

For completeness the live-Docker job was run too: `pytest -q -m integration` →
**5 failed, 13 passed, 5 skipped**, `full-checks/pytest-integration.txt`. CI runs that job on
`ubuntu-latest` only, and all five failures are one Windows-specific cause in two costumes:

* under the scheduled task, `PermissionError: [Errno 13]` reading back a file a container wrote
  into a Windows bind mount (`.../marker/import.log`, `.../out/copy.txt`);
* interactively, the daemon refuses the mount outright — `docker: Error response from daemon:
  CreateFile C:\Users\pk\AppData\Local\Temp\...\client: Access is denied.`, exit 125.

That is the 6.3 bind-mount/uid class the checklist already carries, arriving in the test suite.
It is **not** evidence about the 7.2 change set and is recorded here so nobody has to rediscover
it; if Windows integration coverage is wanted, it needs its own decision.

---

## Clause B — "7.1's Ubuntu gate re-run from the same checkpoint with no other change": NOT RUN

The audit reads this as a reproducibility clause: a second independent execution from an
identically restored VM, so any difference is attributable to the 7.2 changes and nothing else.
I agree with that reading, and it is exactly why the run was not started.

**1. "The same checkpoint" cannot produce a run at all, let alone one "with no other change."**
The 7.1 lane established this a few hours earlier, on the box, and its capture is in
`pyplan/gates/7.1-ubuntu-2026-09-04/state-as-restored.txt`: `pre-7.2-gate-2026-09-02` restores with
a **complete 08-31 install** at `~/wowserver` (state file `completed: [clone-core … import]`) and
its **stack running** — three containers holding 3724/8085/3306 — with **43 GB free**. Press 1
refused on the spot: `[refuse] free space on Docker's disk and the server folder: 41 GB free, and
the install needs 48 GB` (`press1.log:21`, exit 1). The 7.1 lane could only proceed by removing the
old volumes, four images and 12.91 GB of BuildKit cache **by hand** (`disk-reclaim.txt`).

A second run from that checkpoint would refuse identically, then require the same hand cleanup —
which *is* another change, and one no two operators would perform identically. There is no
sequence of steps from `pre-7.2-gate-2026-09-02` that satisfies the sentence as written.

**2. The audit's own sequencing puts this run after work that has not happened.**
`AUDIT-2026-09-04.md` §5 item 12: *"Then, and only then, run 7.2"* — after item 9, the LAN/auth/
account **and an actual 3.3.5a client login**. Clause 14 of the 7.1 gate is still open: that box
has no WoW client (`find / -maxdepth 4 -iname Wow.exe` returns nothing) and the clients live on
`m910q`, which another lane owns.

**3. Restoring would destroy the evidence two open clauses depend on.**
The 7.1 lane deliberately left `yulon-ubuntu` running with account `GATE0904` and the realm on
`172.30.55.119:8085` so its clause 14 could be closed without redoing the install — and that same
server is what 7.10 was told to drive (see `pyplan/gates/7.10-ubuntu-2026-09-04/`, run today
against it). A restore discards both.

### What would unblock it

A **genuinely clean checkpoint**, which is an owner call because it changes a shared VM's
checkpoint set. Read-only from the host, the tree today is:

```
clean-ssh                      Standard  2026-08-28 15:25:58   (root)
post-hunt-cleaned-2026-08-31   Standard  2026-08-31 13:13:56   parent clean-ssh
pre-7.2-gate-2026-09-02        Standard  2026-09-02 11:10:04   parent clean-ssh
```

`clean-ssh` is the one the 6.5 Ubuntu gate describes as *"no Docker, no images, untouched home"*.
`pre-7.2-gate-2026-09-02` is a child of it by name, but the disk it froze was two days of installs
later. So either:

* **(a)** re-run *both* 7.1's Ubuntu gate and 7.2's from `clean-ssh`, and reword both lines to name
  that checkpoint — the two runs are then comparable and clause 1 of 7.1 becomes satisfiable; or
* **(b)** take a new clean checkpoint from this box after a wipe, once clause 14 has been closed
  against the install currently on it, and name that one.

Either way the box has to be surrendered, so clause 14 (a client login) should be closed first —
it needs no reinstall, only a machine with a 3.3.5a client that can reach `172.30.55.119:8085`.

---

## The clause-B consequence the audit wanted, delivered another way

The audit's §3 closing note: *"a proper 7.2 re-run would naturally produce the second, independent
compose capture that clause 10 needs to stop being circular."*

**That capture already exists, and it did not need the 7.2 re-run.** The 7.1 lane's 2026-09-04
install is independent of the 08-31 one the fixture was minted from: a fresh clone of
`mod-playerbots/azerothcore-wotlk` taken that morning, compiled from an emptied BuildKit cache
(`ccache-stats.txt` T0 = 0.0 GiB), on a box whose previous install had been removed.

I re-derived the comparison from that install with my own script and the project's own seam
(`compose-recheck.txt`, run on the box, `docker compose config` only — nothing was changed):

* route A, `docker compose config`: exit 0, 219 lines, 0 bytes stderr;
* route B, `docker compose config --format json`: exit 0, 9,322 bytes, 0 bytes stderr;
* the E.2 brief's own transform applied verbatim: 5 services, 0 absolute `/home/` paths left;
* `support_compose.compare(mine, fixture)` → **0 service differences**;
* `support_compose.compare_stack(mine, fixture)` → 1 line, and the controls
  `compare_stack(fixture, fixture)` and `compare_stack(mine, mine)` return **the same single
  line**, so it is a property of the transform, not of either run.

That reproduces the 7.1 lane's numbers by a different route and a different script, and it is what
takes the circularity out of clause 10.

### It also reproduces the 7.1 lane's open finding about the fixture

The byte diff between my brief-transformed capture and the committed fixture is 38 lines, and
**none of them is a difference between the two installs**:

* `command: null` / `entrypoint: null` on every service and `ipam: {}` on every network — emitted
  by the `--format json` route, absent from the YAML route;
* four nested `name:` keys — `networks.ac-network.name`, `networks.default.name`,
  `volumes.client-data.name`, `volumes.db-data.name` — **present in every real capture and absent
  from the committed fixture**.

The fixture's minting recipe, `pyplan/phase7-plans/7.1-spine-azerothcore-linux.md:6486`, is
`raw.pop("name", None)` at the **top level** plus a path rewrite. That cannot reach the four
nested keys, and the fixture contains **zero** occurrences of `yulon-wow-wotlk` and no `"name"` key
at any depth. `networks` and `volumes` in it are bare `{}`. So the committed fixture does not
correspond to the recipe its own brief documents, and nothing recorded says why. **Either the brief
or the fixture is wrong**; this is the second lane to reach that conclusion independently, and it
wants a decision, because `test_compose_fixture.py` compares every future render against it.

---

## The 7.2 change set itself, audited structurally

`change-set-audit.txt`, taken against `badee625`. Every item on the 7.2 line, checked:

| 7.2 line item | Found |
|---|---|
| six `install-*.sh`, `dml-start.sh`, `wow-manage.sh` | **0 under `pylauncher/`**, 0 anywhere outside `archive/guides/` (unrelated games). Deleted by `2fddaa0e` |
| `installer.Installer` | gone — the only `class Installer` hit is `InstallerError` |
| `PROMPT_RULES`, `make_responder` | gone from the product; the 7 and 5 remaining hits are all in `tests/` or in prose recording that they were deleted |
| `bash_available` | gone from the product; `tests/support_bash.py` re-implements it for the tests that still need a bash probe, which the test file's own docstring calls out as deliberate ("Spelled inline since 7.2") |
| `Install.script*` fields | gone; one hit left, a comment in `catalog.py:690` |
| gaming mode → `catalog/installers/steam-deck/setup-gaming-mode.sh` | present, 9,641 bytes |
| `contribution.md` harness paragraph | no `install-*.sh` / `PROMPT_RULES` / `make_responder` mentions remain |
| style-guide §3 rows for `catalog/installer.py` and `catalog/catalog.py` | present, plus a `catalog/native.py` row that says *"Since 7.2 this is the only kind of engine there is"* |
| the three CMaNGOS entries `platforms: []` | **diverges, legitimately.** They read `["linux"]`, not `[]`. The line said `[]` *"until their own gates"*; 7.3, 7.4a/b/c and 7.6 have since landed, which is what widened them. Worth a note on the line, not a defect |

## Files

| File | What it is |
|---|---|
| `full-checks/SUMMARY.txt` | the six CI checks, exit codes and elapsed times |
| `full-checks/ruff.txt`, `black.txt`, `mypy-native.txt`, `mypy-win32.txt`, `mypy-darwin.txt`, `pytest.txt` | each check's full output, with its argv, the CI line it mirrors, and its exit code |
| `full-checks/pytest-integration.txt` | the live-Docker job on Windows — an observation, see above |
| `full-checks/run-checks-as-run.ps1` | the script, exactly as it ran |
| `attempt1-basetemp-denied.txt`, `attempt1-diagnosis-traceback.txt` | the invalid first attempt and the run that isolated its cause |
| `compose-recheck.txt` | the independent compose capture and comparison, taken on the box |
| `change-set-audit.txt` | the 7.2 change set checked item by item against `badee625` |
