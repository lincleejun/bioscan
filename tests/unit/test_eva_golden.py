"""The public aesthetic golden set (scripts/eva_golden.py, data/aesthetic/eva-golden-v1.csv).

What matters: the general head is never fitted on the images it is tested on, every star level is
clearly apart from its neighbours, and only images the crowd agrees on are used.
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from bioscan import aesthetic as aes
from bioscan.cli import aesgolden as ag

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("eva_golden", ROOT / "scripts" / "eva_golden.py")
eg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eg)


def committed() -> list[dict]:
    with open(aes.EVA_HOLDOUT, newline="") as f:
        return list(csv.DictReader(f))


def test_committed_list_is_balanced_and_bands_do_not_touch():
    rows = committed()
    assert len(rows) == 100 and len({r["image_id"] for r in rows}) == 100
    for stars, (lo, hi) in eg.BANDS.items():
        band = [float(r["mean"]) for r in rows if int(r["stars"]) == stars]
        assert len(band) == eg.PER_STAR and all(lo <= m < hi for m in band)
    tops = [max(float(r["mean"]) for r in rows if int(r["stars"]) == s) for s in range(1, 5)]
    bottoms = [min(float(r["mean"]) for r in rows if int(r["stars"]) == s) for s in range(2, 6)]
    assert all(b - t >= 0.3 for t, b in zip(tops, bottoms))     # a gap between every two neighbouring stars
    assert all(int(r["votes"]) >= 30 for r in rows)


def test_select_keeps_only_agreeing_images_per_band():
    rows = []
    for stars, (lo, hi) in eg.BANDS.items():
        mid = (lo + min(hi, 10)) / 2
        for i in range(60):   # sd 0.5 (agree) for the first 30, 3.0 (disagree) for the rest
            rows.append({"image_id": f"{stars}-{i}", "mean": mid, "sd": 0.5 if i < 30 else 3.0, "votes": 30})
    chosen = eg.select(rows)
    assert len(chosen) == 100 and all(r["sd"] == 0.5 for r in chosen)
    assert chosen == eg.select(rows)                            # seeded: the same list every time


def test_general_head_never_sees_the_held_out_images(tmp_path):
    held = sorted(aes.eva_holdout())[:2]
    (tmp_path / "data").mkdir()
    (tmp_path / aes.EVA_IMAGES).mkdir(parents=True)
    lines = ["image_id=user_id=score"]
    for iid in [*held, "999999901", "999999902"]:
        (tmp_path / aes.EVA_IMAGES / f"{iid}.jpg").write_bytes(b"x")
        lines += [f"{iid}=u1=6.0", f"{iid}=u2=7.0"]
    (tmp_path / aes.EVA_VOTES).write_text("\n".join(lines) + "\n")
    got = {Path(p).stem for p, _, _ in aes.read_eva(tmp_path)}
    assert got == {"999999901", "999999902"}
    assert len(aes.read_eva(tmp_path, exclude=set())) == 4
    prov = aes.eva_holdout_provenance()
    assert prov["images"] == 100 and len(prov["sha256"]) == 64


def test_build_makes_a_golden_folder_bench_can_score(tmp_path):
    ids = [r["image_id"] for r in committed()]
    (tmp_path / "eva" / aes.EVA_IMAGES).mkdir(parents=True)
    for iid in ids:
        (tmp_path / "eva" / aes.EVA_IMAGES / f"{iid}.jpg").write_bytes(b"x")
    assert eg.build(tmp_path / "eva", tmp_path / "golden") == 100
    g = ag.read_golden(tmp_path / "golden")
    keep = {r["grade"]: {x["keep"] for x in g["images"] if x["grade"] == r["grade"]} for r in g["images"]}
    assert keep == {1.0: {0}, 2.0: {0}, 3.0: {None}, 4.0: {1}, 5.0: {1}}
    with pytest.raises(SystemExit):
        eg.build(tmp_path / "eva", tmp_path / "golden")        # frozen once built
