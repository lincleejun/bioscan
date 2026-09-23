"""The adapter seam: the contract tests' fake adapters expose the real adapters' methods with the
same parameters, and an Engine built from them is what app.py and identify (pipeline.Models) need,
so the fast contract tests exercise what production calls."""
import dataclasses
import inspect
import sys
from pathlib import Path

import pytest

from bioscan.service import pipeline
from bioscan.service.adapters.bioclip import BioCLIP
from bioscan.service.adapters.geo import GeoPrior
from bioscan.service.adapters.owlv2 import OWLv2
from bioscan.service.adapters.siglip2 import SigLIP2
from bioscan.service.engine import MODELS, Engine, Loaders

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
import conftest  # noqa: E402
from conftest import FakeBioCLIP, FakeGeo, FakeOWLv2, Fakes, FakeSigLIP2  # noqa: E402

# adapter -> the methods Engine and identify call on it
PAIRS = [(FakeSigLIP2, SigLIP2, ["embed_images", "gate"]), (FakeOWLv2, OWLv2, ["detect", "detect_batch"]),
         (FakeBioCLIP, BioCLIP, ["encode_images", "probs"]), (FakeGeo, GeoPrior, ["probs"])]


@pytest.mark.parametrize("fake, real, methods", PAIRS, ids=[r.__name__ for _, r, _ in PAIRS])
def test_fakes_have_the_real_adapters_methods(fake, real, methods):
    for name in methods:
        want = list(inspect.signature(getattr(real, name)).parameters)
        have = list(inspect.signature(getattr(fake, name)).parameters)
        assert have == want, f"{fake.__name__}.{name}{have} != {real.__name__}.{name}{want}"


def test_one_loader_per_adapter():
    assert [f.name for f in dataclasses.fields(Loaders)] == ["siglip2", "owlv2", "species", "geo"]
    assert list(MODELS) == ["siglip2", "owlv2", "bioclip"]


def test_constructor_loads_nothing():
    e = Engine(device="cpu")                  # production loaders; nothing is built until ensure()
    assert e.loaded() == [] and (e.siglip2, e.owlv2, e.bioclip, e.names, e.priors) == (None, None, None, {}, {})
    assert e.loaders == Loaders()


def test_production_species_loader_places_name_lists_at_load(monkeypatch):
    """The name lists go to the device while loading, so a failure there fails ensure() (-> 503)
    instead of every later identify."""
    from bioscan.service import engine as engine_mod

    fakes = Fakes()
    placed = []

    class Bio(FakeBioCLIP):
        def place(self, matrix):
            placed.append(matrix.shape)
            if fail:
                raise RuntimeError("MPS out of memory")

    monkeypatch.setattr(engine_mod, "load_species", lambda device: (Bio(fakes), {"bird": conftest.owl_list()}))
    loaders = dataclasses.replace(fakes.engine().loaders, species=Loaders().species)
    fail = True
    e = Engine("cpu", loaders)
    with pytest.raises(RuntimeError, match="out of memory"):
        e.ensure(["identify"])
    assert e.loaded() == ["siglip2", "owlv2"]            # species not marked loaded: the next request retries
    fail = False
    e.ensure(["identify"])
    assert e.loaded() == ["siglip2", "owlv2", "bioclip"] and placed == [(2, 4), (2, 4)]


def test_loaded_engine_meets_app_and_identify():
    e = Fakes().engine()
    e.ensure(["embed"])
    assert e.loaded() == ["siglip2"]          # only what the products need
    e.ensure(["identify"])
    assert e.loaded() == ["siglip2", "owlv2", "bioclip"] and e.device == "cpu"
    assert all(callable(getattr(e, m)) for m in ("loaded", "ensure", "info", "frame"))     # what app.py calls
    seam = [n for n in pipeline.Models.__annotations__]
    assert sorted(seam) == ["bioclip", "names", "owlv2", "priors", "siglip2"]
    assert all(getattr(e, n) for n in seam)   # every adapter and every datum identify reads is there
    assert set(e.priors) == {"bird"} and e.geo is not None
