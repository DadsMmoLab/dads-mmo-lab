# T58 — a module install needs host `git`, nothing says so, and the error names neither

**Status:** FILED — two users, same symptom, cause located.
**Filed:** 2026-09-14 by the lead, from Ravness and Smn.sez, relayed by the owner.
**Branch:** `fix/module-install-without-host-git`, from `upstream/Yulon`.

## What the users saw

> *"Yulon can find the server files, start and stop the server and see my account on it, but
> when I ask it to install a module of any kind I get this error"* — Ravness

```
install mod-aoe-loot FAILED: [WinError 2] The system cannot find the file specified
```

Two users, independently, on `0.8.67-fixtest`. A working server — realm online, 500 bots — and
not one module installable.

## The mechanism

`WinError 2` from `subprocess` is the **executable** not being found, not a missing file in the
clone. It is `git`.

- The **server** install clones through `ContainerGit`, which runs git inside a container. So
  Docker alone is enough to get a running server, and a user never learns whether they have
  git.
- The **module** install does not. `controller_view.py:1199` builds the applier with no `git=`
  argument, and `Applier.__init__` defaults to `RunnerGit()` — **host git**:

```python
self.git: Git = git if git is not None else RunnerGit()
```

So every module install on every platform requires a `git` on PATH that the server install
never needed. A user who installed Docker and nothing else gets a perfect server and a Modules
tab that cannot install anything.

**And the message names neither git nor a remedy.** `install()` catches `GitError` — *"one
failure vocabulary for the whole applier"* — and a missing executable raises
`FileNotFoundError`, which is not one. It escapes raw, so the user is shown a Windows error code
about an unnamed file.

## Two fixes, because either alone leaves a bad outcome

1. **Do not require host git at all.** `git_available()` already exists (and is careful: it
   rejects macOS's Command Line Tools stub, which pops a modal installer instead of running).
   When the host has no usable git, use `ContainerGit` — the same seam the server install uses,
   already tested, already the default for the larger job.
2. **Say what is wrong when neither works.** A `FileNotFoundError` from a spawn must not reach
   a user as `[WinError 2]`. It names `git`, and what to do about it.

## Not in scope, and stated rather than assumed

Smn.sez's server lives inside a WSL distro, reached from Windows as
`\\wsl.localhost\dml-arch\home\dml\games\Wowbots`. Whether `ContainerGit` can mount a UNC path
of that shape is a separate question this ticket does not answer — Docker Desktop cannot bind a
`\\wsl.localhost\` path directly. If the fallback cannot work there, that user still needs the
clear message from fix 2, and the WSL-backed install route wants its own ticket.

## Definition of done

1. `Applier` chooses its git seam instead of assuming one: host git when it is usable,
   containerised git when it is not.
2. A spawn failure is reported as a missing git, with a remedy, never as a bare OS error.
3. A test that drives `install()` with no host git and asserts the module still installs.
4. A test that asserts the message names `git` when nothing can run it.

## Evidence the ticket owes

The gate's last line, a named mutation per test, and — because the reporters are on Windows and
this is measured on Linux — a note on which half remains unproven there.
