"""Model registry: device choice, lazy loading, batch sizes. One instance per process.

Everything the /run pipeline asks of the models goes through `frame`, `identify`, `ensure`,
`loaded` and `info` -- the contract tests swap this class for a fake with those five.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Protocol, runtime_checkable

import numpy as np

import bioscan
from bioscan.service import pipeline, products

log = logging.getLogger("bioscan.engine")

SIGLIP_BATCH = 32
BIOCLIP_BATCH = 16

# What each product needs loaded, from the product registry.
NEEDS = {name: p.needs for name, p in products.REGISTRY.items()}


@runtime_checkable
class EngineProtocol(Protocol):
    """What app.py needs from an engine. Engine implements it with real models; the contract
    tests' FakeEngine implements it with fixed answers; tests/unit/test_engine_protocol.py keeps
    both in step (names and signatures)."""

    device: str

    def loaded(self) -> list[str]: ...

    def ensure(self, want: list[str]) -> None: ...

    def info(self) -> dict[str, Any]: ...

    def frame(self, images: list[Any]) -> tuple[np.ndarray, list[dict[str, float]]]: ...

    def identify_many(self, frames: list[Any], opts: dict[str, Any]) -> list[Any]: ...


def pick_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def load_species(device: str) -> tuple[Any, dict[str, Any]]:
    """BioCLIP 2.5 Huge plus the name lists (AviList birds, MDD mammals) in its text space."""
    from bioscan.service import names
    from bioscan.service.adapters.bioclip import BioCLIP

    log.info("loading BioCLIP 2.5 Huge on %s", device)
    bioclip = BioCLIP(device)
    lists = names.load_lists(bioclip.model, bioclip.tokenizer, device)
    return bioclip, lists


# Model name -> loader (an Engine method). Order is load order and the order of `loaded()`.
MODELS = {"siglip2": lambda e: e._load_siglip2(), "owlv2": lambda e: e._load_owlv2(),
          "bioclip": lambda e: e._load_bioclip()}


class Engine:
    BIOCLIP_BATCH = BIOCLIP_BATCH

    def __init__(self, device: str | None = None) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")    # weights come from ~/.cache/huggingface only
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.device = device or pick_device()
        self.siglip2: Any = None
        self.owlv2: Any = None
        self.bioclip: Any = None
        self.names: dict[str, Any] = {}
        self.priors: dict[str, Any] = {}      # kind -> geo.PriorBinding
        self._matrices: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---- loading ----
    def loaded(self) -> list[str]:
        return [n for n in MODELS if getattr(self, n) is not None]

    def ensure(self, want: list[str]) -> None:
        """Load whatever `want` needs that is not loaded yet. Raises on failure (-> 503)."""
        with self._lock:
            for name in MODELS:                       # registry order: gate, detector, species
                if name in {m for p in want for m in NEEDS[p]} and getattr(self, name) is None:
                    MODELS[name](self)

    def _load_siglip2(self) -> None:
        from bioscan.service.adapters.siglip2 import SigLIP2

        log.info("loading SigLIP2 on %s", self.device)
        self.siglip2 = SigLIP2(self.device)

    def _load_owlv2(self) -> None:
        from bioscan.service.adapters.owlv2 import OWLv2

        log.info("loading OWLv2 on %s", self.device)
        self.owlv2 = OWLv2(self.device)

    def _load_bioclip(self) -> None:
        """BioCLIP, the name lists in its text space and the location prior of each list that has one."""
        from bioscan.service.adapters.geo import GeoPrior, PriorBinding

        bioclip, self.names = load_species(self.device)
        self._matrices = {k: bioclip.torch.from_numpy(nl.matrix).to(self.device) for k, nl in self.names.items()}
        birdnet = GeoPrior.load()
        self.priors = {k: PriorBinding(birdnet, birdnet.index(nl.birdnet)) for k, nl in self.names.items()
                       if birdnet is not None and nl.birdnet}
        log.info("location priors: %s", {k: b.name for k, b in self.priors.items()} or "unavailable")
        self.bioclip = bioclip

    @property
    def geo(self) -> Any:
        """The BirdNET prior model (what `bioscan names geo-gaps` needs), or None."""
        bird = self.priors.get("bird")
        return bird.model if bird is not None else None

    def name_matrix(self, kind: str) -> Any:
        return self._matrices[kind]

    def info(self) -> dict[str, Any]:
        from bioscan.service import settings
        from bioscan.service.adapters import bioclip, owlv2, siglip2

        lists = {k: f"{nl.list_id}@{nl.sha}" for k, nl in self.names.items()}
        priors = {k: b.name for k, b in self.priors.items()}
        return {"version": bioscan.__version__, "settings": settings.fingerprint(),
                "models": {"gate": f"{siglip2.MODEL_ID}@{siglip2.REVISION[:12]}",
                           "detect": f"{owlv2.MODEL_ID}@{owlv2.REVISION[:12]}",
                           "species": f"{bioclip.MODEL_ID.removeprefix('hf-hub:')}@{bioclip.REVISION[:12]}",
                           "names": lists,
                           "geo": priors.get("bird"), "priors": priors}}

    # ---- inference ----
    def frame(self, images: list[Any]) -> tuple[np.ndarray, list[dict[str, float]]]:
        """Whole-frame SigLIP2 vectors (n, 768) and gate probabilities; one forward serves both."""
        vecs = np.concatenate([self.siglip2.embed_images(images[i:i + SIGLIP_BATCH])
                               for i in range(0, len(images), SIGLIP_BATCH)])
        return vecs, self.siglip2.gate(vecs)

    def identify_many(self, frames: list[Any], opts: dict[str, Any]) -> list[Any]:
        """pipeline.Frame list -> identify output (or the Exception it raised) per frame."""
        return pipeline.identify_many(self, frames, opts)

    def identify(self, image: Any, gate: dict[str, float], lat: float | None, lon: float | None,
                 taken_at: str | None, opts: dict[str, Any], detail: Any = None) -> dict[str, Any]:
        """One frame (convenience; the service calls identify_many)."""
        return pipeline.identify(self, image, gate, lat, lon, taken_at, opts, detail)
