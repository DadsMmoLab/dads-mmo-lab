"""Re-record `module-sql-listing.json` (T689): the SQL file names at each module repository's HEAD.

Run by hand when an upstream module moves (modules track their latest commit):

    python tests/data/record_module_sql_listing.py manifests tests/data/module-sql-listing.json \\
        <an empty scratch folder>

A blobless shallow clone per repository a shipped manifest names SQL in; names only.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path


def main(manifests: Path, out: Path, work: Path) -> None:
    repos: dict[str, str | None] = {}
    for path in sorted(manifests.glob("**/*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(item, dict) or "id" not in item:
            continue
        source = item.get("source") or {}
        if source.get("repo") and any(step.get("path") for step in item.get("sql") or []):
            repos[source["repo"]] = source.get("branch")
    listing = {}
    for repo, branch in sorted(repos.items()):
        dest = work / repo.replace("/", "_")
        if not dest.exists():
            cmd = ["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout"]
            cmd += ["--branch", branch] if branch else []
            subprocess.run([*cmd, f"https://github.com/{repo}.git", str(dest)], check=True)
        git = ["git", "-C", str(dest)]
        head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True)
        names = subprocess.run(
            [*git, "ls-tree", "-r", "--name-only", "HEAD"], capture_output=True, text=True
        ).stdout.split("\n")
        files = [n for n in names if n.endswith(".sql") or (n.endswith(".dist") and "sql" in n)]
        listing[repo] = {
            "commit": head.stdout.strip(),
            "branch": branch or "default",
            "files": sorted(files),
        }
    out.write_text(
        json.dumps({"recorded": datetime.date.today().isoformat(), "repos": listing}, indent=1)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
