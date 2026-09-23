"""OWLv2 open-vocabulary detector (migrated from PhotoOS analysis/adapters/owlv2.py + scan/vocab.py).

Generic words find the animal; names only lift confidence. The prompt set is chosen by the
whole-frame gate class; every box is filed under that vocabulary's kind, and the crop gate
(products.judge) can still veto it or promote it to a bird.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_ID = "google/owlv2-base-patch16-ensemble"
REVISION = "cfd3195ba4ea9592eec887ded089f4c08eff231d"

# gate class -> {prompt: first-pass floor}
VOCAB: dict[str, dict[str, float]] = {
    "bird": {"a bird": 0.3},
    "mammal": {"a mammal": 0.1, "an animal": 0.1, "a wild animal": 0.1,
               **{f"a {n}": 0.2 for n in ("deer", "bear", "seal", "sea lion", "whale", "moose", "caribou",
                                          "sea otter", "squirrel", "fox", "coyote", "mountain goat", "rabbit",
                                          "elk", "bison", "marmot",
                                          # large carnivores and brush mammals the golden set missed
                                          "black bear", "grizzly bear", "mountain lion", "cougar", "bobcat",
                                          "lynx", "wolf", "wild cat", "raccoon", "skunk", "badger", "bighorn sheep",
                                          "pronghorn", "wild pig", "beaver", "chipmunk")},
               "an opossum": 0.2, "an otter": 0.2},
    "other_animal": {"an animal": 0.1, "a reptile": 0.15, "a lizard": 0.2, "a snake": 0.2, "a turtle": 0.2,
                     "a frog": 0.2, "a fish": 0.2, "an insect": 0.15, "a butterfly": 0.2, "a spider": 0.2},
}


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
        torch = self.torch
        inputs = self.processor(text=[list(prompts)], images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        # OWLv2 pads to a square of the long side, so boxes come back in that padded frame.
        side = max(image.size)
        result = self.processor.post_process_grounded_object_detection(
            outputs, threshold=threshold, target_sizes=torch.tensor([[side, side]]))[0]
        out = []
        for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
            clipped = clip_to_frame(tuple(float(v) for v in box), *image.size)
            if clipped is not None:
                out.append(Detection(prompts[int(label)], float(score), clipped))
        return out
