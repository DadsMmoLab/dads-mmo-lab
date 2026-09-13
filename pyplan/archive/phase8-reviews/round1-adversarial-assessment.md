# Codex adversarial review — my assessment before the reconciliation

Five findings, three high. I checked each against my own text rather than against the design it
came from. **All five hold.** None is refuted; two are partly anticipated by the winning design and
were flattened out of the decisions page, which is itself the defect.

## 1 (high) — the seam promises reply semantics the attach transport cannot deliver. HOLDS.

The architecture table gives `channel.py` "the three-outcome answer type, the channel protocol, its
three implementations" with no statement that one of the three answers weakly. The transport's own
header says nothing reads the stream while it arrives, the reply is cut out by prompt position, and
output landing between the two prompts is claimed as part of the reply and cannot be separated
(`console.py:10-12`, `:468-527`, `:522-527`). Owner answer 10 makes Tortoise attach-only, so on that
tree **every** account and GM action would be an indeterminate write reported as a determinate one.

Design A had the machinery — a per-command optional verify read — and my page dropped it into a
single word ("verify read") inside a features row. The fix is not new work; it is saying the true
thing: a mutation on a reply-less transport is **indeterminate until its own verify read answers**,
and an operation with no verifier is not offered on that transport.

## 2 (high) — the pre-write port probe is check-then-act and cannot prevent the fatal bind. HOLDS.

My correction 3 says 8.1 "proves the port is free before it writes anything". Between the probe and
the bind there is a window, and a bind can fail for reasons a probe cannot see. On the CMaNGOS trees
the consequence is `exit(-1)` with no character saves plus a restart loop.

Codex's remedy is better than mine and simplifies the step: **require the world stopped** for the
enable press. The panel had already made the press two-press-armed *because* it restarts a running
world; requiring stopped removes the hazard rather than warning about it, makes the rollback a
config revert plus a normal start, and costs a user nothing they were not already being asked for.

## 3 (high) — the diagnostics land after the step that needs them. HOLDS, and it is an ordering error.

8.1 recreates servers and can produce a restart loop; restart-loop detection, the command interlock
on an unstable server, and the pre-stop log snapshot are all 8.2. So the first live gate runs the
highest-risk change with none of the instruments the plan itself says are needed, and a rollback can
destroy the container log that would explain the failure. The snapshot and the restart-count read
are small and depend on nothing in 8.1. **8.2 moves before 8.1.**

## 4 (medium) — gear sets are promised on four families on an unread schema. HOLDS.

The delta table records the CMaNGOS and Tortoise inventory joins as unverified. The checklist's 8.4
line qualifies its actions with "drawn only where the tree has the command" — which is a statement
about command tables and does not cover gear-set *reading*, which is a schema question. So the box
reads as promising all four. Either the read happens first or the box says WotLK.

## 5 (medium) — a mandatory exit box is blocked on hardware nobody has, and uninstall is last. HOLDS.

The exit box requires every `8.x` ticked and 8.8 needs a Steam machine that does not exist on this
side — the same shape as Phase 7's macOS box, which the owner resolved by assigning it to Baerthe
rather than by leaving the phase unable to exit. And validating the destructive recovery path last,
after eight steps have mutated installs, is backwards.

The uninstall half interacts with owner answer 9: the throwaway Vanilla install is shared between
8.1d and 8.9. That still works if 8.9's WotLK half moves early on its own throwaway install and the
Vanilla half consumes the shared one at the end of that box's sequence. Whether 8.8 blocks the exit
box is an owner question, not mine.

## What this review did not raise, and I checked

It did not challenge the two facts that decided the design ranking (when the SOAP thread starts, and
that the accept loop is inline) — both of which I verified myself against the fetched trees. It did
not dispute the per-tree data. It did not find a step that deletes working code.

---

## Two things I checked while assessing, which make the fixes cheaper than they look

**Requiring the world stopped uses the path Phase 7 already proved.** `start_staged()` runs
`compose up -d --no-deps <db> <auth> <world>` (`docker.py:766`). On a stopped install that brings the
services up with whatever configuration is currently rendered, including a changed override
environment — so the enable press becomes: write the configuration while the server is down, then
the user's ordinary Start brings it up with the new value. No recreate of a running container, no
re-triggering the import one-shot (that is what `--no-deps` is for), and the failure mode becomes
"the server I just started did not come up", which the app already knows how to show. The two-press
arm is then unnecessary, because there is nothing running to interrupt.

**The reordering splits cleanly, because the read side never needed the channel.** The dashboard's
counts and the bot marker are database reads through the seam that already exists, and the log
snapshot is two docker commands. Neither touches SOAP. So the first step becomes observability —
log snapshot, restart-count detection, the command interlock on an unstable server, and the reads —
and the channel becomes the second step, arriving on a server the app can already watch. The read
module moves with it; nothing is duplicated.
