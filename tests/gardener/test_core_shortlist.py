"""Contract revision 9: AI 初选 ranks a shoot's photos within that shoot, so a weak shoot still gets a shortlist."""
from __future__ import annotations

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, photos, wait_task

API = "/api/v1"


def test_a_weak_shoot_still_gets_its_top_share(env, tmp_path):
    # the fake reducer puts every frame in one burst and rejects all but the first: the AI picks one photo
    src = tmp_path / "card"
    photos(src, 25)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        picks = c.get(f"{API}/assets", params={"task": t["id"], "ai": "pick"}).json()["total"]
        s = c.get(f"{API}/tasks/{t['id']}/shortlist").json()
        assert picks == 1
        assert (s["scored"], s["size"], len(s["asset_ids"]), len(set(s["asset_ids"]))) == (25, 20, 20, 20)
        top = c.get(f"{API}/assets/{s['asset_ids'][0]}").json()
        assert top["ai"]["verdict"] == "pick"                       # the AI's own pick leads, rejects follow
        assert c.get(f"{API}/tasks/nope/shortlist").status_code == 404


def test_a_small_shoot_shortlists_every_scored_photo(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        assert c.get(f"{API}/tasks/{t['id']}/shortlist").json()["size"] == 3
