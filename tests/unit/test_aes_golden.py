"""`bioscan bench aesthetic`: the aesthetic golden set scores any model's scores file.

Each test states the decision the metric protects: choosing the owner's winner in a shot group,
not throwing away keepers when the bottom of a trip is culled, not moving a score when only the
file name or encoding changes, and showing a systematic lean on one slice of the album.
"""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

from bioscan.cli import aesgolden as ag
from bioscan.cli import bench
from bioscan.cli import main as cli

ROOT = Path(__file__).resolve().parents[2]


def build(tmp: Path) -> Path:
    """Two trips; per trip two shot groups of three (winner 5 stars, others 3 and 2) and four single
    frames (stars 1, 2, 4, 5); keep = stars >= 3; half the frames sliced `style:minimal`, half
    `style:busy`; two planted copies (rename, blur) of one frame per trip; a 3-frame re-rating."""
    rows = []
    for t in ("trip-a", "trip-b"):
        for gi in range(2):
            for j, stars in enumerate((5, 3, 2)):
                rows.append({"path": f"{t}/g{gi}-{j}.jpg", "trip": t, "group": f"{t}-g{gi}", "best": int(j == 0),
                             "category": "bird", "stars": stars, "keep": int(stars >= 3),
                             "slices": "style:minimal" if gi == 0 else "style:busy",
                             "reasons": "" if stars >= 3 else "composition"})
        for j, stars in enumerate((1, 2, 4, 5)):
            rows.append({"path": f"{t}/s{j}.jpg", "trip": t, "category": "landscape", "stars": stars,
                         "keep": int(stars >= 3), "slices": "style:minimal" if j % 2 else "style:busy",
                         "reasons": "" if stars >= 3 else "soft_subject"})
        rows.append({"path": f"planted/{t}-copy.jpg", "variant_of": f"{t}/s3.jpg", "variant": "rename"})
        rows.append({"path": f"planted/{t}-blur.jpg", "variant_of": f"{t}/s3.jpg", "variant": "blur"})
    for r in rows[:3]:
        r["stars2"] = r["stars"]
    g = tmp / "golden"
    g.mkdir()
    with open(g / "images.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ag.IMAGE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return g


def stars_of(g: Path) -> dict[str, float]:
    with open(g / "images.csv") as f:
        return {r["path"]: float(r["stars"] or 0) for r in csv.DictReader(f)}


def write_scores(path: Path, scores: dict[str, float | None], extra: dict | None = None) -> Path:
    with open(path, "w") as f:
        for p, s in scores.items():
            f.write(json.dumps({"path": p, "score": s, **(extra or {}).get(p, {})}) + "\n")
    return path


def good_scores(g: Path) -> dict[str, float]:
    """A model that agrees with the owner: stars plus a small per-frame tie-breaker; the renamed copy
    scores like its original, the blurred copy lower."""
    st = stars_of(g)
    out = {p: s + 0.01 * i for i, (p, s) in enumerate(st.items()) if not p.startswith("planted/")}
    for t in ("trip-a", "trip-b"):
        out[f"planted/{t}-copy.jpg"] = out[f"{t}/s3.jpg"]
        out[f"planted/{t}-blur.jpg"] = out[f"{t}/s3.jpg"] - 3
    return out


def score(g: Path, scores: dict, tmp: Path, name: str = "s.ndjson") -> dict:
    return ag.evaluate(ag.read_golden(g), ag.read_scores(write_scores(tmp / name, scores), g),
                       scores_path=str(tmp / name))


def test_model_that_agrees_with_owner_scores_perfect_choices(tmp_path):
    g = build(tmp_path)
    a = score(g, good_scores(g), tmp_path)["metrics"]["all"]
    assert a["pair_acc"] == 1.0 and a["pairs"] == 8          # every winner beats both others of its group
    assert a["group_top1"] == 1.0 and a["groups"] == 4
    assert a["group_top1_random"] == pytest.approx(1 / 3, abs=1e-6)
    assert a["keepers_lost_at_20"] == 0.0 and a["drop_auc"] == 1.0
    assert a["degrade_acc"] == 1.0 and a["invariance_rate"] == 1.0
    assert a["missing_rate"] == 0.0 and a["spearman"] > 0.95


def test_inverted_model_fails_the_choices_it_would_get_wrong(tmp_path):
    g = build(tmp_path)
    bad = {p: -s for p, s in good_scores(g).items()}
    a = score(g, bad, tmp_path)["metrics"]["all"]
    assert a["pair_acc"] == 0.0 and a["group_top1"] == 0.0
    assert a["keepers_lost_at_20"] > 0.2              # culling the "worst" throws away keepers
    assert a["drop_auc"] == 0.0 and a["degrade_acc"] == 0.0 and a["spearman"] < -0.9


def test_missing_score_counts_as_a_miss_not_a_skip(tmp_path):
    g = build(tmp_path)
    s = good_scores(g)
    del s["trip-a/g0-0.jpg"]                           # the winner of one group was never scored
    a = score(g, s, tmp_path)["metrics"]["all"]
    assert a["missing_rate"] > 0 and a["scored"] == a["expected"] - 1
    assert a["group_top1"] == 0.75                     # that group now picks the wrong frame
    assert a["pair_acc"] == 0.75


def test_score_that_depends_on_the_file_name_fails_invariance(tmp_path):
    g = build(tmp_path)
    s = good_scores(g)
    s["planted/trip-a-copy.jpg"] = s["trip-a/s3.jpg"] - 2.5    # same bytes, other name, other score
    a = score(g, s, tmp_path)["metrics"]["all"]
    assert a["invariance_rate"] == 0.5 and a["invariance_rate_by_kind"]["rename"] == 0.5
    assert a["invariance_max_shift"] > ag.INVARIANCE_TOL


def test_lean_toward_one_slice_shows_as_a_residual(tmp_path):
    g = build(tmp_path)
    golden = ag.read_golden(g)
    s = good_scores(g)
    minimal = {r["path"] for r in golden["images"] if "style:minimal" in r["slices"]}
    s = {p: v + (4 if p in minimal else 0) for p, v in s.items()}   # likes minimal frames beyond the owner
    m = score(g, s, tmp_path)["metrics"]
    lo, _hi = m["slice:style:minimal"]["residual_ci"]
    _lo, hi = m["slice:style:busy"]["residual_ci"]
    assert lo > 0 and hi < 0
    assert abs(score(g, good_scores(g), tmp_path, "t.ndjson")["metrics"]["slice:style:minimal"]["residual"]) < 0.1


def test_reasons_and_sub_scores_are_scored_when_the_model_gives_them(tmp_path):
    g = build(tmp_path)
    s = good_scores(g)
    extra = {p: {"reasons": ["soft_subject"] if p.endswith(("s0.jpg", "s1.jpg")) else [],
                 "dims": {"light": v, "composition": -v}} for p, v in s.items()}
    rep = ag.evaluate(ag.read_golden(g), ag.read_scores(write_scores(tmp_path / "r.ndjson", s, extra=extra), g))
    r = rep["reasons"]
    assert r["by_reason"]["soft_subject"] == 1.0 and r["by_reason"]["composition"] == 0.0
    assert r["reason_precision"] == 1.0
    assert rep["dims"]["dims_spearman"]["light"] > 0.9 and rep["dims"]["dims_spearman"]["composition"] < -0.9
    assert rep["owner_ceiling"] == {"n": 3, "spearman": 1.0}


def test_bioscan_run_output_is_a_scores_file(tmp_path):
    g = build(tmp_path)
    s = good_scores(g)
    lines = [{"type": "meta"}] + [
        {"type": "result", "path": str(g / p), "products": {"aesthetics": {"score": v, "head_id": "eva-head-v1@abc"}}}
        for p, v in s.items() if p != "trip-b/s0.jpg"] + [{"type": "error", "path": str(g / "trip-b/s0.jpg"),
                                                           "message": "decode"}, {"type": "done"}]
    f = tmp_path / "run.ndjson"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    rep = ag.evaluate(ag.read_golden(g), ag.read_scores(f, g), scores_path=str(f))
    assert rep["meta"]["model"] == "eva-head-v1@abc"
    assert rep["metrics"]["all"]["failed"] == 1 and rep["missing"] == ["trip-b/s0.jpg"]


@pytest.mark.parametrize("col,val,msg", [("variant", "crop", "variant must be"), ("reasons", "ugly", "unknown reasons"),
                                         ("keep", "2", "keep and best")])
def test_golden_set_rejects_what_it_cannot_score(tmp_path, col, val, msg):
    g = build(tmp_path)
    rows = list(csv.DictReader(open(g / "images.csv")))
    rows[0][col] = val
    if col == "variant":
        rows[0]["variant_of"] = rows[1]["path"]
    with open(g / "images.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ag.IMAGE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(ag.GoldenError, match=msg):
        ag.read_golden(g)


def test_compare_flags_a_worse_model_and_refuses_another_golden_set(tmp_path):
    g = build(tmp_path)
    good = score(g, good_scores(g), tmp_path, "a.ndjson")
    worse = dict(good_scores(g))
    for t in ("trip-a", "trip-b"):                    # the new model prefers a loser in two groups
        worse[f"{t}/g0-1.jpg"] = worse[f"{t}/g0-0.jpg"] + 1
    new = score(g, worse, tmp_path, "b.ndjson")
    budget = tmp_path / "budget.toml"
    budget.write_text('[[rule]]\nmetric = "group_top1"\nmax_drop_pts = 5\n')
    c = ag.compare(good, new, ag.read_budget(budget), reps=50)
    assert c["groups"]["base_only"] == 2 and c["groups"]["new_only"] == 0
    assert [v["rule"] for v in c["violations"]] == ["group_top1 max_drop_pts 5"]
    (tmp_path / "a.json").write_text(json.dumps(good))
    (tmp_path / "b.json").write_text(json.dumps(new))
    rc = cli.main(["bench", "aesthetic", "compare", str(tmp_path / "a.json"), str(tmp_path / "b.json"),
                   "--budget", str(budget), "--boot", "20"])
    assert rc == 1
    other = dict(new, meta={**new["meta"], "golden_sha256": "0" * 64})
    with pytest.raises(bench.BenchError, match="not comparable"):
        ag.compare(good, other)


def test_score_command_writes_report_and_split_filters(tmp_path):
    g = build(tmp_path)
    f = write_scores(tmp_path / "s.ndjson", good_scores(g))
    assert cli.main(["bench", "aesthetic", "score", str(g), str(f), "--out", str(tmp_path / "out")]) == 0
    rep = json.loads((tmp_path / "out" / "report.json").read_text())
    assert rep["schema"] == ag.SCHEMA and "Headline" in (tmp_path / "out" / "report.md").read_text()
    none = ag.evaluate(ag.read_golden(g), ag.read_scores(f, g), split="dev")
    assert none["metrics"]["all"]["n"] == 0            # every row defaults to the test split


def test_plant_makes_known_answer_copies_once(tmp_path):
    spec = importlib.util.spec_from_file_location("aes_plant", ROOT / "scripts" / "aes_plant.py")
    plant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plant)
    g = tmp_path / "g"
    g.mkdir()
    for i in range(3):
        Image.new("RGB", (64, 48), (40 * i, 120, 200)).save(g / f"f{i}.jpg")
    (g / "images.csv").write_text("path,stars\nf0.jpg,3\nf1.jpg,4\nf2.jpg,\n")
    added = plant.plant(g, n=5, seed=1)
    kinds = {a["variant"] for a in added}
    assert kinds == set(plant.KINDS) - {"resize2048"}    # 64 px is below the decode size: nothing to resize
    assert {a["variant_of"] for a in added} == {"f0.jpg", "f1.jpg"}   # the unrated frame is never planted
    copy = next(a for a in added if a["variant"] == "rename")
    assert (g / copy["path"]).read_bytes() == (g / copy["variant_of"]).read_bytes()
    golden = ag.read_golden(g)                         # the result is a valid golden set
    assert sum(1 for r in golden["images"] if r["variant"]) == len(added)
    with pytest.raises(SystemExit):
        plant.plant(g, n=5, seed=1)


def test_table_ranks_models_and_measures_deviation_in_grade_units(tmp_path):
    """The arena: the model that agrees with the owner ranks first; a noisier copy of it ranks below but ties
    (its ΔSpearman interval spans 0); an inverted model is last with a wide deviation. The residual CSV names
    the frames the models disagree on most."""
    g = build(tmp_path)
    good = good_scores(g)
    noisy = dict(good)
    noisy["trip-a/s0.jpg"], noisy["trip-b/g1-2.jpg"] = 3.5, 3.6      # two 1-2 star frames pushed up a little
    bad = {p: -s for p, s in good.items()}
    reps = [score(g, sc, tmp_path, f"{n}.ndjson") for n, sc in (("good", good), ("noisy", noisy), ("bad", bad))]
    for r, n in zip(reps, ("good", "noisy", "bad")):
        r["meta"]["model"] = n
    a = ag.arena(reps, reps=200)
    rows = {r["model"]: r for r in a["rows"]}
    assert [r["model"] for r in a["rows"]] == ["good", "noisy", "bad"]
    assert rows["good"]["grade_mae"] == 0 and rows["good"]["exact"] == 1 and rows["good"]["cross_grade_pair_acc"] == 1
    assert rows["noisy"]["tie"] and not rows["bad"]["tie"]
    assert rows["bad"]["cross_grade_pair_acc"] == 0 and rows["bad"]["grade_mae"] > 1
    assert a["frames"][0]["off_by_two"] >= 1                          # the inverted model is off on the worst frame
    md = ag.table_md(reps, reps=50, csv_path=tmp_path / "res.csv")
    assert "| 1 | good |" in md and "| 2= | noisy |" in md and "| 3 | bad |" in md
    with open(tmp_path / "res.csv") as f:
        head = next(csv.reader(f))
    assert head[:2] == ["path", "grade"] and "bad:cal" in head
    other = dict(reps[1], meta={**reps[1]["meta"], "golden_sha256": "0" * 64})
    with pytest.raises(bench.BenchError, match="not comparable"):
        ag.arena([reps[0], other], reps=10)
