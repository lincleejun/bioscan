import os

__version__ = "0.1.0"

# The project was named bioscan until 2026-11: a BIOSCAN_X variable still counts as GARDENER_ENGINE_X (the new name wins).
for _k in [k for k in os.environ if k.startswith("BIOSCAN_")]:
    os.environ.setdefault("GARDENER_ENGINE_" + _k.removeprefix("BIOSCAN_"), os.environ[_k])
