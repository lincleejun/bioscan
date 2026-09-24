"""Model registry: device choice, lazy loading, the whole-frame pass. One instance per process.

app.py asks it for `ensure`, `loaded` and `device`, the run module (run.py) for `frame` and `info`;
identify (pipeline.py) reads its three model adapters and the data they need (`siglip2`, `owlv2`, `bioclip`, `names`, `priors`). The
adapters are built by `Loaders`: the real models in production, fixed-answer fakes in the contract
tests, so those tests run this class and the whole identify pipeline for real.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

import bioscan
from bioscan.service import products

log = logging.getLogger("bioscan.engine")

SIGLIP_BATCH = 32

# What each product needs loaded, from the product registry.
NEEDS = {name: p.needs for name, p in products.REGISTRY.items()}


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


def _load_species(device: str) -> tuple[Any, dict[str, Any]]:
    """load_species with each list's matrix already on the device, so a failure there is a load failure."""
    bioclip, lists = load_species(device)
    for nl in lists.values():
        bioclip.place(nl.matrix)
    return bioclip, lists


def _load_siglip2(device: str) -> Any:
    from bioscan.service.adapters.siglip2 import SigLIP2

    return SigLIP2(device)


def _load_owlv2(device: str) -> Any:
    from bioscan.service.adapters.owlv2 import OWLv2

    return OWLv2(device)


def _load_geo() -> Any:
    from bioscan.service.adapters.geo import GeoPrior

    return GeoPrior.load()


@dataclass(frozen=True)
class Loaders:
    """How each model adapter is built: the seam between bioscan and the models. Production uses
    the defaults (weights from the HF cache); the contract tests pass fakes with the same methods."""
    siglip2: Callable[[str], Any] = _load_siglip2       # device -> gate and crop check: embed_images, gate
    owlv2: Callable[[str], Any] = _load_owlv2           # device -> detector: detect, detect_batch
    species: Callable[[str], tuple[Any, dict[str, Any]]] = _load_species  # device -> (BioCLIP, {kind: NameList})
    geo: Callable[[], Any] = _load_geo                  # () -> location prior source (labels, probs) or None


# Model name -> loader (an Engine method). Order is load order and the order of `loaded()`.
MODELS = {"siglip2": lambda e: e._load_siglip2(), "owlv2": lambda e: e._load_owlv2(),
          "bioclip": lambda e: e._load_bioclip()}


class Engine:
    def __init__(self, device: str | None = None, loaders: Loaders | None = None) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")    # weights come from ~/.cache/huggingface only
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.device = device or pick_device()
        self.loaders = loaders or Loaders()
        self.siglip2: Any = None
        self.owlv2: Any = None
        self.bioclip: Any = None
        self.names: dict[str, Any] = {}
        self.priors: dict[str, Any] = {}      # kind -> geo.LocationPrior
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
        log.info("loading SigLIP2 on %s", self.device)
        self.siglip2 = self.loaders.siglip2(self.device)

    def _load_owlv2(self) -> None:
        log.info("loading OWLv2 on %s", self.device)
        self.owlv2 = self.loaders.owlv2(self.device)

    def _load_bioclip(self) -> None:
        """BioCLIP, the name lists in its text space and the location prior of each list that has one."""
        from bioscan.service.adapters import geo

        bioclip, self.names = self.loaders.species(self.device)
        self.priors = geo.priors_for(self.names, self.loaders.geo())
        log.info("location priors: %s", {k: b.name for k, b in self.priors.items()} or "unavailable")
        self.bioclip = bioclip

    @property
    def geo(self) -> Any:
        """The BirdNET prior model (what `bioscan names geo-gaps` needs), or None."""
        bird = self.priors.get("bird")
        return bird.source if bird is not None else None

    def info(self) -> dict[str, Any]:
        from bioscan.service import settings
        from bioscan.service.adapters import bioclip, owlv2, siglip2

        lists = {k: f"{nl.list_id}@{nl.sha}" for k, nl in self.names.items()}
        # every loaded name list, with its location prior's name or None (bird, mammal)
        priors = {k: b.name for k, b in self.priors.items()} | {k: None for k in self.names if k not in self.priors}
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
