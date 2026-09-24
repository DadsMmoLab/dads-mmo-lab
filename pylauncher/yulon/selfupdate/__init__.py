"""Downloading, proving and installing a new build of Yu'lon itself (T90 plan 3).

Five small modules, and **no Qt in any of them**. `detect` says what kind of
install this is and what artifact it wants; `fetch` downloads it with progress
and checks it against the release's `SHA256SUMS`; `stage` unpacks it beside the
install and proves the new build opens; `swap` writes the helper script that
replaces the install AFTER this process has quit; `cleanup` removes the `.old`
left behind by the previous update. `apply` is the sequence, with every refusal
cleaning up after itself.

The rules the whole package is written to, each of them paid for once:

* **The running app never renames its own files.** A tarball swap under a live
  process leaves its lazy imports reading the new tree through the old path, so
  the rename is done by a helper that waits for this process to exit.
* **Paths reach the helper as ARGUMENTS**, never interpolated into script text.
  A folder can hold a quote, a `$(…)`, an `&` or a `%`.
* **Every refusal leaves the running install byte for byte as it was**, and
  removes whatever it staged.
* **A release with no `SHA256SUMS`, or an artifact that file does not list, is
  never self-installed.** The artifact NAME is computed here from the tag
  (`Install.artifact_name`) and never read off the feed.
"""
