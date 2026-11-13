"""The CLI is a thin client: importing it (and every subcommand's module except the admin
`names` commands, which load models on purpose) must not pull in numpy, torch or the service. The
plugin manifests are read the same way: importing them never imports a stage (`<plugin>.stage`)."""
import os
import subprocess
import sys

HEAVY = ("numpy", "torch", "transformers", "open_clip", "PIL", "gardener.service")


def test_cli_modules_do_not_import_heavy_deps(tmp_path):
    code = ("import sys, tempfile; d0 = tempfile.mkdtemp(); import gardener.cli.main, gardener.cli.client, gardener.cli.render, gardener.cli.gt, "
            "gardener.cli.eval, gardener.cli.bench, gardener.cli.geobench, gardener.cli.geotag_cli, gardener.geotag, "
            "gardener.cli.aesbench, gardener.aesthetic, "
            "gardener.contract, gardener.naming, gardener.formats, "
            "gardener.plugin, gardener.plugins, gardener.profile, gardener.cli.config, gardener.cli.cull, gardener.cull; "
            "gardener.contract.PRODUCTS; "
            # `gardener-engine aesthetic ratings` reads XMP and scores heads without numpy
            "gardener.cli.main.parser().parse_args(['aesthetic', 'eval', d0, '--out', d0, '--no-curve']); "
            "gardener.aesthetic.ratings_from_folder(d0); "
            # what `gardener-engine cull` does around the service: reducers, records and every writer
            "ev = {'type': 'result', 'path': '/p/a.jpg', 'products': {'embed': {'vector': [1.0, 0.0]}}}; "
            "r = gardener.cull.apply([ev], {'burst': {}, 'select': {}}); recs = gardener.cull.records(r); "
            "import tempfile as _t, os as _o; _d = _t.mkdtemp(); cc = gardener.cli.cull; "
            "cc.write_html(recs, r, [], _o.path.join(_d, 'p.html')); cc.write_csv(recs, [], _o.path.join(_d, 'c.csv')); "
            "cc.write_links(recs, _o.path.join(_d, 'l')); "
            # `gardener-engine summarize` and `gardener-engine report`
            "import gardener.cli.report as rp; rp.render(rp.summarize(r), _o.path.join(_d, 'report.html')); "
            "gardener.profile.resolve(gardener.profile.builtin(), 'album'); "
            # what `gardener-engine run --profile` and `gardener-engine config show` do before any request is sent
            "import tempfile, pathlib; d = tempfile.mkdtemp(); pathlib.Path(d, 'a.jpg').touch(); "
            "cli = gardener.cli.main; cli.build_payload(cli.parser().parse_args(['run', d, '--profile', 'album']), "
            "gardener.profile.builtin()); cli.main(['config', 'show', '--profile', 'album']); "
            "ca = cli.parser().parse_args(['cull', d, '--html', _o.path.join(_d, 'x.html')]); "
            "cc.build_request(ca, cc.resolve(ca)); "
            f"print([m for m in sys.modules if m.split('.')[0] in {HEAVY!r} or m.startswith('gardener.service') "
            "or m.endswith('.stage')])")
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}       # no developer gardener.toml
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path,
                         env=env).stdout.strip()
    assert "run order aesthetics, embed, identify, quality, scene" in out and out.splitlines()[-1] == "[]", out


def test_products_come_from_the_plugin_manifests():
    from gardener import contract
    from gardener.plugins import BUILTIN

    assert contract.PRODUCTS == tuple(m.name for m in BUILTIN) == ("identify", "embed", "jpg", "geotag", "aesthetics",
                                                                   "quality", "scene", "facing")
