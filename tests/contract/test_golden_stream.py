"""Golden streams: what /run, /products, the settings fingerprint and `bioscan eval` produce on the
fake-adapter Engine, recorded once and compared byte for byte, so a refactor of the service (the
plugin migration, docs/research/2026-09-24-plugin-architecture.md) cannot change a byte of output
unnoticed.

Normalised before comparing: every timing value (`timing_ms.*`, `done.elapsed_ms`) becomes 0.0, the
test's tmp folder becomes `<tmp>`, and a Python object address in an error message becomes `0x?`. Everything else is compared as the service wrote it.

The cases run in order on ONE engine and one client (chunk 2): the engine's loaded state is part of
what a stream reports (`engine.models.names`), so the order is part of the recording.

Re-record only on purpose (a reviewed output change): BIOSCAN_REGOLDEN=1 uv run pytest tests/contract/test_golden_stream.py
"""
import json
import os
import re
from pathlib import Path

import pytest
from conftest import FAIL_SIZE, Fakes, client_for, make_jpg

from bioscan import naming

GOLDEN = Path(__file__).parent / "golden"
REPO = str(Path(__file__).resolve().parents[2])
REGOLDEN = os.environ.get("BIOSCAN_REGOLDEN") == "1"
TMP = "<tmp>"


def _normalise_event(ev: dict) -> dict:
    if isinstance(ev.get("timing_ms"), dict):
        ev["timing_ms"] = dict.fromkeys(ev["timing_ms"], 0.0)
    if ev.get("type") == "done" and "elapsed_ms" in ev:
        ev["elapsed_ms"] = 0.0
    return ev


def normalise_stream(text: str, tmp: Path) -> str:
    """One NDJSON body with timings zeroed and the tmp folder replaced; serialised the way
    app.py serialises events (json.dumps, ensure_ascii=False), one per line."""
    text = re.sub(r"object at 0x[0-9a-f]+", "object at 0x?", text.replace(str(tmp), TMP))
    return "".join(json.dumps(_normalise_event(json.loads(line)), ensure_ascii=False) + "\n"
                   for line in text.splitlines() if line.strip())


def check(name: str, got: str) -> None:
    path = GOLDEN / name
    if REGOLDEN:
        path.parent.mkdir(exist_ok=True)
        path.write_text(got, encoding="utf-8")
    assert path.is_file(), f"no golden {path}; record with BIOSCAN_REGOLDEN=1"
    want = path.read_text(encoding="utf-8")
    assert got == want, f"{name} differs from its golden (diff the two; re-record only on purpose)"


def _photos(tmp: Path) -> dict[str, str]:
    (tmp / "in").mkdir()
    junk = tmp / "in" / "junk.jpg"
    junk.write_bytes(b"not an image")
    return {"a": make_jpg(tmp / "in" / "a.jpg"),
            "big": make_jpg(tmp / "in" / "big.jpg", (3000, 2000)),
            "c": make_jpg(tmp / "in" / "c.jpg", (40, 60)),
            "bad": make_jpg(tmp / "in" / "bad.jpg", FAIL_SIZE),
            "junk": str(junk), "missing": str(tmp / "in" / "missing.jpg")}


def _cases(p: dict[str, str], tmp: Path) -> list[tuple[str, dict]]:
    here = {"lat": 37.4, "lon": -122.1, "taken_at": "2026-05-01T08:00:00-07:00"}
    return [
        ("identify-defaults", {"inputs": [{"path": p["a"], **here}, {"path": p["big"]}, {"path": p["c"]}]}),
        ("identify-species-off", {"inputs": [{"path": p["a"]}, {"path": p["c"], **here}], "want": ["identify"],
                                  "options": {"identify": {"species": False}}}),
        ("identify-candidates", {"inputs": [{"path": p["a"], **here}],
                                 "options": {"identify": {"candidates": ["Megascops asio"], "top_k": 1}}}),
        ("identify-geo-off", {"inputs": [{"path": p["a"], **here}, {"path": p["big"], **here}],
                              "options": {"identify": {"geo": False, "top_k": 2}}}),
        ("identify-switches-off", {"inputs": [{"path": p["a"], **here}], "options": {"identify": {
            "range_veto": False, "kind_check": False, "mammal_geo": False}}}),
        ("embed", {"inputs": [{"path": p["a"]}, {"path": p["c"]}], "want": ["embed"]}),
        ("identify-embed-f16", {"inputs": [{"path": p["c"], **here}], "want": ["embed", "identify"],
                                "options": {"embed": {"format": "f16_base64"}}}),
        ("jpg", {"inputs": [{"path": p["a"]}, {"path": p["big"]}], "want": ["jpg"],
                 "options": {"jpg": {"out_dir": str(tmp / "out")}}}),
        ("all-products", {"inputs": [{"path": p["a"], **here}, {"path": p["big"]}, {"path": p["c"]}],
                          "want": ["jpg", "embed", "identify"], "options": {"jpg": {"out_dir": str(tmp / "out")}}}),
        ("errors", {"inputs": [{"path": p["a"]}, {"path": p["junk"]}, {"path": p["bad"]}, {"path": p["missing"]},
                               {"path": p["c"]}], "want": ["identify", "embed"]}),
    ]


REFUSED = [
    {"inputs": [{"path": "/x.jpg"}], "want": ["video"]},
    {"inputs": []},
    {"inputs": [{"path": "relative/x.jpg"}]},
    {"inputs": [{"path": "/x.jpg"}], "want": []},
    {"inputs": [{"path": "/x.jpg"}], "options": {"identify": {"top_k": "5"}}},
    {"inputs": [{"path": "/x.jpg"}], "options": {"identify": {"nope": 1}}},
    {"inputs": [{"path": "/x.jpg"}], "options": {"video": {}}},
    {"inputs": [{"path": "/x.jpg"}], "options": {"jpg": {"out_dir": "rel"}}},
    {"inputs": [{"path": "/x.jpg"}], "options": {"embed": {"format": "png"}}},
    {"inputs": [{"path": "/x.jpg"}], "options": {"identify": {"candidates": ["Nonexistus rex"]}}},
]


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    """Every case's normalised stream, the refusals and /health before and after, on one engine."""
    tmp = tmp_path_factory.mktemp("golden")
    photos = _photos(tmp)
    fakes = Fakes()
    out: dict[str, str] = {}
    with client_for(fakes.engine(), chunk=2) as c:
        health = [json.dumps(c.get("/health").json()) + "\n"]
        for name, body in _cases(photos, tmp):
            r = c.post("/run", json=body)
            assert r.status_code == 200, (name, r.text)
            out[name] = normalise_stream(r.text, tmp)
        refused = []
        for body in REFUSED:
            r = c.post("/run", json=body)
            refused.append(json.dumps({"status": r.status_code, "body": r.json()}, ensure_ascii=False) + "\n")
        out["refused"] = "".join(refused)
        health.append(json.dumps(c.get("/health").json()) + "\n")
        out["health"] = "".join(health)
        out["products"] = c.get("/products").text + "\n"
    out["_tmp"] = str(tmp)
    return out


CASES = [name for name, _ in _cases({k: "" for k in ("a", "big", "c", "bad", "junk", "missing")}, Path("/"))]


@pytest.mark.parametrize("name", CASES)
def test_run_stream_matches_golden(recorded, name):
    check(f"run-{name}.ndjson", recorded[name])


def test_species_off_on_a_fresh_engine_matches_golden(tmp_path):
    """Recorded after A2 (not at b02f189): species off on a fresh engine never loads BioCLIP, so
    the stream reports no name lists and /health no bioclip."""
    photos = _photos(tmp_path)
    with client_for(Fakes().engine(), chunk=2) as c:
        r = c.post("/run", json={"inputs": [{"path": photos["a"]}, {"path": photos["big"]}],
                                 "options": {"identify": {"species": False}}})
        health = json.dumps(c.get("/health").json()) + "\n"
    check("run-fresh-species-off.ndjson", normalise_stream(r.text, tmp_path) + health)


def test_refusals_match_golden(recorded):
    check("refused.ndjson", recorded["refused"])


def test_health_matches_golden(recorded):
    check("health.ndjson", recorded["health"])


def test_products_json_matches_golden(recorded):
    """GET /products byte for byte (the response body, not a re-serialisation). The A0 recording
    (identify, embed, jpg) is the unchanged start of today's body; stages added since (geotag) are
    appended after it and recorded in products-added.json."""
    a0 = (GOLDEN / "products.json").read_text(encoding="utf-8")
    body = recorded["products"]
    assert a0.endswith("}\n") and body.startswith(a0[:-2] + ","), "the A0 part of /products changed"
    check("products-added.json", "{" + body[len(a0) - 1:])


def test_settings_fingerprint_matches_golden():
    from bioscan.service import settings

    check("fingerprint.txt", settings.fingerprint() + "\n")


def test_eval_on_a_preds_file_matches_golden(recorded, tmp_path):
    """`bioscan eval --preds` and `bench report` over a preds file made from the defaults stream."""
    from bioscan.cli import bench
    from bioscan.cli import eval as ev

    stream = recorded["identify-defaults"].replace(TMP, recorded["_tmp"])
    events = [json.loads(line) for line in stream.splitlines()]
    tmp = recorded["_tmp"]
    preds = tmp_path / "preds.ndjson"
    meta = {"type": "meta", "schema": ev.PREDS_SCHEMA, "options": {"identify": {"top_k": 5, "geo": True}},
            "groundtruth": "gt.csv", "groundtruth_sha256": None, "synonyms_sha256": None, "created": "x"}
    preds.write_text("".join(json.dumps(e) + "\n" for e in [meta] + [e for e in events if e["type"] != "progress"]))
    gt = tmp_path / "gt.csv"
    gt.write_text("path,scientific,tier,lat,lon,taken_at,source,kind\n"
                  f"{tmp}/in/a.jpg,Megascops kennicottii,golden,37.4,-122.1,,t,bird\n"
                  f"{tmp}/in/big.jpg,Megascops asio,golden,,,,t,bird\n"
                  f"{tmp}/in/c.jpg,Canis latrans,golden,,,,t,mammal\n"
                  f"{tmp}/in/gone.jpg,Megascops asio,own,,,,t,bird\n")
    report, complete = ev.run_eval(str(gt), str(tmp_path / "out"), False, "http://unused", str(preds))
    assert complete
    lines = [ln for ln in report.replace(str(tmp_path), TMP).replace(tmp, TMP).replace(REPO, "<repo>").splitlines()
             if not ln.startswith(("- generated", "- wall_s"))]
    check("eval-report.md", "\n".join(lines) + "\n")
    lists = bench.load_name_lists({"bird": str(naming.AVILIST_MAP_CSV)})   # never data/mdd (gitignored, a developer's file)
    rep = bench.report_from_preds(str(preds), str(gt), tier="golden", lists=lists)
    scored = {k: rep[k] for k in ("metrics", "per_species", "per_family", "images")}
    check("bench-report.json", json.dumps(scored, indent=1, ensure_ascii=False).replace(tmp, TMP).replace(REPO, "<repo>") + "\n")
