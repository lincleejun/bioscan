"""Copies of the database a person can go back to (ADR 0005: the album core stays safe). `<data dir>/backups/` holds
`gardener-<UTC time>-<reason>.sqlite`: one when the service starts and the newest is over a day old, one before every
schema migration, one before a restore. The newest KEEP are kept. A restore replaces the database with a backup that
passes SQLite's integrity check, after backing up what it replaces; it runs only while no service holds the data
directory (`gardener serve --restore`). Every step is logged."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("gardener.backup")
KEEP = 5                 # newest copies kept; a copy is as large as the database
EVERY_S = 24 * 3600      # a start makes one when the newest is older than this


def folder(data_dir: Path) -> Path:
    return Path(data_dir) / "backups"


def listing(data_dir: Path) -> list[Path]:
    """Newest first."""
    return sorted(folder(data_dir).glob("gardener-*.sqlite"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def make(data_dir: Path, reason: str) -> Path | None:
    """A consistent copy of the live database (SQLite's online backup: safe while it is open and in WAL mode).
    None when there is no database yet or the disk has no room for it (logged)."""
    db = Path(data_dir) / "gardener.sqlite"
    if not db.is_file():
        return None
    need = sum(p.stat().st_size for p in Path(data_dir).glob("gardener.sqlite*"))
    if shutil.disk_usage(data_dir).free < 2 * need:
        log.warning("no backup (%s): the disk has less than twice the database's %d bytes free", reason, need)
        return None
    folder(data_dir).mkdir(exist_ok=True)
    out = folder(data_dir) / f"gardener-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{reason}.sqlite"
    part = out.with_suffix(".part")
    src, dst = sqlite3.connect(db), sqlite3.connect(part)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    os.replace(part, out)
    for old in listing(data_dir)[KEEP:]:
        old.unlink()
        log.info("backup dropped (only the newest %d are kept): %s", KEEP, old.name)
    log.info("backup made (%s): %s, %d bytes", reason, out.name, out.stat().st_size)
    return out


def on_start(data_dir: Path) -> Path | None:
    newest = next(iter(listing(data_dir)), None)
    if newest is not None and time.time() - newest.stat().st_mtime < EVERY_S:
        return None
    return make(data_dir, "start")


def check(path: Path) -> str:
    """"ok", or what SQLite's integrity check says is wrong."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return "; ".join(r[0] for r in c.execute("PRAGMA integrity_check"))
    except sqlite3.DatabaseError as e:
        return str(e)
    finally:
        c.close()


def restore(data_dir: Path, which: str) -> Path:
    """Replace the database with the backup `which` (a file name in backups/, a path, or "latest"). The caller holds
    the data directory's lock. ValueError when there is no such backup or it fails the integrity check."""
    data_dir = Path(data_dir)
    found = listing(data_dir)
    src = (found[0] if found else None) if which == "latest" else next(
        (p for p in found if p.name == which), Path(which).expanduser() if Path(which).expanduser().is_file() else None)
    if src is None:
        raise ValueError(f"没有这个备份：{which}（用 --backups 查看）")
    state = check(src)
    if state != "ok":
        raise ValueError(f"备份 {src.name} 没有通过完整性检查，未恢复：{state}")
    kept = make(data_dir, "before-restore")
    db = data_dir / "gardener.sqlite"
    part = db.with_suffix(".restore-part")
    shutil.copyfile(src, part)
    for side in ("-wal", "-shm"):                      # the old database's journal must not be replayed onto the copy
        Path(str(db) + side).unlink(missing_ok=True)
    os.replace(part, db)
    log.warning("database restored from %s; what it replaced is kept as %s", src.name, kept.name if kept else "nothing")
    return src
