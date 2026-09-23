"""Real-model smoke: SigLIP2 + OWLv2 + BioCLIP 2.5 Huge (+ BirdNET geo when it loads) through the
real /run pipeline on 77 iNaturalist golden-set photos (tests/models/sample.csv).

Only runs with BIOSCAN_MODEL_TESTS=1 and the weights in the HF cache (tests/models/download.py);
CI's models.yml does both. Species are ranked against a small in-memory name list encoded with
the BioCLIP text tower (every AviList species of the sampled bird genera, tests/models/mammals.csv
for mammals), because the full AviList/MDD CSVs and TreeOfLife vectors are not in the repo.
Thresholds sit below what the first CI run measured: they catch a broken model, adapter or
dependency upgrade, not a point of accuracy. The report (BIOSCAN_REPORT) has the real numbers.
"""
import csv
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from bioscan import contract

pytestmark = pytest.mark.skipif(os.environ.get("BIOSCAN_MODEL_TESTS") != "1",
                                reason="real-model smoke: set BIOSCAN_MODEL_TESTS=1 (see tests/models/download.py)")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PHOTOS = Path(os.environ.get("BIOSCAN_PHOTO_CACHE", "~/.cache/bioscan/model-test-photos")).expanduser()
PHOTO_URL = "https://inaturalist-open-data.s3.amazonaws.com/photos/{pid}/medium.{ext}"

# kind -> metric -> floor. Measured on CPU, 2026-09-23 (models runs 3 and 5, identical):
#   bird   (42): gate 97.6 %, detect 95.2 %, top-1 88.1 %, top-5 92.9 %
#   mammal (35): gate 91.4 %, detect 91.4 %, top-1 85.7 %, top-5 91.4 %
# Floors leave about four images of room per kind; a drop past them is a regression to explain.
FLOORS = {"bird": {"gate_acc": 0.88, "detect_rate": 0.85, "top1": 0.78, "top5": 0.83},
          "mammal": {"gate_acc": 0.80, "detect_rate": 0.80, "top1": 0.74, "top5": 0.80}}


def sample() -> list[dict]:
    with open(HERE / "sample.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch(row: dict) -> Path | None:
    path = PHOTOS / f"{row['observation']}_{row['photo_id']}.jpg"
    if path.is_file() and path.stat().st_size:
        return path
    for ext in ("jpg", "jpeg", "png"):
        req = urllib.request.Request(PHOTO_URL.format(pid=row["photo_id"], ext=ext),
                                     headers={"User-Agent": "bioscan-model-tests"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
        except Exception:  # noqa: BLE001 - try the next extension, then give up on this photo
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
    return None


def small_lists(bioclip) -> dict:
    from bioscan.service import names

    rows = sample()
    genera = {r["scientific"].split(" ")[0] for r in rows if r["kind"] == "bird"}
    with open(ROOT / "data" / "names" / "avilist_map.csv", newline="", encoding="utf-8") as f:
        birds = [r for r in csv.DictReader(f) if r["scientific"].split(" ")[0] in genera]
    with open(HERE / "mammals.csv", newline="", encoding="utf-8") as f:
        mammals = list(csv.DictReader(f))

    def build(list_id, kind, cls, recs):
        tax = [names._taxonomy(cls, r["order"], r["family"], *r["scientific"].split(" ", 1)) for r in recs]
        common = [r["common"] for r in recs]
        matrix = names.encode([names.tol_text(t, c) for t, c in zip(tax, common)], bioclip.model, bioclip.tokenizer,
                              bioclip.device)
        labels = {}
        if names.LISTS[kind].label_map:          # rows come from avilist_map.csv, BirdNET labels included
            labels = {"birdnet": [r["birdnet_label"] for r in recs],
                      "birdnet_how": [r["birdnet_how"] or "none" for r in recs]}
        return names.NameList(list_id, kind, [t[6] for t in tax], common, tax, matrix, ["none"] * len(recs),
                              sha=f"test-{len(recs)}", **labels)

    return {"bird": build("avilist-2025-test-subset", "bird", "Aves", birds),
            "mammal": build("mdd-test-subset", "mammal", "Mammalia", mammals)}


@pytest.fixture(scope="module")
def run():
    """Photos -> real Engine -> real /run -> (ground-truth rows, events, engine)."""
    from fastapi.testclient import TestClient

    from bioscan.cli import eval as ev
    from bioscan.service import engine as engine_mod
    from bioscan.service import names
    from bioscan.service.adapters.bioclip import BioCLIP
    from bioscan.service.app import create_app

    rows = sample()
    with ThreadPoolExecutor(8) as pool:
        paths = list(pool.map(fetch, rows))
    have = [(r, p) for r, p in zip(rows, paths) if p is not None]
    assert len(have) >= 0.9 * len(rows), f"only {len(have)}/{len(rows)} photos downloaded"

    device = engine_mod.pick_device()
    bioclip = BioCLIP(device)
    lists = small_lists(bioclip)
    engine = engine_mod.Engine(device, engine_mod.Loaders(species=lambda device: (bioclip, lists)))
    engine.ensure(["identify", "embed"])
    if os.environ.get("BIOSCAN_REQUIRE_GEO") == "1":
        assert engine.geo is not None, "BirdNET geo prior failed to load (BIOSCAN_REQUIRE_GEO=1)"

    inputs = [{"path": str(p), "lat": float(r["lat"]), "lon": float(r["lon"]), "taken_at": r["taken_at"]}
              for r, p in have]
    with TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(4), chunk=16)) as c:
        resp = c.post("/run", json={"inputs": inputs, "want": ["identify", "embed"]})
        assert resp.status_code == 200, resp.text
        events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    gt = ev.normalise_truth([{"path": str(p), "scientific": r["scientific"], "tier": "inat-sample", "kind": r["kind"]}
                             for r, p in have], names.read_synonyms(ev.SYNONYMS_CSV))
    return gt, events, engine


@pytest.fixture(scope="module")
def metrics(run):
    from bioscan.cli import eval as ev

    gt, events, engine = run
    preds = ev.load_preds(json.dumps(e) for e in events)
    m = ev.compute(gt, preds)
    rescued = sum(1 for e in events if e["type"] == "result" and e["products"]["identify"]["gate"]["class"]
                  in ("none", "person") and e["products"]["identify"]["boxes"])
    meta = {"photos": len(gt), "device": engine.device, "geo": engine.geo is not None,
            "names": {k: len(nl.scientific) for k, nl in engine.names.items()}, "gate-rescued images": rescued,
            "floors": json.dumps(FLOORS)}
    report = ev.report_md(m, meta)
    per_image = ["", "## Per image", "", "| truth | gate | boxes | top-1 | level |", "|---|---|---|---|---|"]
    by_path = {e["path"]: e for e in events if e["type"] == "result"}
    for r in gt:
        ident = by_path.get(r["path"], {}).get("products", {}).get("identify", {})
        boxes = ident.get("boxes") or []
        best = max(boxes, key=lambda b: b["score"]) if boxes else None
        sp = (best or {}).get("species") or {}
        top1 = (sp.get("top") or [{}])[0].get("scientific", "–")
        per_image.append(f"| {r['scientific']} | {(ident.get('gate') or {}).get('class', 'error')} | {len(boxes)} | "
                         f"{top1} | {sp.get('level', '–')} |")
    out = os.environ.get("BIOSCAN_REPORT")
    if out:
        Path(out).write_text(report + "\n".join(per_image) + "\n")
        Path(out).with_suffix(".ndjson").write_text("".join(json.dumps(e) + "\n" for e in events))
    return {kind: v for (_tier, kind), v in m.items()}


def test_every_photo_gets_a_result(run):
    gt, events, _ = run
    errors = [e for e in events if e["type"] == "error"]
    assert not errors, errors[:3]
    done = events[-1]
    assert done["type"] == "done" and done["ok"] == len(gt) and done["failed"] == 0


def test_embed_is_a_unit_siglip2_vector(run):
    _, events, _ = run
    for e in (e for e in events if e["type"] == "result"):
        v = np.asarray(e["products"]["embed"]["vector"], np.float32)
        assert v.shape == (768,) and abs(float(np.linalg.norm(v)) - 1) < 1e-3


def test_boxes_are_well_formed(run):
    _, events, _ = run
    for e in (e for e in events if e["type"] == "result"):
        problems = contract.identify_problems(e["products"]["identify"])
        assert not problems, (e["path"], problems)
        for b in e["products"]["identify"]["boxes"]:
            x0, y0, x1, y1 = b["xyxy"]
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and b["kind"] in ("bird", "mammal", "other_animal")
            sp = b["species"]
            if b["kind"] in ("bird", "mammal"):
                post = [c["posterior"] for c in sp["top"]]
                assert post == sorted(post, reverse=True) and sp["level"] in ("species", "genus", "family", "unconfirmed")


@pytest.mark.parametrize("kind", ["bird", "mammal"])
def test_accuracy_floors(metrics, kind):
    m = metrics[kind]
    low = {k: round(m[k], 3) for k, floor in FLOORS[kind].items() if m[k] < floor}
    assert not low, f"{kind}: below floor {low} (all: { {k: round(m[k], 3) for k in FLOORS[kind]} })"


def test_geo_gaps_on_real_birdnet(run, capsys):
    """`bioscan names geo-gaps` against the real BirdNET model (skipped when it cannot load)."""
    from bioscan.cli import main as cli

    _, _, engine = run
    if engine.geo is None:
        pytest.skip("BirdNET geo model unavailable")
    a = cli.parser().parse_args(["names", "geo-gaps", "--lat", "37.4", "--lon", "-122.1", "--date", "2026-05-01"])
    assert cli.cmd_names_geo_gaps(a, engine.geo) == 0
    out = capsys.readouterr().out
    listed = [line.split(" (")[0].strip() for line in out.splitlines()[1:]]
    with open(ROOT / "data" / "names" / "avilist_map.csv", newline="", encoding="utf-8") as f:
        labelled = {r["scientific"] for r in csv.DictReader(f) if r["birdnet_label"]}
    assert listed and not set(listed) & labelled          # only unlabelled species are ever listed
    report = os.environ.get("BIOSCAN_REPORT")
    if report and Path(report).is_file():
        with open(report, "a") as f:
            f.write("\n## geo-gaps at 37.4,-122.1 (2026-05-01)\n\n```\n" + out + "```\n")


def test_detail_path_with_real_models(run, tmp_path):
    """The golden photos are ~500 px, so the main run never builds a detail copy. Here six bird
    photos are upscaled to 4000 px: the 3072 px detail copy must reach BioCLIP, and the result must
    stay sane (same pipeline, no errors, a species answer for every photo that had one)."""
    from fastapi.testclient import TestClient
    from PIL import Image

    from bioscan.service import pipeline
    from bioscan.service.app import create_app

    gt, events, engine = run
    base = {e["path"]: e for e in events if e["type"] == "result"}
    birds = [r["path"] for r in gt if r["kind"] == "bird" and base[r["path"]]["products"]["identify"]["boxes"]][:6]
    big = []
    for p in birds:
        with Image.open(p) as im:
            scale = 4000 / max(im.size)
            im.convert("RGB").resize((round(im.width * scale), round(im.height * scale)), Image.Resampling.LANCZOS) \
                .save(tmp_path / Path(p).name, quality=95)
        big.append(str(tmp_path / Path(p).name))
    seen = []
    real = pipeline.identify_many
    mp = pytest.MonkeyPatch()
    mp.setattr(pipeline, "identify_many", lambda models, frames, opts: seen.extend(f.detail.size for f in frames)
               or real(models, frames, opts))
    try:
        with TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(2))) as c:
            evs = [json.loads(line) for line in c.post("/run", json={"inputs": [{"path": p} for p in big]}).text.splitlines()]
    finally:
        mp.undo()
    assert not [e for e in evs if e["type"] == "error"] and evs[-1]["ok"] == len(big)
    assert len(seen) == len(big) and all(max(s) == 3072 for s in seen)
    for e in (e for e in evs if e["type"] == "result"):
        assert e["image"]["width"] <= 4000 and all(b["species"] for b in e["products"]["identify"]["boxes"])
    same = sum(base[p]["products"]["identify"]["boxes"][0]["species"]["top"][0]["scientific"]
               == next(e for e in evs if e.get("path") == q)["products"]["identify"]["boxes"][0]["species"]["top"][0]["scientific"]
               for p, q in zip(birds, big) if next(e for e in evs if e.get("path") == q)["products"]["identify"]["boxes"])
    report = os.environ.get("BIOSCAN_REPORT")
    if report and Path(report).is_file():
        with open(report, "a") as f:
            f.write(f"\n## Detail path\n\n{len(big)} bird photos upscaled to 4000 px, detail copies {seen}; "
                    f"top-1 same as the 500 px run on {same}/{len(big)} (upscaling adds no detail, so this is a "
                    f"consistency check, not an accuracy one).\n")


def test_every_map_label_exists_in_birdnet(run):
    """avilist_map.csv birdnet_label values (some synced by hand after a synonyms.csv edit) must be
    labels the real BirdNET model has, else that species silently gets no prior."""
    _, _, engine = run
    labels = set(engine.geo.labels)
    with open(ROOT / "data" / "names" / "avilist_map.csv", newline="", encoding="utf-8") as f:
        missing = [(r["scientific"], r["birdnet_label"]) for r in csv.DictReader(f)
                   if r["birdnet_label"] and r["birdnet_label"] not in labels]
    assert not missing, missing[:10]
