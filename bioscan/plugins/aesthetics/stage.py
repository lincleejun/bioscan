"""aesthetics as a stage: one matmul per chunk on the CPU pool. The general head is the committed
EVA head (bioscan.aesthetic.BUILTIN_HEAD); when it is missing or unusable the stage still answers,
with score null (or the personal head's score) and a `note`. A personal head file that cannot be
used is a 400 before the run (check_loaded)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from bioscan import aesthetic as aes
from bioscan.plugin import Item, StageBase
from bioscan.plugins.aesthetics import MANIFEST, OFF

log = logging.getLogger("bioscan.aesthetics")
_WARNED: set[str] = set()          # each missing-head message is logged once per process


@lru_cache(maxsize=8)
def _head(path: str, stamp: tuple[float, int]) -> tuple[aes.Head, np.ndarray, float]:
    """A head file, parsed and folded once per (path, mtime, size)."""
    h = aes.load_head(path)
    a, c = h.folded()
    return h, np.asarray(a, dtype=np.float32), c


def head(path: str | Path) -> tuple[aes.Head, np.ndarray, float]:
    """HeadError when the file is missing, unreadable or invalid."""
    p = Path(path).expanduser()
    try:
        st = p.stat()
    except OSError:
        raise aes.HeadError(f"{p}: no such head file") from None
    return _head(str(p), (st.st_mtime, st.st_size))


def general() -> tuple[tuple[aes.Head, np.ndarray, float] | None, str | None]:
    """(the builtin head, None), or (None, why not)."""
    try:
        return head(aes.BUILTIN_HEAD), None
    except aes.HeadError as e:
        missing = not Path(aes.BUILTIN_HEAD).is_file()
        why = ("general head not installed: data/aesthetic/eva-head-v1.json is trained by `bioscan aesthetic train "
               "--eva` or the aesthetic workflow (data/aesthetic/README.md)") if missing else f"general head unusable: {e}"
        return None, why


def _scores(h: tuple[aes.Head, np.ndarray, float] | None, V: np.ndarray) -> list[float | None]:
    if h is None:
        return [None] * len(V)
    hd, a, c = h
    raw = V @ a + c
    return [round(float(x), 4) for x in (raw - hd.lo) / (hd.hi - hd.lo)]


class Aesthetics(StageBase):
    def check_loaded(self, engine: Any, o: dict[str, Any]) -> None:
        if Path(o["head"]).is_absolute():
            try:
                head(o["head"])
            except aes.HeadError as e:
                raise ValueError(f"options.aesthetics.head: {e}") from None

    def reads_paths(self, o: dict[str, Any]) -> list[str]:
        return [o["head"]] if Path(o["head"]).is_absolute() else []

    def settings(self) -> dict[str, Any]:
        g, why = general()
        return {"head_format": aes.HEAD_VERSION, "embedding": aes.EMBEDDING,
                "builtin": g[0].id if g else None, **({"builtin_missing": why} if why else {})}

    def plugin_id(self, o: dict[str, Any]) -> str | None:
        if o["head"] == OFF:
            return None
        g, _ = general()
        try:
            p = head(o["head"])[0] if Path(o["head"]).is_absolute() else None
        except aes.HeadError:          # checked before the run; gone since: the run reports it per chunk
            return f"v{MANIFEST.version}@unreadable"
        return f"v{MANIFEST.version}@{aes.head_id(g[0] if g else None, p, o['blend']) or 'none'}"

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        if o["head"] == OFF:
            return [None] * len(items)
        g, why = general()
        p = head(o["head"]) if Path(o["head"]).is_absolute() else None
        if why and why not in _WARNED:
            _WARNED.add(why)
            log.warning("aesthetics: %s", why)
        V = np.stack([np.asarray(it.vec, dtype=np.float32) for it in items])
        gs, ps = _scores(g, V), _scores(p, V)
        hid = aes.head_id(g[0] if g else None, p[0] if p else None, o["blend"])
        out: list[Any] = []
        for it, gv, pv in zip(items, gs, ps):
            score = aes.blend(gv, pv, o["blend"])
            score = None if score is None else round(score, 4)
            it.facts["aesthetic"] = score
            res = {"score": score, "general": gv, "personal": pv, "head_id": hid}
            if why:
                res["note"] = why
            out.append(res)
        return out


STAGE = Aesthetics()
