"""The cull stages through the real /run on the fake Engine: quality reads identify's boxes (species
off is enough), each new stage's version and settings fingerprint go to result.engine.plugins, and
a run without such a stage keeps the engine block it always had."""
from conftest import events, make_jpg
from PIL import Image

from bioscan import plugin
from bioscan.plugins.quality import stage as quality_stage


def result(evs):
    return next(e for e in evs if e["type"] == "result")


def test_quality_reads_identify_boxes_and_reports_its_fingerprint(client, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    res = result(events(client.post("/run", json={"inputs": [{"path": p, "taken_at": "2026-05-01T08:00:00.37"}],
                                                  "want": ["quality", "identify"],
                                                  "options": {"identify": {"species": False}}})))
    assert list(res["products"]) == ["identify", "quality"]
    q = res["products"]["quality"]
    best = res["products"]["identify"]["boxes"][0]
    assert q["subject"]["box"] == best["id"] and q["subject"]["sharpness"] == best["quality"]["sharpness"]
    assert q["subject"]["blur"] is None and q["reject_reasons"] == []        # a flat test JPEG: blur unknown
    assert q["capture"] == {"taken_at": "2026-05-01T08:00:00.37", "camera": None}
    assert res["engine"]["plugins"] == {"quality": plugin.fingerprint(1, quality_stage.STAGE.settings())}
    plain = result(events(client.post("/run", json={"inputs": [{"path": p}]})))
    assert "plugins" not in plain["engine"]
    assert list(plain["engine"]) == ["version", "settings", "models", "detail_edge"]


def test_scene_labels_from_the_frame_vector_and_the_gate(client, engine, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    res = result(events(client.post("/run", json={"inputs": [{"path": p}, {"path": make_jpg(tmp_path / "b.jpg")}],
                                                  "want": ["scene"]})))
    s = res["products"]["scene"]
    assert s["label"] == "wildlife" and s["scores"]["wildlife"] == 0.95 and s["horizon"] is None   # gate bird+mammal
    assert abs(sum(s["scores"].values()) - 1) < 1e-3 and list(s["scores"]) == list(scene_defaults()["labels"])
    assert list(res["engine"]["plugins"]) == ["scene"] and engine.loaded() == ["siglip2"]
    encoded = engine.siglip2.texts
    assert encoded == sum(len(v) for v in scene_defaults()["labels"].values())
    events(client.post("/run", json={"inputs": [{"path": p}], "want": ["scene"]}))
    assert engine.siglip2.texts == encoded                   # prompts are encoded once per label set
    own = {"labels": {"landscape": ["same"], "other": ["same"]}}
    res = result(events(client.post("/run", json={"inputs": [{"path": p}], "want": ["scene"],
                                                  "options": {"scene": own}})))
    assert res["products"]["scene"] == {"label": "landscape", "scores": {"landscape": 0.5, "other": 0.5},
                                        "horizon": None}
    r = client.post("/run", json={"inputs": [{"path": p}], "want": ["scene"],
                                  "options": {"scene": {"labels": {"only": ["x"]}}}})
    assert r.status_code == 400 and "at least two labels" in r.json()["error"]


def scene_defaults():
    from bioscan.plugins.scene import MANIFEST

    return MANIFEST.defaults


def test_album_profile_end_to_end_then_the_reducers(client, engine, tmp_path, monkeypatch):
    """/run with "profile": "album" on the fake Engine (chunks of 32: every stage for real but the
    models), then burst and select over the stream as `bioscan cull` runs them. A general head is
    installed, so select reads the aesthetics stage's score."""
    from aesthetic_helpers import random_head

    from bioscan import aesthetic as aes
    from bioscan import cull, profile

    head = aes.load_head(aes.write_head(random_head(1, name="eva-head-v1"), tmp_path / "eva-head-v1.json"))
    monkeypatch.setattr(aes, "BUILTIN_HEAD", tmp_path / "eva-head-v1.json")

    d = tmp_path / "album"
    d.mkdir()
    paths = [make_jpg(d / f"{i}.jpg") for i in range(4)]
    Image.new("RGB", (64, 48), (15, 15, 15)).save(d / "dark.jpg")
    paths.append(str(d / "dark.jpg"))
    times = ["2026-05-01T08:00:00.10", "2026-05-01T08:00:00.40", "2026-05-01T08:00:00.70",
             "2026-05-01T08:05:00", "2026-05-01T08:10:00"]
    evs = events(client.post("/run", json={"inputs": [{"path": p, "taken_at": t} for p, t in zip(paths, times)],
                                           "profile": "album"}))
    results = [e for e in evs if e["type"] == "result"]
    assert len(results) == 5 and evs[-1]["type"] == "done" and engine.loaded() == ["siglip2", "owlv2"]
    assert list(results[0]["products"]) == ["identify", "embed", "aesthetics", "quality", "scene"]
    assert list(results[0]["engine"]["plugins"]) == ["aesthetics", "quality", "scene"]
    assert results[0]["engine"]["plugins"]["aesthetics"] == f"v1@{head.id}"
    assert "species" not in results[0]["products"]["identify"]["boxes"][0]
    res = profile.resolve(profile.builtin(), "album")
    records = {r["path"]: r for r in cull.records(cull.apply(evs, res.reducer_run()))}
    # the fake frame vectors are all alike, so time decides: 0-2 are one burst, 3 and dark stand alone
    assert [records[p]["burst"] for p in paths] == ["b0001"] * 3 + [None, None]
    assert records[paths[4]]["status"] == "reject" and records[paths[4]]["reasons"] == ["underexposed"]
    assert sorted(r["burst_rank"] for r in records.values() if r["burst"]) == [1, 2, 3]
    # one wildlife category: the burst's best and 3.jpg are alike (the same fake vector): one pick
    assert [r["status"] for r in records.values()].count("pick") == 1
    assert {r["category"] for r in records.values()} == {"wildlife"}
    scores = {e["path"]: e["products"]["aesthetics"]["score"] for e in results}
    assert all(isinstance(v, float) for v in scores.values())
    assert {p: r["aesthetic"] for p, r in records.items()} == scores          # select read the stage's score


def test_quality_without_identify_is_a_400(client, tmp_path):
    r = client.post("/run", json={"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["quality"]})
    assert r.status_code == 400 and "stage quality reads 'boxes'" in r.json()["error"]


def test_quality_rejects_a_dark_frame_and_reads_the_camera(client, tmp_path):
    p = tmp_path / "dark.jpg"
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "NIKON CORPORATION", "NIKON Z 9"
    Image.new("RGB", (64, 48), (20, 20, 20)).save(p, exif=exif)
    q = result(events(client.post("/run", json={"inputs": [{"path": str(p)}], "want": ["identify", "quality"]})))
    assert q["products"]["quality"]["reject_reasons"] == ["underexposed"]
    assert q["products"]["quality"]["capture"]["camera"] == "NIKON CORPORATION NIKON Z 9"
