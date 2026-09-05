# Modules tab: ownership before a destructive clone

Branch `fix/modules-tab-ownership` (worktree `C:\Users\perzi\dml-modules`, based on `yulon-phase7`).

## The defect

`Applier.install()` handed `server_dir / "modules" / <id>` straight to the clone seam with no
check of any kind. `yulon/git.py`'s clone then either `shutil.rmtree`s a destination that is not a
git checkout, or runs `git fetch` + `git reset --hard FETCH_HEAD` on one that is — without ever
comparing its origin. The shipped Modules tab binds "Install selected" to `applier.install()`
directly, so a user who had installed or edited a module by hand under the conventional
`modules/<id>` path (the AzerothCore convention; the id comes from a catalog this app chose) lost
it on one press. Non-git content was deleted outright, tracked and untracked working-tree changes
were discarded by the hard reset, and no message anywhere described replacement.

`remove()` was worse in one respect: `shutil.rmtree(clone)` needs no git seam at all.

## The shape

A per-clone claim file, `.yulon-clone.json`, written inside the clone after a successful clone and
read before the next one starts. It records the item id and `composegen.install_id()` of the clone's
own absolute path.

Inside the clone rather than beside it because `modules/` is scanned by AzerothCore's CMake and a
sibling entry there would be a new kind of thing in it; because `git reset --hard` does not remove
untracked files, so the claim survives the very update path it authorises; and because `remove()`
deleting the clone deletes the claim with it, leaving no second place to forget about. It joins
`include.sh` as the second file this engine already writes into a clone.

`Applier._require_own_clone()` is the one guard, and all three writers through `clone_dir()` go
through it. Evidence in order, nothing written before all of it is in:

| what is at `modules/<id>` | answer |
| --- | --- |
| nothing | allowed — the ordinary first install |
| a file | refused |
| a directory, no `.git`, with content | refused (hand-installed content; the `rmtree` case) |
| a directory, no `.git`, empty | allowed — nothing to lose |
| a checkout this app's claim vouches for | allowed — the update path |
| a claim that will not parse / names another folder or item | refused (`Ownership.UNKNOWN`) |
| a checkout, `origin` unreadable | refused |
| a checkout of another repository | refused, by name |
| a checkout of the RIGHT repository with no claim | refused |

## Reuse of the native guards

* `Ownership` — moved verbatim to a new dependency-free `yulon/ownership.py` and re-exported from
  `catalog/native.py`, so every `native.Ownership` reference still resolves and `apply.py` gets the
  same three answers without importing the install engine. `UNKNOWN` fails closed in both.
* `_same_repo()` / `_repo_key()` — moved to `yulon/git.py` as the public `same_repo()`, the one
  place both engines can reach. `catalog/native.py` and `catalog/families/azerothcore.py` now call
  `git.same_repo()`; behaviour is unchanged, and the existing loose-comparison test still covers it.
* `native.read_claim()` — **shape reused, contents diverge**, said in `read_clone_claim()`'s
  docstring. There, one record covers a whole server install and the clone stages must corroborate
  it against `git remote get-url origin`, because the record says nothing about any particular
  sub-checkout. Here the record is per-clone: it sits inside the very directory in question and
  names the item whose catalog id chose the path, so it *is* the corroboration. That is why an
  `OWNED` module clone is not re-checked against `origin` — the check would only add a container
  round-trip per update and a class of false refusals on a machine whose git cannot answer.
* `native._guard()`'s not-empty refusal and `refuse_unowned_checkout()`'s three sentences — reused
  as wording and rule, adapted to the module case: the remedy sentence says to move the folder
  aside, and adds that a module clone holds nothing but the module, so re-cloning costs only the
  download. (A server dir cannot say that, which is why its message says "install into another
  folder" instead.)
* `remote_url` — `git.py` grew a one-method `RemoteReader` Protocol rather than widening `Git`,
  which would break every fake. `Applier` narrows its real seam to it with `isinstance()`, so the
  question goes through the same transport the clones do (host git, or the containerized git on a
  machine with none), and falls back to `RunnerGit` for a fake that only clones.

## The two questions asked

**Does `Applier` have an uninstall path with the same exposure?** Yes, and worse. `remove()` ran
remove-time patches and SQL, undeployed, then `shutil.rmtree(clone)` on whatever was at that path.
It is now guarded by the same method, and deliberately *before* the SQL — a refusal must leave the
install exactly as it was, and remove-time SQL is not undoable. `remove()` of this app's own clone
still deletes it, which is asserted.

**Is `clone_dir()` used anywhere else that writes?** Three call sites in `apply.py` and nothing
else in the codebase (`tests/test_apply.py` calls it read-only). The third is `configure()`, whose
`in_clone` patches rewrite a line in a file inside the checkout. It cannot delete anything, but it
is still this app editing a file it did not put there, so it asks the same question — gated on the
manifest actually having a configure-time `in_clone` patch, so a refusal is never about a folder
the run would not have touched.

## Mutation evidence

Every mutant applied to `yulon/apply.py` with every `__pycache__` under the tree purged on both
sides of the edit, restored by writing the original text back.

| mutant | killed by |
| --- | --- |
| M1 non-git leftovers refusal deleted | `test_a_module_folder_the_user_made_by_hand_is_never_deleted`, `test_remove_refuses_to_delete_a_module_folder_this_app_did_not_clone` |
| M2 origin comparison made always-match | `test_a_checkout_of_another_repository_is_refused_by_name` |
| M3 unclaimed checkout allowed through (`raise` → `return`) | `test_the_right_repository_this_app_never_cloned_is_still_refused` |
| M4 unreadable origin treated as permission | `test_git_that_will_not_say_what_a_checkout_is_refuses_rather_than_guesses` |
| M5 damaged claim reads as `OWNED` | `test_a_claim_this_app_cannot_read_as_its_own_fails_closed[empty, truncated]` |
| M6 wrong version / not-an-object reads as `OWNED` | `test_a_claim_this_app_cannot_read_as_its_own_fails_closed[future-version, not-an-object]` |
| M7 claim ignores which folder it names | `test_a_claim_copied_from_another_folder_describes_a_folder_that_is_not_here` |
| M8 `remove()` guard deleted | `test_remove_refuses_to_delete_a_module_folder_this_app_did_not_clone` |
| M9 claim never written after a clone | `test_a_first_install_needs_no_folder_and_the_second_updates_this_apps_own` (+ 5 pre-existing) |
| M10 missing clone dir falls through the guard | `test_a_first_install_needs_no_folder_and_the_second_updates_this_apps_own` (+ 8 pre-existing) |
| M11 `configure()` guard never fires | `test_configure_never_rewrites_a_line_in_a_checkout_this_app_did_not_clone` |

M10 is the mutant this work actually hit for real: the first version of the guard had no
"nothing at the path" branch and refused every first install.

## What the harm tests assert

Each refusal test builds a real `modules/mod-ah-bot` with `src/mine.cpp` in it ("// my patch, three
evenings"), runs `install()` / `configure()` / `remove()`, and then asserts three things: the
`ApplyError` was raised, `git.calls == []` (the seam where the harm lives was never reached), and
`(clone / "src" / "mine.cpp").read_bytes()` is byte-identical to what was written. The remove test
additionally asserts no SQL ran. Two tests also assert the seam was not even *asked* — a folder with
no `.git`, and an unreadable claim, are both refused before git is consulted.

## Checks

From `C:\Users\perzi\dml-modules\pylauncher` with the `dads-mmo-lab` venv python:

* `pytest -m "not integration" -q` → **1319 passed, 71 skipped, 16 deselected** (baseline 1305 + 14 new)
* `mypy yulon main.py` → **Success: no issues found in 43 source files**
* `ruff check .` → **All checks passed!**
* `black --check .` → **88 files would be left unchanged**

## Concerns

1. **A module installed by an older build has no claim, so the first Install after this change is
   refused.** That is the rule the brief asks for (a matching `origin` is not ownership) and the cost
   the native engine already accepted, but it is a real one-time papercut for existing users and the
   message is the whole mitigation: it names the folder, says nothing was changed, and says moving
   it aside costs only the download. A migration that adopted every existing `modules/<id>` whose
   origin matches would undo exactly the protection being added, so it was not written.
2. **`Ownership` moving to `yulon/ownership.py` touches `catalog/native.py`**, a file other branches
   are working in. The move is a re-export, so no other file had to change, but it is a merge point.
3. The claim file is untracked inside a git checkout, so it appears in `git status` for anyone who
   looks inside a module clone. `.gitignore` inside somebody else's repository is not ours to write,
   so it is left visible — which is arguably right: a user who finds it can read what it says.

---

# Fix round: the remedy, the sourceless hole, and the migration

Review returned **merge-with-nits** on the above. Five items, in the order they were given.

## F1 — a refusal whose remedy causes the harm it prevents

`install_id()` hashes the ABSOLUTE path, so moving or renaming a server folder makes every claim
under it read `UNKNOWN` at once — and the message told the user to delete the file or move the
folder aside. For `remove()` that is advice to strand yourself: moving the clone aside makes
`clone.exists()` false, so this run's remove-time SQL and `_undeploy()` either fail outright (an
`in_clone` patch or an SQL file that is no longer there) or silently leave the deployed files
behind, and the module stays half-installed in the database with no route back through the app.

`_require_own_clone()` now takes the `action`. Every remedy ends in "and then *that action* again"
rather than "install … again"; `_removal_note()` adds one sentence to `remove()`'s refusals only —
removing also undoes the database changes and the deployed files, so moving or deleting the folder
is not by itself an uninstall — and "move the folder aside" is withheld from the `UNKNOWN` refusal
for `remove()`, where it is the opposite of a remedy. The `UNKNOWN` text now names the cause a user
can recognise: an install folder that was moved, renamed or copied, and the file records the
folder's own path, so it stops matching.

**Does `remove()` accept weaker proof than `install()`? Yes — one specific piece of it.** The
asymmetry is in what a refusal costs. Refusing `install()` costs the user nothing they had: the
folder is untouched and the remedy is a download. Refusing `remove()` costs the only in-app route
to a clean database, and there is no second one — the rows and the deployed files stay forever, and
the folder they were told to move aside was never the problem. So `remove()`, and only `remove()`,
also accepts `claim_written_by_this_app()`: a claim that parses, carries this version, names THIS
item, and differs from `OWNED` in exactly one field — the folder it was written in. That is this
app's own handwriting. A hand-installing user does not produce it, and a stranger's checkout has no
claim file at all. What it cannot tell apart is a MOVED install from a COPIED one — nothing on disk
can — and that is the whole of what is given up: in a copy, `remove()` deletes a clone this app made
a copy of, for the item the user has just asked to remove. `install()` is not given the same
licence, because there the identical input would authorise `git reset --hard` inside a copy somebody
may be keeping precisely because it is not the original.

## F2 — the sourceless hole

`install()`'s guard sat inside `if manifest.source is not None`. The schema allows exactly one
sourceless type (`mod`; `module`/`ale`/`keg` all require a source), and a sourceless manifest never
clones — so everything at its clone path was put there by somebody else *by definition*, which
makes it the case that needs the question asked most, not least. An install-time `in_clone` patch
rewrote a line in it. Now guarded the way `configure()` is, gated on such a patch actually existing
so a refusal is never about a folder the run would not have touched.

## F3 — recorded, not fixed

`pyplan/bug-checklist.md` gained the catalog-URL-change entry, in its neighbours' shape, naming why
this change makes it harder to notice: `OWNED` returns before `remote_url()` is called, so the one
path that read a clone's real `origin` no longer runs for the clones this app owns — the only
clones the defect can happen to.

## F4 — defended, not merely accepted

If an upstream module ever tracks a file at `.yulon-clone.json`, `reset --hard` restores their copy
over the claim, it reads `UNKNOWN`, and install, configure and remove all refuse from then on — over
a file the user cannot delete without dirtying their checkout. Renaming the file would only make the
collision less likely; it would not make it recoverable. Git separates the two with certainty
instead: **a claim this app writes is never committed to a module's repository**, so a file at that
name which `git status` reports as unchanged from HEAD is the repository's own content and not a
claim at all. On `UNKNOWN`, `self.unmodified(clone, CLAIM_FILE) is True` demotes the answer to
`UNCLAIMED` and the folder is judged on its origin and on F5's evidence, exactly as an unclaimed one
is. `is True`, not truthiness — `None` is "git could not be asked" and keeps refusing. The loop is
stable: adopt, clone, write the claim, reset restores theirs, adopt again. No lockout at any point.

## F5 — the migration

Adoption of an existing `modules/<id>` requires three independent facts and refuses on any one:

1. `same_repo(origin, manifest.source.url)` — established before `_may_adopt()` is called.
2. `server_dir_claim(self.server_dir)` is `OWNED` — `.yulon-install.json`, OUTSIDE the folder, at
   the server dir, says this app CREATED this server directory, and its `install_id` names THIS
   directory (so a copied server folder is refused, the way the per-clone claim refuses one). A
   user hand-installing a module into their own AzerothCore tree cannot fabricate it, and it is not
   something a module repository can carry — it is not in the adopted folder at all. This is the
   fact that does the work.
3. `is_unmodified(clone, ".")` is `True` — `reset --hard FETCH_HEAD` destroys precisely what
   `status` reports, so an empty answer is the proof that adopting costs nothing, and `None` is not
   that proof.

`is_unmodified` reaches `Applier` as a seam narrowed the way `remote_url` is, through a new
one-method `git.TreeReader` Protocol — not added to `RemoteReader`, because a fake satisfies a
Protocol by having the methods and widening that one would silently stop every existing fake from
narrowing.

## Mutation evidence

Same method as the first pass: one mutant at a time in `yulon/apply.py`, every `__pycache__` under
the tree purged on both sides of the edit, restored by writing the original text back. Fourteen
mutants, fourteen killed.

| mutant | killed by |
| --- | --- |
| M12 every refusal says "install … again" again | `test_a_remove_refusal_never_tells_the_user_to_sidestep_the_database_cleanup` |
| M13 the "not by itself an uninstall" sentence dropped | same |
| M14 a relocated install stays locked out of `remove()` | `test_a_moved_install_can_still_be_uninstalled_through_the_app` |
| M15 the relocation licence given to `install()` too | same (its refusal half) |
| M16 the licence stops reading WHICH item the claim names | `test_the_relocation_licence_reads_which_item_the_claim_names` |
| M17 `install()`'s guard back inside `if manifest.source is not None` | `test_a_sourceless_manifest_still_asks_whose_folder_it_is_rewriting` |
| M18 the tracked-claim escape hatch deleted | `test_a_repository_that_tracks_the_claim_file_s_name_does_not_lock_the_module_out` |
| M19 "git could not say" counts as "that file is the repository's" | `test_a_claim_this_app_cannot_read_as_its_own_fails_closed`, `test_a_moved_install_…` |
| M20 adoption never offered | `test_a_module_from_an_older_build_is_adopted_when_all_three_facts_agree` (+1) |
| M21 fact 2 dropped | `test_a_hand_installed_module_in_someone_else_s_server_dir_is_not_adopted`, `test_a_copied_server_folder_does_not_adopt_the_clones_in_the_copy` |
| M22 fact 3 dropped | `test_a_checkout_with_local_work_in_it_is_not_adopted[edited, git-could-not-say]` |
| M23 fact 3 accepts "could not ask" | the same test's `[git-could-not-say]` case |
| M24 fact 1 dropped (adoption tried before the origin comparison) | `test_a_stranger_s_checkout_in_a_folder_this_app_installed_is_not_adopted` |
| M25 the server-dir record trusted by presence, not by the path it names | `test_a_copied_server_folder_does_not_adopt_the_clones_in_the_copy` |

M16 is the one that survived its first run: the relocation licence checked that a `clone_id` was
present and foreign without re-checking the `item_id`, so one module's stale record lying in a
folder would have authorised another module's remove. The test came out of the mutant, not the
other way round.

## Checks

From `C:\Users\perzi\dml-modules\pylauncher` with the `dads-mmo-lab` venv python:

* `pytest -m "not integration" -q` → **1330 passed, 71 skipped, 16 deselected** (1319 + 11 new)
* `mypy yulon main.py` → **Success: no issues found in 43 source files**
* `ruff check .` → **All checks passed!**
* `black --check .` → **88 files would be left unchanged**

## Concerns

1. **Adoption is blocked by an UNTRACKED file, which is stricter than the harm requires.**
   `is_unmodified(clone, ".")` reports `?? name` as well as ` M name`, and a hard reset does not
   delete untracked files — so nothing there is at risk. It costs one class of real user: a module
   whose repository does not ship `include.sh` has one `touch`ed into it by `install()` itself, so
   exactly those modules will not adopt, and their users get the refusal and its remedy instead of
   the migration. Kept deliberately: the direction of the error is a re-clone, and the alternative
   is deciding which untracked files in somebody's folder are innocent.
2. ~~**`apply.py` now imports `catalog.native`.**~~ **Closed in fix round 2.** `server_dir_claim` is
   a seam on `Applier.__init__` beside `remote_url` and `unmodified`, and the `catalog.native`
   import moved inside the one function that needs it — the game-agnostic apply engine was pulling
   the whole native install engine in behind it, for `networking`, `accounts`, `maintenance`,
   `repair` and the UI alike, to read one JSON file at the server dir.
3. **A user who commits this app's claim file into their own checkout** makes it read as repository
   content (F4) and so as `UNCLAIMED`. They then still need the F5 facts to be adopted, and a hard
   reset would move their branch to `FETCH_HEAD` — recoverable through the reflog, and it takes
   committing a file named after this app to reach.

---

# Fix round 2: the fourth fact

F5's three facts were not enough. The third, `is_unmodified(clone, ".")`, is `git status
--porcelain`, which compares the working tree and the index AGAINST HEAD and is therefore silent
about HEAD itself. A user who cloned this catalog's own repository into a server directory this app
created and then COMMITTED their customisations passed all three, was adopted, and had `git fetch` +
`git reset --hard FETCH_HEAD` run over work no check had ever looked at.

Fact 4, `no_local_commits()`, asks the question the third only looks like, through a third
one-method Protocol (`HistoryReader`) narrowed from the same `Git` the clones go through. It fails
closed on `None` like the rest.

The round also made the clone claim atomic — written beside itself and renamed over its own name, so
a half-written claim (which reads `UNKNOWN`, which refuses every caller, including `remove()`'s
relocation licence) cannot exist; moved `server_dir_claim` to a seam and the `catalog.native` import
inside its function (Concern 2 above); and corrected three docstring claims that overstated what
adoption covers.

# Fix round 3: the ref that fact 4 asked about

**Fact 4 shipped with a defect that made it useless on the whole catalog, and the defect lived in a
docstring nothing tested.** `no_local_commits()` counted `rev-list <ref>..HEAD` against
`refs/remotes/origin/<branch>`, or `refs/remotes/origin/HEAD` when the manifest named none, on the
stated grounds that the update's fetch "also moves" that ref. Half of that is true. Measured against
real git, `file://` remote, depth 1:

* `git fetch origin <named-branch>` does update `refs/remotes/origin/<branch>`.
* `git fetch origin HEAD` — the literal command both update paths run when `branch is None` —
  updates only `FETCH_HEAD`. A refspec on the command line replaces the remote's configured one, so
  nothing is opportunistically updated: `refs/remotes/origin/HEAD` is written once at clone time and
  never again by this code, and the default branch's own tracking ref is left just as stale.

So after ONE legitimate app-driven update, `no_local_commits(clone, None)` answered "1 commit ahead"
for a checkout in which the user had committed nothing, fact 4 refused, and the user was told the
install "throws away anything you have changed there". **All 21 `pylauncher/manifests/wow-wotlk/
modules/*.json` omit `source.branch`**, so that was the entire catalog — and specifically the
population the migration exists for: a module installed by a build older than the claim file, which
by definition has had time to receive an update. It failed closed, so nothing was destroyed; it
simply defeated its own purpose.

Every `branch is None` test in the round-2 diff was mocked, and the one real-git test passed
`"main"` — the single case that worked.

## The comparison point

`FETCH_HEAD`, after `no_local_commits()` runs the update's own fetch. It is the literal thing
`_update()` resets to, so the check stops predicting the update and reads it. The reviewer's
alternative — resolving the remote's real default branch with `git ls-remote --symref origin HEAD`
— does not avoid the fetch: the measurement above shows `origin/main` stale too, so that route
would still have to fetch and would then spend a second network call on a question `FETCH_HEAD` has
already answered.

**The cost is a network round trip inside an adoption check**, which is why fact 4 is asked last:
only once the claim file and the clean tree have both said yes, moments before the install fetches
the same refs anyway. Nothing outside `.git` is written and no working tree is touched. With the
network down the fetch fails, the fact is `None`, `_may_adopt()` refuses — the same direction as
every other `None` here, for an install that could not have proceeded either way. Depth is not
passed, for `_update()`'s reason; verified on a depth-1 clone that the shallow boundary survives and
the count stays right.

In `ContainerGit` the fetch must be `writes=True`: `_READ_ONLY_CONTAINER_ARGS` begins `--network
none`, so a reader container cannot reach a remote at all. That brings the read-write mount and the
SELinux `:z`, and it is safe only because `_may_adopt()` reaches fact 4 only after
`server_dir_claim()` has said this app created the server directory. The `rev-list` that follows
stays a read.

## Mutation evidence

One mutant at a time in `yulon/git.py`, every `__pycache__` under the tree purged on both sides of
the edit, restored by writing the original text back.

| mutant | killed by |
| --- | --- |
| M26 the branchless path back to `refs/remotes/origin/HEAD` | `test_this_apps_own_update_does_not_make_a_branchless_clone_look_like_the_users` (alone, with every other test deselected) |
| M27 the fetch dropped from `RunnerGit.no_local_commits()` | the same, + `test_a_committed_change_is_invisible_to_status_and_visible_to_the_count` |
| M28 the containerized fetch asked as a read (`writes=False`) | `test_the_containerized_history_question_fetches_in_a_container_that_has_a_network` |
| M29 `_fetch_ref()` defaults to `main` instead of `HEAD` | `test_no_local_commits_counts_what_head_has_that_the_update_would_not[host, containerized]` |
| M30 a failed fetch answers `True` instead of `None` | the same, both back-ends |
| M31 the count's zero test inverted | the same, + both real-git tests |

M26 is the mandated one and the point of the round: it is invisible to every mocked test's argv
assertions on its own, and only a real-git test that performs an `_update()` cycle first can see it.

## Checks

From `C:\Users\perzi\dml-modules\pylauncher`:

* `pytest -q` → **1351 passed, 76 skipped** (baseline 1349/76 + 2). The two are
  `test_this_apps_own_update_does_not_make_a_branchless_clone_look_like_the_users` (real git) and
  `test_the_containerized_history_question_fetches_in_a_container_that_has_a_network`. The existing
  mocked history test was rewritten and renamed, not added, so it changes no count; the skip set is
  unchanged because this machine has a git and both real-git tests run.
* `mypy yulon main.py` → **Success: no issues found in 43 source files**
* `ruff check .` → **All checks passed!**
* `black --check .` → **88 files would be left unchanged**
