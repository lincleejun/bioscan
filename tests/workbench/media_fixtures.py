"""Helpers for the media tests (unit 2): tiny JPEGs with EXIF, and fake model adapters for a real
Engine (a minimal copy of tests/contract/conftest.py's fakes: every frame is a bird, one box,
Western over Eastern Screech-Owl)."""
from __future__ import annotations

import struct

import numpy as np
from PIL import Image

from gardener.service.adapters.owlv2 import Detection
from gardener.service.engine import Engine, Loaders
from gardener.service.names import NameList

GATE = {"bird": 0.93, "mammal": 0.02, "other_animal": 0.01, "person": 0.0, "none": 0.04}
BOX = (0.31, 0.22, 0.58, 0.71)
OWLS = [("Megascops kennicottii", "Western Screech-Owl"), ("Megascops asio", "Eastern Screech-Owl")]


def make_jpg(path, size=(64, 48), taken="2025:03:01 08:12:00", subsec=None, orientation=None, gps=None,
             make="SONY", model="ILCE-7M4", color=(90, 120, 30)) -> str:
    """A small JPEG with EXIF capture time (and sub-seconds, orientation, GPS (lat, lon) when given)."""
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = make, model
    if orientation:
        exif[0x0112] = orientation
    ifd = exif.get_ifd(0x8769)
    if taken:
        ifd[0x9003] = taken
    if subsec:
        ifd[0x9291] = subsec
    if gps:
        lat, lon = gps
        g = exif.get_ifd(0x8825)
        g[1], g[3] = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
        g[2] = tuple(float(x) for x in _dms(abs(lat)))
        g[4] = tuple(float(x) for x in _dms(abs(lon)))
    Image.new("RGB", size, color).save(path, exif=exif)
    return str(path)


def _dms(v: float) -> tuple[float, float, float]:
    d = int(v)
    m = int((v - d) * 60)
    return d, m, round((v - d - m / 60) * 3600, 4)


class FakeSigLIP2:
    logit_scale = 10.0

    def embed_images(self, images):
        return np.full((len(images), 768), 1 / np.sqrt(768), dtype=np.float32)

    def embed_texts(self, texts):
        rows = [np.random.default_rng(sum(t.encode())).normal(size=768) for t in texts]
        return np.stack([r / np.linalg.norm(r) for r in rows]).astype(np.float32)

    def gate(self, vecs):
        return [dict(GATE) for _ in vecs]


class FakeOWLv2:
    def detect(self, image, prompts, *, threshold):
        return self.detect_batch([image], prompts, threshold=threshold)[0]

    def detect_batch(self, images, prompts, *, threshold, batch=8):
        out = []
        for im in images:
            w, h = im.size
            box = (BOX[0] * w, BOX[1] * h, BOX[2] * w, BOX[3] * h)
            out.append([d for d in [Detection(prompts[0], 0.84, box)] if d.confidence >= threshold])
        return out


class FakeBioCLIP:
    def encode_images(self, images):
        return np.ones((len(images), 4), np.float32)

    def probs(self, features, matrix):
        return np.array([[0.81, 0.19]] * len(features))

    def logits(self, features, matrix):
        return np.log(self.probs(features, matrix))


def owl_list() -> NameList:
    tax = [["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", s] for s, _ in OWLS]
    return NameList(list_id="fake-owls", kind="bird", scientific=[s for s, _ in OWLS],
                    common=[c for _, c in OWLS], taxonomy=tax, matrix=np.zeros((2, 4), np.float32),
                    tol_how=["exact"] * 2, sha="test")


def fake_engine(fail: str | None = None) -> Engine:
    """A real Engine on fake adapters; `fail` names a model ("siglip2", "owlv2", "bioclip") whose load raises."""
    def loader(name, make):
        def load(*_):
            if name == fail:
                raise RuntimeError(f"{name} weights missing")
            return make()
        return load

    return Engine("cpu", Loaders(siglip2=loader("siglip2", FakeSigLIP2), owlv2=loader("owlv2", FakeOWLv2),
                                 species=loader("bioclip", lambda: (FakeBioCLIP(), {"bird": owl_list()})),
                                 geo=lambda: None))


def tiff_raw(strips=None, tiles=None, sub=False, jpeg=None, endian="<") -> bytes:
    """A minimal TIFF RAW: IFD0 (with `sub`, pointing to one SubIFD that holds the data tags, IFD0
    keeping only an embedded JPEG at `jpeg`) and strip / tile data of the given (offset, length)
    layout, padded to the declared end. Arrays of more than one value follow their IFD."""
    def ifd(at, entries):
        out, extra = struct.pack(endian + "H", len(entries)), b""
        for tag, values in entries:
            out += struct.pack(endian + "HHL", tag, 4, len(values))
            if len(values) == 1:
                out += struct.pack(endian + "L", values[0])
            else:
                out += struct.pack(endian + "L", at + 2 + 12 * len(entries) + 4 + len(extra))
                extra += struct.pack(endian + "L" * len(values), *values)
        return out + struct.pack(endian + "L", 0) + extra

    data = []
    for (o, c), layout in (((0x0111, 0x0117), strips), ((0x0144, 0x0145), tiles)):
        if layout:
            data += [(o, [a for a, _ in layout]), (c, [n for _, n in layout])]
    head = (b"II*\x00" if endian == "<" else b"MM\x00*") + struct.pack(endian + "L", 8)
    if sub:
        first = [(0x0201, [jpeg[0]]), (0x0202, [jpeg[1]])] if jpeg else []
        sub_at = 8 + len(ifd(8, [*first, (0x014A, [0])]))
        body = ifd(8, [*first, (0x014A, [sub_at])]) + ifd(sub_at, data)
    else:
        body = ifd(8, data)
    end = max(o + n for o, n in (strips or []) + (tiles or []) + ([jpeg] if jpeg else []))
    out = head + body
    return out + b"\x01" * (end - len(out))
