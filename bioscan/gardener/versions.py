"""Versions of one capture (docs/gardener/contract.md 单元 3): files of the same shot kept in different
folders (an ARW, its DNG conversion, the camera JPEG) and exported JPEGs that lost their EXIF are
one capture. One asset (the representative) stands for it in groups, lists and review; the others
point to it with `assets.version_of` and stay reachable from its detail.

Rules, in this order, among a task's photos:
1. same capture: the camera's file number (the leading letters + digits of the name, e.g. DSC01234),
   the capture time to the second and the camera agree;
2. export: a photo without a capture time whose file number matches a capture is linked when its
   preview looks the same (64-bit difference hash, any 90° rotation, at most MAX_DIST bits apart)
   and no capture taken at another time looks as close.
The representative is the RAW (DNG after other RAWs), then the largest image, then the path. A
person's "not the same capture" (`version_lock`) is never overridden."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bioscan.gardener import store as st
from bioscan.gardener.store import dumps, loads, now

NUMBER = re.compile(r"^([a-z_]*\d{3,})")
MAX_DIST = 10           # bits of 64; same-day different shots sit above this ~97% of the time
AMBIGUOUS = 2           # another capture within this many bits of the best: leave the export alone
VERSION_NA = "同一拍摄的其他版本：分析与分组以代表版本为准"
RAW_EXT = (".arw", ".cr2", ".cr3", ".nef", ".nrw", ".orf", ".raf", ".rw2", ".pef", ".srw", ".3fr", ".iiq")


def file_number(path: str) -> str | None:
    m = NUMBER.match(Path(path).stem.lower())
    return m.group(1) if m else None


def dhashes(image_path: str | Path) -> list[int]:
    """The 64-bit difference hash of the image at 0°, 90°, 180° and 270°."""
    from PIL import Image

    out = []
    with Image.open(image_path) as im:
        g = im.convert("L")
        for angle in (0, 90, 180, 270):
            px = list(g.rotate(angle, expand=True).resize((9, 8), Image.Resampling.LANCZOS).getdata())
            bits = 0
            for r in range(8):
                for c in range(8):
                    bits = (bits << 1) | (px[r * 9 + c + 1] > px[r * 9 + c])
            out.append(bits)
    return out


def _rank(row: dict[str, Any]) -> tuple:
    ext = Path(row["primary_path"]).suffix.lower()
    kind = 0 if ext in RAW_EXT else 1 if ext == ".dng" else 2
    return kind, -((row["width"] or 0) * (row["height"] or 0)), row["primary_path"]


def plan(rows: list[dict[str, Any]], hashes: Callable[[str], list[int] | None]) -> dict[str, tuple[str, str]]:
    """rows: a task's photos {id, primary_path, taken_at, camera, width, height, version_lock}.
    Returns {asset id: (representative id, "same_capture" | "export")} for every version."""
    free = [r for r in rows if not r.get("version_lock")]
    captures: dict[tuple, list[dict[str, Any]]] = {}
    for r in free:
        n = file_number(r["primary_path"])
        if n and r["taken_at"]:
            captures.setdefault((n, r["taken_at"][:19], r["camera"]), []).append(r)
    out: dict[str, tuple[str, str]] = {}
    reps: dict[str, list[dict[str, Any]]] = {}          # file number -> one representative per capture
    for (n, _, _), members in captures.items():
        members.sort(key=_rank)
        reps.setdefault(n, []).append(members[0])
        for m in members[1:]:
            out[m["id"]] = (members[0]["id"], "same_capture")
    locked_reps = [r for r in rows if r.get("version_lock") and r["taken_at"] and file_number(r["primary_path"])]
    for r in locked_reps:                               # a locked asset is its own capture, still a match target
        reps.setdefault(file_number(r["primary_path"]), []).append(r)
    for r in free:
        n = file_number(r["primary_path"])
        if r["taken_at"] or not n or n not in reps:
            continue
        mine = hashes(r["id"])
        if not mine:
            continue
        scored = []
        for rep in reps[n]:
            theirs = hashes(rep["id"])
            if theirs:
                scored.append((min(bin(h ^ theirs[0]).count("1") for h in mine), rep))
        if not scored:
            continue
        scored.sort(key=lambda s: (s[0], _rank(s[1])))
        best, rep = scored[0]
        rival = any(d <= best + AMBIGUOUS and o["taken_at"][:19] != rep["taken_at"][:19] for d, o in scored[1:])
        if best <= MAX_DIST and not rival:
            out[r["id"]] = (rep["id"], "export")
    return out


def link(store: st.Store, task_id: str, thumb: Callable[[str], Path], model_caps: list[str]) -> dict[str, int]:
    """Apply `plan` to the task: (re)point versions, take versions out of auto collections (and put
    them back when they stop being versions), and mark their model capabilities 不适用 while they are
    versions. Hashes are cached in assets.meta.dhash. Returns {"same_capture": n, "export": n}.
    A missing file stands for no capture (its versions stand on their own again) and is left as it is.
    Assets are shared between tasks: a link to a representative outside this task is another task's and stays."""
    every = store.all("SELECT a.id, a.primary_path, a.taken_at, a.camera, a.width, a.height, a.version_of, "
                      "a.version_reason, a.version_lock, a.preview_status, a.meta, "
                      "coalesce(json_extract(a.meta, '$.missing'), 0) missing FROM task_assets ta JOIN assets a "
                      "ON a.id = ta.asset_id WHERE ta.task_id = ? AND a.media = 'photo'", (task_id,))
    mine = {r["id"] for r in every}
    rows = [r for r in every if not r["missing"]]
    by_id = {r["id"]: r for r in rows}
    fresh: dict[str, list[int]] = {}

    def hashes(aid: str) -> list[int] | None:
        r = by_id[aid]
        cached = loads(r["meta"], {}).get("dhash")
        if cached:
            return [int(h, 16) for h in cached]
        if r["preview_status"] != "ready" or not thumb(aid).is_file():
            return None
        try:
            fresh[aid] = dhashes(thumb(aid))
        except OSError:
            return None
        return fresh[aid]

    want = plan(rows, hashes)
    t = now()
    counts = {"same_capture": 0, "export": 0}
    with store.tx() as c:
        for aid, hs in fresh.items():
            meta = loads(c.execute("SELECT meta FROM assets WHERE id = ?", (aid,)).fetchone()[0], {})
            c.execute("UPDATE assets SET meta = ? WHERE id = ?", (dumps(meta | {"dhash": [f"{h:016x}" for h in hs]}), aid))
        # another task's link (representative outside this task) is not this task's to change; a planned
        # representative that is such a version hands its versions to its own representative: no chains
        outside = {r["id"]: r["version_of"] for r in rows if r["version_of"] and r["version_of"] not in mine}
        for r in rows:
            if r["id"] in outside:
                continue
            target = want.get(r["id"])
            if target and target[0] in outside:
                target = (outside[target[0]], target[1])
            old = (r["version_of"], r["version_reason"]) if r["version_of"] else None
            if target != old and not set_version(c, r["id"], target, model_caps, t):
                continue
            if target:
                counts[target[1]] += 1
    return counts


def set_version(c: Any, aid: str, target: tuple[str, str] | None, model_caps: list[str], t: str) -> bool:
    """One asset becomes a version of target[0] (or its own capture again when target is None). A person's
    "not the same capture" (version_lock) wins, also when it lands while `link` is planning: False."""
    if target:
        if not c.execute("UPDATE assets SET version_of = ?, version_reason = ?, updated_at = ? WHERE id = ? "
                         "AND version_lock = 0", (target[0], target[1], t, aid)).rowcount:
            return False
        c.execute("UPDATE assets SET version_of = ?, updated_at = ? WHERE version_of = ?", (target[0], t, aid))  # no chains
        # out of the auto collections of the tasks the representative stands in; another task keeps it
        c.execute("UPDATE collection_members SET removed_at = ?, removed_by = 'version' WHERE asset_id = ? "
                  "AND added_by = 'auto' AND removed_at IS NULL AND collection_id IN (SELECT co.id FROM collections co "
                  "JOIN task_assets ta ON ta.task_id = co.task_id WHERE ta.asset_id = ?)", (t, aid, target[0]))
        for cap in model_caps:              # a result it already has stays; nothing new runs on a version
            c.execute("INSERT INTO asset_caps (asset_id, capability, state, error, updated_at) VALUES "
                      "(?, ?, 'not_applicable', ?, ?) ON CONFLICT(asset_id, capability) DO UPDATE SET "
                      "state = 'not_applicable', error = excluded.error, updated_at = excluded.updated_at "
                      "WHERE state IN ('pending', 'failed', 'unavailable', 'not_enabled')", (aid, cap, VERSION_NA, t))
    else:
        c.execute("UPDATE assets SET version_of = NULL, version_reason = NULL, updated_at = ? WHERE id = ?", (t, aid))
        c.execute("UPDATE collection_members SET removed_at = NULL, removed_by = NULL WHERE asset_id = ? "
                  "AND removed_by = 'version'", (aid,))
        c.execute("UPDATE asset_caps SET state = 'pending', error = NULL, updated_at = ? WHERE asset_id = ? "
                  "AND state = 'not_applicable' AND error = ?", (t, aid, VERSION_NA))
    return True
