"""The CLI is a thin client: importing it (and every subcommand's module except the admin
`names` commands, which load models on purpose) must not pull in numpy, torch or the service. The
plugin manifests are read the same way: importing them never imports a stage (`<plugin>.stage`)."""
import os
import subprocess
import sys

HEAVY = ("numpy", "torch", "transformers", "open_clip", "PIL", "bioscan.service")


def test_cli_modules_do_not_import_heavy_deps(tmp_path):
    code = ("import sys, tempfile; d0 = tempfile.mkdtemp(); import bioscan.cli.main, bioscan.cli.client, bioscan.cli.render, bioscan.cli.gt, "
            "bioscan.cli.eval, bioscan.cli.bench, bioscan.cli.geobench, bioscan.cli.geotag_cli, bioscan.geotag, "
            "bioscan.cli.aesbench, bioscan.aesthetic, "
            "bioscan.contract, bioscan.naming, bioscan.formats, "
            "bioscan.plugin, bioscan.plugins, bioscan.profile, bioscan.cli.config, bioscan.cli.cull, bioscan.cull; "
            "bioscan.contract.PRODUCTS; "
            # `bioscan aesthetic ratings` reads XMP and scores heads without numpy
            "bioscan.cli.main.parser().parse_args(['aesthetic', 'eval', d0, '--out', d0, '--no-curve']); "
            "bioscan.aesthetic.ratings_from_folder(d0); "
            # what `bioscan cull` does around the service: reducers, records and every writer
            "ev = {'type': 'result', 'path': '/p/a.jpg', 'products': {'embed': {'vector': [1.0, 0.0]}}}; "
            "r = bioscan.cull.apply([ev], {'burst': {}, 'select': {}}); recs = bioscan.cull.records(r); "
            "import tempfile as _t, os as _o; _d = _t.mkdtemp(); cc = bioscan.cli.cull; "
            "cc.write_html(recs, r, [], _o.path.join(_d, 'p.html')); cc.write_csv(recs, [], _o.path.join(_d, 'c.csv')); "
            "cc.write_links(recs, _o.path.join(_d, 'l')); "
            # `bioscan summarize` and `bioscan report`
            "import bioscan.cli.report as rp; rp.render(rp.summarize(r), _o.path.join(_d, 'report.html')); "
            "bioscan.profile.resolve(bioscan.profile.builtin(), 'album'); "
            # what `bioscan run --profile` and `bioscan config show` do before any request is sent
            "import tempfile, pathlib; d = tempfile.mkdtemp(); pathlib.Path(d, 'a.jpg').touch(); "
            "cli = bioscan.cli.main; cli.build_payload(cli.parser().parse_args(['run', d, '--profile', 'album']), "
            "bioscan.profile.builtin()); cli.main(['config', 'show', '--profile', 'album']); "
            "ca = cli.parser().parse_args(['cull', d, '--html', _o.path.join(_d, 'x.html')]); "
            "cc.build_request(ca, cc.resolve(ca)); "
            f"print([m for m in sys.modules if m.split('.')[0] in {HEAVY!r} or m.startswith('bioscan.service') "
            "or m.endswith('.stage')])")
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}       # no developer bioscan.toml
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path,
                         env=env).stdout.strip()
    assert "run order aesthetics, embed, identify, quality, scene" in out and out.splitlines()[-1] == "[]", out


def test_products_come_from_the_plugin_manifests():
    from bioscan import contract
    from bioscan.plugins import BUILTIN

    assert contract.PRODUCTS == tuple(m.name for m in BUILTIN) == ("identify", "embed", "jpg", "geotag", "aesthetics",
                                                                   "quality", "scene")
