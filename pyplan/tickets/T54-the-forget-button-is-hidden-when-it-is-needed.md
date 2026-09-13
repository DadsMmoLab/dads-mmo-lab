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
