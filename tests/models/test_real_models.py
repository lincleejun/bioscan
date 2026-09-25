"""Real-model smoke: SigLIP2 + OWLv2 + BioCLIP 2.5 Huge (+ BirdNET geo when it loads) through the
real /run pipeline on 95 iNaturalist photos (tests/models/sample.csv): 77 birds and mammals from the
golden set and 18 other animals (reptiles, amphibians, insects, a spider, a fish; CC0 / CC BY).

Only runs with BIOSCAN_MODEL_TESTS=1 and the weights in the HF cache (tests/models/download.py);
CI's models.yml does both. Birds and mammals are ranked against a small in-memory name list encoded
with the BioCLIP text tower (every AviList species of the sampled bird genera, tests/models/mammals.csv
for mammals), because the full AviList/MDD CSVs are not in the repo. Other animals are ranked
against the real all-taxa list (every species-level TreeOfLife animal row outside birds and
mammals), which download.py builds into ~/.cache/bioscan/names; without it their floors are skipped,
unless BIOSCAN_REQUIRE_ALLTAXA=1 (CI), where that is a failure.
Thresholds sit below what the first CI run measured: they catch a broken model, adapter or
dependency upgrade, not a point of accuracy. The report (BIOSCAN_REPORT) has the real numbers.

The v1.5 accuracy switches (range veto, kind check, mammal prior) are measured here too: the same
photos are identified again with all three off, replaying the model outputs of the main run (so the
second pass costs only the species matmuls), and the report gets an on/off table with every image
whose answer changed. Switched on they may lose at most one Top-1 hit, and add at most one confident
error, per kind (a tripwire; `bioscan bench compare` with its budgets is the gate). With the all-taxa
list loaded the kind check also weighs it, so this table is where its effect on birds and mammals
shows. models-report.json (the harness report) is written from the main, switches-on run.

The album profile is measured last: scripts/cull_synth.py degrades the first BIOSCAN_ALBUM_SOURCES
(24) bird and mammal photos into labelled rejects (subject blur and smear, frame shake, cut-off
crop, +-2 EV, shrunk subject) and bursts, the same engine runs them with "profile": "album", and
the burst and select reducers and the harness score them (models-report-album.json, tier album:
reject precision / recall per reason, keepers lost, burst pairwise F1, scene accuracy). Its floors
are loose until the first CI run measures them.

Inference cache (BIOSCAN_INFER_CACHE, a file; models.yml keeps it between runs): every model answer
this module computes is stored by the exact pixels (and prompts) handed to the model, and the next
run replays what it finds there. A change to decoding, crops or prompts misses and runs the model;
models.yml keys the cache on the adapters (model revisions) and uv.lock, so a model or dependency
change starts empty, and a `v*` tag or a `cold` dispatch runs without it. The report's "Model time"
section says what was computed and what was replayed.
"""
import csv
import hashlib
import json
import os
import pickle
import time
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
# other_animal (18): not measured yet; loose first floors, to tighten after the first CI run.
FLOORS = {"bird": {"gate_acc": 0.88, "detect_rate": 0.85, "top1": 0.78, "top5": 0.83},
          "mammal": {"gate_acc": 0.80, "detect_rate": 0.80, "top1": 0.74, "top5": 0.80},
          "other_animal": {"gate_acc": 0.40, "detect_rate": 0.45, "top1": 0.25, "top5": 0.35}}


def sample() -> list[dict]:
    with open(HERE / "sample.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch(row: dict) -> Path | None:
    path = PHOTOS / f"{row['observation']}_{row['photo_id']}.jpg"
    if path.is_file() and path.stat().st_size:
        return path
    for ext in ("jpg", "jpeg", "png", "JPG", "JPEG"):          # the bucket's keys are case-sensitive
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


INFER_CACHE = os.environ.get("BIOSCAN_INFER_CACHE") or None
CACHE_VERSION = 1                     # bump when a key or a stored row changes shape
MEMORY: dict[str, dict] = {}          # proxy -> key -> answer (what the inference cache holds)
USED: dict[str, set] = {}             # proxy -> keys this session read or wrote (all the cache keeps)
TIMES: dict[str, list[float]] = {}    # proxy -> [images computed, seconds computing, images replayed]
# with the inference cache every call replays what it can; without it only the switches-off pass does
IDLE = "replay" if INFER_CACHE else "pass"


class Replay:
    """Model adapter proxy. mode "record": pass every call through and remember each image's answer
    by its bytes; "replay": answer remembered images from memory (the rest pass through and are
    remembered); "pass": just pass through. Recording never changes an answer, so without the
    inference cache the main run is the plain pipeline."""
    mode = IDLE

    def __init__(self, inner):
        name = type(self).__name__
        self._inner, self._memory = inner, MEMORY.setdefault(name, {})
        self._used, self._times = USED.setdefault(name, set()), TIMES.setdefault(name, [0, 0.0, 0])

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def key(image, *extra):
        return (image.size, image.mode, hashlib.sha1(image.tobytes()).hexdigest(), *extra)

    def _timed(self, compute, images):
        t0 = time.perf_counter()
        out = compute(images)
        self._times[0] += len(images)
        self._times[1] += time.perf_counter() - t0
        return out

    def _rows(self, images, keys, compute, stack, keep=lambda row: row):
        if Replay.mode == "replay":
            todo = [i for i, k in enumerate(keys) if k not in self._memory]
            if todo:
                for i, row in zip(todo, self._timed(compute, [images[i] for i in todo])):
                    self._memory[keys[i]] = keep(row)
            self._times[2] += len(keys) - len(todo)
            self._used.update(keys)
            return stack([self._memory[k] for k in keys])
        out = self._timed(compute, images)
        if Replay.mode == "record":
            self._memory.update((k, keep(row)) for k, row in zip(keys, out))
            self._used.update(keys)
        return out


class ReplaySigLIP2(Replay):
    def embed_images(self, images):
        return self._rows(images, [self.key(im) for im in images], self._inner.embed_images, np.stack)


class ReplayOWLv2(Replay):
    def detect_batch(self, images, prompts, *, threshold, **kw):
        return self._rows(images, [self.key(im, tuple(prompts), threshold) for im in images],
                          lambda ims: self._inner.detect_batch(ims, prompts, threshold=threshold, **kw), list)

    def detect(self, image, prompts, *, threshold):
        return self.detect_batch([image], prompts, threshold=threshold)[0]


class ReplayBioCLIP(Replay):
    def encode_images(self, images):
        import torch

        # kept as numpy rows: a pickled tensor row would carry its whole batch's storage
        return self._rows(images, [self.key(im) for im in images], self._inner.encode_images,
                          lambda rows: torch.from_numpy(np.stack(rows)).to(self._inner.device),
                          keep=lambda row: row.cpu().numpy().copy())


def encode_names(texts: list[str], bioclip) -> np.ndarray:
    """names.encode with the BioCLIP text tower, through the inference cache when it is on."""
    from bioscan.service import names

    key = hashlib.sha1("\0".join(texts).encode()).hexdigest()
    memory, used = MEMORY.setdefault("text", {}), USED.setdefault("text", set())
    times = TIMES.setdefault("text", [0, 0.0, 0])
    if INFER_CACHE and key in memory:
        times[2] += len(texts)
    else:
        t0 = time.perf_counter()
        memory[key] = names.encode(texts, bioclip.model, bioclip.tokenizer, bioclip.device)
        times[0] += len(texts)
        times[1] += time.perf_counter() - t0
    used.add(key)
    return memory[key]


@pytest.fixture(scope="module", autouse=True)
def infer_cache():
    """Loads the inference cache before the module's first model call and saves what this session
    used after its last; then adds the "Model time" section to the report."""
    path = Path(INFER_CACHE) if INFER_CACHE else None
    if path is not None and path.is_file():
        with open(path, "rb") as f:
            saved = pickle.load(f)
        if saved.get("version") == CACHE_VERSION:
            for name, memory in saved["memory"].items():
                MEMORY.setdefault(name, {}).update(memory)
    yield
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = {name: {k: MEMORY[name][k] for k in used} for name, used in USED.items()}
        with open(path.with_suffix(".tmp"), "wb") as f:
            pickle.dump({"version": CACHE_VERSION, "memory": keep}, f, protocol=pickle.HIGHEST_PROTOCOL)
        path.with_suffix(".tmp").replace(path)
    report = os.environ.get("BIOSCAN_REPORT")
    if report and Path(report).is_file():
        rows = ["", "## Model time", "",
                f"Inference cache: {'on (' + str(path.name) + ')' if path else 'off'}. Computed = the model ran; "
                "replayed = the answer came from the cache or the main run. With replays, the report's "
                "identify ms and images/s are not the models' speed.", "",
                "| model call | images computed | seconds computing | images replayed |", "|---|---|---|---|"]
        rows += [f"| {name} | {n} | {s:.0f} | {r} |" for name, (n, s, r) in TIMES.items()]
        with open(report, "a") as f:
            f.write("\n".join(rows) + "\n")


STATE: dict = {}      # the main run's inputs, for the switches-off pass


def small_lists(bioclip) -> dict:
    from bioscan.service import names

    rows = sample()
    genera = {r["scientific"].split(" ")[0] for r in rows if r["kind"] == "bird"}
    with open(ROOT / "data" / "names" / "avilist_map.csv", newline="", encoding="utf-8") as f:
        birds = [r for r in csv.DictReader(f) if r["scientific"].split(" ")[0] in genera]
    with open(HERE / "mammals.csv", newline="", encoding="utf-8") as f:
        mammals = list(csv.DictReader(f))
    with open(ROOT / "data" / "names" / names.LISTS["mammal"].label_map, newline="", encoding="utf-8") as f:
        mdd = {r["scientific"]: r for r in csv.DictReader(f)}
    mammals = [{**r, **{k: mdd[r["scientific"]][k] for k in ("birdnet_label", "birdnet_how")}} for r in mammals]

    def build(list_id, kind, cls, recs):
        tax = [names._taxonomy(cls, r["order"], r["family"], *r["scientific"].split(" ", 1)) for r in recs]
        common = [r["common"] for r in recs]
        matrix = encode_names([names.tol_text(t, c) for t, c in zip(tax, common)], bioclip)
        # BirdNET labels as the label maps give them (avilist_map.csv, mdd_map.csv), each list's policy
        labels = {"birdnet": [r["birdnet_label"] for r in recs], "birdnet_how": [r["birdnet_how"] or "none" for r in recs]}
        return names.NameList(list_id, kind, [t[6] for t in tax], common, tax, matrix, ["none"] * len(recs),
                              sha=f"test-{len(recs)}", **labels, unlabelled=names.LISTS[kind].unlabelled)

    lists = {"bird": build("avilist-2025-test-subset", "bird", "Aves", birds),
             "mammal": build("mdd-test-subset", "mammal", "Mammalia", mammals)}
    if names.all_taxa_path().is_file():
        other = names.load_all_taxa(tol_files=(Path("/nonexistent"),) * 2)     # from the cache, never built here
        lists[other.kind] = other
    elif os.environ.get("BIOSCAN_REQUIRE_ALLTAXA") == "1":
        raise AssertionError(f"all-taxa list missing at {names.all_taxa_path()} (run tests/models/download.py)")
    return lists


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
    engine = engine_mod.Engine(device, engine_mod.Loaders(
        siglip2=lambda d: ReplaySigLIP2(engine_mod._load_siglip2(d)),
        owlv2=lambda d: ReplayOWLv2(engine_mod._load_owlv2(d)),
        species=lambda device: (ReplayBioCLIP(bioclip), lists)))
    engine.ensure(engine_mod.MODELS)                 # all three: the run below wants identify + embed
    if os.environ.get("BIOSCAN_REQUIRE_GEO") == "1":
        assert engine.geo is not None, "BirdNET geo prior failed to load (BIOSCAN_REQUIRE_GEO=1)"
        assert set(engine.priors) == {"bird", "mammal"}, engine.priors

    inputs = [{"path": str(p), "lat": float(r["lat"]), "lon": float(r["lon"]), "taken_at": r["taken_at"]}
              for r, p in have]
    Replay.mode = "replay" if INFER_CACHE else "record"
    try:
        with TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(4), chunk=16)) as c:
            resp = c.post("/run", json={"inputs": inputs, "want": ["identify", "embed"]})
            assert resp.status_code == 200, resp.text
            events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    finally:
        Replay.mode = IDLE
    STATE["inputs"] = inputs
    gt = ev.normalise_truth([{"path": str(p), "scientific": r["scientific"], "tier": "inat-sample", "kind": r["kind"],
                              "lat": r["lat"], "lon": r["lon"]}
                             for r, p in have], names.read_synonyms(ev.SYNONYMS_CSV))
    return gt, events, engine


def write_bench_report(path: Path, gt: list[dict], preds: dict, events: list[dict], engine) -> None:
    """report.json (`bioscan bench`) of this run, which models.yml compares with baselines/ci-smoke.json.
    Name-list membership and families come from the small in-memory lists this test built."""
    from bioscan import naming
    from bioscan.cli import bench
    from bioscan.cli import eval as ev

    lists = {kind: {naming.norm_binomial(s): t[4] for s, t in zip(nl.scientific, nl.taxonomy)}
             for kind, nl in engine.names.items()}
    done = next((e for e in reversed(events) if e["type"] == contract.DONE), None)
    rep = bench.build_report(gt, preds, groundtruth=str(HERE / "sample.csv"),
                             synonyms_sha256=bench.sha256_of(ev.SYNONYMS_CSV), done=done, lists=lists,
                             complete=done is not None,
                             tier="smoke", options={"want": ["identify", "embed"], "identify": "service defaults",
                                      "synonyms": True, "device": engine.device})
    bench.write_json(rep, path)


@pytest.fixture(scope="module")
def metrics(run):
    from bioscan.cli import eval as ev

    gt, events, engine = run
    preds = ev.load_preds(json.dumps(e) for e in events)
    m = ev.compute(gt, preds)
    rescued = sum(1 for e in events if e["type"] == "result" and e["products"]["identify"]["gate"]["class"]
                  in ("none", "person") and e["products"]["identify"]["boxes"])
    meta = {"photos": len(gt), "device": engine.device, "geo": engine.geo is not None,
            "names": {k: len(nl.scientific) for k, nl in engine.names.items()},
            "name matrices MiB": {k: f"{nl.matrix.nbytes / 2**20:.1f} ({nl.matrix.dtype})" for k, nl in engine.names.items()},
            "gate-rescued images": rescued, "floors": json.dumps(FLOORS)}
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
        write_bench_report(Path(out).with_suffix(".json"), gt, preds, events, engine)
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
    _, events, engine = run
    for e in (e for e in events if e["type"] == "result"):
        problems = contract.identify_problems(e["products"]["identify"])
        assert not problems, (e["path"], problems)
        for b in e["products"]["identify"]["boxes"]:
            x0, y0, x1, y1 = b["xyxy"]
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and b["kind"] in ("bird", "mammal", "other_animal")
            sp = b["species"]
            if b["kind"] in engine.names:
                assert sp["list"] == engine.names[b["kind"]].list_id
                post = [c["posterior"] for c in sp["top"]]
                assert post[1:] == sorted(post[1:], reverse=True) and sp["level"] in ("species", "genus", "family", "unconfirmed")
                if post[0] < max(post):           # range veto: an in-range congener ahead of an out-of-range top
                    from bioscan.service import rules

                    vetoed = max(sp["top"], key=lambda c: c["posterior"])
                    first = sp["top"][0]
                    assert first["taxonomy"][5] == vetoed["taxonomy"][5] and sp["level"] != "species"
                    assert vetoed["p_geo"] < rules.RANGE_EPS <= rules.RANGE_TAU <= first["p_geo"]


@pytest.mark.parametrize("kind", ["bird", "mammal", "other_animal"])
def test_accuracy_floors(run, metrics, kind):
    if kind not in run[2].names:
        pytest.skip(f"no {kind} name list loaded (all-taxa cache missing; see tests/models/download.py)")
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


@pytest.mark.parametrize("label_map", ["avilist_map.csv", "mdd_map.csv"])
def test_every_map_label_exists_in_birdnet(run, label_map):
    """Label map birdnet_label values (some synced by hand after a synonyms.csv edit; mdd_map.csv
    rebuilt from the label files offline) must be labels the real BirdNET model has, else that
    species silently gets no prior. A lump's labels are joined by '|'."""
    _, _, engine = run
    labels = set(engine.geo.labels)
    with open(ROOT / "data" / "names" / label_map, newline="", encoding="utf-8") as f:
        missing = [(r["scientific"], lab) for r in csv.DictReader(f)
                   for lab in r["birdnet_label"].split("|") if lab and lab not in labels]
    assert not missing, missing[:10]


SWITCHES_OFF = {"range_veto": False, "kind_check": False, "mammal_geo": False}


@pytest.fixture(scope="module")
def switched_off(run):
    """The main run's photos again with the three accuracy switches off, the models' outputs replayed."""
    from fastapi.testclient import TestClient

    from bioscan.service.app import create_app

    _, _, engine = run
    Replay.mode = "replay"
    try:
        with TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(4), chunk=16)) as c:
            resp = c.post("/run", json={"inputs": STATE["inputs"], "want": ["identify"],
                                        "options": {"identify": SWITCHES_OFF}})
            assert resp.status_code == 200, resp.text
    finally:
        Replay.mode = IDLE
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def _best(ev):
    boxes = ((ev or {}).get("products") or {}).get("identify", {}).get("boxes") or []
    best = max(boxes, key=lambda b: b["score"]) if boxes else None
    sp = (best or {}).get("species") or {}
    return (best or {}).get("kind", "–"), (sp.get("top") or [{}])[0].get("scientific", "–"), sp.get("level", "–")


SWITCH_SLACK = 1    # images per kind and measure: ~38 photos a kind, one flip is noise; the harness budget gates


def test_accuracy_switches_do_not_regress(run, switched_off):
    """On (the main run) vs off: per kind, at most SWITCH_SLACK Top-1 hits lost and SWITCH_SLACK
    confident errors (species-level wrong answers) added; a coarse tripwire only. The real gate is
    `bioscan bench compare` against the committed baseline with its regression budgets. Every
    changed image goes to the report with its before/after."""
    from bioscan.cli import eval as ev

    gt, events, engine = run
    on, off = ev.load_preds(json.dumps(e) for e in events), ev.load_preds(json.dumps(e) for e in switched_off)
    rows = ["", "## Accuracy switches: on vs off (range veto, kind check, mammal prior)", "",
            "The kind check weighs every loaded list: " + ", ".join(k for k in ("bird", "mammal", "other_animal")
                                                                   if k in engine.names) + ".", "",
            "| kind | n | Top-1 off | Top-1 on | Top-5 off | Top-5 on | coverage off | coverage on | "
            "confident errors off | confident errors on |", "|---|---|---|---|---|---|---|---|---|---|"]
    worse = []
    for kind in [k for k in ("bird", "mammal", "other_animal") if k in engine.names]:
        items = [r for r in gt if r["kind"] == kind]
        o = {name: [ev.outcome(r, preds.get(r["path"])) for r in items] for name, preds in (("off", off), ("on", on))}
        hits = {k: sum(x["top1"] for x in v) for k, v in o.items()}
        top5 = {k: sum(x["top5"] for x in v) for k, v in o.items()}
        cov = {k: sum(x["species_level"] for x in v) for k, v in o.items()}
        wrong = {k: sum(x["species_level"] and not x["top1"] for x in v) for k, v in o.items()}
        rows.append(f"| {kind} | {len(items)} | {hits['off']} | {hits['on']} | {top5['off']} | {top5['on']} | "
                    f"{cov['off']} | {cov['on']} | {wrong['off']} | {wrong['on']} |")
        if hits["on"] < hits["off"] - SWITCH_SLACK or wrong["on"] > wrong["off"] + SWITCH_SLACK:
            worse.append(f"{kind}: Top-1 {hits['off']} -> {hits['on']}, confident errors {wrong['off']} -> {wrong['on']}")
    rows += ["", f"This test fails only past {SWITCH_SLACK} image per kind and measure (Top-1 lost, confident "
             "error added); with ~38 photos a kind that is a tripwire, not a verdict. The gate is the harness: "
             "`bioscan bench compare` against the committed baseline, within its regression budgets.",
             "", "| truth | off: kind, top-1, level | on: kind, top-1, level |", "|---|---|---|"]
    for r in gt:
        a, b = _best(off.get(r["path"])), _best(on.get(r["path"]))
        if a != b:
            rows.append(f"| {r['scientific']} | {', '.join(a)} | {', '.join(b)} |")
    report = os.environ.get("BIOSCAN_REPORT")
    if report and Path(report).is_file():
        with open(report, "a") as f:
            f.write("\n".join(rows) + "\n")
    assert all(e["type"] != "error" for e in switched_off)
    assert not worse, worse


# ---- album profile: the synthetic reject set (scripts/cull_synth.py) from these photos ---------------

ALBUM_SOURCES = int(os.environ.get("BIOSCAN_ALBUM_SOURCES", "24"))
# (plugin, scope, metric) -> (op, floor) on the album report's plugin_metrics. Loose first floors, set
# before any real-model run (2026-09-24): they catch a broken stage, reducer or script, not a point of
# accuracy; tighten them after the first CI run, like FLOORS above. data/standards.toml has the bars.
ALBUM_FLOORS = {("quality", "all", "keepers_lost"): ("<=", 0.35),
                ("quality", "all", "reject_recall"): (">=", 0.40),
                ("quality", "all", "reject_precision"): (">=", 0.50),
                ("quality", "soft", "reject_recall"): (">=", 0.30),
                ("quality", "overexposed", "reject_recall"): (">=", 0.50),
                ("quality", "underexposed", "reject_recall"): (">=", 0.50),
                ("burst", "all", "burst_pair_f1"): (">=", 0.50),
                ("scene", "all", "scene_acc"): (">=", 0.50)}


@pytest.fixture(scope="module")
def album(run, tmp_path_factory):
    """The first ALBUM_SOURCES bird and mammal photos whose best box is clear of the edges, degraded
    into labelled rejects and bursts (scene: wildlife), run through the album profile on the same
    engine, then burst + select and the harness (models-report-album.json next to BIOSCAN_REPORT)."""
    import importlib.util

    from fastapi.testclient import TestClient

    from bioscan import profile
    from bioscan.cli import bench
    from bioscan.cli import eval as ev
    from bioscan.service.app import create_app

    gt, events, engine = run
    spec = importlib.util.spec_from_file_location("cull_synth", ROOT / "scripts" / "cull_synth.py")
    synth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synth)
    wildlife = {r["path"]: "wildlife" for r in gt if r["kind"] in ("bird", "mammal")}
    sources = [s for s in synth.sources_from_events(events, wildlife)
               if s["scene"] and synth.usable(tuple(s["box"]))][:ALBUM_SOURCES]
    out = tmp_path_factory.mktemp("album")
    rows = synth.build(sources, out, seed=7)
    with TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(4), chunk=16)) as c:
        resp = c.post("/run", json={"inputs": [{"path": r["path"], "taken_at": r["taken_at"]} for r in rows],
                                    "profile": "album"})
        assert resp.status_code == 200, resp.text
    evs = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    done = next((e for e in reversed(evs) if e["type"] == contract.DONE), None)
    res = profile.resolve(profile.builtin(), "album")
    rep = bench.build_report(rows, ev.load_preds(json.dumps(e) for e in evs),
                             groundtruth=str(out / "groundtruth-album.csv"), done=done, lists={},
                             complete=done is not None, tier="album", profile="album", reducers=res.reducer_run(),
                             options={"want": list(res.want), "profile": "album", "device": engine.device,
                                      "sources": len(sources), "seed": 7})
    report = os.environ.get("BIOSCAN_REPORT")
    if report:
        bench.write_json(rep, Path(report).with_name(Path(report).stem + "-album.json"))
        if Path(report).is_file():
            counts: dict[str, int] = {}
            for r in rows:
                v = r["variant"].rstrip("0123456789")
                counts[v] = counts.get(v, 0) + 1
            with open(report, "a") as f:
                f.write(f"\n## Album profile on the synthetic reject set\n\n{len(sources)} sources, {len(rows)} photos "
                        f"({', '.join(f'{k} {v}' for k, v in counts.items())}); floors {ALBUM_FLOORS}.\n\n"
                        + bench.plugin_md(rep["plugin_metrics"]) + "\n")
    return rows, evs, rep


def test_album_every_photo_gets_a_result(album):
    rows, evs, _ = album
    assert len(rows) >= 60, f"only {len(rows)} album photos (too few sources with a box clear of the edges)"
    assert not [e for e in evs if e["type"] == "error"] and evs[-1]["ok"] == len(rows)
    res = next(e for e in evs if e["type"] == "result")
    assert list(res["products"]) == ["identify", "embed", "aesthetics", "quality", "scene"]
    # aesthetics names its head ("v1@none" while no general head is committed); quality and scene
    # report their settings fingerprints
    assert set(res["engine"]["plugins"]) == {"aesthetics", "quality", "scene"}


def test_album_floors(album):
    pm = album[2]["plugin_metrics"]
    low = {}
    for (plugin, scope, metric), (op, floor) in ALBUM_FLOORS.items():
        v = ((pm.get(plugin) or {}).get(scope) or {}).get(metric)
        if v is None or (v < floor if op == ">=" else v > floor):
            low[f"{plugin}.{scope}.{metric}"] = v
    assert not low, f"album metrics past their floors: {low}"


def test_album_report_compares_and_scores(album):
    """bench compare and scorecard work on a real album report (models.yml then compares it with
    baselines/ci-album.json once that exists)."""
    from bioscan.cli import bench

    rep = album[2]
    c = bench.compare(rep, rep, bench.read_budget(ROOT / "baselines" / "budget-album.toml"))
    assert c["verdict"] == "ok" and c["plugin_metrics"]["quality"]["all"]["keepers_lost"]["delta"] == 0
    sc = bench.scorecard(rep, bench.read_standards(bench.STANDARDS_TOML), "album")
    assert sc["rows"] and all(r["status"] != "n/a" for r in sc["rows"] if r["scope"] == "all")
