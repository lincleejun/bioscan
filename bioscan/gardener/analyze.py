"""Analysis through BioScan (docs/gardener/contract.md §3, §4, §10 X2/X3): the capabilities' stages run
in-process on bioscan.service.run.RunQueue, each result is normalised per capability for the UI,
and the raw products are kept (`raw:<stage>`) so `reduce` can run the burst and select reducers
(bioscan.cull) over a whole task later. Model-free capabilities (meta, place_event) are not run
here. A model that does not load makes its capabilities `unavailable` with the reason; no score is
ever made up."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any

import bioscan
from bioscan import contract, cull, profile
from bioscan.gardener import capabilities

ALBUM = "album"         # the profile whose scene, burst and select options Gardener uses

GROUP_ZH = {"wildlife": "野生动物", "landscape": "风光", "night": "夜景", "people": "人物", "macro": "微距",
            "architecture": "建筑", "food": "美食", "other": "其他", cull.UNCATEGORISED: "未分类"}
LABEL_ZH = {
    "bird_portrait": "鸟类特写", "bird_flight": "飞鸟", "bird_habitat": "鸟与生境", "mammal_portrait": "哺乳动物特写",
    "mammal_habitat": "哺乳动物与生境", "other_animal": "其他动物", "herd_flock": "鸟群兽群", "domestic": "家养动物",
    "mountain": "山地", "coast": "海岸", "freshwater": "湖泊河流", "forest": "森林", "grassland": "草原",
    "desert": "荒漠", "snow_ice": "冰雪", "sky": "天空", "astro": "星空", "aurora": "极光", "moon": "月亮",
    "city_lights": "城市夜景", "portrait": "人像", "group": "合影", "street": "街拍", "event": "活动",
    "sport": "运动", "flower": "花卉", "plant": "植物", "fungi": "菌类", "insect_macro": "昆虫微距",
    "building": "建筑外观", "interior": "室内", "cityscape": "城市风貌", "monument": "古迹地标", "rural": "乡村",
    "food_drink": "餐饮", "vehicle": "交通工具", "still_life": "静物", "art": "艺术品", "utility": "文档截图",
    "abstract": "抽象"}
REASON_ZH = {"soft_subject": "主体不清晰", "motion": "运动模糊", "defocus": "失焦", "motion_or_defocus": "运动模糊或失焦",
             "overexposed": "过曝", "underexposed": "欠曝", "subject_cut": "主体被画面边缘裁切",
             "subject_too_small": "主体过小", "no_subject": "像有动物但未找到主体"}
FLAG_ZH = {"horizon_tilt": "提示：地平线倾斜", "tight_headroom": "提示：主体上方空间紧", "no_lead_room": "提示：主体朝向一侧空间不足"}


@dataclass
class ItemResult:
    asset_id: str
    capability: str          # a capability id, or raw:<stage> (the stage's raw product)
    status: str              # done | failed | unrecognized | unavailable
    data: dict | None
    error: str | None
    version: str | None


def zh(name: str | None) -> str | None:
    """A scene group or label in Chinese (the English id when there is no translation)."""
    return None if name is None else GROUP_ZH.get(name) or LABEL_ZH.get(name) or name


def _resolve(caps: list[str]) -> profile.Resolved:
    """The album profile with the stages `caps` need; identify names species only with `species`."""
    want = capabilities.stages_for(caps)
    options: dict[str, dict[str, Any]] = {"identify": {"species": "species" in caps}}
    if "embed" in want:
        options["embed"] = {"format": "f16_base64"}
    return profile.resolve(profile.builtin(), ALBUM, want, options, check=lambda m, o: m.check(o))


def _model_caps(caps: list[str]) -> list[str]:
    return [c for c in caps if capabilities.BY_ID[c].stages]


def _short(model_id: str) -> str:
    return model_id.rsplit("@", 1)[-1]


# ---- normalising one result -------------------------------------------------------------------

def _content(p: dict[str, Any]) -> tuple[str, dict | None]:
    s = p.get("scene")
    if not s:
        return "unrecognized", None
    label, group = s.get("label"), s.get("group") or s.get("label")
    scores = s.get("scores") or {}
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:5]
    return "done", {"group": group, "group_zh": zh(group), "label": label, "label_zh": zh(label),
                    "score": scores.get(label), "top": [[k, v] for k, v in top]}


def _subject(p: dict[str, Any]) -> tuple[str, dict | None]:
    ident = p.get("identify") or {}
    boxes = [{"xyxy": b["xyxy"], "score": b["score"], "kind": b["kind"]} for b in ident.get("boxes") or []]
    data = {"class": (ident.get("gate") or {}).get("class"), "boxes": boxes}
    return ("done" if boxes else "unrecognized"), data


def _species(p: dict[str, Any]) -> tuple[str, dict | None]:
    """The best box with a named candidate (boxes come highest score first)."""
    for b in (p.get("identify") or {}).get("boxes") or []:
        sp = b.get("species")
        if sp and sp.get("top"):
            first, level = sp["top"][0], sp["level"]
            return "done", {"taxon": sp["taxon"], "common": first["common"] if level == "species" else None,
                            "level": level, "p": first["posterior"] if contract.mass(sp) is None else contract.mass(sp),
                            "top": [{"scientific": c["scientific"], "common": c["common"], "p": c["posterior"]}
                                    for c in sp["top"]],
                            "uncertain": level != "species"}
    return "unrecognized", None


def _quality(p: dict[str, Any]) -> tuple[str, dict | None]:
    q = p.get("quality")
    if q is None:
        return "unrecognized", None
    reasons = list(q.get("reject_reasons") or [])
    return "done", {"sharpness": cull._sharpness(q), "exposure": cull._exposure(q), "reasons": reasons,
                    "reasons_zh": [REASON_ZH.get(r, r) for r in reasons]}


def _aesthetic(p: dict[str, Any]) -> tuple[str, dict | None, str | None]:
    a = p.get("aesthetics") or {}
    if a.get("score") is None:      # no head scored it: say why, never a number
        return "unavailable", None, a.get("note") or "美学模型未给出分数"
    return "done", {"score": a["score"]}, None


def _burst(p: dict[str, Any]) -> tuple[str, dict | None]:
    e = p.get("embed")
    return ("done", {"vector_dim": e.get("dim")}) if e else ("unrecognized", None)


NEEDS = {"content": ("identify", "scene"), "subject": ("identify",), "species": ("identify",),
         "burst": ("embed",), "quality": ("identify", "quality"), "aesthetic": ("aesthetics",)}


def normalise(asset_id: str, products: dict[str, Any], caps: list[str], versions: dict[str, str],
              stage_versions: dict[str, str]) -> list[ItemResult]:
    """One BioScan result as an ItemResult per model capability in `caps`, then one raw:<stage> per product."""
    out = []
    for c in _model_caps(caps):
        error = None
        if c == "aesthetic":
            status, data, error = _aesthetic(products)
        else:
            status, data = {"content": _content, "subject": _subject, "species": _species, "burst": _burst,
                            "quality": _quality}[c](products)
        out.append(ItemResult(asset_id, c, status, data, error, versions.get(c)))
    for stage, product in products.items():
        out.append(ItemResult(asset_id, f"raw:{stage}", "done", product, None, stage_versions.get(stage)))
    return out


# ---- the analyzer -----------------------------------------------------------------------------

class Analyzer:
    def __init__(self, engine: Any, *, decode_workers: int = 2, chunk: int = 16, detail_edge: int | None = None,
                 decode_pool: Executor | None = None) -> None:
        """`decode_pool`: an executor for decoding (tests pass a thread pool); default a self-healing
        process pool of `decode_workers`."""
        from bioscan.service.run import RunQueue

        self.engine = engine
        self.runs = RunQueue(engine, decode_pool=decode_pool, decode_workers=decode_workers, chunk=chunk,
                             detail_edge=detail_edge)

    def close(self) -> None:
        self.runs.close()

    def _stage_versions(self, plan: Any) -> dict[str, str]:
        from bioscan.service import stages

        fps = stages.fingerprints(plan)
        return {s: f"{s}@{fps.get(s) or f'bioscan-{bioscan.__version__}'}" for s in plan.want}

    def versions(self, caps: list[str]) -> dict[str, str]:
        """{capability: version}: the stage fingerprints (or the BioScan version) and the model
        revisions each capability runs on, e.g. `scene@v3@<sha12>;siglip2@<rev12>`. Name-list
        checksums are left out (they are known only once BioCLIP is loaded), so the string is the
        same before and after the models load."""
        mc = _model_caps(caps)
        if not mc:
            return {}
        plan = _resolve(caps).plan
        sv = self._stage_versions(plan)
        info = self.engine.info()
        models = info["models"]
        ident = f"identify@bioscan-{bioscan.__version__}+{info.get('settings', '')}"
        parts = {"content": [sv.get("scene"), ident, "siglip2@" + _short(models["gate"]),
                             "owlv2@" + _short(models["detect"])],
                 "subject": [ident, "owlv2@" + _short(models["detect"])],
                 "species": [ident, "bioclip@" + _short(models["species"])],
                 "burst": [sv.get("embed"), "siglip2@" + _short(models["gate"])],
                 "quality": [sv.get("quality"), ident],
                 "aesthetic": [sv.get("aesthetics"), "siglip2@" + _short(models["gate"])]}
        return {c: ";".join(x for x in parts[c] if x) for c in mc}

    def _needs(self, cap: str) -> set[str]:
        """The models one capability needs on its own."""
        return set(_resolve([cap]).plan.models)

    async def analyze(self, items: list[dict], caps: list[str],
                      should_stop: Callable[[], bool]) -> AsyncIterator[ItemResult]:
        """items: {asset_id, path, lat, lon, taken_at} (photos). Yields, per item, one ItemResult per
        model capability in `caps` and one raw:<stage> per product. should_stop is checked between
        chunks; the chunk in progress always finishes."""
        from bioscan.service import stages

        mc = _model_caps(caps)
        if not mc or not items:
            return
        loop = asyncio.get_running_loop()
        versions = self.versions(caps)
        failed: dict[str, str] = {}
        for m in _resolve(caps).plan.models:        # one at a time: a model that fails costs only its caps
            try:
                await loop.run_in_executor(None, self.engine.ensure, [m])
            except Exception as exc:  # noqa: BLE001
                failed[m] = f"模型 {m} 加载失败：{type(exc).__name__}: {exc}"
        down = {c: "；".join(failed[m] for m in sorted(self._needs(c) & set(failed))) for c in mc}
        down = {c: why for c, why in down.items() if why}
        run = [c for c in caps if c not in down]
        res = _resolve(run) if _model_caps(run) else None
        if res is not None:
            try:
                await loop.run_in_executor(None, stages.check_loaded, self.engine, res.plan)
            except Exception as exc:  # noqa: BLE001
                down.update({c: f"{type(exc).__name__}: {exc}" for c in _model_caps(run)})
                res = None
        for it in items:
            for c in mc:
                if c in down:
                    yield ItemResult(it["asset_id"], c, "unavailable", None, down[c], versions.get(c))
        if res is None:
            return
        run = _model_caps(run)
        sv = self._stage_versions(res.plan)
        by_path: dict[str, list[str]] = {}
        first: dict[str, dict] = {}
        for it in items:
            by_path.setdefault(it["path"], []).append(it["asset_id"])
            first.setdefault(it["path"], it)
        inputs = [{"path": p, "lat": it.get("lat"), "lon": it.get("lon"), "taken_at": it.get("taken_at")}
                  for p, it in first.items()]

        async def stop() -> bool:
            return bool(should_stop())

        def failed_for(path: str, errs: list[tuple[str | None, str]]) -> list[ItemResult]:
            return [r for aid in by_path.get(path, []) for r in _failures(aid, errs, run, versions)]

        pending: tuple[str, list[tuple[str | None, str]]] | None = None   # one image's errors come together
        async for ev in self.runs.events(inputs, res.plan, stop):
            if ev["type"] == "error" and pending and ev["path"] == pending[0]:
                pending[1].append((ev["product"], ev["message"]))
                continue
            if pending:
                for r in failed_for(*pending):
                    yield r
                pending = None
            if ev["type"] == "error":
                pending = (ev["path"], [(ev["product"], ev["message"])])
            elif ev["type"] == "result":
                for aid in by_path.get(ev["path"], []):
                    for r in normalise(aid, ev["products"], run, versions, sv):
                        yield r
        if pending:
            for r in failed_for(*pending):
                yield r

    def reduce(self, rows: list[dict], caps: list[str]) -> dict[str, dict]:
        """rows: {asset_id, path, taken_at, camera, products: {stage: raw product}}, a whole task's
        photos. Returns {asset_id: {"burst": {id, rank, size} | None, "suggestion": {verdict, score,
        rank, reasons} | None}}; burst ids are local to this call (contract §10 X3). burst runs with
        the burst capability, select (the AI suggestion) with aesthetic."""
        reducers = {}
        res = _resolve(caps) if _model_caps(caps) else None
        if res is None:
            return {r["asset_id"]: {"burst": None, "suggestion": None} for r in rows}
        if "burst" in caps:
            reducers["burst"] = res.reducer_options["burst"]
        if "aesthetic" in caps:
            reducers["burst"] = res.reducer_options["burst"]
            reducers["select"] = res.reducer_options["select"]
        events = []
        for r in rows:
            p = dict(r.get("products") or {})
            q = dict(p.get("quality") or {})
            cap = dict(q.get("capture") or {})
            cap["taken_at"] = cap.get("taken_at") or r.get("taken_at")
            cap["camera"] = cap.get("camera") or r.get("camera")
            q["capture"] = cap
            p["quality"] = q            # burst reads time and camera here; without quality only these
            events.append({"type": "result", "path": r["asset_id"], "products": p})
        out_events = cull.apply(events, reducers) if reducers else events
        per_category = (res.reducer_options.get("select") or {}).get("per_category")
        out: dict[str, dict] = {}
        for ev in out_events:
            p = ev["products"]
            b, s = p.get("burst") if "burst" in reducers else None, p.get("select")
            burst = None
            if b and b.get("id"):
                burst = {"id": b["id"], "rank": s["burst_rank"] if s else b["index"] + 1, "size": b["size"]}
            out[ev["path"]] = {"burst": burst, "suggestion": _suggestion(s, burst, per_category) if s else None}
        return out


def _failures(aid: str, errs: list[tuple[str | None, str]], caps: list[str],
              versions: dict[str, str]) -> list[ItemResult]:
    """An image with errors gets no result event (RunQueue drops its other products): every
    capability fails, with its own stage's message, else the message that cost it its result."""
    out = []
    for c in caps:
        own = [m for prod, m in errs if prod is None or prod in NEEDS[c]]
        msg = own[0] if own else "未得到结果：" + "；".join(f"{prod}: {m}" for prod, m in errs)
        out.append(ItemResult(aid, c, "failed", None, msg, versions.get(c)))
    return out


def _suggestion(s: dict[str, Any], burst: dict | None, per_category: int | None) -> dict[str, Any]:
    """select's status as an AI suggestion; reasons only from what select itself reports."""
    status, cat = s["status"], zh(s.get("category"))
    reasons: list[str] = []
    if status == "pick":
        verdict = "pick"
        if s.get("rank"):
            reasons.append(f"「{cat}」中排第 {s['rank']}")
        if burst:
            reasons.append(f"连拍 {burst['size']} 张中最佳帧")
        reasons += [FLAG_ZH.get(f, f) for f in s.get("flags") or []]
    elif status == "reject":
        verdict = "reject"
        reasons = [REASON_ZH.get(r, r) for r in s.get("reasons") or []]
    else:
        verdict = "none"
        if status == "duplicate":
            reasons.append("连拍中非最佳帧" if burst and (s.get("burst_rank") or 1) > 1 else "与已选画面高度相似")
        elif status == "spare" and s.get("rank"):
            reasons.append(f"「{cat}」中排第 {s['rank']}，超出每类 {per_category} 张的推荐数")
    return {"verdict": verdict, "score": s.get("aesthetic"), "rank": s.get("rank"), "reasons": reasons}
