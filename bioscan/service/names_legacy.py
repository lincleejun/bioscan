"""Stand-in for names.py until goal N lands: PhotoOS's 11045 bird species, same interface.

Source: HF dataset imageomics/TreeOfLife-200M `embeddings/txt_emb_species.json` (the list PhotoOS's
tools/prepare_bioclip_huge.py filtered to Aves). Text vectors are encoded with the BioCLIP 2.5 Huge
text tower using PhotoOS's prompt and cached per model + list hash. No mammal list yet, so
`load_lists` returns only "bird".
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

LIST_ID = "tol200m-birds-legacy"
MODEL_NAME = "bioclip-2.5-vith14"
SOURCE = ("imageomics/TreeOfLife-200M", "embeddings/txt_emb_species.json")


@dataclass
class NameList:
    list_id: str
    kind: str
    scientific: list[str]
    common: list[str]
    taxonomy: list[list[str]]     # 7 levels; the last is the binomial
    matrix: np.ndarray            # (N, 1024) float32, L2-normalised
    sha: str = ""


def birds_from_tol(raw: list[Any]) -> list[tuple[list[str], str]]:
    """TreeOfLife [[7 taxa], common] rows -> sorted unique Aves species (taxonomy with binomial last, common)."""
    out: dict[str, tuple[list[str], str]] = {}
    for taxa, common in raw:
        if len(taxa) < 7 or taxa[2] != "Aves" or not all(taxa[i] for i in (3, 4, 5, 6)):
            continue
        if not re.fullmatch(r"[a-z][a-z-]+", taxa[6]):
            continue
        scientific = f"{taxa[5]} {taxa[6]}"
        out.setdefault(scientific, ([*taxa[:6], scientific], common or ""))
    return [out[k] for k in sorted(out)]


def prompt(taxonomy: list[str], common: str) -> str:
    return f"a photo of {common or taxonomy[6]}, {taxonomy[6]}, a bird in the taxonomic family {taxonomy[4]}"


def encode(model: Any, tokenizer: Any, device: str | None, texts: list[str], batch: int = 256) -> np.ndarray:
    import torch

    chunks = []
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            f = model.encode_text(tokenizer(texts[i:i + batch]).to(device))
            chunks.append((f / f.norm(dim=-1, keepdim=True)).float().cpu().numpy())
    return np.concatenate(chunks).astype(np.float32)


def load_lists(model: Any, tokenizer: Any, device: str | None,
               cache_dir: Path = Path("~/.cache/bioscan/names")) -> dict[str, NameList]:
    """`model`/`tokenizer` are the open_clip BioCLIP 2.5 Huge model and tokenizer."""
    from huggingface_hub import hf_hub_download

    rows = birds_from_tol(json.loads(Path(hf_hub_download(SOURCE[0], SOURCE[1], repo_type="dataset")).read_text()))
    texts = [prompt(t, c) for t, c in rows]
    sha = hashlib.sha256("\n".join(texts).encode()).hexdigest()[:16]
    cache = Path(cache_dir).expanduser() / f"{MODEL_NAME}-{sha}.npz"
    if cache.is_file():
        matrix = np.load(cache)["matrix"]
    else:
        matrix = encode(model, tokenizer, device, texts)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, matrix=matrix)
    return {"bird": NameList(LIST_ID, "bird", [t[6] for t, _ in rows], [c for _, c in rows],
                             [t for t, _ in rows], matrix, sha)}


def stats(lists: dict[str, NameList]) -> dict[str, Any]:
    return {kind: {"list": nl.list_id, "total": len(nl.scientific), "official": 0, "encoded": len(nl.scientific)}
            for kind, nl in lists.items()}
