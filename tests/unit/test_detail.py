"""Species crops from the larger "detail" copy: same framing, more pixels; nothing else moves."""
from types import SimpleNamespace

import numpy as np
from PIL import Image

from bioscan.service import products
from bioscan.service.adapters.owlv2 import Detection


def frame(size=(3000, 2000)) -> Image.Image:
    rng = np.random.default_rng(1)
    small = (rng.random((size[1] // 20, size[0] // 20, 3)) * 255).astype(np.uint8)
    return Image.fromarray(small).resize(size, Image.Resampling.NEAREST)


def test_species_crops_same_framing_more_pixels():
    full = frame()
    image, detail = full.resize((2048, 1365)), full.resize((3072, 2048))
    boxes = [(1000.0, 600.0, 1040.0, 640.0), (100.0, 100.0, 900.0, 700.0), (2000.0, 1300.0, 2048.0, 1365.0)]
    plain = products.species_crops(image, boxes)
    sharp = products.species_crops(image, boxes, detail)
    for p, s in zip(plain, sharp):
        assert abs(s.width / p.width - 1.5) < 0.01 and abs(s.height / p.height - 1.5) < 0.01
        # same field of view: the detail crop scaled back down matches the 2048 crop
        diff = np.abs(np.asarray(s.resize(p.size), np.float32) - np.asarray(p, np.float32)).mean()
        assert diff < 12, diff
    assert plain[0].size == (320, 320) and sharp[0].size == (480, 480)   # min_side scales with the image


def test_species_crops_without_detail_is_old_behaviour():
    image = frame((2048, 1365))
    box = (500.0, 400.0, 700.0, 520.0)
    assert products.species_crops(image, [box])[0].tobytes() == products.crop_with_context(image, box).tobytes()
    assert products.species_crops(image, [box], image.copy())[0].size == products.crop_with_context(image, box).size


class Engine:
    """identify() with stub models: one bird box, BioCLIP records the crop sizes it was given."""

    def __init__(self, names):
        self.seen: list[tuple[int, int]] = []
        gate = {"bird": 0.9, "mammal": 0.05, "other_animal": 0.02, "person": 0.01, "none": 0.02}
        self.owlv2 = SimpleNamespace(detect=lambda im, prompts, threshold: [Detection("a bird", 0.8, (900, 500, 960, 560))])
        self.siglip2 = SimpleNamespace(embed_images=lambda ims: ims, gate=lambda ims: [dict(gate) for _ in ims])
        self.names, self.priors = names, {}
        self.bioclip = SimpleNamespace(encode_images=self._encode, probs=lambda f, m: np.array([[0.7, 0.3]] * len(f)))

    def _encode(self, ims):
        self.seen += [im.size for im in ims]
        return ims


def test_identify_detail_changes_only_the_species_crop():
    from bioscan.service.names import NameList

    tax = [["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", f"Megascops {s}"]
           for s in ("kennicottii", "asio")]
    birds = NameList("avilist-2025", "bird", [t[6] for t in tax], ["", ""], tax, np.zeros((2, 4), np.float32),
                     ["exact"] * 2)
    full = frame()
    image, detail = full.resize((2048, 1365)), full.resize((3072, 2048))
    opts = {"top_k": 2, "geo": True, "species": True}
    gate = {"bird": 0.9, "mammal": 0.05, "other_animal": 0.02, "person": 0.01, "none": 0.02}
    a, b = Engine({"bird": birds}), Engine({"bird": birds})
    old = products.identify(a, image, gate, None, None, None, opts)
    new = products.identify(b, image, gate, None, None, None, opts, detail=detail)
    assert old == new                              # boxes, quality, species output: identical here
    assert a.seen == [(320, 320)] and b.seen == [(480, 480)]
