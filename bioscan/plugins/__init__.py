"""The built-in plugins. Each lives in `bioscan/plugins/<name>/`: `__init__.py` holds its stdlib
MANIFEST, `stage.py` the service code, imported only when a run needs it.

BUILTIN is the one list of stages: contract.PRODUCTS, /products, option groups and the order
products are reported in all come from it. Standard library only."""
from __future__ import annotations

from bioscan.plugin import Manifest
from bioscan.plugins.aesthetics import MANIFEST as AESTHETICS
from bioscan.plugins.burst import MANIFEST as BURST
from bioscan.plugins.embed import MANIFEST as EMBED
from bioscan.plugins.geotag import MANIFEST as GEOTAG
from bioscan.plugins.identify import MANIFEST as IDENTIFY
from bioscan.plugins.jpg import MANIFEST as JPG
from bioscan.plugins.quality import MANIFEST as QUALITY
from bioscan.plugins.scene import MANIFEST as SCENE
from bioscan.plugins.select import MANIFEST as SELECT

# report order; new stages go last, so existing outputs keep their key order
BUILTIN: tuple[Manifest, ...] = (IDENTIFY, EMBED, JPG, GEOTAG, AESTHETICS, QUALITY, SCENE)
BY_NAME: dict[str, Manifest] = {m.name: m for m in BUILTIN}
# Reducers (Manifest.kind "reducer"): model-free units over a run's results, run by the CLI or
# offline (bioscan.cull), in this order. Profiles name them; the service never runs them.
REDUCERS: tuple[Manifest, ...] = (BURST, SELECT)
