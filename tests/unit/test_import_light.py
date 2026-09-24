"""The CLI is a thin client: importing it (and every subcommand's module except the admin
`names` commands, which load models on purpose) must not pull in numpy, torch or the service. The
plugin manifests are read the same way: importing them never imports a stage (`<plugin>.stage`)."""
import os
import subprocess
import sys

HEAVY = ("numpy", "torch", "transformers", "open_clip", "PIL", "bioscan.service")


def test_cli_modules_do_not_import_heavy_deps(tmp_path):
    code = ("import sys; import bioscan.cli.main, bioscan.cli.client, bioscan.cli.render, bioscan.cli.gt, "
            "bioscan.cli.eval, bioscan.cli.bench, bioscan.contract, bioscan.naming, bioscan.formats, "
            "bioscan.plugin, bioscan.plugins, bioscan.profile, bioscan.cli.config; bioscan.contract.PRODUCTS; "
            "bioscan.profile.resolve(bioscan.profile.builtin(), 'album'); "
            # what `bioscan run --profile` and `bioscan config show` do before any request is sent
            "import tempfile, pathlib; d = tempfile.mkdtemp(); pathlib.Path(d, 'a.jpg').touch(); "
            "cli = bioscan.cli.main; cli.build_payload(cli.parser().parse_args(['run', d, '--profile', 'album']), "
            "bioscan.profile.builtin()); cli.main(['config', 'show', '--profile', 'album']); "
            f"print([m for m in sys.modules if m.split('.')[0] in {HEAVY!r} or m.startswith('bioscan.service') "
            "or m.endswith('.stage')])")
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}       # no developer bioscan.toml
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path,
                         env=env).stdout.strip()
    assert "run order embed, identify" in out and out.splitlines()[-1] == "[]", out


def test_products_come_from_the_plugin_manifests():
    from bioscan import contract
    from bioscan.plugins import BUILTIN

    assert contract.PRODUCTS == tuple(m.name for m in BUILTIN) == ("identify", "embed", "jpg")
