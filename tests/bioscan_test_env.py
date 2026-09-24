"""A pytest plugin (pyproject addopts `-p bioscan_test_env`): no test reads this machine's bioscan.toml
files or profile variables, which would hand the CLI (run, eval, bench run, serve) a developer's
default_profile or [serve] table. A test that wants files passes them to profile.load, or patches
profile.config_files itself."""
import os

import pytest


@pytest.fixture(autouse=True)
def no_user_config(tmp_path_factory, monkeypatch):
    from bioscan import profile

    for var in ("BIOSCAN_CONFIG", "BIOSCAN_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    real = profile.config_files
    home = tmp_path_factory.mktemp("home")                  # holds no bioscan.toml, as user or project folder

    def config_files(env=None, cwd=None, h=None):
        return real({k: v for k, v in (os.environ if env is None else env).items() if k != "XDG_CONFIG_HOME"},
                    cwd or home, h or home)

    config_files.real = real                                # the unpatched function, for its own test
    monkeypatch.setattr(profile, "config_files", config_files)
