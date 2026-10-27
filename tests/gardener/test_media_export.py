"""export: the Gardener XML, copies with sha256 verification, conflicts, protected sources."""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import pytest

from bioscan.gardener.export import NS, export

N = "{" + NS + "}"


def detail(i, *paths, flag="pick", stars=3):
    return {"id": i, "media": "photo", "name": paths[0].name, "taken_at": "2025-03-01T08:12:00",
            "flag": flag, "use": {"pick": "record", "reject": "reject"}.get(flag, "pending"), "stars": stars, "judged": True,
            "files": [{"path": str(p), "role": "raw" if p.suffix == ".ARW" else "jpeg", "size": p.stat().st_size}
                      for p in paths],
            "ai": {"verdict": "pick", "score": 0.71, "reasons": ["「野生动物」中排第 1", "bad\x01char <&>"]},
            "ai_history": [{"version": "aesthetics@v1@abc"}], "collections": [{"id": "c1", "name": "事件 & 地点"}]}


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "card"
    d.mkdir()
    (d / "A.ARW").write_bytes(b"raw-a")
    (d / "A.JPG").write_bytes(b"jpg-a")
    (d / "A.xmp").write_bytes(b"<xmp/>")
    other = tmp_path / "card2"
    other.mkdir()
    (other / "A.JPG").write_bytes(b"different")
    return d, other


COLS = [{"id": "c1", "name": "事件 & 地点", "kind": "manual", "group_kind": "event", "review": {"state": "completed"},
         "member_ids": ["a1"]}]
SCOPE = {"kind": "collection", "value": "c1", "scope": "picked"}


def snapshot(d):
    return {p.name: p.read_bytes() for p in d.iterdir()}


def test_xml_only_by_default(tmp_path, src):
    d, _ = src
    before = snapshot(d)
    out = tmp_path / "out"
    rep = export(out, title="a/b", scope=SCOPE, collections=COLS, assets=[detail("a1", d / "A.ARW", d / "A.JPG")],
                 copy=False, protected=[d])
    assert rep["assets"] == 1 and rep["files"] == 2 and rep["copied"] == 0 and not rep["verified"]
    assert rep["notes"] == ["未包含来源目录中的 XMP sidecar"]
    assert [p.name for p in out.iterdir()] == [rep["xml_path"].rsplit("/", 1)[1]]
    text = open(rep["xml_path"], encoding="utf-8").read()
    assert "这不是 XMP" in text and "\x01" not in text
    root = ET.parse(rep["xml_path"]).getroot()
    assert root.tag == N + "gardener-export" and root.get("compatibility") == "gardener-only"
    assert root.find(N + "scope").get("scope") == "picked"
    col = root.find(f"{N}collections/{N}collection")
    assert col.get("review") == "completed" and col.find(N + "member").get("asset") == "a1"
    a = root.find(f"{N}assets/{N}asset")
    assert a.get("flag") == "pick" and a.get("use") == "record" and a.get("stars") == "3"
    assert [f.get("path") for f in a.iter(N + "file")] == [str(d / "A.ARW"), str(d / "A.JPG")]
    ai = a.find(N + "ai")
    assert ai.get("version") == "aesthetics@v1@abc" and [r.text for r in ai] == ["「野生动物」中排第 1", "badchar <&>"]
    assert snapshot(d) == before


def test_copy_verifies_and_resolves_conflicts(tmp_path, src):
    d, other = src
    before = snapshot(d)
    out = tmp_path / "out"
    assets = [detail("a1", d / "A.ARW", d / "A.JPG"), detail("a2", other / "A.JPG")]
    rep = export(out, title="Trip: 3/1", scope=SCOPE, collections=COLS, assets=assets, copy=True, protected=[d, other])
    folder = out / "files" / "Trip_ 3_1"
    assert sorted(p.name for p in folder.iterdir()) == ["A (1).JPG", "A.ARW", "A.JPG"]
    assert (rep["copied"], rep["renamed"], rep["identical"], rep["failed"], rep["verified"]) == (3, 1, 0, [], True)
    assert rep["bytes"] == len(b"raw-a") + len(b"jpg-a") + len(b"different")
    file_el = next(f for f in ET.parse(rep["xml_path"]).getroot().iter(N + "file") if f.get("path") == str(d / "A.ARW"))
    assert file_el.get("sha256") == hashlib.sha256(b"raw-a").hexdigest() and file_el.get("copied") == str(folder / "A.ARW")
    assert not (folder / "A.xmp").exists() and snapshot(d) == before

    again = export(out, title="Trip: 3/1", scope=SCOPE, collections=COLS, assets=assets[:1], copy=True,
                   protected=[d])
    assert (again["identical"], again["copied"]) == (2, 0) and again["verified"]
    skip = export(out, title="Trip: 3/1", scope=SCOPE, collections=COLS, assets=[detail("x", other / "A.JPG")],
                  copy=True, conflict="skip", protected=[])
    assert (skip["skipped"], skip["identical"], skip["verified"]) == (1, 0, False)   # A.JPG differs: not copied
    with pytest.raises(ValueError):
        export(out, title="new", scope=SCOPE, collections=COLS, assets=assets, copy=True, conflict="fail",
               protected=[])
    assert not (out / "files" / "new").exists()                   # fail: nothing copied


def test_skip_policy_and_missing_source(tmp_path, src):
    d, other = src
    out = tmp_path / "out"
    export(out, title="t", scope=SCOPE, collections=[], assets=[detail("a1", d / "A.JPG")], copy=True, protected=[])
    gone = detail("g", d / "A.ARW")
    gone["files"].append({"path": str(d / "missing.JPG"), "role": "jpeg", "size": 1})
    rep = export(out, title="t", scope=SCOPE, collections=[], assets=[detail("a2", other / "A.JPG"), gone],
                 copy=True, conflict="skip", protected=[])
    assert rep["skipped"] == 1 and rep["copied"] == 1 and rep["failed"][0]["path"] == str(d / "missing.JPG")
    assert not rep["verified"]


def test_refuses_destination_inside_a_source(tmp_path, src):
    d, _ = src
    with pytest.raises(ValueError):
        export(d / "exports", title="t", scope=SCOPE, collections=[], assets=[], copy=False, protected=[d])
    assert not (d / "exports").exists()
    with pytest.raises(ValueError):
        export(tmp_path / "o", title="t", scope=SCOPE, collections=[], assets=[], copy=True, conflict="merge",
               protected=[])
