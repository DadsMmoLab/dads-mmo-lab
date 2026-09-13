# T57 — the SteamOS Docker install fails on the keyring, then advises a fix for the symptom

**Status:** FILED — not fixed. Needs a Steam Deck (or a SteamOS VM) to do honestly.
**Filed:** 2026-09-13 by the lead, from NaniWat's report on 0.8.65-Public, relayed by the owner.
**Related:** T56 is the SECOND error the same user hit; this is the first.

## What the user saw

```
DockerUnavailableError: Docker isn't available and could not be set up automatically.
Some steps did not run:
  pacman -Sy --noconfirm docker docker-compose docker-buildx: exit 1
    warning: Public keyring not found; have you run 'pacman-key --init'?
    error: keyring is not writable
    error: required key missing from keyring
    error: failed to commit transaction (unexpected error)
  systemctl enable --now docker: exit 1
    Failed to enable unit: Unit docker.service does not exist
  usermod -aG docker deck: exit 6
    usermod: group 'docker' does not exist
You said yes, but adding deck to the docker group did not work — see the step above for why.
Until it does, Yu'lon cannot use Docker here. To do it yourself:
  sudo usermod -aG docker deck, then log out and back in.
```

## The defect is the advice, not the detection

Read the failures downward and they are **one cause**: the keyring is not initialised, so the
package never installs, so there is no `docker.service`, so there is no `docker` group.

**The one actionable instruction we give addresses the last link in that chain.** If the user
runs `sudo usermod -aG docker deck` it fails again with `group 'docker' does not exist`, because
the group is created by the package that never installed. We print the root cause and then
recommend a remedy for the symptom furthest from it.

"see the step above for why" is doing a lot of work there, and it is asking a user who told us
*"I am terrible with linux"* to perform the diagnosis themselves.

## What SteamOS actually needs

The user got unstuck with a community guide, which is the shape the fix wants:

1. `sudo steamos-readonly disable` — the root filesystem is read-only by default,
2. `sudo pacman-key --init`,
3. `sudo pacman-key --populate archlinux holo`,
4. then the package install we already run.

Steps 1–3 are exactly what our error reports as missing and never offers to do.

## Why this is not fixed in the same change as T56

Every step above changes a system-level property of a machine this project has never run on.
`steamos-readonly disable` in particular persists, is not ours to leave behind silently, and
an update can re-enable it. Guessing at the semantics from a gist and shipping it blind is the
shape of mistake that costs somebody their Deck. It wants a real device, or a SteamOS VM,
before any of it is written.

## Definition of done, when it is taken up

1. **Detect the keyring state before running `pacman`**, so the failure is anticipated rather
   than reported after the fact.
2. **Make the remedy match the CAUSE.** A refusal whose only instruction cannot succeed is
   worse than no instruction: it spends the user's trust as well as their time. At minimum the
   message must lead with the keyring, not the group.
3. **Decide whether Yu'lon runs steps 1–3 itself or instructs.** Both are defensible; doing it
   silently is not. If it runs them, `steamos-readonly` must be restored, and the fact that it
   was touched has to be visible.
4. A press on a real Steam Deck, start to finish.

## The general lesson, which is not SteamOS-specific

**When several steps fail in a chain, the remedy belongs to the FIRST failure, not the last.**
This message enumerates all three correctly and then advises on the third. Worth checking
wherever else a multi-step provisioning path reports "some steps did not run".
