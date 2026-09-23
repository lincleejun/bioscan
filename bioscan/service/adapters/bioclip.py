"""BioCLIP 2.5 Huge (ViT-H/14, 1024-d) species encoder (migrated from PhotoOS bioclip_huge.py,
content-tag head and envelope wrapping removed). Only this model; no other BioCLIP version."""
from __future__ import annotations

from typing import Any

import numpy as np

MODEL_ID = "hf-hub:imageomics/bioclip-2.5-vith14"
MODEL_NAME = "bioclip-2.5-vith14"


class BioCLIP:
    def __init__(self, device: str) -> None:
        import open_clip
        import torch

        self.torch, self.device = torch, device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(MODEL_ID, device=device)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(MODEL_ID)
        self.logit_scale = float(self.model.logit_scale.exp().item())

    def encode_images(self, images: list[Any]) -> Any:
        """(n, 1024) L2-normalised torch tensor on device."""
        with self.torch.no_grad():
            x = self.torch.stack([self.preprocess(im) for im in images]).to(self.device)
            f = self.model.encode_image(x)
            return f / f.norm(dim=-1, keepdim=True)

    def probs(self, features: Any, matrix: Any) -> np.ndarray:
        """softmax over a name list; `matrix` is the (N, 1024) list tensor on device."""
        with self.torch.no_grad():
            return (self.logit_scale * features @ matrix.T).softmax(dim=-1).float().cpu().numpy()
