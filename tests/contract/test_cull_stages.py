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
