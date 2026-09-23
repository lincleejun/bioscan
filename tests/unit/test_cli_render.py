import json

from bioscan.cli.render import Renderer

OWL = {"scientific": "Megascops kennicottii", "common": "Western Screech-Owl",
       "taxonomy": ["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", "Megascops kennicottii"],
       "p_visual": 0.81, "p_geo": 0.62, "posterior": 0.91}
CARIBOU = {"scientific": "Rangifer tarandus", "common": "",
           "taxonomy": ["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Rangifer", "Rangifer tarandus"],
           "p_visual": 0.77, "p_geo": None, "posterior": 0.77}


def box(i, kind, level, top, lst="avilist-2025"):
    return {"id": i, "xyxy": [0.1, 0.1, 0.5, 0.5], "score": 0.84, "kind": kind,
            "quality": {"sharpness": 0.7, "exposure": 0.05},
            "species": {"list": lst, "level": level, "top": top}}


def result(path, cls, boxes):
    return {"type": "result", "path": path, "sha256": "x", "image": {"width": 6000, "height": 4000, "orientation": 1},
            "engine": {"version": "0.1.0", "models": {}},
            "products": {"identify": {"gate": {"class": cls, "probs": {}}, "boxes": boxes}},
            "timing_ms": {"decode": 300, "identify": 200}}


# The spec section 7 example stream.
STREAM = [
    {"type": "progress", "product": "identify", "done": 1, "total": 4},
    result("/v/DSC00364.ARW", "bird", [box(0, "bird", "species", [OWL]), box(1, "bird", "unconfirmed", [OWL])]),
    result("/v/DSC00458.ARW", "none", []),
    result("/v/DSC00566.ARW", "mammal", [box(0, "mammal", "species", [CARIBOU], "mdd-2025")]),
    {"type": "error", "path": "/v/bad.ARW", "product": None, "message": "LibRaw: corrupt"},
    {"type": "done", "ok": 3, "failed": 1, "elapsed_ms": 4000},
]


def render(stream):
    r = Renderer()
    # round-trip through JSON like the real NDJSON stream
    lines = [x for x in (r.feed(json.loads(json.dumps(e))) for e in stream) if x is not None]
    return lines, r.summary()


def test_pretty_lines_match_spec_examples():
    lines, _ = render(STREAM)
    assert lines[0] == "DSC00364.ARW  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] unconfirmed"
    assert lines[1] == "DSC00458.ARW  none"
    assert lines[2] == "DSC00566.ARW  mammal  1 box    [1] Rangifer tarandus 0.77 种"
    assert lines[3] == "bad.ARW  ERROR [decode] LibRaw: corrupt"
    assert len(lines) == 4  # progress/done print nothing


def test_summary():
    _, s = render(STREAM)
    assert "   1  Western Screech-Owl" in s
    assert "   1  Rangifer tarandus" in s
    assert "unconfirmed boxes: 1" in s
    assert "images: 3 ok, 1 failed" in s
    assert "/v/bad.ARW: decode: LibRaw: corrupt" in s
    assert "4.0s total, 1000 ms/image" in s


def test_genus_level_sums_top_mass_and_other_animal():
    a = dict(OWL, posterior=0.4)
    b = dict(OWL, scientific="Megascops asio", posterior=0.3,
             taxonomy=OWL["taxonomy"][:6] + ["Megascops asio"])
    ev = result("/v/x.jpg", "bird", [box(0, "bird", "genus", [a, b])])
    ev["products"]["identify"]["boxes"].append({"id": 1, "xyxy": [0, 0, 1, 1], "score": 0.5, "kind": "other_animal", "species": None})
    lines, _ = render([ev])
    assert lines[0] == "x.jpg  bird    2 boxes  [1] Megascops 0.70 属  [2] other_animal 0.50"
