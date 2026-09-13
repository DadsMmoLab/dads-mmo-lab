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

## Review round 1 — three findings, and the first was that there was nothing to review

Adversarial Codex review, 2026-09-14. Run BEFORE opening the PR, which is the order the earlier
tickets tonight got wrong.

**HIGH — the branch contained only this ticket.** The fix was written, tested green, and never
committed, so the review read a Markdown file. *"Uncommitted working-tree changes are not part
of the reviewed branch and would not ship."* Committed, then re-reviewed. **Second time in one
night** that uncommitted work was treated as done — the other was T53's fixes, reported to the
owner as pushed when they were not.

**HIGH — the fallback cannot reach the reporter's install.** `ContainerGit` bind-mounts the
destination and Docker Desktop refuses a `\\wsl.localhost\...` mount source, which this
repository already knows (`platform._WSL_SHARE_PREFIXES`, `wsl_linux_path()`). Smn.sez's server
is at `\\wsl.localhost\dml-arch\home\dml\games\Wowbots`. Silently choosing that seam would have
traded a fast host-git failure for a slow containerised one, after pulling an image, and still
installed nothing. Now refused precisely, naming WSL and a remedy, using the same
`wsl_linux_path()` test the rest of the app uses to spot that shape.

**HIGH — the error could lie about data loss.** The first fix caught every `OSError` around the
WHOLE `clone()` call. Both seams `rmtree()` and `mkdir()` the destination *before* spawning git,
so a permission error or I/O failure mid-delete would have been reported as *"git could not be
started … Nothing was changed"* with part of the destination already gone. Narrowed to
`FileNotFoundError` at the spawn.

**And the gate caught the reviewer's fourth point concretely:** `git_available()` spawned
`git --version` on every Applier construction, including UI paths that never clone, and three
controller tests that assert exactly which commands a tab runs went red. The seam is chosen
lazily now, on the first clone that needs one, and the three reader seams (`remote_url`,
`unmodified`, `no_local_commits`) bind at call time instead of inspecting the seam's type in
`__init__`.

Gate after: `4371 passed, 8 skipped`. ruff/black clean, mypy clean on all three.
