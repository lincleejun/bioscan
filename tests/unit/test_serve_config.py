"""Serve config: flag beats BIOSCAN_* beats default, once, for `bioscan serve` and `bioscan-serve` alike."""
import os

import pytest

from bioscan import serve_config
from bioscan.cli import main as cli
from bioscan.serve_config import ServeConfig, resolve
from bioscan.service import app


def test_defaults():
    assert resolve(env={}) == ServeConfig(host="127.0.0.1", port=8765, decode_workers=4, chunk=32, detail_edge=3072,
                                          allow_roots=[])


def test_decode_workers_and_chunk_flag_env_default():
    env = {"BIOSCAN_DECODE_WORKERS": "2", "BIOSCAN_CHUNK": "8"}
    assert (resolve(env=env).decode_workers, resolve(env=env).chunk) == (2, 8)
    assert (resolve(decode_workers=6, env=env).decode_workers, resolve(decode_workers=6, env=env).chunk) == (6, 8)
    assert resolve(env={"BIOSCAN_CHUNK": ""}).chunk == 32            # empty counts as unset
    with pytest.raises(SystemExit, match="^decode_workers must be >= 1$"):
        resolve(decode_workers=0, env={})
    with pytest.raises(SystemExit, match="^chunk must be >= 1$"):
        resolve(env={"BIOSCAN_CHUNK": "0"})
    with pytest.raises(ValueError):
        resolve(env={"BIOSCAN_CHUNK": "abc"})


def test_detail_edge_flag_env_default():
    assert resolve(env={}).detail_edge == 3072
    assert resolve(env={"BIOSCAN_DETAIL_EDGE": "4096"}).detail_edge == 4096
    assert resolve(detail_edge=2500, env={"BIOSCAN_DETAIL_EDGE": "4096"}).detail_edge == 2500
    assert resolve(detail_edge=2048, env={}).detail_edge is None and resolve(detail_edge=0, env={}).detail_edge is None
    assert resolve(env={"BIOSCAN_DETAIL_EDGE": "2000"}).detail_edge is None


def test_allow_roots_flag_env_default():
    assert resolve(env={}).allow_roots == []
    assert resolve(env={"BIOSCAN_ALLOW_ROOTS": f"/a{os.pathsep}/b{os.pathsep}"}).allow_roots == ["/a", "/b"]
    assert resolve(allow_roots=["/c"], env={"BIOSCAN_ALLOW_ROOTS": "/a"}).allow_roots == ["/c"]


@pytest.fixture
def served(monkeypatch):
    """What each entry point hands to app.serve, with no service started."""
    got: list[ServeConfig] = []
    monkeypatch.setattr(app, "serve", got.append)
    for var in ("BIOSCAN_DECODE_WORKERS", "BIOSCAN_CHUNK", "BIOSCAN_DETAIL_EDGE", "BIOSCAN_ALLOW_ROOTS"):
        monkeypatch.delenv(var, raising=False)
    return got


def test_both_entry_points_resolve_the_same_config_without_touching_the_environment(served, monkeypatch):
    monkeypatch.setenv("BIOSCAN_CHUNK", "8")
    before = dict(os.environ)
    flags = ["--port", "9000", "--decode-workers", "3", "--detail-edge", "4000", "--allow-root", "/p"]
    assert cli.main(["serve", *flags]) == 0
    app.main(flags)
    assert served[0] == served[1] == ServeConfig(host="127.0.0.1", port=9000, decode_workers=3, chunk=8,
                                                 detail_edge=4000, allow_roots=["/p"])
    assert dict(os.environ) == before


def test_bioscan_serve_makes_allow_roots_absolute(served, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["serve", "--allow-root", "photos"])
    assert served[0].allow_roots == [str(tmp_path / "photos")]


def test_validation_error_before_the_service_loads(served):
    with pytest.raises(SystemExit, match="^chunk must be >= 1$"):
        cli.main(["serve", "--chunk", "0"])
    with pytest.raises(SystemExit, match="^decode_workers must be >= 1$"):
        app.main(["--decode-workers", "-1"])
    assert served == []


def test_launchd_bakes_flags_or_defaults_not_the_environment(monkeypatch, capsysbinary):
    import plistlib

    monkeypatch.setenv("BIOSCAN_CHUNK", "8")
    cli.main(["serve", "--launchd", "--decode-workers", "2"])
    args = plistlib.loads(capsysbinary.readouterr().out)["ProgramArguments"]
    assert args[args.index("--decode-workers") + 1] == "2"
    assert args[args.index("--chunk") + 1] == str(serve_config.CHUNK)
