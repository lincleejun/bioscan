"""The CLI is a thin client: importing it (and every subcommand's module except the admin
`names` commands, which load models on purpose) must not pull in numpy, torch or the service."""
import subprocess
import sys

HEAVY = ("numpy", "torch", "transformers", "open_clip", "PIL", "bioscan.service")


def test_cli_modules_do_not_import_heavy_deps():
    code = ("import sys; import bioscan.cli.main, bioscan.cli.client, bioscan.cli.render, bioscan.cli.gt, "
            "bioscan.cli.eval, bioscan.cli.bench, bioscan.contract, bioscan.naming, bioscan.formats; "
            f"print([m for m in sys.modules if m.split('.')[0] in {HEAVY!r} or m.startswith('bioscan.service')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]", out
