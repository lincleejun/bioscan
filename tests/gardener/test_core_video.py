"""A video is a whole clip in v0.1: metadata, cover, playback and marks. The image models never
see it (they failed it as an undecodable image before), and the progress counters stay in range."""
from __future__ import annotations

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, photos, wait_task

API = "/api/v1"


def test_video_is_not_sent_to_image_models_and_counts_stay_in_range(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 2)
    (src / "clip.mov").write_bytes(b"\0" * 100)
    (src / "clip2.mp4").write_bytes(b"\0" * 100)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        assert t["scan"]["status"] == "completed"
        assert t["scan"]["analyzed"] <= t["scan"]["total_analysis"] and t["scan"]["previewed"] <= t["scan"]["discovered"]
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()["summary"]
        assert run["failed"] == [] and run["analyzed"]["species"] == 2
        videos = [i for i in c.get(f"{API}/assets", params={"task": t["id"], "media": "video"}).json()["items"]]
        assert len(videos) == 2
        for v in videos:
            assert v["analysis"] == "done" and v["preview"] == "ready" and v["ai"] is None   # meta done, rest n/a
            caps = c.get(f"{API}/assets/{v['id']}").json()["caps"]
            assert caps["meta"]["state"] == "done"
            assert {caps[k]["state"] for k in ("content", "species", "quality", "aesthetic", "burst", "subject")} \
                == {"not_applicable"}
            assert "视频" in caps["species"]["error"]
        r = c.post(f"{API}/marks", json={"asset_ids": [videos[0]["id"]], "flag": "pick", "stars": 4}).json()
        assert r["assets"][0]["flag"] == "pick" and r["assets"][0]["stars"] == 4
