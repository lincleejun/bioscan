"""`bioscan aesthetic ratings|train|eval` against a fake service, the embeddings cache, the report
and its scorecard, and scripts/train_aesthetic_head.py end to end on a fake engine."""
import json
import math
import random
import sys
from pathlib import Path

import pytest
from aesthetic_helpers import random_head, unit

from bioscan import aesthetic as aes
from bioscan.cli import aesbench, bench, client
from bioscan.cli.main import main

ROOT = Path(__file__).resolve().parents[2]


def planted(n_trips=6, per_trip=40, seed=0):
    """Ratings over trips and a vector per image whose first coordinate carries the rating."""
    r = random.Random(seed)
    rows, vecs = [], {}
    for t in range(n_trips):
        for i in range(per_trip):
            stars = r.choice([1, 2, 3, 3, 4, 5])
            path = f"/photos/trip{t}/img{i:03d}.jpg"
            rows.append((path, stars, f"trip{t}"))
            v = [r.gauss(0, 0.05) for _ in range(aes.DIM)]
            v[0] += stars / 5 + r.gauss(0, 0.15)
            v[1 + t] += 0.3                                   # a trip signature, like a place or light
            vecs[path] = unit(v)
    return rows, vecs


@pytest.fixture
def service(monkeypatch):
    """A fake /run that embeds known paths (f16 base64, as asked) and errors on the rest."""
    calls = []
    table: dict[str, list[float]] = {}

    def run(payload, url):
        calls.append(payload)
        assert payload["want"] == ["embed"] and payload["options"] == {"embed": {"format": "f16_base64"}}
        for inp in payload["inputs"]:
            p = inp["path"]
            if p in table:
                yield json.dumps({"type": "result", "path": p, "products": {"embed": {
                    "model": "siglip2-base-patch16-224", "dim": 768, "vector": aes.f16_encode(table[p])}}}).encode()
            else:
                yield json.dumps({"type": "error", "path": p, "product": None, "message": "decode: nope"}).encode()
        yield b'{"type": "done", "ok": 0, "failed": 0}'

    monkeypatch.setattr(client, "run", run)
    return table, calls


def _csv(p, rows):
    p.write_text("path,rating,trip\n" + "".join(f"{a},{b},{c}\n" for a, b, c in rows))
    return str(p)


def ratings_csv(tmp_path, rows):
    return _csv(tmp_path / "ratings.csv", rows)


def test_embed_paths_uses_and_extends_the_cache(service, tmp_path, monkeypatch):
    table, calls = service
    table.update({"/a.jpg": unit([1.0] + [0.0] * 767), "/b.jpg": unit([0.0, 1.0] + [0.0] * 766)})
    monkeypatch.setattr(aesbench, "_stamp", lambda p: [1, 2.0])           # the files "exist, unchanged"
    cache = tmp_path / "emb.ndjson"
    vecs, failed = aesbench.embed_paths(["/a.jpg", "/b.jpg", "/gone.jpg"], "u", cache)
    assert set(vecs) == {"/a.jpg", "/b.jpg"} and failed == {"/gone.jpg": "decode: nope"}
    assert vecs["/a.jpg"][0] == pytest.approx(1.0, rel=1e-3) and len(calls) == 1
    vecs2, _ = aesbench.embed_paths(["/a.jpg", "/b.jpg"], "u", cache)
    assert len(calls) == 1 and vecs2 == vecs                              # all from the cache
    monkeypatch.setattr(aesbench, "_stamp", lambda p: [1, 3.0])           # changed files are embedded again
    aesbench.embed_paths(["/a.jpg"], "u", cache)
    assert len(calls) == 2 and calls[-1]["inputs"] == [{"path": "/a.jpg"}]


def test_ratings_command(tmp_path, capsys):
    rows, _ = planted(2, 5)
    out = tmp_path / "out.csv"
    assert main(["aesthetic", "ratings", ratings_csv(tmp_path, rows), "--csv", str(out)]) == 0
    text = capsys.readouterr().out
    assert "10 rated images in 2 trips" in text and "trip0: 5 rated" in text
    assert out.read_text().splitlines()[0] == "path,rating,pick,label,trip"


def test_train_personal_head_on_planted_ratings(service, tmp_path, capsys, monkeypatch):
    table, _ = service
    rows, vecs = planted()
    table.update(vecs)
    monkeypatch.setattr(aes, "BUILTIN_HEAD", tmp_path / "absent.json")
    out = tmp_path / "me.json"
    assert main(["aesthetic", "train", "--ratings", ratings_csv(tmp_path, rows), "--out", str(out), "--name", "me",
                 "--seed", "1"]) == 0
    text = capsys.readouterr()
    assert "no general head installed" in text.err
    head = aes.load_head(out)
    assert head.name == "me" and (head.lo, head.hi) == (0, 5) and head.provenance["n"] == len(rows)
    assert head.provenance["cv"]["by"] == "group" and head.provenance["cv"]["srcc"] > 0.6
    assert head.provenance["ratings_sha256"] == aes.ratings_sha(aes.read_ratings(str(tmp_path / "ratings.csv")))
    assert "5-fold CV by group: SRCC" in text.out
    # the same inputs and seed give the same head
    assert main(["aesthetic", "train", "--ratings", str(tmp_path / "ratings.csv"), "--out", str(tmp_path / "me2.json"),
                 "--name", "me", "--seed", "1"]) == 0
    a, b = json.loads(out.read_text()), json.loads((tmp_path / "me2.json").read_text())
    assert a["weights"] == b["weights"] and a["bias"] == b["bias"]


def test_train_with_a_prior_head_records_it(service, tmp_path, monkeypatch):
    table, _ = service
    rows, vecs = planted(4, 20)
    table.update(vecs)
    prior = aes.write_head(random_head(5, name="eva-head-v1"), tmp_path / "eva.json")
    monkeypatch.setattr(aes, "BUILTIN_HEAD", prior)
    out = tmp_path / "me.json"
    assert main(["aesthetic", "train", "--ratings", ratings_csv(tmp_path, rows), "--out", str(out)]) == 0
    assert aes.load_head(out).provenance["prior"] == aes.load_head(prior).id


def test_train_needs_one_source_and_enough_vectors(service, tmp_path):
    with pytest.raises(SystemExit, match="exactly one of --eva"):
        main(["aesthetic", "train"])
    with pytest.raises(SystemExit, match="need at least 10"):
        main(["aesthetic", "train", "--ratings", ratings_csv(tmp_path, [("/x.jpg", 3, "t")]),
              "--out", str(tmp_path / "h.json")])


def test_eval_report_and_scorecard(service, tmp_path, capsys, monkeypatch):
    table, _ = service
    rows, vecs = planted(6, 60, seed=3)
    table.update(vecs)
    monkeypatch.setattr(aes, "BUILTIN_HEAD", tmp_path / "absent.json")
    src = ratings_csv(tmp_path, rows)
    personal = tmp_path / "me.json"
    assert main(["aesthetic", "train", "--ratings", src, "--out", str(personal)]) == 0
    # the "general" head: fitted on half the trips, so it knows the planted signal as a real one would
    half = [r for r in rows if r[2] in ("trip0", "trip1", "trip2")]
    general = tmp_path / "eva.json"
    assert main(["aesthetic", "train", "--ratings", _csv(tmp_path / "half.csv", half), "--out", str(general), "--name", "eva-head-v1"]) == 0
    out = tmp_path / "run"
    assert main(["aesthetic", "eval", src, "--out", str(out), "--head", str(general), "--personal", str(personal),
                 "--curve", "50,100,1000", "--folds", "3"]) == 0
    rep = json.loads((out / "report.json").read_text())
    assert rep["schema"] == "bioscan-aesthetic-report" and rep["version"] == 1
    m = rep["meta"]
    assert (m["tier"], m["profile"], m["served"], m["n"], m["trips"]) == ("aesthetic-own", "album", "blended", 360, 6)
    assert m["personal_in_sample"] is True and m["picks_from"] == "rating>=4"
    assert set(rep["by_head"]) == {"general", "personal", "blended"}
    allm = rep["metrics"]["all"]
    for k in ("n", "spearman", "spearman_ci", "kendall", "plcc", "ndcg_at_k", "precision_at_k", "precision_at_k_ci",
              "precision_at_k_random", "spearman_trip_mean", "trips"):
        assert k in allm, k
    assert rep["by_head"]["personal"]["spearman"] > 0.6 and rep["by_head"]["personal"]["precision_at_k"] > \
        rep["by_head"]["personal"]["precision_at_k_random"]
    assert [t["trip"] for t in rep["per_trip"]["blended"]] == [f"trip{i}" for i in range(6)]
    pts = {p["n"]: p for p in rep["curve"]["points"]}
    assert pts[50]["runs"] == 9 and pts[1000]["runs"] == 0 and pts[100]["personal"] > 0.5
    md = (out / "report.md").read_text()
    assert "in-sample" in md and "Learning curve" in md and "| blended |" in md
    # the report is scored by `bench scorecard` against the aesthetic-own standards
    capsys.readouterr()
    code = main(["bench", "scorecard", str(out / "report.json"), "--md", str(out / "scorecard.md")])
    sc = (out / "scorecard.md").read_text()
    assert code in (0, 1) and "tier: aesthetic-own" in sc and "aesthetics.aesthetic_own.all.spearman" in sc


def test_eval_without_any_head_still_reports(service, tmp_path, monkeypatch):
    table, _ = service
    rows, vecs = planted(2, 10)
    table.update(vecs)
    monkeypatch.setattr(aes, "BUILTIN_HEAD", tmp_path / "absent.json")
    assert main(["aesthetic", "eval", ratings_csv(tmp_path, rows), "--out", str(tmp_path / "r"), "--no-curve"]) == 0
    rep = json.loads((tmp_path / "r" / "report.json").read_text())
    assert rep["by_head"] == {} and rep["metrics"]["all"] == {"n": 20, "trips": 2} and rep["curve"] is None


def test_standards_rows_read_by_bench():
    rows = [s for s in bench.read_standards(bench.STANDARDS_TOML) if bench.standard_tier(s) == "aesthetic-own"]
    assert {s["metric"] for s in rows} >= {"spearman", "kendall", "ndcg_at_k", "precision_at_k"}
    assert all(s["profile"] == "album" and s["scope"] == "all" for s in rows)


# ---- scripts/train_aesthetic_head.py -------------------------------------------------------------

class FakeFrameEngine:
    """Engine stand-in: ensure() and frame() like the real one; a frame's vector carries its brightness."""
    instances = []

    def __init__(self, device=None):
        self.device = device or "cpu"
        self.ensured = []
        FakeFrameEngine.instances.append(self)

    def ensure(self, models):
        self.ensured += list(models)

    def frame(self, images):
        import numpy as np

        out = []
        for im in images:
            b = sum(im.getpixel((0, 0))) / 765
            v = np.full(768, 0.01, dtype=np.float32)
            v[0], v[1] = b, 1 - b
            out.append(v / np.linalg.norm(v))
        return np.stack(out), [{} for _ in images]


def test_train_script_end_to_end_on_a_fake_engine(tmp_path, monkeypatch, capsys):
    """read_eva -> the service's decode -> Engine.frame (fake) -> fit -> head file -> base64 markers."""
    import base64

    from PIL import Image

    from bioscan.service import engine

    eva = tmp_path / "eva"
    (eva / "data").mkdir(parents=True)
    (eva / aes.EVA_IMAGES).mkdir(parents=True)
    lines = ["image_id=user_id=score=difficulty=visual=composition=quality=semantic=vote_time=1=2=3=4"]
    r = random.Random(0)
    for i in range(60):
        level = r.randint(0, 255)
        Image.new("RGB", (32, 24), (level, level, level)).save(eva / aes.EVA_IMAGES / f"{100 + i}.jpg")
        for u in range(3):
            score = min(10, max(0, round(10 * level / 255 + r.gauss(0, 0.5))))
            lines.append(f"{100 + i}=U{u}={score}.0=2=3=3=3=3=10=0=0=0=0")
    (eva / aes.EVA_VOTES).write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(engine, "Engine", FakeFrameEngine)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import train_aesthetic_head as script
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    out = tmp_path / "eva-head-v1.json"
    assert script.main(["--eva-dir", str(eva), "--out", str(out), "--print-base64", "--folds", "3"]) == 0
    text = capsys.readouterr().out
    head = aes.load_head(out)
    assert head.name == "eva-head-v1" and head.provenance["n"] == 60 and "CC0" in head.provenance["licence"]
    assert head.provenance["cv"]["srcc"] > 0.8 and FakeFrameEngine.instances[-1].ensured == ["siglip2"]
    b64 = text.split("===== BEGIN eva-head-v1.json base64 =====\n")[1].split("===== END")[0]
    assert base64.b64decode(b64) == out.read_bytes()
    assert "EVA n 60" in text and "CV SRCC" in text
    # a rerun reads every vector from the cache: no engine is built
    n = len(FakeFrameEngine.instances)
    assert script.main(["--eva-dir", str(eva), "--out", str(out)]) == 0
    assert len(FakeFrameEngine.instances) == n
    assert math.isfinite(head.bias)
