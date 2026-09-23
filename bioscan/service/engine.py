"""Model registry: device choice, lazy loading, batch sizes. One instance per process.

Everything the /run pipeline asks of the models goes through `frame`, `identify`, `ensure`,
`loaded` and `info` -- the contract tests swap this class for a fake with those five.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import numpy as np

import bioscan
from bioscan.service import products

log = logging.getLogger("bioscan.engine")

SIGLIP_BATCH = 32
OWLV2_BATCH = 8      # ponytail: OWLv2 runs one frame at a time today; batch across frames if it dominates
BIOCLIP_BATCH = 16

# What each product needs loaded.
NEEDS = {"identify": ("siglip2", "owlv2", "bioclip"), "embed": ("siglip2",), "jpg": ()}


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
        self.geo: Any = None
        self._matrices: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---- loading ----
    def loaded(self) -> list[str]:
        return [n for n in ("siglip2", "owlv2", "bioclip") if getattr(self, n) is not None]

    def ensure(self, want: list[str]) -> None:
        """Load whatever `want` needs that is not loaded yet. Raises on failure (-> 503)."""
        with self._lock:
            for name in {m for p in want for m in NEEDS[p]}:
                if getattr(self, name) is None:
                    getattr(self, f"_load_{name}")()

    def _load_siglip2(self) -> None:
        from bioscan.service.adapters.siglip2 import SigLIP2

        log.info("loading SigLIP2 on %s", self.device)
        self.siglip2 = SigLIP2(self.device)

    def _load_owlv2(self) -> None:
        from bioscan.service.adapters.owlv2 import OWLv2

        log.info("loading OWLv2 on %s", self.device)
        self.owlv2 = OWLv2(self.device)

    def _load_bioclip(self) -> None:
        from bioscan.service.adapters.geo import GeoPrior

        bioclip, self.names = load_species(self.device)
        self._matrices = {k: bioclip.torch.from_numpy(nl.matrix).to(self.device) for k, nl in self.names.items()}
        self.geo = GeoPrior.load()
        log.info("geo prior: %s", "birdnet geo 3.0" if self.geo else "unavailable")
        self.bioclip = bioclip

    def name_matrix(self, kind: str) -> Any:
        return self._matrices[kind]

    def info(self) -> dict[str, Any]:
        from bioscan.service.adapters import bioclip, owlv2, siglip2

        lists = {k: f"{nl.list_id}@{nl.sha}" for k, nl in self.names.items()}
        return {"version": bioscan.__version__,
                "models": {"gate": siglip2.MODEL_ID, "detect": f"{owlv2.MODEL_ID}@{owlv2.REVISION[:12]}",
                           "species": bioclip.MODEL_ID.removeprefix("hf-hub:"), "names": lists,
                           "geo": "birdnet-geo-3.0" if self.geo else None}}

    # ---- inference ----
    def frame(self, images: list[Any]) -> tuple[np.ndarray, list[dict[str, float]]]:
        """Whole-frame SigLIP2 vectors (n, 768) and gate probabilities; one forward serves both."""
        vecs = np.concatenate([self.siglip2.embed_images(images[i:i + SIGLIP_BATCH])
                               for i in range(0, len(images), SIGLIP_BATCH)])
        return vecs, self.siglip2.gate(vecs)

    def identify(self, image: Any, gate: dict[str, float], lat: float | None, lon: float | None,
                 taken_at: str | None, opts: dict[str, Any]) -> dict[str, Any]:
        return products.identify(self, image, gate, lat, lon, taken_at, opts)
