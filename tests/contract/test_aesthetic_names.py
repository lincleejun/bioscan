"""English names in `bioscan aesthetic score --species` exports: the real identify pipeline on fake adapters
names one photo to species, one to genus and one to family, and the CSV and page carry an English name
beside each Latin one (#36)."""
import csv
import json

import numpy as np
from conftest import FakeOWLv2, Fakes, FakeSigLIP2, client_for, events
from PIL import Image

from bioscan.cli import aesscore as sc
from bioscan.service.engine import Engine, Loaders
from bioscan.service.names import NameList

BIRDS = [("Spinus psaltria", "Lesser Goldfinch", "Fringillidae"), ("Vireo gilvus", "Warbling Vireo", "Vireonidae"),
         ("Vireo olivaceus", "Red-eyed Vireo", "Vireonidae"), ("Buteo jamaicensis", "Red-tailed Hawk", "Accipitridae"),
         ("Aquila chrysaetos", "Golden Eagle", "Accipitridae"),
         ("Haliaeetus leucocephalus", "Bald Eagle", "Accipitridae"), ("Accipiter cooperii", "Cooper's Hawk", "Accipitridae")]
PROBS = [[0.94, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],     # red photo: the goldfinch, clearly
         [0.02, 0.45, 0.45, 0.02, 0.02, 0.02, 0.02],     # green photo: one of two vireos
         [0.01, 0.01, 0.01, 0.3, 0.25, 0.22, 0.2]]       # blue photo: some hawk or eagle


class ByColour:
    """Species: the answer for a crop is the PROBS row of its strongest colour channel."""

    def encode_images(self, images):
        return np.array([np.asarray(im.convert("RGB"), np.float32).mean(axis=(0, 1)) for im in images])

    def probs(self, features, matrix):
        return np.array([PROBS[int(np.argmax(f))] for f in features])

    def logits(self, features, matrix):
        return np.log(self.probs(features, matrix))


def bird_list():
    return NameList(list_id="fake-birds", kind="bird", scientific=[s for s, _, _ in BIRDS], common=[c for _, c, _ in BIRDS],
                    taxonomy=[["Animalia", "Chordata", "Aves", "X", f, s.split()[0], s] for s, _, f in BIRDS],
                    matrix=np.zeros((len(BIRDS), 4), np.float32), tol_how=["exact"] * len(BIRDS), sha="test")


def test_species_genus_and_family_rows_carry_english_names(tmp_path):
    fakes = Fakes()
    engine = Engine("cpu", Loaders(siglip2=lambda device: FakeSigLIP2(fakes), owlv2=lambda device: FakeOWLv2(fakes),
                                   species=lambda device: (ByColour(), {"bird": bird_list()}), geo=lambda: None))
    paths = []
    for name, rgb in (("goldfinch", (200, 20, 20)), ("vireo", (20, 200, 20)), ("hawk", (20, 20, 200))):
        Image.new("RGB", (64, 48), rgb).save(tmp_path / f"{name}.jpg")
        paths.append(str(tmp_path / f"{name}.jpg"))
    with client_for(engine) as c:
        evs = events(c.post("/run", json={"inputs": [{"path": p} for p in paths], "want": ["identify"],
                                          "options": {"identify": {"species": True, "geo": False}}}))
    rows = sc.rows_of(evs)
    sc.write_csv(rows, [], str(tmp_path / "aes.csv"))
    sc.write_html(rows, [], str(tmp_path / "aes.html"), "t")
    with open(tmp_path / "aes.csv") as f:
        got = {r["path"].rsplit("/", 1)[1]: (r["species"], r["common"], r["level"]) for r in csv.DictReader(f)}
    assert got == {"goldfinch.jpg": ("Spinus psaltria", "Lesser Goldfinch", "species"),
                   "vireo.jpg": ("Vireo", "a vireo", "genus"),
                   "hawk.jpg": ("Accipitridae", "a hawk or eagle", "family")}
    page = (tmp_path / "aes.html").read_text()
    # the page's taxon tree and cards read the English name from DATA (no species <select> since the redesign)
    data = json.loads(page.split("const DATA=", 1)[1].split(";const ROOT", 1)[0])
    assert {(d["sp"], d["cn"], d["lv"]) for d in data} == set(got.values())
