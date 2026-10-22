"""faces.cluster and versions.plan as pure functions."""
from __future__ import annotations

import numpy as np

from bioscan.gardener import faces, versions


def vec(*xs):
    v = np.zeros(128, dtype=np.float32)
    v[:len(xs)] = xs
    return faces.encode(v / np.linalg.norm(v))


def test_faces_cluster_by_person_largest_first_and_deterministic():
    found = [("a1", vec(1, 0.1), 1.0), ("b1", vec(0, 1), 0.9), ("a2", vec(1, -0.1), 0.5), ("a3", vec(0.9, 0.2), 0.4),
             ("b2", vec(0.1, 1), 0.3), ("a1", vec(1, 0.05), 0.2), ("c1", vec(0, 0, 1), 0.8)]
    people = faces.cluster(found)
    assert people == [["a1", "a2", "a3"], ["b1", "b2"], ["c1"]]          # a1's second face does not count twice
    assert [sorted(p) for p in faces.cluster(list(reversed(found)))] == people   # weights, not input order, seed people
    assert faces.cluster(found, threshold=0.999) != people
    assert faces.cluster([]) == []


def photo(i, path, taken_at=None, camera="ILCE-7M4", w=6000, h=4000, lock=0):
    return {"id": i, "primary_path": path, "taken_at": taken_at, "camera": camera, "width": w, "height": h,
            "version_lock": lock}


def test_versions_plan_same_capture_exports_rivals_and_locks():
    t1, t2 = "2026-08-29T09:21:34.417", "2026-08-29T09:21:34"
    rows = [photo("jpg", "/c/JPG/DSC01234.JPG", t2), photo("dng", "/c/DNG/DSC01234.dng", t1),
            photo("arw", "/c/RAW/DSC01234.ARW", t1),
            photo("x1", "/c/exp/DSC01234-3116c5ee.jpg", w=1616, h=1080),        # export of that capture
            photo("o5", "/c/RAW/DSC05555.ARW", "2026-08-30T10:00:00"), photo("p5", "/d/RAW/DSC05555.ARW", "2025-01-02T10:00:00"),
            photo("x5", "/c/exp/DSC05555.jpg"),                                   # the counter wrapped: two captures look alike
            photo("x9", "/c/exp/DSC09999.jpg"), photo("o9", "/c/RAW/DSC09999.ARW", "2026-08-31T08:00:00"),
            photo("own", "/c/exp/DSC01234-crop.jpg", lock=1),                     # a person said: its own picture
            photo("sheet", "/c/exp/contactsheet.jpg")]
    hashes = {"arw": [0b1111], "x1": [0b1110, 0], "o5": [0], "p5": [0b1], "x5": [0b11],
              "o9": [0], "x9": [(1 << 40) - 1], "own": [0b1111], "sheet": [0b1111]}
    got = versions.plan(rows, lambda i: hashes.get(i))
    assert got["jpg"] == ("arw", "same_capture") and got["dng"] == ("arw", "same_capture")   # RAW stands for it
    assert got["x1"] == ("arw", "export")
    assert "x5" not in got                     # 2 vs 1 bit from captures a year apart: too close to call
    assert "x9" not in got                     # same number, another picture
    assert "own" not in got and "sheet" not in got
    assert set(got) == {"jpg", "dng", "x1"}
