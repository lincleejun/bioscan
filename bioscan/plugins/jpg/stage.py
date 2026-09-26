"""jpg as a stage: writes each decoded image as `<out_dir>/<stem>-<sha8>.jpg` on the CPU pool."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from bioscan.plugin import Item, StageBase, each

QUALITY = 92


def jpg(image: Image.Image, src_path: str, out_dir: str, sha256: str, edge: int = 2048,
        detail: Image.Image | None = None) -> dict[str, Any]:
    """`<stem>-<sha8>.jpg`: DSC0001.ARW from two cards, or a RAW+JPG pair, get two files; a re-run
    rewrites the same one. `edge` above the 2048 image takes the detail copy when there is one
    (up to the service's detail edge); the default writes the 2048 image untouched."""
    target = Path(out_dir) / f"{Path(src_path).stem}-{sha256[:8]}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    src = detail if detail is not None and edge > max(image.size) else image
    if max(src.size) > edge:
        src = src.copy()
        src.thumbnail((edge, edge))
    src.save(target, "JPEG", quality=QUALITY)
    return {"path": str(target), "width": src.width, "height": src.height}


class Jpg(StageBase):
    def writes(self, o: dict[str, Any]) -> list[str]:
        return [o["out_dir"]]

    def settings(self) -> dict[str, Any]:
        return {"quality": QUALITY}

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        return each(items, lambda it: jpg(it.dec.image, it.dec.path, o["out_dir"], it.dec.sha256, o["edge"], it.dec.detail))


STAGE = Jpg()
