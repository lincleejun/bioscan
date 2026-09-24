"""bioscan/aesthetic.py: head files (format, validation, sha), scoring and blending, the owner's
ratings from XMP (sidecar and embedded) and CSV, trip splits, and the ranking metrics."""
import io
import json
import math
import struct

import pytest
from aesthetic_helpers import random_head, unit, xmp
from PIL import Image

from bioscan import aesthetic as aes

# A Lightroom Classic sidecar as written for a RAW file (trimmed by hand): three stars, a red label.
LIGHTROOM_SIDECAR = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000 1.000000, 0000/00/00-00:00:00">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmp:ModifyDate="2026-05-01T09:12:44-07:00"
   xmp:Rating="3"
   xmp:Label="Red"
   tiff:Make="SONY"
   crs:Version="16.0">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


# ---- head files ------------------------------------------------------------------------------

def test_embedding_constant_matches_the_adapter():
    from bioscan.service.adapters import siglip2

    assert aes.EMBEDDING == f"{siglip2.MODEL_ID}@{siglip2.REVISION[:12]}"


def test_head_roundtrip_sha_and_validation(tmp_path):
    doc = random_head(1)
    path = aes.write_head(doc, tmp_path / "h.json")
    h = aes.load_head(path)
    assert h.sha == doc["sha"] == aes.canonical_sha(json.loads(path.read_text())) and len(h.sha) == 64
    assert h.id == f"test-head:{doc['sha'][:12]}"
    assert aes.write_head(random_head(1), tmp_path / "again.json").read_text() == path.read_text()   # deterministic
    assert random_head(2)["sha"] != doc["sha"]

    def broken(change, match):
        d = json.loads(path.read_text())
        change(d)
        with pytest.raises(aes.HeadError, match=match):
            aes.parse_head(d)

    broken(lambda d: d["weights"].__setitem__(0, d["weights"][0] + 1), "sha mismatch")      # edited weights
    broken(lambda d: d.update(format="x"), "not a bioscan-aesthetic-head")
    broken(lambda d: d.update(version=2), "head version 2")
    broken(lambda d: d.update(embedding="openai/clip@abc"), "fitted on 'openai/clip@abc'")
    broken(lambda d: d.update(dim=512), "dim 512")
    broken(lambda d: d.pop("provenance"), "missing provenance")
    broken(lambda d: d.update(weights=d["weights"][:10]), "weights must be 768")
    broken(lambda d: d["std"].__setitem__(3, 0.0), "std must be > 0")
    broken(lambda d: d.update(target={"lo": 5, "hi": 5}), "lo < hi")
    broken(lambda d: d["mean"].__setitem__(0, float("nan")), "finite")
    with pytest.raises(aes.HeadError, match="no such head file"):
        aes.load_head(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(aes.HeadError, match="cannot read head"):
        aes.load_head(tmp_path / "bad.json")


def test_predict_folds_and_scales():
    h = aes.parse_head(random_head(3, lo=0, hi=10))
    v = unit([math.sin(i) for i in range(aes.DIM)])
    raw = h.bias + sum(w * (x - m) / s for w, x, m, s in zip(h.weights, v, h.mean, h.std))
    a, c = h.folded()
    assert h.raw(v) == pytest.approx(raw) == pytest.approx(sum(ai * x for ai, x in zip(a, v)) + c)
    assert h.predict(v) == pytest.approx(raw / 10)


def test_blend_and_head_id():
    g, p = aes.parse_head(random_head(1, name="eva-head-v1")), aes.parse_head(random_head(2, name="me"))
    assert aes.blend(0.2, 0.6, 0.5) == pytest.approx(0.4) and aes.blend(0.2, 0.6, 1.0) == 0.6
    assert aes.blend(None, 0.6, 0.3) == 0.6 and aes.blend(0.2, None, 0.3) == 0.2 and aes.blend(None, None, 0.5) is None
    assert aes.head_id(g, None, 0.5) == g.id and aes.head_id(None, None, 0.5) is None
    assert aes.head_id(g, p, 0.25) == f"{g.id}+{p.id}~0.25"


def test_f16_vectors_roundtrip():
    v = [0.5, -0.25, 0.125, 1e-3]
    assert aes.f16_decode(aes.f16_encode(v)) == pytest.approx(v, rel=1e-3)
    assert aes.vector_of({"vector": [0.1, 0.2]}) == [0.1, 0.2]
    assert aes.vector_of({"vector": aes.f16_encode([0.5])}) == [0.5]


# ---- ratings: XMP and CSV ----------------------------------------------------------------------

def test_parse_xmp_forms():
    assert aes.parse_xmp(LIGHTROOM_SIDECAR) == {"rating": 3.0, "pick": 0, "label": "Red"}
    assert aes.parse_xmp(xmp(rating=5, element=True)) == {"rating": 5.0, "pick": 0, "label": ""}
    assert aes.parse_xmp(xmp(rating=2, pick=1)) == {"rating": 2.0, "pick": 1, "label": ""}
    assert aes.parse_xmp(xmp(rating=-1)) == {"rating": 0.0, "pick": -1, "label": ""}      # Lightroom/Bridge reject
    assert aes.parse_xmp(xmp(label="Green"))["rating"] is None                            # unrated
    assert aes.parse_xmp(xmp(rating=0, label="Red"))["rating"] is None                   # 0 = unrated (XMP spec)
    assert aes.parse_xmp(xmp(rating=0, pick=-1)) == {"rating": 0.0, "pick": -1, "label": ""}   # a reject flag
    assert aes.parse_xmp(xmp(rating=0, pick=1))["rating"] is None                        # a pick gives no grade


def test_stars_rule():
    assert aes.stars_of(3, 0) == (3.0, 0) and aes.stars_of(5, 1) == (5.0, 1)
    assert aes.stars_of(0, 0) is None and aes.stars_of(None, 0) is None and aes.stars_of(None, 1) is None
    assert aes.stars_of(-1, 0) == aes.stars_of(None, -1) == aes.stars_of(0, -1) == (aes.REJECT_GRADE, -1)
    assert aes.stars_of(2, -1) == (2.0, -1)          # stars and a reject flag: the stars stay, the flag too


def test_folder_rating_zero_is_skipped_and_reject_kept(tmp_path):
    """JPEGs with embedded xmp:Rating 0 yield no ratings; -1 is kept as a reject, apart from unrated."""
    for i in range(3):
        jpeg_with_xmp(tmp_path / f"zero{i}.jpg", xmp(rating=0))
    assert aes.ratings_from_folder(str(tmp_path)) == []
    rej = jpeg_with_xmp(tmp_path / "rejected.jpg", xmp(rating=-1))
    two = jpeg_with_xmp(tmp_path / "two.jpg", xmp(rating=2))
    rows = {r.path: r for r in aes.ratings_from_folder(str(tmp_path))}
    assert set(rows) == {rej, two}
    assert (rows[rej].rating, rows[rej].pick) == (aes.REJECT_GRADE, -1) and (rows[two].rating, rows[two].pick) == (2, 0)
    assert aes.picks_of(list(rows.values())) == [False, False]
    assert aes.parse_xmp("<x:xmpmeta><broken") is None and aes.parse_xmp("no packet") is None


def jpeg_with_xmp(path, packet: str):
    """A JPEG with the packet in an APP1 segment right after SOI, as Lightroom embeds it."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "JPEG")
    body = b"http://ns.adobe.com/xap/1.0/\x00" + packet.encode("utf-8")
    data = buf.getvalue()
    path.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + data[2:])
    return str(path)


def test_ratings_from_folder_sidecar_and_embedded(tmp_path):
    (tmp_path / "trip-a").mkdir()
    (tmp_path / "trip-b" / "day2").mkdir(parents=True)
    raw = tmp_path / "trip-a" / "DSC001.ARW"
    raw.write_bytes(b"not really a raw")
    (tmp_path / "trip-a" / "DSC001.xmp").write_text(LIGHTROOM_SIDECAR)                   # Lightroom's name
    raw2 = tmp_path / "trip-a" / "DSC002.ARW"
    raw2.write_bytes(b"x")
    (tmp_path / "trip-a" / "DSC002.ARW.xmp").write_text(xmp(rating=1))                    # darktable's name
    emb = jpeg_with_xmp(tmp_path / "trip-b" / "day2" / "b.jpg", xmp(rating=4, pick=1))
    jpeg_with_xmp(tmp_path / "trip-b" / "unrated.jpg", xmp(label="Blue"))                # no rating: skipped
    # a sidecar wins over the embedded packet
    both = jpeg_with_xmp(tmp_path / "top.jpg", xmp(rating=1))
    (tmp_path / "top.xmp").write_text(xmp(rating=5))
    rows = {r.path: r for r in aes.ratings_from_folder(str(tmp_path))}
    assert set(rows) == {str(raw), str(raw2), emb, both}
    assert (rows[str(raw)].rating, rows[str(raw)].label, rows[str(raw)].source, rows[str(raw)].trip) == \
        (3.0, "Red", "sidecar", "trip-a")
    assert rows[str(raw2)].rating == 1.0
    assert (rows[emb].rating, rows[emb].pick, rows[emb].source, rows[emb].trip) == (4.0, 1, "embedded", "trip-b")
    assert (rows[both].rating, rows[both].source, rows[both].trip) == (5.0, "sidecar", ".")


def test_ratings_from_csv(tmp_path):
    csv = tmp_path / "r.csv"
    csv.write_text("path,rating,pick,trip\nimg/a.jpg,4,1,\n/abs/b.jpg,-1,,t2\n/abs/c.jpg,,,t2\n"
                   "/abs/d.jpg,0,,t2\n/abs/e.jpg,0,-1,t2\n/abs/f.jpg,,1,t2\n")
    rows = aes.read_ratings(str(csv))
    # the XMP rule: 0 / blank = unrated (skipped), -1 or a reject flag = reject, a pick alone gives no grade
    assert [(r.path, r.rating, r.pick, r.trip) for r in rows] == [(str(tmp_path / "img" / "a.jpg"), 4.0, 1, "img"),
                                                                  ("/abs/b.jpg", 0.0, -1, "t2"),
                                                                  ("/abs/e.jpg", 0.0, -1, "t2")]
    (tmp_path / "bad.csv").write_text("file,stars\nx,1\n")
    with pytest.raises(ValueError, match="needs columns path,rating"):
        aes.read_ratings(str(tmp_path / "bad.csv"))


def test_picks_and_ratings_sha():
    rows = [aes.Rating("/a", 5), aes.Rating("/b", 3), aes.Rating("/c", 4)]
    assert aes.picks_of(rows) == [True, False, True]                      # rating >= 4 without explicit picks
    rows[1].pick = 1
    assert aes.picks_of(rows) == [False, True, False]                     # explicit picks win
    assert aes.ratings_sha(rows) == aes.ratings_sha(list(reversed(rows)))
    assert aes.ratings_sha(rows) != aes.ratings_sha(rows[:2])


# ---- splits ----------------------------------------------------------------------------------

def test_group_folds_never_split_a_trip():
    trips = [f"t{i % 7}" for i in range(200)] + ["big"] * 90
    folds = aes.group_folds(trips, 5, seed=3)
    assert len(folds) == len(trips) and set(folds) == set(range(5))
    for t in set(trips):
        assert len({f for f, g in zip(folds, trips) if g == t}) == 1, t        # a trip sits in one fold
    assert folds == aes.group_folds(trips, 5, seed=3)                         # seeded
    assert aes.group_folds(["a", "a", "b"], 5, seed=0) in ([0, 0, 1], [1, 1, 0])   # k capped at the trips
    assert aes.trip_of("/r/trip/x/y.jpg", "/r") == "trip" and aes.trip_of("/r/y.jpg", "/r") == "."


# ---- metrics ---------------------------------------------------------------------------------

def test_rank_metrics_on_known_values():
    assert aes.ranks([10, 20, 20, 5]) == [2, 3.5, 3.5, 1]
    assert aes.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1)
    assert aes.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1)
    assert aes.spearman([1, 2, 3], [5, 5, 5]) is None
    # scipy.stats.spearmanr([1,2,3,4,5],[2,1,4,3,5]) = 0.8; kendalltau (tau-b) = 0.6
    assert aes.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)
    assert aes.kendall([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.6)
    # tau-b with ties: scipy.stats.kendalltau([1,2,2,3],[1,2,3,3]) = 0.8
    assert aes.kendall([1, 2, 2, 3], [1, 2, 3, 3]) == pytest.approx(0.8)
    lo, hi = aes.rank_ci(0.5, 100)
    assert lo < 0.5 < hi and aes.rank_ci(0.5, 3) is None


def test_ndcg_and_hits():
    ratings = [3, 2, 3, 0, 1, 2]
    perfect = [6, 4, 5, 1, 2, 3]            # orders 3,3,2,2,1,0
    assert aes.ndcg_at_k(perfect, ratings, 6) == pytest.approx(1)
    # Wikipedia's DCG example: relevances 3,2,3,0,1,2 in this order -> nDCG@6 = 0.785 (gain rel, not 2^rel-1);
    # with gain 2^rel-1: DCG = 7 + 3/log2 3 + 7/2 + 0 + 1/log2 6 + 3/log2 7, IDCG from 3,3,2,2,1,0
    got = aes.ndcg_at_k([6, 5, 4, 3, 2, 1], ratings, 6)
    dcg = 7 + 3 / math.log2(3) + 7 / 2 + 0 + 1 / math.log2(6) + 3 / math.log2(7)
    idcg = 7 + 7 / math.log2(3) + 3 / 2 + 3 / math.log2(5) + 1 / math.log2(6)
    assert got == pytest.approx(dcg / idcg)
    assert aes.ndcg_at_k([1, 2], [0, 0], 2) is None
    picked = [True, False, True, False]
    assert aes.hits_at_k([0.9, 0.8, 0.1, 0.7], picked, 2) == 1
    assert aes.hits_at_k([0.5, 0.5, 0.5, 0.5], picked, 2, ["a", "b", "c", "d"]) == 1     # ties by path
