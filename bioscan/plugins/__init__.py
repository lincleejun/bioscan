"""The built-in plugins. Each lives in `bioscan/plugins/<name>/`: `__init__.py` holds its stdlib
MANIFEST, `stage.py` the service code, imported only when a run needs it.

BUILTIN is the one list of stages: contract.PRODUCTS, /products, option groups and the order
products are reported in all come from it. Standard library only."""
from __future__ import annotations

from bioscan.plugin import Manifest
from bioscan.plugins.aesthetics import MANIFEST as AESTHETICS
from bioscan.plugins.embed import MANIFEST as EMBED
from bioscan.plugins.geotag import MANIFEST as GEOTAG
from bioscan.plugins.identify import MANIFEST as IDENTIFY
from bioscan.plugins.jpg import MANIFEST as JPG

# report order; new stages go last, so existing outputs keep their key order
BUILTIN: tuple[Manifest, ...] = (IDENTIFY, EMBED, JPG, GEOTAG, AESTHETICS)
BY_NAME: dict[str, Manifest] = {m.name: m for m in BUILTIN}
