"""Acting on a review's marks: copy the keeps to a folder, move the drops to another or delete them,
each photo with its XMP sidecars. Shared by `bioscan aesthetic apply` (a decisions file) and the
service's POST /apply (the review page, when a service runs). Standard library only.

The service only accepts /apply with the token in `~/.cache/bioscan/serve-token` (made on the first
`bioscan serve`, mode 0600), which `aesthetic score` bakes into the page: a page that knows the
token is one this user generated, and no other site can read the file."""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from bioscan import xmp

TOKEN_FILE = Path("~/.cache/bioscan/serve-token")


def token(create: bool = False, path: Path = TOKEN_FILE) -> str | None:
    """The machine's service token; `create` makes one when there is none."""
    p = path.expanduser()
    if p.is_file():
        return p.read_text(encoding="utf-8").strip() or None
    if not create:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    p.write_text(t, encoding="utf-8")
    os.chmod(p, 0o600)
    return t


def files_of(photo: str) -> list[Path]:
    """The photo and the XMP sidecars it has."""
    return [Path(photo), *(s for s in xmp.sidecar_paths(photo) if s.is_file())]


def apply(keep: list[str] | None = None, drop: list[str] | None = None, *, keep_to: str | None = None,
          drop_to: str | None = None, delete: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Copy `keep` to `keep_to`; move `drop` to `drop_to`, or delete them when `delete`. A photo already
    at the target (same name) is skipped, a missing one listed. Returns counts and the paths skipped
    or missing: {"copied", "moved", "deleted", "sidecars", "skipped": [...], "missing": [...]}."""
    out: dict[str, Any] = {"copied": 0, "moved": 0, "deleted": 0, "sidecars": 0, "skipped": [], "missing": []}
    jobs: list[tuple[list[str], str | None, str]] = []
    if keep and keep_to:
        jobs.append((keep, keep_to, "copied"))
    if drop and (drop_to or delete):
        jobs.append((drop, None if delete else drop_to, "deleted" if delete else "moved"))
    for paths, dest, key in jobs:
        for p in paths:
            src = Path(p)
            if not src.is_file():
                out["missing"].append(p)
                continue
            if dest and (Path(dest) / src.name).exists():
                out["skipped"].append(p)
                continue
            for f in files_of(p):
                if not dry_run:
                    if dest is None:
                        f.unlink()
                    else:
                        Path(dest).mkdir(parents=True, exist_ok=True)
                        (shutil.copy2 if key == "copied" else shutil.move)(str(f), str(Path(dest) / f.name))
                if f != src:
                    out["sidecars"] += 1
            out[key] += 1
    return out
