# Tickets

One file per ticket, `T<number>-<what-is-wrong>.md`: the spec, the hand reports, the reviews and the
close. The ticket's own `**Status:**` line is the record; this index only copies it.

The table below was read from those lines on 2026-09-14. A ticket updated since then is right and
this table is not. To read the current state:

```bash
grep -m1 -H '^\*\*Status:\*\*' pyplan/tickets/T*.md
```

Numbers missing here (T31, T45–T48, T51, T55 and any after T59) were filed on branches that had
not merged into `Yulon` when this index was made.

## Not closed, by their Status line

| Ticket | Status line says | What it is about |
|---|---|---|
| [T11](T11-tortoise-upgrade-route-for-marked-installs.md) | ACCEPT | A Tortoise install that already carries an import marker gets the character updates too |
| [T16](T16-chosen-level-does-not-hold-at-join.md) | IN PROGRESS | A chosen level sent at join time is accepted and does not hold |
| [T28](T28-catalog-tiles-are-unequal-columns.md) | IN PROGRESS | The catalog's two tile columns take unequal widths, so the tiles come out different sizes |
| [T29](T29-steam-deck-path-on-arch-gamescope-and-a-server-to-play.md) | (no status line) | the Deck path for Steam's Resume (gamescope on `yulon-arch`) and a server behind the entries |
| [T37](T37-a-full-docker-disk-is-answered-with-move-the-install.md) | OPEN | a full Docker disk is answered with "install to a drive that has room", which the user already did |
| [T38](T38-a-failed-build-reports-the-command-not-the-error.md) | OPEN | a failed build reports the command that failed, never the error that failed it |
| [T39](T39-the-wsl-data-root-is-the-wrong-filesystem.md) | OPEN | under Docker Desktop's WSL integration the free-space check measures the wrong filesystem |
| [T40](T40-remedies-that-name-an-action-the-platform-cannot-take.md) | OPEN | four preflight rows tell a Linux user to open Docker Desktop |
| [T41](T41-the-modules-tab-never-says-what-is-installed.md) | OPEN | the Modules tab lists the catalog and never says which modules are installed |
| [T42](T42-the-modules-tab-reads-installed-first-per-family-with-status-on-every-row.md) | FILED | the Modules tab reads installed first, per family, with status on every row |
| [T49](T49-remove-fails-on-windows-on-read-only-git-files.md) | FILED | removing a module fails on Windows, and the fix was already in the tree |
| [T50](T50-docker-desktop-keeps-the-build-log.md) | FILED | on Docker Desktop the compiler's error is not in the stream at all, and T38 assumed it was |
| [T52](T52-we-ship-a-module-that-cannot-compile.md) | FILED | we ship a module that cannot compile, and we pin nothing |
| [T54](T54-the-forget-button-is-hidden-when-it-is-needed.md) | FILED | "Forget this install…" is hidden exactly when the user needs it |
| [T57](T57-steamos-install-fails-and-then-advises-the-wrong-fix.md) | FILED | the SteamOS Docker install fails on the keyring, then advises a fix for the symptom |
| [T58](T58-module-install-needs-host-git-and-does-not-say-so.md) | FILED | a module install needs host `git`, nothing says so, and the error names neither |

## Closed, by their Status line

| Ticket | Status line says | What it is about |
|---|---|---|
| [T1](T1-tick-8.6.md) | ACCEPTED and MERGED | Tick 8.6, and correct the two gate lines that name a dead box |
| [T2](T2-8.7a-direct-sql-press.md) | ACCEPTED as evidence and MERGED | Press the direct-SQL guard against a live world, so 8.7a can tick |
| [T3](T3-7.10-clause-35.md) | CLOSED | The 7.10 clause that compares a plan to itself, corrected and re-run |
| [T4](T4-tortoise-rebuild-and-reimport.md) | ACCEPTED and MERGED | Rebuild the Tortoise image on the m910q, then the reimport with no pre-application at all |
| [T5](T5-my-party-spec-level-dismiss-all.md) | CLOSED | My Party: chosen spec, chosen level, dismiss all — the three things 8.6's own line promises and the panel says it lacks |
| [T6](T6-record-corrections-the-lead-owes.md) | ACCEPTED and MERGED | Four record corrections the lead owes, each found by a reviewer on another ticket |
| [T7](T7-wire-the-direct-sql-guard-into-the-app.md) | CLOSED | The direct-SQL guard reaches the app's own buttons, and "Stop, then install" can actually be done |
| [T8](T8-rebuild-regenerates-the-dockerfile.md) | CLOSED | A Rebuild that recompiles the Dockerfile on disk can never carry a template fix to an existing install |
| [T9](T9-repair-knows-mangos-sha.md) | ACCEPTED and MERGED | Repair cannot repair a Tortoise install: the shared password reset does not know `mangos_sha` |
| [T10](T10-tortoise-character-updates-phase.md) | ACCEPTED and MERGED | The Tortoise catalog applies the fork's `character_updates/` directory, so an install stops dying on a calendar |
| [T12](T12-accounts-no-scheme-falls-through.md) | CLOSED | No account path defaults an unrecognised scheme to AzerothCore's columns |
| [T13](T13-dml-uninvite-verifies-the-master.md) | CLOSED | `dml_uninvite` removes a bot only from the party of the master that asked |
| [T14](T14-apply-pending-database-updates-button.md) | CLOSED | A button applies a re-runnable phase to an established install |
| [T15](T15-refusal-sentences-true-under-rebuild.md) | CLOSED | The two Dockerfile refusals give advice that is true under Rebuild as well as Install |
| [T17](T17-steam-shortcuts-on-a-fullscreen-steam-stand-in.md) | CLOSED | 8.8 Steam integration, gated on fullscreen Steam on a Linux VM as the Deck stand-in |
| [T18](T18-deploy-playerbots-conf-so-a-spec-can-take-effect.md) | CLOSED | A chosen spec can take effect once the install has a deployed `playerbots.conf` |
| [T19](T19-updates-button-cannot-reach-a-marker-less-install.md) | CLOSED | The updates button cannot reach a marker-less install, and prints the install's cancel note |
| [T20](T20-paused-world-passes-the-direct-sql-guard.md) | CLOSED | A paused worldserver passes the direct-SQL guard |
| [T21](T21-a-failed-group-read-after-an-uninvite-reads-as-removed.md) | CLOSED | A failed group read after a genuine uninvite is reported as "removed" |
| [T22](T22-wotlk-password-repair-defaults-the-scheme.md) | CLOSED | The WotLK password-repair callback defaults the account scheme to AzerothCore |
| [T23](T23-clause35-runner-two-fail-open-paths.md) | CLOSED | The 7.10 clause-35 runner has two fail-open paths (restart window, firewall backup) |
| [T24](T24-engine-route-reruns-ddl-under-a-running-world.md) | CLOSED | The engine's install route re-applies a flagged phase without proving the world is down |
| [T25](T25-rebuild-marker-prefix-and-touched-before-recreate.md) | CLOSED | A rebuild replaces a file whose first line merely begins with Yu'lon's marker, and marks the recreate touched before it starts |
| [T26](T26-my-party-adds-a-named-character-as-a-bot.md) | CLOSED | My Party adds a chosen existing character as a bot (own alts, other accounts, friends' and family's characters) |
| [T27](T27-a-conf-activation-never-recommends-a-restart.md) | CLOSED | Activating a module's conf never recommends a restart, though the world reads it only after one |
| [T30](T30-tortoise-moves-to-the-penqle-core-and-the-tortoisebots-module.md) | CLOSED 2026 | Tortoise moves to the Penqle core with the TortoiseBots module |
| [T32](T32-the-foreign-container-refusal-names-the-folder-and-the-failed-field-is-copyable.md) | CLOSED 2026 | the foreign-container refusal names the folder it belongs to, and the FAILED field is copyable |
| [T33](T33-a-yes-on-a-question-dialog-reads-as-no.md) | CLOSED 2026 | a Yes on a question dialog reads as No: the static `QMessageBox.question()` returns an `int` |
| [T34](T34-a-tab-whose-folder-is-gone-can-be-forgotten.md) | CLOSED 2026 | a tab whose server folder is gone can be forgotten, and a missing folder is not "Docker could not be found" |
| [T35](T35-the-log-panel-gets-a-stage-strip-and-three-tones.md) | CLOSED 2026 | the log panel gets a stage strip with real progress, and three tones |
| [T36](T36-a-client-folder-can-be-set-on-an-install-after-the-fact.md) | CLOSED 2026 | a client folder can be set, changed or cleared on an install after the fact |
| [T43](T43-a-tuning-tab-for-the-modules.md) | CLOSED 2026 | a Tuning tab: every module setting, with the file behind it editable |
| [T44](T44-the-modules-tab-carries-everything-the-mockup-promised.md) | CLOSED 2026 | the Modules and Tuning tabs carry everything the mockups promised, and look like them |
| [T53](T53-conflicts-with-was-declared-and-never-enforced.md) | FIXED 2026 | `conflicts_with` was declared, parsed, tested, and never enforced |
| [T56](T56-preflight-never-checks-for-compose.md) | FIXED 2026 | preflight asks whether Docker answers, never whether Compose exists |
| [T59](T59-lootpet-install-is-broken-in-upstream.md) | FIXED on the branch | the Loot Pet manifest on `Yulon` cannot install |
