"""SigLIP2: the whole-frame gate, the crop gate and the embed vector (migrated from PhotoOS
content_local.py + scan/gate.py, manifest/envelope wrapping removed).

SigLIP2, not BioCLIP, for the gate: on 40 certain bird crops BioCLIP's text side called 20 % of
them fish or insects; SigLIP2 passed all of them. Prompts are averaged per class. The "none"
class names what used to be called a mammal before it existed: bark, cliffs, glaciers, statues.
"""
from __future__ import annotations

from typing import Any

import numpy as np

MODEL_ID = "google/siglip2-base-patch16-224"
MODEL_NAME = "siglip2-base-patch16-224"

# Order matters: softmax_gate zips it onto the class matrix rows.
GATE_CLASSES = ("bird", "mammal", "other_animal", "person", "none")
GATE_PROMPTS: dict[str, list[str]] = {
    "bird": ["a photo of a bird.", "a bird perched on a branch.", "a bird flying.", "a small bird.",
             "a duck, gull, hawk, owl or songbird."],
    "mammal": ["a photo of a mammal.", "a deer, bear, seal, whale, squirrel or fox.", "a wild mammal.", "a dog or cat."],
    "other_animal": ["a photo of a reptile or amphibian.", "a lizard, snake, turtle or frog.", "a fish.",
                     "an insect, butterfly or spider."],
    "person": ["a photo of a person.", "a person standing.", "people walking.", "a portrait of a man or woman.",
               "a painting of a person."],
    "none": ["a landscape with mountains, sea or sky.", "a sunset.", "leaves, trees or flowers.",
             "a building or street.", "a plate of food.", "an object or artifact.", "snow and ice.",
             "a photo with no animal in it.", "a tree trunk, bark or forest.", "a rock face, cliff or boulder.",
             "a glacier, snow or ice field.", "a painting, mural or artwork.", "a statue or sculpture.",
             "a city, street or building."],
}


def softmax_gate(vecs: np.ndarray, class_matrix: np.ndarray, scale: float) -> list[dict[str, float]]:
    """(n, d) L2-normalised vectors -> one {class: prob} per row."""
    logits = scale * (np.atleast_2d(vecs) @ class_matrix.T)
    logits = logits - logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p = p / p.sum(axis=1, keepdims=True)
    return [{name: float(v) for name, v in zip(GATE_CLASSES, row)} for row in p]


def _pooled(output: Any) -> Any:
    """transformers>=5 returns BaseModelOutputWithPooling from get_*_features."""
    pooled = getattr(output, "pooler_output", None)
    if pooled is None:
        pooled = output[1] if isinstance(output, (tuple, list)) and len(output) > 1 else output
    return pooled


class SigLIP2:
    def __init__(self, device: str) -> None:
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch, self.device = torch, device
        self.processor = AutoProcessor.from_pretrained(MODEL_ID)
        self.model = AutoModel.from_pretrained(MODEL_ID).to(device).eval()
        self.logit_scale = float(self.model.logit_scale.exp().item())
        rows = []
        for name in GATE_CLASSES:
            m = self.embed_texts(GATE_PROMPTS[name]).mean(axis=0)
            rows.append(m / np.linalg.norm(m))
        self.class_matrix = np.stack(rows)

    def _norm(self, feats: Any) -> np.ndarray:
        feats = _pooled(feats)
        return (feats / feats.norm(dim=-1, keepdim=True)).float().cpu().numpy()

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        inputs = self.processor(text=texts, padding="max_length", max_length=64, truncation=True,
                                return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            return self._norm(self.model.get_text_features(**inputs))

    def embed_images(self, images: list[Any]) -> np.ndarray:
        inputs = self.processor(images=images, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            return self._norm(self.model.get_image_features(**inputs))

    def gate(self, vecs: np.ndarray) -> list[dict[str, float]]:
        return softmax_gate(vecs, self.class_matrix, self.logit_scale)
