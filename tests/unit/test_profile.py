"""Profiles and bioscan.toml (bioscan/profile.py): the built-in profiles, the merge order, unknown
keys, the CLI's payload (unchanged without a profile), eval's request, the serve file layer and
`bioscan config show`."""
import json
import subprocess
import sys

import pytest

from bioscan import profile, serve_config
from bioscan.cli import config as cli_config
from bioscan.cli.main import build_payload, main, parser

BUILTIN = profile.builtin()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def files(tmp_path, user="", project="", extra=None):
    """(label, path) list as config_files gives it, with the given contents ("" = no file)."""
    out = [("user", tmp_path / "home" / ".config" / "bioscan" / "bioscan.toml"), ("project", tmp_path / "bioscan.toml")]
    for (_, p), text in zip(out, (user, project)):
        if text:
            write(p, text)
    if extra is not None:
        out.append(("BIOSCAN_CONFIG", write(tmp_path / "extra.toml", extra)))
    return out


def test_builtin_profiles():
    assert BUILTIN.names() == ["album", "full", "wildlife"]
    full = profile.resolve(BUILTIN, "full")
    assert full.want == ["identify"] and full.plan.models == ("bioclip", "owlv2", "siglip2")
    assert all(src == "default" for src in full.sources["identify"].values())
    album = profile.resolve(BUILTIN, "album")
    assert album.want == ["identify", "embed", "aesthetics"] and album.options["identify"]["species"] is False
    assert album.sources["identify"]["species"] == "profiles.toml" and album.plan.models == ("owlv2", "siglip2")
    wild = profile.resolve(BUILTIN, "wildlife")
    assert wild.want == ["geotag", "identify"] and wild.plan.stages == ("geotag", "identify")
    assert wild.options["geotag"]["gpx"] == [] and "geotag" not in profile.resolve(BUILTIN, "full").want


def test_no_profile_resolves_as_before(tmp_path):
    """The service's request without a profile: `full`, i.e. every stage's defaults under the request."""
    from bioscan.plugin import merge_options

    for opts in (None, {"identify": {"top_k": 3}}, {"jpg": {"out_dir": "/o"}, "embed": {"format": "f16_base64"}}):
        assert profile.resolve(BUILTIN, "full", options=opts).options == merge_options(opts)


# ---- the CLI's payload -------------------------------------------------------------------

@pytest.fixture
def photos(tmp_path):
    for n in ("a.jpg", "b.ARW"):
        (tmp_path / n).write_bytes(b"")
    return tmp_path


@pytest.mark.parametrize("args, want, options", [
    ([], ["identify"], {"identify": {"top_k": 5, "geo": True, "species": True}}),
    (["--want", "identify,jpg", "--jpg-out", "OUT", "--no-geo"], ["identify", "jpg"],
     {"identify": {"top_k": 5, "geo": False, "species": True}, "jpg": {"out_dir": "OUT"}}),
    (["--want", "embed,identify", "--top-k", "3", "--no-species", "--candidates", "Bubo, Strigidae"],
     ["embed", "identify"], {"identify": {"top_k": 3, "geo": True, "species": False, "candidates": ["Bubo", "Strigidae"]}}),
    (["--want", "embed"], ["embed"], {}),
    (["--want", "jpg,embed", "--jpg-out", "OUT"], ["jpg", "embed"], {"jpg": {"out_dir": "OUT"}}),
])
def test_payload_without_a_profile_is_the_one_sent_before_profiles(photos, tmp_path, args, want, options):
    """Byte for byte (key order included) the body `bioscan run` built at b02f189."""
    out = str(tmp_path / "o")
    a = parser().parse_args(["run", str(photos), *[out if x == "OUT" else x for x in args]])
    body = build_payload(a, BUILTIN)
    expected = {"inputs": body["inputs"], "want": want,
                "options": json.loads(json.dumps(options).replace('"OUT"', json.dumps(out)))}
    assert json.dumps(body) == json.dumps(expected)


def test_payload_errors_unchanged(photos):
    with pytest.raises(SystemExit, match="--want jpg needs --jpg-out DIR"):
        build_payload(parser().parse_args(["run", str(photos), "--want", "jpg"]), BUILTIN)
    with pytest.raises(SystemExit, match="--want must be a non-empty subset of identify,embed,jpg"):
        build_payload(parser().parse_args(["run", str(photos), "--want", "video"]), BUILTIN)


def test_payload_with_a_profile(photos, tmp_path):
    body = build_payload(parser().parse_args(["run", str(photos), "--profile", "album"]), BUILTIN)
    assert body["want"] == ["identify", "embed", "aesthetics"] and "profile" not in body
    assert body["options"] == {"identify": {"top_k": 5, "geo": True, "species": False}}
    body = build_payload(parser().parse_args(["run", str(photos), "--profile", "album", "--top-k", "2",
                                              "--want", "identify"]), BUILTIN)
    assert body["want"] == ["identify"] and body["options"] == {"identify": {"top_k": 2, "geo": True, "species": False}}
    cfg = profile.load(files(tmp_path, user='[profile.shoot]\nstages = ["identify", "jpg"]\n'
                                            '[profile.shoot.options.jpg]\nout_dir = "/tmp/shoot"\n'
                                            '[profile.shoot.options.identify]\nrange_veto = false\n'), env={})
    body = build_payload(parser().parse_args(["run", str(photos), "--profile", "shoot"]), cfg)
    assert body["want"] == ["identify", "jpg"] and body["options"] == {
        "identify": {"top_k": 5, "geo": True, "species": True, "range_veto": False}, "jpg": {"out_dir": "/tmp/shoot"}}
    with pytest.raises(SystemExit, match=r"unknown profile 'nope'"):
        build_payload(parser().parse_args(["run", str(photos), "--profile", "nope"]), BUILTIN)


# ---- layers --------------------------------------------------------------------------------

def test_merge_order_and_sources(tmp_path):
    cfg = profile.load(files(
        tmp_path,
        user='default_profile = "wildlife"\n[profile.wildlife.options.identify]\ntop_k = 7\ngeo = false\n'
             '[serve]\nport = 9001\nchunk = 8\n',
        project='[profile.wildlife.options.identify]\ntop_k = 9\n[serve]\nchunk = 16\n',
        extra='[profile.wildlife.options.identify]\nkind_check = false\n'), env={})
    assert cfg.default_profile() == ("wildlife", f"default_profile in user {tmp_path}/home/.config/bioscan/bioscan.toml")
    res = profile.resolve(cfg, "wildlife", options={"identify": {"kind_check": True}}, source="flag")
    o, src = res.options["identify"], res.sources["identify"]
    assert (o["top_k"], o["geo"], o["kind_check"], o["species"]) == (9, False, True, True)
    assert src["top_k"] == f"project {tmp_path}/bioscan.toml" and src["geo"].startswith("user ")
    assert src["kind_check"] == "flag" and src["species"] == "default"
    assert profile.resolve(cfg, "wildlife").sources["identify"]["kind_check"] == f"BIOSCAN_CONFIG {tmp_path}/extra.toml"
    assert {k: v for k, (v, _) in cfg.serve().items()} == {"port": 9001, "chunk": 16}


def test_profile_choice(tmp_path):
    cfg = profile.load(files(tmp_path, project='default_profile = "album"\n'), env={"BIOSCAN_PROFILE": "wildlife"})
    assert profile.select(cfg, "full") == ("full", "--profile")
    assert profile.select(cfg, None) == ("wildlife", "BIOSCAN_PROFILE")
    cfg = profile.load(files(tmp_path, project='default_profile = "album"\n'), env={})
    assert profile.select(cfg, None)[0] == "album"
    assert profile.select(BUILTIN, None) == ("full", "default")


@pytest.mark.parametrize("text, message", [
    ('colour = "red"\n', r"unknown keys \['colour'\]"),
    ('[profile.x]\nstage = ["identify"]\n', r"unknown keys profile.x.\['stage'\]"),
    ('[profile.x]\nstages = ["video"]\n', r"profile.x.stages: unknown stages \['video'\]"),
    ('[profile.x]\nstages = []\n', "profile.x.stages must not be empty"),
    ('[profile.x]\nreducers = ["burst"]\n', r"unknown reducers \['burst'\]"),
    ('[profile.x.options.video]\na = 1\n', "unknown stage profile.x.options.video"),
    ('[profile.x.options.identify]\ntopk = 1\n', r"unknown options profile.x.options.identify: \['topk'\]"),
    ('[profile.full.options.identify]\ntop_k = 1\n', r"\[profile.full\] is built in and fixed"),
    ('[serve]\nportt = 1\n', "unknown key serve.portt"),
    ('[serve]\nport = "80"\n', "serve.port must be int"),
    ('[serve]\nallow_roots = "/a"\n', "serve.allow_roots must be a list of paths"),
    ('default_profile = 3\n', "default_profile must be a string"),
    ('[profile\n', "project .*bioscan.toml: "),
])
def test_bad_files_are_errors_naming_the_file(tmp_path, text, message):
    with pytest.raises(ValueError, match=message) as e:
        profile.load(files(tmp_path, project=text), env={})
    assert str(tmp_path / "bioscan.toml") in str(e.value)


def test_missing_files(tmp_path):
    assert profile.load(files(tmp_path), env={}).names() == ["album", "full", "wildlife"]    # none: fine
    with pytest.raises(ValueError, match="BIOSCAN_CONFIG: no such file"):
        profile.load([("BIOSCAN_CONFIG", tmp_path / "none.toml")], env={})
    env = {"BIOSCAN_CONFIG": str(tmp_path / "x.toml"), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    real = getattr(profile.config_files, "real", profile.config_files)       # tests/bioscan_test_env.py
    assert [(k, str(p)) for k, p in real(env, cwd=tmp_path)] == [
        ("user", f"{tmp_path}/xdg/bioscan/bioscan.toml"), ("project", f"{tmp_path}/bioscan.toml"),
        ("BIOSCAN_CONFIG", f"{tmp_path}/x.toml")]


def test_request_errors_keep_their_messages():
    with pytest.raises(ValueError, match=r"^unknown options.identify: \['nope'\]$"):
        profile.resolve(BUILTIN, "full", options={"identify": {"nope": 1}})
    with pytest.raises(ValueError, match=r"^unknown profile 'x' \(known: album, full, wildlife\)$"):
        profile.resolve(BUILTIN, "x")
    with pytest.raises(ValueError, match="profile must be a string"):
        profile.resolve(BUILTIN, 3)


# ---- eval ----------------------------------------------------------------------------------

def test_eval_request():
    assert cli_config.eval_request(None, False, {}, BUILTIN) is None          # the request eval always sent
    r = cli_config.eval_request("album", False, {"kind_check": False}, BUILTIN)
    assert r == {"profile": "album", "want": ["identify", "embed", "aesthetics"],
                 "options": {"identify": {"top_k": 5, "geo": True, "kind_check": False, "species": False}}}
    assert cli_config.eval_request("wildlife", True, {}, BUILTIN)["options"]["identify"]["geo"] is False


def test_eval_meta_line_records_the_profile(tmp_path, monkeypatch):
    from bioscan.cli import client
    from bioscan.cli import eval as ev

    sent = []
    monkeypatch.setattr(client, "health", lambda url: {})
    monkeypatch.setattr(client, "run", lambda payload, url: sent.append(payload) or iter([b'{"type": "done"}']))
    gt = write(tmp_path / "gt.csv", "path,scientific,tier,lat,lon,taken_at,source,kind\n/a.jpg,X y,t,,,,s,bird\n")
    ev.run_eval(str(gt), str(tmp_path / "o"), False, "u", identify_opts={})
    ev.run_eval(str(gt), str(tmp_path / "p"), False, "u", request=cli_config.eval_request("album", False, {}, BUILTIN))
    assert sent[0]["want"] == ["identify"] and sent[0]["options"] == {"identify": {"top_k": 5, "geo": True}}
    assert sent[1]["want"] == ["identify", "embed", "aesthetics"]
    assert "profile" not in ev.read_preds_meta(tmp_path / "o" / "preds.ndjson")
    assert ev.read_preds_meta(tmp_path / "p" / "preds.ndjson")["profile"] == "album"


# ---- serve file layer ---------------------------------------------------------------------

def test_serve_config_flag_env_file_default():
    file = {"port": (9001, "project x"), "chunk": (8, "project x"), "detail_edge": (4000, "user y"),
            "allow_roots": (["~/P"], "user y"), "host": ("0.0.0.0", "user y")}
    c, src = serve_config.resolve_sources(env={}, file=file)
    assert (c.host, c.port, c.chunk, c.detail_edge, c.decode_workers) == ("0.0.0.0", 9001, 8, 4000, 4)
    assert c.allow_roots[0].endswith("/P") and not c.allow_roots[0].startswith("~")
    assert src == {"host": "user y", "port": "project x", "decode_workers": "default", "chunk": "project x",
                   "detail_edge": "user y", "allow_roots": "user y"}
    c, src = serve_config.resolve_sources(chunk=2, env={"BIOSCAN_DETAIL_EDGE": "5000", "BIOSCAN_CHUNK": "4"}, file=file)
    assert (c.chunk, src["chunk"], c.detail_edge, src["detail_edge"]) == (2, "flag", 5000, "env BIOSCAN_DETAIL_EDGE")
    assert serve_config.resolve(env={}, file={"chunk": 8}).chunk == 8
    with pytest.raises(SystemExit, match="^chunk must be >= 1$"):
        serve_config.resolve(env={}, file={"chunk": 0})


def test_serve_reads_the_file(tmp_path, monkeypatch):
    from bioscan.service import app

    got = []
    monkeypatch.setattr(app, "serve", got.append)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("XDG_CONFIG_HOME", "BIOSCAN_CONFIG", "BIOSCAN_CHUNK", "BIOSCAN_ALLOW_ROOTS", "BIOSCAN_DETAIL_EDGE",
                "BIOSCAN_DECODE_WORKERS"):
        monkeypatch.delenv(var, raising=False)
    project = write(tmp_path / "bioscan.toml", '[serve]\nport = 9100\nchunk = 12\nallow_roots = ["/photos"]\n')
    monkeypatch.setattr(profile, "config_files", lambda env=None: [("project", project)])
    assert main(["serve"]) == 0
    app.main([])
    assert got[0] == got[1] == serve_config.ServeConfig("127.0.0.1", 9100, 4, 12, 3072, ["/photos"])
    assert main(["serve", "--port", "9200"]) == 0 and got[2].port == 9200


def test_launchd_bakes_the_file_and_points_the_job_at_it(tmp_path, monkeypatch, capsysbinary):
    import plistlib

    project = write(tmp_path / "bioscan.toml", '[serve]\nport = 9100\nallow_roots = ["/photos"]\ndetail_edge = 4096\n')
    monkeypatch.setattr(profile, "config_files", lambda env=None: [("project", project)])
    for var in ("BIOSCAN_CHUNK", "BIOSCAN_DECODE_WORKERS"):
        monkeypatch.delenv(var, raising=False)
    assert main(["serve", "--launchd", "--chunk", "8"]) == 0
    p = plistlib.loads(capsysbinary.readouterr().out)
    args = p["ProgramArguments"]
    assert args[args.index("--port") + 1] == "9100" and args[args.index("--chunk") + 1] == "8"
    assert args[-4:] == ["--allow-root", "/photos", "--detail-edge", "4096"]
    assert p["EnvironmentVariables"]["BIOSCAN_CONFIG"] == str(project)


# ---- config show ---------------------------------------------------------------------------

def test_config_show(tmp_path, capsys):
    cfg = profile.load(files(tmp_path, project='[profile.album.options.identify]\ntop_k = 3\n[serve]\nchunk = 8\n'),
                       env={})
    d = cli_config.show("album", cfg, env={"BIOSCAN_DETAIL_EDGE": "4000"})
    assert d["profile"] == {"name": "album", "from": "--profile"}
    assert d["stages"] == {"want": ["identify", "embed", "aesthetics"], "from": "profiles.toml",
                           "run_order": ["aesthetics", "embed", "identify"]}
    assert d["models"] == ["owlv2", "siglip2"] and d["frame_pass"] and d["detail_copy"]
    assert d["options"]["identify"]["top_k"] == {"value": 3, "from": f"project {tmp_path}/bioscan.toml"}
    assert d["options"]["identify"]["species"] == {"value": False, "from": "profiles.toml"}
    assert d["options"]["embed"]["format"] == {"value": "list", "from": "default"}
    assert d["serve"]["chunk"]["from"].startswith("project ") and d["serve"]["detail_edge"]["from"] == "env BIOSCAN_DETAIL_EDGE"
    text = cli_config.show_text(d)
    assert "profile   album  (from --profile" in text and "identify.species" in text and "serve.chunk" in text
    assert cli_config.show(None, BUILTIN, env={})["profile"] == {"name": "full", "from": "default"}


def test_config_show_command_stays_import_light(tmp_path):
    """`bioscan config show` (and --profile on run/eval/bench) resolve profiles with no heavy import."""
    heavy = ("numpy", "torch", "transformers", "open_clip", "PIL")
    code = ("import sys; from bioscan.cli import main; main.main(['config', 'show', '--profile', 'album']); "
            "main.main(['config', 'show', '--json']); "
            f"print('HEAVY', [m for m in sys.modules if m.split('.')[0] in {heavy!r} "
            "or m.startswith('bioscan.service') or m.endswith('.stage')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path,
                         env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}).stdout
    assert "HEAVY []" in out and "run order aesthetics, embed, identify" in out and '"run_order"' in out
