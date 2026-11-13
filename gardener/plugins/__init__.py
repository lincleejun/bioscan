"""The built-in plugins. Each lives in `gardener/plugins/<name>/`: `__init__.py` holds its stdlib
MANIFEST, `stage.py` the service code, imported only when a run needs it.

BUILTIN is the one list of stages: contract.PRODUCTS, /products, option groups and the order
products are reported in all come from it. Standard library only."""
from __future__ import annotations

from gardener.plugin import Manifest
from gardener.plugins.aesthetics import MANIFEST as AESTHETICS
from gardener.plugins.burst import MANIFEST as BURST
from gardener.plugins.embed import MANIFEST as EMBED
from gardener.plugins.facing import MANIFEST as FACING
from gardener.plugins.geotag import MANIFEST as GEOTAG
from gardener.plugins.identify import MANIFEST as IDENTIFY
from gardener.plugins.jpg import MANIFEST as JPG
from gardener.plugins.quality import MANIFEST as QUALITY
from gardener.plugins.scene import MANIFEST as SCENE
from gardener.plugins.select import MANIFEST as SELECT

# report order; new stages go last, so existing outputs keep their key order
BUILTIN: tuple[Manifest, ...] = (IDENTIFY, EMBED, JPG, GEOTAG, AESTHETICS, QUALITY, SCENE, FACING)
BY_NAME: dict[str, Manifest] = {m.name: m for m in BUILTIN}
# Reducers (Manifest.kind "reducer"): model-free units over a run's results, run by the CLI or
# offline (gardener.cull), in this order. Profiles name them; the service never runs them.
REDUCERS: tuple[Manifest, ...] = (BURST, SELECT)
