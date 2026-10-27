"""契约修订 7B, Lightroom side: retrying only the photos that did not reach Lightroom, and develop presets with an
export step whose 修图进度 comes only from the plugin's per-photo results. Lightroom itself is not run here."""
from __future__ import annotations

import json
from pathlib import Path

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, wait_task

API = "/api/v1"


def shoot(c, tmp_path, n=4):
    src = tmp_path / "card"
    for i in range(n):
        make_jpg(src / f"IMG_{i}.jpg", color=(30 * i, 80, 10), when=1_700_000_000 + 60 * i)
    t = wait_task(c, create_task(c, [str(src)])["id"])
    items = sorted(c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"], key=lambda i: i["name"])
    return src, [i["id"] for i in items]


def doc_of(h):
    return json.loads(Path(h["doc_path"]).read_text())


def result(h, photos):
    Path(h["result_path"]).write_text(json.dumps({"schema": 2, "id": h["id"], "photos": photos}))


def ok(aid, **extra):
    return {"asset_id": aid, "status": "ok", "photo_id": 7, "uuid": "U-" + aid, "path": "/x",
            "stars": {"action": "same", "value": 0}, "keywords": {"action": "same", "value": []}} | extra


def test_retry_sends_only_failed_and_offline_photos_and_reuses_the_lightroom_identity(env, tmp_path):
    with env.client(lightroom_dir=tmp_path / "lr") as c:
        src, a = shoot(c, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": a, "use": "edit"})
        first = c.post(f"{API}/handoffs", json={"asset_ids": [a[1]]}).json()
        result(first, [ok(a[1])])                                        # IMG_1 is in the catalog now
        assert c.get(f"{API}/handoffs/{first['id']}").json()["status"] == "applied"

        (src / "IMG_3.jpg").rename(tmp_path / "IMG_3.jpg")                # the disk went away
        h = c.post(f"{API}/handoffs", json={"asset_ids": a, "name": "东滩"}).json()
        assert c.post(f"{API}/handoffs/{h['id']}/retry").status_code == 409   # no result yet: nothing to retry
        result(h, [ok(a[0]), {"asset_id": a[1], "status": "failed", "reason": "no write access"},
                   {"asset_id": a[2], "status": "skipped", "reason": "file_missing"}])
        got = c.get(f"{API}/handoffs/{h['id']}").json()
        assert got["status"] == "partial" and got["retryable"] == 3

        (tmp_path / "IMG_3.jpg").rename(src / "IMG_3.jpg")                # back online
        c.post(f"{API}/marks", json={"asset_ids": [a[2]], "use": "reject"})   # rejected since: not sent again
        r = c.post(f"{API}/handoffs/{h['id']}/retry")
        assert r.status_code == 201, r.text
        again = r.json()
        assert again["name"] == "东滩" and again["scope"]["retry_of"] == h["id"]
        assert [i["asset_id"] for i in again["items"]] == [a[1], a[3]]
        photos = {p["asset_id"]: p for p in doc_of(again)["photos"]}
        assert photos[a[1]]["uuid"] == "U-" + a[1]                         # found again, never imported twice
        assert photos[a[3]]["uuid"] is None

        result(again, [ok(a[1]), ok(a[3])])
        assert c.post(f"{API}/handoffs/{again['id']}/retry").status_code == 400    # nothing left to retry
