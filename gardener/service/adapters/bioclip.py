"""BioCLIP 2.5 Huge (ViT-H/14, 1024-d) species encoder (migrated from PhotoOS bioclip_huge.py,
content-tag head and envelope wrapping removed). Only this model; no other BioCLIP version."""
from __future__ import annotations

from typing import Any

import numpy as np

MODEL_ID = "hf-hub:imageomics/bioclip-2.5-vith14"
MODEL_NAME = "bioclip-2.5-vith14"
REVISION = "6e3d04e3d6522012c88181085c5ae666e14c45cd"   # HF commit; open_clip's hf-hub: path cannot pin one


def snapshot_dir() -> str:
    """The pinned snapshot in the HF cache (downloaded by tests/models/download.py; offline after)."""
    from huggingface_hub import snapshot_download

    return snapshot_download(MODEL_ID.removeprefix("hf-hub:"), revision=REVISION, ignore_patterns=["*.bin"])


class BioCLIP:
    def __init__(self, device: str) -> None:
        import open_clip
        import torch

        self.torch, self.device = torch, device
        local = "local-dir:" + snapshot_dir()
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(local, device=device)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(local)
        self.logit_scale = float(self.model.logit_scale.exp().item())
        self._lists: dict[int, tuple[np.ndarray, Any]] = {}     # id(matrix) -> (matrix, its device copy)

    def encode_images(self, images: list[Any]) -> Any:
        """(n, 1024) L2-normalised torch tensor on device."""
        with self.torch.no_grad():
            x = self.torch.stack([self.preprocess(im) for im in images]).to(self.device)
            f = self.model.encode_image(x)
            return f / f.norm(dim=-1, keepdim=True)

    def probs(self, features: Any, matrix: np.ndarray) -> np.ndarray:
        """softmax over a name list; `matrix` is its (N, 1024) float32 or float16 array
        (NameList.matrix), copied to the device on first use and kept."""
        with self.torch.no_grad():
            return self._logits(features, matrix).softmax(dim=-1).float().cpu().numpy()

    def logits(self, features: Any, matrix: np.ndarray) -> np.ndarray:
        """The scaled similarities `probs` takes the softmax of: comparable across name lists."""
        with self.torch.no_grad():
            return self._logits(features, matrix).float().cpu().numpy()

    def _logits(self, features: Any, matrix: np.ndarray) -> Any:
        m = self.place(matrix)
        if m.dtype == features.dtype:
            return self.logit_scale * features @ m.T
        # float16 list (all-taxa): upcast a slice at a time, so no float32 copy of it is ever kept
        step = 65536
        return self.torch.cat([self.logit_scale * features @ m[i:i + step].to(features.dtype).T
                               for i in range(0, m.shape[0], step)], dim=-1)

    def place(self, matrix: np.ndarray) -> Any:
        """The device copy of a name list's matrix, made on the first call and kept."""
        hit = self._lists.get(id(matrix))
        if hit is None:                 # the entry holds the array, so its id is never reused meanwhile
            hit = self._lists[id(matrix)] = (matrix, self.torch.from_numpy(matrix).to(self.device))
        return hit[1]
