"""The CLI's side of profiles (bioscan/profile.py): load the bioscan.toml files, expand the chosen
profile under the command line's flags, build eval's request, and `bioscan config show`. Stdlib only."""
from __future__ import annotations

import json
import os
from typing import Any

from bioscan import contract, profile, serve_config

PROFILE_HELP = ("profile to expand: its stages and options, under these flags (bioscan/profiles.toml: full, wildlife, "
                "album; more in bioscan.toml). default: env BIOSCAN_PROFILE, else default_profile in bioscan.toml, "
                "else full (the behaviour before profiles)")
FLAG = "flag"


def load_config() -> profile.Config:
    """profiles.toml plus the bioscan.toml files; a bad file is a usage error naming it."""
    try:
        return profile.load()
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None


def expand(config: profile.Config, name: str | None, want: list[str] | None, options: dict) -> profile.Resolved:
    """The CLI's profile (profile.select) expanded under the flags' `want` and `options`."""
    chosen, source = profile.select(config, name)
    try:
        return profile.resolve(config, chosen, want, options, source=FLAG)
    except ValueError as e:
        raise SystemExit(f"error: profile {chosen} (from {source}): {e}") from None


def request_options(res: profile.Resolved, always: dict[str, dict[str, Any]] | None = None) -> dict[str, dict]:
    """The `options` of a request for `res`: per wanted stage, `always` (sent whatever their source)
    and then every value a profile layer or a flag set (defaults are left to the service)."""
    out: dict[str, dict] = {}
    for m in contract.PRODUCTS:
        if m in res.want:
            o = dict((always or {}).get(m, {}))
            o |= {k: v for k, v in res.options[m].items() if res.sources[m][k] != profile.DEFAULT and k not in o}
            if o:
                out[m] = o
    return out


def eval_request(name: str | None, no_geo: bool, identify_opts: dict,
                 config: profile.Config | None = None) -> dict | None:
    """`bioscan eval` / `bench run` with a profile: {"profile", "want", "options"} for the run, or
    None when no profile is chosen (the request eval sent before profiles). eval always asks for
    top_k 5 (Top-5 is a metric); --no-geo and --identify-opt override the profile."""
    config = config or load_config()
    chosen, source = profile.select(config, name)
    if source == profile.DEFAULT:
        return None
    res = expand(config, name, None, {"identify": {"top_k": 5, **({"geo": False} if no_geo else {}), **identify_opts}})
    if "identify" not in res.want:
        raise SystemExit(f"error: profile {chosen} has no identify stage; eval scores identify")
    first = {"identify": {"top_k": 5, "geo": res.options["identify"]["geo"], **identify_opts}}
    return {"profile": chosen, "want": res.want, "options": request_options(res, first)}


def show(name: str | None, config: profile.Config | None = None, env=os.environ) -> dict[str, Any]:
    """What `bioscan config show` prints: the files, the chosen profile, its plan and every value's source."""
    config = config or load_config()
    chosen, source = profile.select(config, name)
    try:
        res = profile.resolve(config, chosen)
        serve, serve_src = serve_config.resolve_sources(env=env, file=config.serve())
    except (ValueError, SystemExit) as e:
        raise SystemExit(f"error: profile {chosen} (from {source}): {e}") from None
    return {
        "files": [{"file": "bioscan/profiles.toml", "role": "built in", "found": True}]
                 + [{"file": path, "role": label, "found": found} for label, path, found in config.files],
        "profiles": config.names(),
        "profile": {"name": chosen, "from": source},
        "stages": {"want": res.want, "from": res.want_source, "run_order": list(res.plan.stages)},
        "reducers": res.reducers,
        "models": list(res.plan.models),
        "frame_pass": res.plan.frame,
        "detail_copy": res.plan.detail,
        "options": {m: {k: {"value": v, "from": res.sources[m][k]} for k, v in res.options[m].items()}
                    for m in res.plan.stages},
        "serve": {k: {"value": getattr(serve, k), "from": serve_src[k]} for k in serve_src},
    }


def show_text(d: dict[str, Any]) -> str:
    def val(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)

    lines = ["files (lowest layer first):"]
    lines += [f"  {f['file']}  ({f['role']}{'' if f['found'] else ', not found'})" for f in d["files"]]
    lines += [f"profile   {d['profile']['name']}  (from {d['profile']['from']}; known: {', '.join(d['profiles'])})",
              f"stages    {', '.join(d['stages']['want'])}  (from {d['stages']['from']}); "
              f"run order {', '.join(d['stages']['run_order'])}",
              f"reducers  {', '.join(d['reducers']) or 'none'}",
              f"models    {', '.join(d['models']) or 'none'}",
              f"frame pass {'yes' if d['frame_pass'] else 'no'}, detail copy {'yes' if d['detail_copy'] else 'no'}",
              "options:"]
    rows = [(f"{m}.{k}", val(o["value"]), o["from"]) for m, opts in d["options"].items() for k, o in opts.items()]
    rows += [(f"serve.{k}", val(o["value"]), o["from"]) for k, o in d["serve"].items()]
    width = max(len(r[0]) for r in rows)
    vwidth = min(max(len(r[1]) for r in rows), 40)
    for key, v, src in rows:
        if key.startswith("serve.") and not lines[-1].startswith("  serve."):
            lines.append("serve:")
        lines.append(f"  {key:<{width}}  {v:<{vwidth}}  {src}")
    return "\n".join(lines)


def cmd_config_show(a) -> int:
    d = show(a.profile)
    print(json.dumps(d, indent=2, ensure_ascii=False) if a.json else show_text(d))
    return 0


def add_parser(sub) -> None:
    c = sub.add_parser("config", help="profiles and bioscan.toml").add_subparsers(dest="config_cmd", required=True)
    s = c.add_parser("show", help="the resolved profile, plan and settings, with where each value came from")
    s.add_argument("--profile", help=PROFILE_HELP)
    s.add_argument("--json", action="store_true", help="print JSON")
    s.set_defaults(func=cmd_config_show)
