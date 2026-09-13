# T54 — "Forget this install…" is hidden exactly when the user needs it

**Status:** FILED — user-reported on 0.8.65-Public, cause located.
**Filed:** 2026-09-13 by the lead, from a report relayed by the owner.
**Branch:** `fix/forget-button-hidden-when-docker-is-gone`, from `upstream/Yulon`.

## What the user hit

They installed WotLK to `E:\Games\Yulon Wotlk`, then deleted the folder by hand — and Docker
Desktop with it. Yu'lon still showed the install. Uninstall refused, correctly, and told them
what to do:

> Nothing here says Yu'lon installed it: there is no install record in E:\Games\Yulon Wotlk, so
> this folder is not Yu'lon's to delete. Nothing was removed. **If the folder is gone for good,
> "Forget this install…" drops this tab.**

> *"Where is 'Forget this install' — Cannot find that at all within Yulon"*

They could not find it because it cannot be shown to them.

## The mechanism

The button exists (T34), is created whenever the uninstall seam does, and starts hidden. It is
revealed by `_update_forget_visibility()`, which has **exactly one caller**:

```python
@Slot(object)
def _status_ready(self, result: object) -> None:      # the poll SUCCEEDED
    ...
    self._update_forget_visibility()

@Slot(object)
def _status_failed(self, exc: object) -> None:        # the poll FAILED
    self._status_pending = False
    self.status_label.setText(f"status: Docker not reachable ({exc})")
    self.realm_badge.set_status("stopped")
    # and nothing else
```

`controller.status` asks Docker. **With Docker gone, every poll takes the failure path**, so
the reveal never runs and the button stays hidden for as long as the condition lasts.

`_status_ready` has a second, narrower version of the same hole: it returns early when the
result is not an `InstallStatus`, before reaching the reveal.

## Why this is worse than an ordinary miss

The button's whole purpose is the state where the install is gone. **An install that is gone is
the case most likely to have taken Docker with it** — the user deletes a folder, uninstalls
Docker Desktop, and tidies up. So the control is hidden in precisely the situation it was
built for, and the refusal message confidently names it.

The two halves also share a predicate and agree with each other — `purge.py:244` adds that
sentence under `wsl_distro is None and folder_is_gone(server_dir)`, which is exactly
`_forget_is_eligible()`. The sentence was right. Only the reveal was unreachable.

This is the fourth instance today of a mechanism that exists and is not reached; see
`the-mechanism-exists-and-nothing-calls-it`. The first three were a function nobody called, a
schema field nobody read, and a declaration nobody enforced. This one is a widget nobody shows.

## Definition of done

1. The reveal runs on **every** poll outcome, success or failure — the button's own predicate
   already decides the answer, and it needs no Docker.
2. `_status_ready`'s early return no longer skips it.
3. A test that drives the FAILURE path and asserts the button appears, since that is the path
   the user was on and the one with no coverage.

## Evidence the ticket owes

The gate's last line and a named mutation per test — at minimum, one that restores the reveal
to the success path only and is seen to fail.

## Done — evidence

**Status:** FIXED 2026-09-13.

The reveal now runs on **every** poll outcome. `_status_failed()` calls it, and
`_status_ready()`'s early return no longer skips it. `_forget_is_eligible()` is unchanged and
needs nothing from Docker: it asks `wsl_distro` and `folder_is_gone()`, both local.

**Confirmed against the user's ACTUAL build**, `v0.8.65-Public`, rather than against this
branch:

```
_status_ready    2965   early return 2969   reveal 2987     (success path only)
_status_failed   3174   label, badge, end                   (no reveal)
_forget_is_eligible -> wsl_distro is None and folder_is_gone(server_dir)
```

That also settles the workaround given to them: on their build, a poll that SUCCEEDS with the
folder gone does reveal the button. So "reinstall Docker Desktop, start Yu'lon, press Forget"
is verified rather than assumed — it was offered with a caveat first, and the caveat was
removed only after reading the released file.

**Gate:** `4368 passed, 8 skipped, 23 deselected`. ruff, black, mypy clean on `linux`, `win32`,
`darwin`.

**Mutations**, 2 run, 2 killed (`pyplan/gates/t54-forget-button-2026-09-13/mutations/`):

| | | |
|---|---|---|
| M1 | the reveal goes back to the success path only | the new test fails — the shipped bug |
| M2 | the folder check dropped from the predicate | the button shows for a living install |

The gate was taken twice: the first run passed with a `F821 Undefined name` in the new test,
which ruff caught afterwards. Fixing it changed the file under test, so the run was repeated
rather than quoted.

## Note for whoever reads this next

The button's condition is "the install is gone". **Ask what else is usually true in that
condition** — here, that the user has also removed Docker, which is what the reveal depended
on. A control with one stated condition often has an unstated correlate, and that correlate is
where it breaks.

## Review round 1 — HIGH, and the fix had created it

Adversarial Codex review, 2026-09-13, on the pushed branch. Verdict **needs-attention**.

> A transiently unavailable install drive is treated as a deleted install … the user is then
> told the folder "no longer exists" and can remove Yu'lon's state record, leaving a live
> install and its Docker resources unmanaged.

`folder_is_gone()` returned `True` for any `FileNotFoundError` from `os.stat`. That is the same
error for `E:\Games\Yulon Wotlk` when the folder was deleted **and** when the whole of `E:` is
unplugged, asleep, or a disconnected share. Its docstring promised *"CONFIRMED absence — never
'cannot tell'"*; it delivered "the leaf did not stat".

**This ticket is what made it dangerous.** Before it, an offline drive was protected by
accident: no Docker, no reveal. An offline drive takes Docker with it often enough — the VM
lives on that disk, or the machine has just woken — so the two failures arrive together, and
the reveal would have offered to drop the only record of a LIVE install.

Fixed by making the parent corroborate: the leaf being absent is not an answer until the
directory that would contain it is seen. Parent present → the folder really is gone. Parent
absent → the VOLUME is missing, and the predicate says so by refusing.

The reporter's own install is on `E:\`, which is exactly the shape this would have bitten.

Gate after: `4369 passed, 8 skipped`. ruff/black clean, mypy clean on all three.
M3 (take the leaf's absence as the answer) — KILLED.
