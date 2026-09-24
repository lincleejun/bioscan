"""OWLv2 open-vocabulary detector (migrated from PhotoOS analysis/adapters/owlv2.py + scan/vocab.py).

Generic words find the animal; names only lift confidence. The prompt set is chosen by the
whole-frame gate class; every box is filed under that vocabulary's kind, and the crop gate
(rules.judge) can still veto it or promote it to a bird.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bioscan.service.taxa import VOCAB  # noqa: F401 - re-exported

MODEL_ID = "google/owlv2-base-patch16-ensemble"
REVISION = "cfd3195ba4ea9592eec887ded089f4c08eff231d"
BATCH = 8               # frames per forward pass (960 x 960 each)


@dataclass(frozen=True)
class Detection:
    prompt: str
    confidence: float
    bbox: tuple[float, float, float, float]   # pixels of the image handed to detect()


def clip_to_frame(box: tuple[float, float, float, float], width: int, height: int
                  ) -> tuple[float, float, float, float] | None:
    x0, y0, x1, y1 = box
    x0, x1 = max(0.0, min(x0, x1)), min(float(width), max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(float(height), max(y0, y1))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


class OWLv2:
    def __init__(self, device: str) -> None:
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self.torch, self.device = torch, device
        self.processor = Owlv2Processor.from_pretrained(MODEL_ID, revision=REVISION)
        model: Any = Owlv2ForObjectDetection
        self.model = model.from_pretrained(MODEL_ID, revision=REVISION).to(device).eval()

    def detect(self, image: Any, prompts: list[str], *, threshold: float) -> list[Detection]:
        """Boxes above `threshold`; each carries the prompt it scored highest on."""
        return self.detect_batch([image], prompts, threshold=threshold)[0]

    def detect_batch(self, images: list[Any], prompts: list[str], *, threshold: float,
                     batch: int = BATCH) -> list[list[Detection]]:
        """detect() over many images with the same prompts, `batch` images per forward pass."""
        torch = self.torch
        out: list[list[Detection]] = []
        for start in range(0, len(images), batch):
            part = images[start:start + batch]
            inputs = self.processor(text=[list(prompts)] * len(part), images=part, return_tensors="pt").to(self.device)
            with torch.no_grad():
                outputs = self.model(**inputs)
            # OWLv2 pads each image to a square of its long side, so boxes come back in that frame.
            sides = [[max(im.size)] * 2 for im in part]
            results = self.processor.post_process_grounded_object_detection(
                outputs, threshold=threshold, target_sizes=torch.tensor(sides))
            for image, result in zip(part, results):
                dets = []
                for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
                    clipped = clip_to_frame(tuple(float(v) for v in box), *image.size)
                    if clipped is not None:
                        dets.append(Detection(prompts[int(label)], float(score), clipped))
                out.append(dets)
        return out
