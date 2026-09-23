"""The engine seam: the real Engine and the contract tests' FakeEngine expose the same methods
with the same parameters, so the fast contract tests exercise what production calls."""
import inspect
import sys
from pathlib import Path

import pytest

from bioscan.service.engine import Engine, EngineProtocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
from conftest import FakeEngine  # noqa: E402

METHODS = [n for n, v in vars(EngineProtocol).items() if callable(v) and not n.startswith("_")]


def test_protocol_methods():
    assert sorted(METHODS) == sorted(["loaded", "ensure", "info", "frame", "identify"])


@pytest.mark.parametrize("impl", [Engine, FakeEngine])
@pytest.mark.parametrize("name", METHODS)
def test_same_parameters(impl, name):
    want = list(inspect.signature(getattr(EngineProtocol, name)).parameters)
    have = list(inspect.signature(getattr(impl, name)).parameters)
    assert have == want, f"{impl.__name__}.{name}{have} != protocol {want}"


def test_instances_satisfy_protocol():
    assert isinstance(FakeEngine(), EngineProtocol)
    assert isinstance(Engine(device="cpu"), EngineProtocol)      # no model is loaded by the constructor
