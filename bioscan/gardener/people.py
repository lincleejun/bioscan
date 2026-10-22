"""Named people (docs/gardener/contract.md 契约修订 3). Naming a face, or a person group, records those
faces as the person's confirmed faces (`person_faces`); from then on every task puts faces that match
them (faces.nearest) into that person's group, named with the person's name. Naming a group with a
name that already exists adds it to that person (two clusters of one person become one). Everything
here takes an open transaction; callers regroup afterwards."""
from __future__ import annotations

from typing import Any

from bioscan.gardener import store as st
from bioscan.gardener.store import Conflict, Error, now


def key(person_id: str) -> str:
    return f"person:named:{person_id}"


def person_of(c: Any, name: str) -> str:
    """The person called `name` (created when there is none)."""
    row = c.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    pid, t = st.new_id("p"), now()
    c.execute("INSERT INTO people (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (pid, name, t, t))
    return pid


def confirm(c: Any, person_id: str, faces: list[tuple[str, int, str]], source: str) -> None:
    """faces: (asset id, face index, feature) now confirmed as this person (a face named twice: the last wins)."""
    t = now()
    for aid, i, vec in faces:
        c.execute("INSERT INTO person_faces (asset_id, face, person_id, vec, source, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT(asset_id, face) DO UPDATE SET person_id = excluded.person_id, vec = excluded.vec, "
                  "source = excluded.source, created_at = excluded.created_at", (aid, i, person_id, vec, source, t))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, aid))


def adopt(c: Any, cid: str, person_id: str, actor: str) -> str:
    """Collection `cid` becomes the named person's group in its task: rekeyed, or merged into that task's
    existing group of the person (members move, `cid` is soft-deleted). Returns the live collection id."""
    row = c.execute("SELECT task_id FROM collections WHERE id = ?", (cid,)).fetchone()
    other = c.execute("SELECT id FROM collections WHERE task_id = ? AND auto_key = ? AND id != ? AND deleted_at IS NULL",
                      (row[0], key(person_id), cid)).fetchone()
    name = c.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()[0]
    t = now()
    if other is None:
        # a deleted group of this person in the task keeps its record under another key: the live one takes the key
        c.execute("UPDATE collections SET auto_key = auto_key || '#' || id WHERE task_id = ? AND auto_key = ? AND id != ?",
                  (row[0], key(person_id), cid))
        c.execute("UPDATE collections SET auto_key = ?, name = ?, user_edited = 0, updated_at = ?, version = version + 1 "
                  "WHERE id = ?", (key(person_id), name, t, cid))
        st.collection_history(c, cid, "rename", {"name": name, "person_id": person_id}, actor)
        return cid
    ids = [r[0] for r in c.execute("SELECT asset_id FROM collection_members WHERE collection_id = ? AND removed_at IS NULL",
                                   (cid,))]
    st.add_members(c, other[0], ids, "user")
    c.execute("UPDATE collections SET deleted_at = ?, updated_at = ?, version = version + 1 WHERE id = ?", (t, t, cid))
    st.collection_history(c, cid, "merge", {"into": other[0], "person_id": person_id}, actor)
    st.collection_history(c, other[0], "merge", {"from": cid, "added": len(ids)}, actor)
    return other[0]


def rename(c: Any, person_id: str, name: str, actor: str) -> str:
    """Rename a person; a name that belongs to another person merges this one into it (faces and, per task,
    groups). Returns the person id that carries the name afterwards."""
    other = c.execute("SELECT id FROM people WHERE name = ? AND id != ?", (name, person_id)).fetchone()
    t = now()
    if other is None:
        c.execute("UPDATE people SET name = ?, updated_at = ? WHERE id = ?", (name, t, person_id))
        c.execute("UPDATE collections SET name = ?, updated_at = ?, version = version + 1 WHERE auto_key = ? "
                  "AND user_edited = 0", (name, t, key(person_id)))
        return person_id
    c.execute("UPDATE person_faces SET person_id = ? WHERE person_id = ?", (other[0], person_id))
    for (cid,) in c.execute("SELECT id FROM collections WHERE auto_key = ? AND deleted_at IS NULL",
                            (key(person_id),)).fetchall():
        adopt(c, cid, other[0], actor)
    c.execute("DELETE FROM people WHERE id = ?", (person_id,))
    return other[0]


def check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise Error("name 必填")
    name = name.strip()
    if len(name) > 60:
        raise Error("name 最长 60 字")
    if name.startswith("人物 ") and name[3:].isdigit():
        raise Conflict("“人物 N”是未命名人物的自动名称，请换一个名字")
    return name
