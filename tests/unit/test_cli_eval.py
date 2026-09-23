import json

from bioscan.cli import eval as ev


def top(*names):
    return [{"scientific": n, "posterior": 0.9 - i * 0.1} for i, n in enumerate(names)]


def res(path, cls, boxes, decode=100, ident=50):
    return {"type": "result", "path": path, "timing_ms": {"decode": decode, "identify": ident},
            "products": {"identify": {"gate": {"class": cls}, "boxes": boxes}}}


def box(kind, score, level, names):
    return {"kind": kind, "score": score, "species": {"level": level, "top": top(*names)}}


GT = [
    {"path": "/1", "scientific": "Buteo jamaicensis", "tier": "own", "kind": "bird"},
    {"path": "/2", "scientific": "Buteo jamaicensis", "tier": "own", "kind": "bird"},
    {"path": "/3", "scientific": "Cyanocitta stelleri", "tier": "own", "kind": "bird"},
    {"path": "/4", "scientific": "Cyanocitta stelleri", "tier": "own", "kind": "bird"},
    {"path": "/5", "scientific": "Megascops kennicottii", "tier": "own", "kind": "bird"},
    {"path": "/6", "scientific": "Canis latrans", "tier": "inat", "kind": "mammal"},
]
PREDS = [
    # 1: top-1 hit at species level (best box is the higher-score one)
    res("/1", "bird", [box("bird", 0.3, "species", ["Corvus corax"]),
                       box("bird", 0.9, "species", ["buteo  jamaicensis", "Buteo lineatus"])]),
    # 2: top-5 only, species level -> counts against precision
    res("/2", "bird", [box("bird", 0.8, "species", ["Buteo lineatus", "Buteo jamaicensis"])]),
    # 3: unconfirmed but top-1 right
    res("/3", "bird", [box("bird", 0.7, "unconfirmed", ["Cyanocitta stelleri"])]),
    # 4: gate wrong, no boxes
    res("/4", "mammal", []),
    # 5: decode error
    {"type": "error", "path": "/5", "product": None, "message": "boom"},
    # 6: mammal, wrong at species
    res("/6", "mammal", [box("mammal", 0.6, "species", ["Urocyon cinereoargenteus", "Canis latrans"])], decode=200, ident=80),
    {"type": "done", "ok": 5, "failed": 1, "elapsed_ms": 1},
]


def metrics():
    preds = ev.load_preds(json.dumps(p) for p in PREDS)
    return ev.compute(GT, preds)


def test_top1_top5_coverage_precision():
    m = metrics()
    bird = m[("own", "bird")]
    assert bird["n"] == 5 and bird["failed"] == 1
    assert bird["gate_acc"] == 3 / 5
    assert bird["detect_rate"] == 3 / 5
    assert bird["top1"] == 2 / 5  # /1 and /3
    assert bird["top5"] == 3 / 5  # + /2
    assert bird["coverage"] == 2 / 5  # /1 and /2 at species level
    assert bird["precision"] == 1 / 2
    assert bird["decode_ms"] == (100, 100)
    mam = m[("inat", "mammal")]
    assert (mam["top1"], mam["top5"], mam["precision"]) == (0, 1, 0)
    assert mam["identify_ms"] == (80, 80)


def test_confusion_top10():
    bird = metrics()[("own", "bird")]
    assert set(bird["confusion"]) == {
        ("Buteo jamaicensis", "Buteo lineatus", 1),
        ("Cyanocitta stelleri", "(no box)", 1),
        ("Megascops kennicottii", "(error)", 1),
    }
    gt = [{"path": f"/{i}", "scientific": f"S{i % 12}", "tier": "t", "kind": "bird"} for i in range(40)]
    preds = {r["path"]: res(r["path"], "bird", [box("bird", 1, "species", ["X"])]) for r in gt}
    conf = ev.compute(gt, preds)[("t", "bird")]["confusion"]
    assert len(conf) == 10 and conf[0][2] == 4  # S0..S3 appear 4 times, rest 3


def test_result_beats_error_and_missing_prediction():
    preds = ev.load_preds([json.dumps({"type": "error", "path": "/1", "product": "embed", "message": "x"}),
                           json.dumps(PREDS[0]), ""])
    assert preds["/1"]["type"] == "result"
    m = ev.compute(GT[:2], preds)[("own", "bird")]
    assert m["top1"] == 0.5 and m["failed"] == 1


def test_report_md_has_all_sections(tmp_path):
    md = ev.report_md(metrics(), {"geo": True})
    for s in ("| own | bird | 5 | 1 | 60.0% | 60.0% | 40.0% | 60.0% | 40.0% | 50.0% |",
              "| inat | mammal |", "Confusion Top-10 — own / bird", "| Canis latrans | Urocyon cinereoargenteus | 1 |"):
        assert s in md


def test_run_eval_from_existing_preds(tmp_path):
    gt_csv = tmp_path / "gt.csv"
    gt_csv.write_text("path,scientific,tier,lat,lon,taken_at,source,kind\n" +
                      "".join(f"{r['path']},{r['scientific']},{r['tier']},,,,,{r['kind']}\n" for r in GT))
    preds = tmp_path / "p.ndjson"
    preds.write_text("".join(json.dumps(p) + "\n" for p in PREDS))
    ev.run_eval(str(gt_csv), str(tmp_path / "out"), False, "http://127.0.0.1:1", str(preds))
    assert "| own | bird | 5 |" in (tmp_path / "out" / "report.md").read_text()


def test_eval_service_down_is_clear(tmp_path):
    import pytest
    from bioscan.cli import client
    gt_csv = tmp_path / "gt.csv"
    gt_csv.write_text("path,scientific,tier,kind\n/1,A b,own,bird\n")
    with pytest.raises(client.ServiceError, match="cannot reach"):
        ev.run_eval(str(gt_csv), str(tmp_path / "out"), False, "http://127.0.0.1:1")


def test_truth_synonyms_normalised_and_counted(tmp_path):
    syn = [{"avilist_scientific": "Pica nuttallii", "alias": "Pica nuttalli", "source": "spelling"},
           {"avilist_scientific": "Circus hudsonius", "alias": "Circus cyaneus", "source": "inat"},
           {"avilist_scientific": "Tyto furcata", "alias": "Tyto alba", "source": "birdnet"}]   # not a truth source
    gt = [{"path": "/m", "scientific": "Pica nuttalli", "tier": "inat", "kind": "bird"},
          {"path": "/h", "scientific": "Circus cyaneus", "tier": "inat", "kind": "bird"},
          {"path": "/t", "scientific": "Tyto alba", "tier": "inat", "kind": "bird"},
          {"path": "/r", "scientific": "Buteo jamaicensis", "tier": "inat", "kind": "bird"}]
    preds = {"/m": res("/m", "bird", [box("bird", 1, "species", ["Pica nuttallii"])]),
             "/h": res("/h", "bird", [box("bird", 1, "species", ["Circus hudsonius"])]),
             "/t": res("/t", "bird", [box("bird", 1, "species", ["Tyto furcata"])]),
             "/r": res("/r", "bird", [box("bird", 1, "species", ["Buteo jamaicensis"])])}
    rows = ev.normalise_truth(gt, syn)
    assert [r["scientific"] for r in rows] == ["Pica nuttallii", "Circus hudsonius", "Tyto alba", "Buteo jamaicensis"]
    assert rows[0]["scientific_raw"] == "Pica nuttalli"
    on, off = ev.compute(rows, preds)[("inat", "bird")], ev.compute(gt, preds)[("inat", "bird")]
    assert (on["top1"], on["synonym_hits"]) == (3 / 4, 2)
    assert (off["top1"], off["synonym_hits"]) == (1 / 4, 0)
    assert "Top-1 hits gained by synonym normalisation of the truth (miss -> hit): inat/bird 2" in ev.report_md({("inat", "bird"): on}, {})
