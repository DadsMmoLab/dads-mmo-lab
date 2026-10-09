"""T610 item 3: the fake docker's `create` must not show a half-made container as running.

`tests/test_git.py::test_a_stopped_containerized_clone_ends_its_container_and_never_falls_back`
failed once on fork CI with `assert 0 == 1` at `runner.end_streams_started_on(...) == 1`. The
test waits for `support_fake_docker.running()` and then ends the worker's streams. The fake's
`create` made the container's marker file with `Path.write_text("created")`, which opens the file
truncated before it writes: a reader in that instant finds an empty file, which `running()` reads
as "started, not merely created". The test then pressed Stop while the clone was still in
`docker create`, where no stream is live to end: zero ended. The claim path had the same flaw
(T567) and was fixed there; `create` is made the same way here -- the name is taken with its
state already in it.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from tests.support_fake_docker import end_fake_containers, lay_fake_docker, running


def test_a_container_that_was_only_created_is_never_seen_running(tmp_path: Path) -> None:
    """Mutation: write the marker with `write_text` again and a reader sees it empty."""
    cli, state = lay_fake_docker(tmp_path)
    seen: list[str] = []
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            seen.extend(running(state))

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        for number in range(150):
            done = subprocess.run(
                [str(cli), "create", "--name", f"clone-{number}", "image"],
                capture_output=True,
                check=False,
            )
            assert done.returncode == 0, done.stderr
    finally:
        stop.set()
        watcher.join()
        end_fake_containers(state)
    assert seen == [], f"created, never started, yet seen running: {sorted(set(seen))[:3]}"
