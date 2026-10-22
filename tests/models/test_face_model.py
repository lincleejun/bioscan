"""Gardener people capability on the real YuNet + SFace weights (pinned in bioscan/gardener/faces.py).

Only runs with BIOSCAN_MODEL_TESTS=1 and the weights in the HF cache (tests/models/download.py). The
wildlife sample of tests/models/sample.csv has no people, so the face model must stay quiet on it:
well-formed output, and strong faces (the ones that start clusters) on at most a tenth of the photos (false positives on animals would put
wildlife into person groups)."""
import csv
import os
import urllib.request
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("BIOSCAN_MODEL_TESTS") != "1",
                                reason="real-model smoke: set BIOSCAN_MODEL_TESTS=1 (see tests/models/download.py)")

HERE = Path(__file__).resolve().parent
PHOTOS = Path(os.environ.get("BIOSCAN_PHOTO_CACHE", "~/.cache/bioscan/model-test-photos")).expanduser()
PHOTO_URL = "https://inaturalist-open-data.s3.amazonaws.com/photos/{pid}/medium.jpg"
N = 30


def photos() -> list[Path]:
    with open(HERE / "sample.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[:N]
    out = []
    for r in rows:
        path = PHOTOS / f"{r['observation']}_{r['photo_id']}.jpg"
        if not (path.is_file() and path.stat().st_size):
            PHOTOS.mkdir(parents=True, exist_ok=True)
            try:
                with urllib.request.urlopen(PHOTO_URL.format(pid=r["photo_id"]), timeout=30) as resp:
                    path.write_bytes(resp.read())
            except OSError:
                continue
        out.append(path)
    return out


def test_face_model_is_pinned_and_quiet_on_wildlife():
    from bioscan.gardener import faces

    model = faces.FaceModel()
    model.ensure()
    assert model.version == f"yunet@{faces.YUNET[2][:12]};sface@{faces.SFACE[2][:12]}"
    paths = photos()
    assert len(paths) >= N * 0.8, "too few sample photos could be fetched"
    with_faces = 0
    for p in paths:
        found = model.faces(p)
        for f in found:
            assert all(0.0 <= x <= 1.0 for x in f["box"]) and f["score"] >= faces.MIN_SCORE
            assert abs(float(np.linalg.norm(faces.decode(f["vec"]))) - 1.0) < 1e-3
        with_faces += any(faces.strong(f) for f in found)   # weak faces count only on people photos
    assert with_faces <= len(paths) / 10, f"faces on {with_faces} of {len(paths)} wildlife photos"
