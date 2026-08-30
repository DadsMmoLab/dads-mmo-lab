# macOS clone failure — handoff brief

**For whoever picks this up on a Mac, with or without an agent's help.** Written
to be read cold. It assumes nothing about the five days behind it, and it asks
for the cheap thing first.

The bug: every install on macOS dies at the clone stage with

```
containerized git clone --config core.autocrlf=false --config core.eol=lf \
  --config http.version=HTTP/1.1 --branch Playerbot \
  https://github.com/mod-playerbots/azerothcore-wotlk.git . in /Users/js/wow3 \
  exited 1: Cloning into '.'...
/git/.git: No such file or directory
```

**The same command, typed into Terminal on the same machine, clones fine.** That
is the whole shape of the problem, and it is why this needs a Mac rather than
more reasoning.

---

## Step 1 — the suite on a Darwin interpreter (10 minutes, do this first)

Worth doing even if you never touch the bug. **Nothing in this project has ever
run on a Mac's Python.** `pyplan/macos-gate-run-sheet.md` Step 1 has been open
for weeks purely for want of hardware.

```bash
git clone https://github.com/DadsMmoLab/dads-mmo-lab.git
cd dads-mmo-lab/pylauncher
git checkout Yulon
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

pytest -q
ruff check yulon tests
black --check yulon tests
mypy yulon
```

Send the four result lines. Linux gives roughly `945 passed, 27 skipped`; the
skips are Windows-only and POSIX-shell tests, so expect *fewer* on macOS, not
more. A failure here is a finding in its own right — the last time an untested
platform was assumed fine, `shutil.which`'s win32 branch had grown a `_winapi`
call that killed the app at startup.

## Step 2 — run the app from source

```bash
python main.py
```

This is the part that matters most. Every fix attempted so far has cost a tag,
a CI build, a `.dmg` download and a reinstall — four round trips for four
one-line changes. A Mac that can run from source ends that loop.

Install a server into a **brand new empty folder** under your home directory
(not `~/Documents` — Docker Desktop cannot see inside it on at least one Mac).

## Step 3 — the actual question

The clone runs in `pylauncher/yulon/git.py`, `ContainerGit._capture()`. It
builds an argv and hands it to `runner.run()`:

```python
argv = [program, "run", "--rm", "-v", f"{dest}:/git", "-w", "/git",
        *self._user_args(), self.image, *_LINE_ENDING_ARGS,
        *_HTTP_VERSION_ARGS, *git_args]
proc = runner.run(argv, env=_no_prompt_env())
```

Immediately before that line, dump:

- `argv` — though it is already logged at INFO, and has been verified identical
  to a command that works by hand
- `os.getcwd()` — the app is a launchd child; a shell is not
- `spec.dest.exists()` and `oct(spec.dest.stat().st_mode)` at that instant
- `sys.stdin` / whether fd 0 is open — `runner.run()` uses
  `capture_output=True` and leaves stdin inherited, and a GUI process's stdin
  is not a terminal

Then bisect the one difference that is left: **the same command succeeds from a
shell and fails from this process.** Everything about the command itself has
been eliminated (see the table below). Candidates nobody has been able to test:
the frozen PyInstaller runtime, the launchd process context, the background
thread the install runs on, and the inherited file descriptors.

---

## What is already ruled out

Do not re-test these. Each was killed by a run on the affected Mac, not by an
argument:

| Hypothesis | Killed by |
|---|---|
| `--user <uid>:<gid>` breaks the write | a clone WITH the flag, verified by `ls .git` |
| the folder was under `~/Documents` | he had moved off it before the first report |
| a failed image pull | fixed separately (#113); the clone failure survived |
| `rmtree` + `mkdir` hands Docker a stale inode | `rm -rf`, recreate, clone — works |
| the app's argv differs from a manual one | logged (#114), run verbatim by hand, cloned |
| the environment or the docker binary | `env -i` plus the bundle's own `docker`, cloned |
| the mount is `root:root`, so 501 cannot write | `touch` and `mkdir /git/.git` as `501:20` both exit 0 |
| recreate **and** `--user` together | the exact argv, recreated folder, `--user 501:20`: exit 0 |

The full account, including the two real bugs found while chasing this one, is
in `pyplan/checklist.md` under *"The Mac clone: eight hypotheses, eight
refutations"*.

## How to report a result

One rule, learned expensively. **A test result is an exit code and a listing,
never an impression.** Hypothesis 1 above was raised on day one, dropped
because a manual run "looked like it was working", and came back five days
later with evidence that was itself wrong. Finish every check with:

```bash
… ; echo "exit $?"
ls -lad <the directory>
```

and paste both lines.
