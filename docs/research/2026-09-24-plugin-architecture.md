# Profiles and stage plugins: architecture proposal (2026-09-24)

> Owner decisions (2026-09-24): profiles expand in both CLI and service (`"profile"` in /run); reducers (burst, select) run in the CLI / offline; migration steps 0-4 land first, then geotag (step 5) and culling (step 7) as plugins.


## 1. What the references are

**Pi harness.** This is `pi`, Mario Zechner's minimal coding agent in the `pi-mono` TypeScript monorepo. An **extension** is a module whose default factory receives `ExtensionAPI` and registers everything through it: `pi.on(event)`, `registerTool`, `registerCommand`, `registerFlag` and `registerProvider`. The events include `session_start`, `session_shutdown`, `before_agent_start`, `context`, `tool_call`, `tool_result`, `message_end`, `turn_end` and `agent_end`. Handlers run sequentially in registration order. Some events only notify. Others can transform or cancel: `tool_call` can mutate or block a call. Extensions come from user or project directories, a `--extension` flag, or npm/git "pi packages". Settings merge as "project settings override agent-directory settings; resource lists are combined", with `!pattern`/`+path`/`-path` filters. [extensions.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md), [settings.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/settings.md). Two claims come only from search snippets: that hooks and tools were merged into extensions in v0.35 ([X post](https://x.com/badlogicgames/status/2008008000309469665)), and "four tools, system prompt under 1,000 tokens" ([explainx](https://www.explainx.ai/blog/pi-minimal-agent-harness-mario-zechner-guide-2026)). Both are unverified. Armin Ronacher's and Mario's blogs were blocked.

**DeepSeek harness.** This is `deepseek-ai/deepseek-harness` ("dsh"), an MIT-licensed Node agent harness where "everything is a plugin" ([repo](https://github.com/deepseek-ai/deepseek-harness)). Models, tools, skills, sessions, sandboxes, storage, loops and UI are all plugins. Its kernel is **Cordis**, which was split out of the Koishi chatbot framework. A plugin is `apply(ctx)` plus an optional `inject` (the services it requires) and an optional `Config` schema. Services claim namespaced keys (`ctx.tools`, `ctx.llm`), so "load order is expressed through service requirements rather than manual boot sequencing". Events dispatch as `emit`, `waterfall`, `parallel`, `serial` or `bail`. Registrations made through `ctx.effect()`/`ctx.on()` are reversible, so reload and teardown unwind them cleanly. "Standard" and "Minimal" are different compositions of the same plugins ([cordis-primer.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cordis-primer.md)). Two further details are unverified: the plugin topic `dsh-plugin` and the Standard/Minimal tool sets (the repo page, and the [MindStudio](https://www.mindstudio.ai/blog/deepseek-harness-agentic-coding) snippet). The docs site and The New Stack were blocked. No other candidate found.

**What bioscan should take from them.** From pi: a tiny core, one registration API, a project-over-user config merge. From Cordis: declared dependencies (`inject`) decide order, and named compositions of one plugin set (Standard/Minimal) are exactly profiles. Not to take: both are interactive agent loops with mutable sessions and hot reload; bioscan is a batch pipeline whose results must be reproducible.

## 2. Plugin patterns: what works, what fails

| Pattern | Works | Fails / cost |
|---|---|---|
| **pluggy** ([docs src](https://github.com/pytest-dev/pluggy/blob/main/docs/index.rst)) | Specs checked against impls. An impl "can accept less arguments than defined in the spec", so specs can grow. `firstresult`, wrappers, `warn_on_impl` for deprecation. | Order is implicit (LIFO plus `tryfirst`/`trylast`), which is poor for reproducible results. Hooks are per-call, not batch-shaped. |
| **Entry points** (`load_setuptools_entrypoints`) | Standard discovery for pip-installed packages. | `load()` imports the module, so a heavy plugin breaks the import-light CLI. There is no version negotiation. python.org docs were blocked (unverified). |
| **darktable pixelpipe** ([iop_api.h](https://github.com/darktable-org/darktable/blob/master/src/iop/iop_api.h), [manual](https://docs.darktable.org/usermanual/development/en/darkroom/pixelpipe/the-pixelpipe-and-module-order/) blocked) | Fixed module order, per-module params with `legacy_params` migration, results cached per node. | The module ABI is compiled in, so there are no third-party modules in practice. The order schemes (legacy, v3.0, v5.0) need migrations. |
| **digiKam DPlugin / BQM** ([demo](https://github.com/cgilles/digikam-plugins-demo)) | One interface family (generic, editor, BQM, raw import). BQM tools are plugins loaded at startup. | Plugins must build against the matching digiKam version (≥ 8.2), which ties them to its release cycle. |
| **Lightroom Classic SDK** ([search](https://akrabat.com/writing-a-lightroom-classic-plug-in/), pages blocked) | `Info.lua` manifest with `LrSdkVersion`/`LrSdkMinimumVersion`, a declared host range. | Lua-only, host-bound; more is unverified. It is bioscan's future *consumer*, not a model for its internals. |
| **ComfyUI nodes** ([example](https://github.com/comfyanonymous/ComfyUI/blob/master/custom_nodes/example_node.py.example)) | Nodes declare typed inputs and outputs (now `define_schema()`/`execute()`), with lazy inputs via `check_lazy_status`. | Dependency hell: every node brings its own `requirements.txt` and they conflict in one venv ([#7055](https://github.com/Comfy-Org/ComfyUI/issues/7055), [dependencies](https://docs.comfy.org/development/core-concepts/dependencies)). Failures only show up when a node first runs. |
| **FiftyOne plugins** ([repo](https://github.com/voxel51/fiftyone-plugins)) | A `fiftyone.yml` manifest declares name, version, host-version requirement, operators and secrets. Operators separate `resolve_input` from `execute`. | Install-from-GitHub plus `requirements.txt` has the same shared-venv risk. |
| **Label Studio ML backend** ([repo](https://github.com/HumanSignal/label-studio-ml-backend)) | Each model is a separate HTTP server/Docker with its own deps (`predict`, `fit`, `model_version`), so isolation is complete. | Needs a process per plugin plus network and ops overhead. bioscan already *is* such a backend. |

**Lessons.** (1) Declare inputs and outputs, as ComfyUI and Cordis do, so the host can compute what to load and in which order. (2) Put a manifest with a host-version range in stdlib data (FiftyOne, LrC). (3) Keep dependencies in one venv that the repo owns until there is a real third-party need, because shared-venv plugin ecosystems degrade. (4) Version each unit's params, as darktable does with `legacy_params`.

## 3. bioscan today

| Seam | Plugin-shaped? | Notes |
|---|---|---|
| `products.REGISTRY` (`Product`: needs, uses_frame, on_model_thread, defaults, check, schema, run, writes, check_loaded) | **Yes, nearly.** | Runners are batch-shaped (`(engine, items, opts) -> list[out \| Exception]`). Missing: a version, declared data inputs/outputs, per-unit settings, metrics. `needs` is static, so `identify` with `species=false` still loads BioCLIP. |
| `contract.PRODUCTS` | Hard-wired tuple | It also fixes run order. `app.parse_run` and the CLI `--want` check use it. |
| `engine.Loaders` / `MODELS` | Seam for three fixed models | A frozen dataclass with three fields. A new model (aesthetics) means editing the Engine. |
| `pipeline.identify_many` | One deep module | Gate → detect → crop check → species, all batched across frames. It must stay one unit: splitting it into per-box hooks would break batching. |
| `rules.py` / `pipeline.SWITCHES` | Option switches | Good pattern: each fix is an option, measurable off/on, fingerprinted. |
| `run.RunQueue._run_products` | Generic loop | One special case: `edge = … if "identify" in want`. |
| Contract events | Open for new product keys | "Adding a field is not a bump." Unknown event *types* fail `missing_fields`. |
| `serve_config.resolve` | Flag > env > default | No file layer, serve settings only. |
| Harness (`bench.py`) | Identify-only | `METRICS`/`RATES` are fixed and "every scope has exactly these keys". Standards are keyed `<dimension>.<tier>.<scope>.<metric>`. |
| `settings.fingerprint()` | Identify-only | It includes every UPPER_CASE number in `rules.py`. |

## 4. Target architecture

### 4.1 Choice: a declared stage list, not lifecycle hooks

I pick **stages with declared `reads`/`provides`**, sorted once when the profile is resolved. There is no runtime scheduler. Three reasons:

1. The expensive work is batched per chunk on one model thread. `on_box`/`on_frame` hooks would turn batched GPU calls into per-item ones, the speed regression bench already guards against. A stage keeps the current runner shape: one call per chunk.
2. Declared inputs let the resolver answer "which models, which stages, in what order" *before anything loads*. A bad profile is a 400 and unneeded models never load (Cordis's `inject` idea).
3. Output must be reproducible and fingerprinted. An explicit topological order, with ties broken by name, beats pluggy's LIFO/`tryfirst`.

Cross-image logic (burst, selection) is a second, model-free kind of unit: a **reducer** over result events. Reducers run in the CLI or offline over a preds file, like `bench report`, so the service stays stateless.

### 4.2 Interface sketch

```python
# bioscan/plugin.py  (stdlib only: CLI, profiles and /products import it; no torch)
@dataclass(frozen=True)
class Metric:                                   # harness: one per-image field -> one scope metric
    name: str                                   # "keep_precision"
    kind: Literal["rate", "median"]
    lower_is_better: bool = False
    row: str = ""                               # dotted "module:function" (stdlib), image row -> value | None

@dataclass(frozen=True)
class Manifest:                                 # what a plugin declares, importable without its deps
    name: str                                   # also the product key in result.products
    version: int                                # bump when output meaning changes (like contract.SCHEMA)
    kind: Literal["stage", "reducer"]
    reads: tuple[str, ...] = ()                 # facts: "image", "detail", "vec", "gate", "boxes", "place", "time"
    provides: tuple[str, ...] = ()
    models: Callable[[dict], tuple[str, ...]] = lambda o: ()   # model names needed under these options
    thread: Literal["model", "cpu"] = "cpu"
    options: dict[str, Any] = field(default_factory=dict)      # JSON-schema-ish, with defaults (/products)
    output: dict[str, Any] = field(default_factory=dict)       # description served by /products
    impl: str = ""                              # "bioscan.plugins.quality.stage:STAGE", imported lazily
    metrics: tuple[Metric, ...] = ()
    requires_bioscan: str = ">=1.6,<2"          # host range, like LrSdkMinimumVersion / fiftyone.yml

class Stage(Protocol):                          # service side; may import numpy/torch at module level
    def check(self, opts: dict) -> None: ...                    # ValueError -> 400
    def check_loaded(self, engine: Any, opts: dict) -> None: ...
    def writes(self, opts: dict) -> list[str]: ...              # allow-roots
    def reads_paths(self, opts: dict) -> list[str]: ...         # allow-roots (e.g. a GPX file)
    def settings(self) -> dict[str, Any]: ...                   # this stage's fingerprint input
    def run(self, engine: Any, items: list["Item"], opts: dict) -> list[Any | Exception]: ...

@dataclass
class Item:                                     # today's products.Item plus facts from earlier stages
    dec: Any; inp: dict[str, Any]; vec: Any = None; gate: dict | None = None
    facts: dict[str, Any] = field(default_factory=dict)        # "boxes", "place", ...

class Reducer(Protocol):                        # stdlib, runs over result events (CLI / offline)
    def reduce(self, results: Iterable[dict], opts: dict) -> Iterator[dict]: ...  # per-image additions

def resolve(profile: str, request: dict, *, file: Path | None, env: Mapping[str, str]) -> "Plan": ...
    # -> Plan(stages=[topo order], opts={name: merged}, models=set, reducers=[...]); ValueError on a cycle,
    #    a read nobody provides, an unknown option, or a host-version mismatch
```

`identify` becomes a stage that provides `gate`, `boxes` and `species`. Its body stays `pipeline.identify_many` unchanged. Its `models(o)` returns `("siglip2","owlv2")` plus `"bioclip"` only when `o["species"]`. The Engine gets `Loaders.extra: dict[str, Callable[[str], Any]]` and an extendable `MODELS`, so a plugin model is lazy-loaded and fake-injectable by name, like the three today.

### 4.3 Profiles and `bioscan.toml`

A profile is a named request template: stages, reducers and options. Built-in profiles ship in `bioscan/profiles.toml`. The user's `bioscan.toml` (project, then `~/.config/bioscan/`) may override or add profiles.

```toml
# bioscan.toml
default_profile = "wildlife"

[serve]                                  # serve_config: flag > BIOSCAN_* env > this file > built-in default
port = 8765
chunk = 32
detail_edge = 3072
allow_roots = ["~/Pictures/Wildlife", "~/Pictures/Album"]

[profile.wildlife]                       # user A: names first, aesthetics off; BioCLIP loads
stages = ["geotag", "identify"]
[profile.wildlife.options.identify]
top_k = 5
geo = true
[profile.wildlife.options.geotag]
gpx = ["~/Tracks/2026-05-01.gpx"]
max_gap_s = 300
camera_utc_offset = "-07:00"             # for bodies whose EXIF has no OffsetTimeOriginal

[profile.album]                          # user B: culling, no species; BioCLIP never loads
stages = ["identify", "quality", "scene", "aesthetics", "embed"]
reducers = ["burst", "select"]
[profile.album.options.identify]
species = false                          # boxes still give subject focus; needs SigLIP2 + OWLv2 only
[profile.album.options.scene]
labels = ["landscape", "portrait", "street", "food", "wildlife"]
[profile.album.options.burst]
max_gap_s = 1.5
min_cosine = 0.92
[profile.album.options.select]
per_burst = 1
min_sharpness_rank = 0.5
```

**Merge order.** Built-in stage defaults < `profiles.toml` < project/user `bioscan.toml` profile < `BIOSCAN_PROFILE`/`BIOSCAN_CONFIG` < CLI flags / request `options`. `full` is today's behaviour (identify, embed, jpg as asked), and a request with no `profile` resolves exactly as today. `/run` accepts `"profile": "album"` and expands it with the same stdlib resolver the CLI uses, as `serve_config` is shared today. Unknown keys are errors, as in `budget.toml`.

### 4.4 Registration, statelessness, import-lightness

- **In-repo first.** `bioscan/plugins/<name>/__init__.py` holds only the stdlib `MANIFEST`. `stage.py` holds the heavy code and is imported only when a plan contains the stage. A single list `bioscan.plugins.BUILTIN` replaces the hand-kept `contract.PRODUCTS`. The CLI and `/products` read manifests without torch.
- **Entry points later.** Group `bioscan.plugins`, where each entry resolves to a `Manifest` object. Loading it imports only the stdlib `__init__`. The host checks `requires_bioscan` and refuses a mismatch with a clear 400.
- **Stateless.** Stages see one chunk. Reducers see one run's events and can be re-run from the preds file. Nothing persists between runs except files the user asks for.

### 4.5 Output contract

Each stage with a product writes `result.products[<name>]`. The result event gains `engine.plugins: {name: "v<version>@<settings fp>"}`. `engine.settings` stays the identify fingerprint, byte-identical, so existing baselines stay comparable. Third-party products are namespaced `<dist>.<name>` (e.g. `acme.nima`). Reducers add per-image keys under `products["<reducer>"]` in the CLI's output (`bioscan cull --json`) and in a reduced preds file, never inside the service stream. No new event types, so `contract.SCHEMA` stays 1. Contract tests gain `problems_of(product)` per manifest, the way `identify_problems` works today.

### 4.6 Harness per profile

- Report rows get `profile` in `meta`. Core `metrics` stay as they are. Plugin metrics go in a new top-level `plugin_metrics[plugin][scope]`, which is additive and not a version bump. They are computed from each `Metric.row` (stdlib) and get Wilson intervals from the existing helpers.
- `album` ground truth: a CSV with `keep`, `sharp_ok`, `scene`. Tiers `album-smoke`, `album-own`. Standards gain `profile` (default `wildlife`); budget rules may name plugin metrics.

### 4.7 Lazy models

`Plan.models` is the union of `models(opts)` over the plan's stages. `Engine.ensure` takes that set instead of `want`. `album` loads SigLIP2 and OWLv2 plus an aesthetics model only. `wildlife` never loads the aesthetics model. The detail copy is decoded only when a stage reads `detail`, which replaces the `"identify" in want` special case.

## 5. Migration, one behaviour-preserving step at a time

Every step must be green under `uv run ruff check .` and `uv run pytest`, plus `ci.yml` and `models.yml`. "Same" means **(a)** a fake-engine golden stream: the contract suite gets a recorded NDJSON (timings stripped) for 5 option sets, compared byte for byte; **(b)** `/products` JSON unchanged; **(c)** `settings.fingerprint()` unchanged; **(d)** `bench compare baselines/ci-smoke.json` with 0 broken images and within budget in `models.yml`, and on the Mac for golden/own when step 2 or 7 touches identify.

| # | Step | Verification |
|---|---|---|
| 0 | Add the golden-stream recording test (a–c above) before any refactor | Passes on `main` |
| 1 | `Product` → `Manifest` + `Stage` in-repo. `contract.PRODUCTS` derived from `plugins.BUILTIN`. Same order | a, b, c, d |
| 2 | `models(opts)`: `identify` with `species=false` skips BioCLIP. The detail decode is keyed on `reads` | a, c. New contract test: `engine.loaded()` lacks `bioclip`. d |
| 3 | `Item.facts` plus topological plan from `reads`/`provides`. `Loaders.extra` | a–d. Unit tests: cycle and missing-provider errors |
| 4 | `bioscan/profile.py` (stdlib) + `bioscan.toml` + `serve_config` file layer + `--profile`/`"profile"`. `full` = today | A unit test shows the built payload without a config equals today's. The `serve_config` tests extend to the file layer. a–d |
| 5 | **`geotag` plugin (GPX)**: stdlib parser, CPU stage, provides `place` when both request and EXIF lack it. GPX path checked by allow-roots | Off by default → a–d unchanged. New unit tests for interpolation, gaps and time zones. Then an own-tier run with a GPX: README says one coordinate lifted own top-1 from 79 % to 96 %, which is **unverified for GPX** until run |
| 6 | Harness: `meta.profile`, `plugin_metrics`, standards `profile` field | `bench report` on an existing preds file gives the same core `metrics` (a JSON diff ignoring the new keys) |
| 7 | **Cull plugins**: `quality` (frame and subject sharpness/exposure, reusing `rules.quality`), `scene` (zero-shot on the existing SigLIP2 vector: no new model), then reducers `burst` (capture time + `embed` cosine) and `select`, then `aesthetics` (model to be chosen by the owner) | Each is off in `wildlife`/`full` → a–d unchanged. The album tier gets its own baseline |
| 8 | Entry points + `requires_bioscan` check | A test plugin package installed in a tmp venv in CI |

**Build first:** steps 0–4. That is the minimum for `geotag` and the cull work to land as plugins instead of edits to `products.py`/`run.py`. Step 2 is the first user-visible win, because `album` skips BioCLIP's load and memory.

## 6. Anti-goals (not now)

- **Marketplace, remote install, `bioscan plugins download`.** ComfyUI and FiftyOne show that a shared venv of unreviewed requirements rots. There is also no third-party demand yet.
- **Remote/HTTP plugins (Label Studio style).** bioscan is already the HTTP backend. A per-plugin process adds latency and ops work, and breaks the single model turn.
- **A general DAG engine** (scheduling, per-node caching, conditional branches, UI graph). With fewer than 10 stages, a static sort is enough. Caching contradicts "stateless".
- **Per-box/per-frame hooks and pluggy itself.** Reconsider pluggy only for cross-cutting observers (e.g. an XMP writer on run end) once there are two or more such observers.
- **Hot reload, plugin event types, per-plugin venvs.**

## 7. Risks

1. **Reproducibility drift.** A stage constant outside `rules.py` escapes the fingerprint. Mitigation: `Stage.settings()` is mandatory and a test requires it to be non-empty for stages with thresholds.
2. **Speed.** Keep `identify` one batched stage; `images_per_s` budget guards the plan loop.
3. **Memory on MPS.** `full` plus aesthetics loads four models next to the ~0.9 GiB all-taxa list. Report loaded models in `/health` and document per-profile memory.
4. **Config confusion.** Five layers. Mitigation: `bioscan config show --profile X` prints the resolved plan and each value's source.
5. **Contract growth.** Each product needs a conformance checker like `contract.identify_problems`.
6. **GPX time.** Many bodies write no UTC offset, and a wrong offset silently misplaces photos, which moves the location prior and the range veto. Require `camera_utc_offset` when `taken_at` has none. Report `place_source: "gpx"` per image.
7. **Harness imports.** Plugin metric code must stay stdlib; enforce with an import test.
8. **Chunk boundaries.** Bursts cross chunks, which is why burst and select are reducers and not stages.

## Open decisions for the owner

1. **Where profiles expand:** (a) in both the CLI and the service via `"profile"` in `/run` (proposed), or (b) CLI-only, with the service seeing only `want`/`options`.
2. **Where reducers run:** (a) CLI/offline over results (proposed), or (b) the service at run end, which needs a new event type and a SCHEMA review.
3. **Aesthetics model** for step 7 (licence and MPS memory), chosen before that step starts.
