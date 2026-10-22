"""Gardener capabilities, presets and run modes (docs/gardener/contract.md §3).

A capability is what the user switches on; it is not a model. Each one names the BioScan stages
(bioscan/plugins) and reducers (bioscan/cull.py) it runs, or is `local`: a model the task worker runs
itself (people, on faces.py). The version a run used is recorded per result from the engine's model
ids and the stage fingerprints, not from this table."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    id: str
    name: str                    # Chinese UI label
    description: str
    stages: tuple[str, ...]      # BioScan /run products this capability needs (empty: model-free)
    reducers: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()   # capabilities it depends on (switched on with it)
    local: bool = False              # a model Gardener runs itself, outside the BioScan stages


CAPABILITIES: tuple[Capability, ...] = (
    Capability("meta", "基础清点与元数据", "文件信息、拍摄时间、设备、位置、预览与读取状态", ()),
    Capability("content", "内容识别分类", "场景/内容标签（SigLIP2 场景分类 + 动物门控）", ("identify", "scene")),
    Capability("place_event", "地点与日期", "在主体分组内按地点（GPS）或拍摄日期细分（不调用模型）", ()),
    Capability("subject", "主体检测", "动物主体框（OWLv2），供质量与物种使用", ("identify",)),
    Capability("species", "物种识别", "物种候选及不确定性（BioCLIP + 名录 + 位置先验）", ("identify",),
               requires=("subject",)),
    Capability("burst", "连拍与相似画面", "比较组与推荐帧，保留全部候选（SigLIP2 向量 + 时间）", ("embed",),
               reducers=("burst",)),
    Capability("quality", "质量分析", "模糊、对焦、曝光等风险及依据", ("identify", "quality")),
    Capability("aesthetic", "美学与选择建议", "美学排序与 AI Pick/Reject 建议及依据（只排序，不替人决定）",
               ("aesthetics",), reducers=("select",), requires=("quality", "burst", "content")),
    Capability("people", "人物识别", "本地人脸检测与特征（OpenCV YuNet + SFace），按人聚类分组；特征只存本机",
               (), local=True),
)

BY_ID = {c.id: c for c in CAPABILITIES}

PRESETS: dict[str, dict[str, object]] = {
    "quick": {"name": "快速整理", "capabilities": ["meta", "content", "place_event"]},
    "photo": {"name": "摄影筛选", "capabilities": ["meta", "content", "place_event", "burst", "quality", "aesthetic",
                                                "people"]},
    "wildlife": {"name": "野生动物", "capabilities": ["meta", "content", "place_event", "burst", "quality",
                                                  "aesthetic", "subject", "species", "people"]},
}

# Run modes control resources only, never which capabilities run or how well (PRD §7).
RUN_MODES: dict[str, dict[str, object]] = {
    "quiet": {"name": "安静", "decode_workers": 1, "chunk": 8, "preview_workers": 1, "pause_s": 0.5},
    "balanced": {"name": "均衡", "decode_workers": 2, "chunk": 16, "preview_workers": 3, "pause_s": 0.0},
    "full": {"name": "全速", "decode_workers": 4, "chunk": 32, "preview_workers": 6, "pause_s": 0.0},
}
DEFAULT_MODE = "balanced"


def expand(ids: list[str]) -> list[str]:
    """`ids` plus what they require, in CAPABILITIES order; meta is always on. ValueError on an unknown id."""
    unknown = sorted(set(ids) - set(BY_ID))
    if unknown:
        raise ValueError(f"unknown capabilities: {unknown}")
    on = {"meta", *ids}
    todo = list(on)
    while todo:
        for r in BY_ID[todo.pop()].requires:
            if r not in on:
                on.add(r)
                todo.append(r)
    return [c.id for c in CAPABILITIES if c.id in on]


def stages_for(ids: list[str]) -> list[str]:
    """The BioScan products (/run `want`) the capabilities need, de-duplicated, in first-use order."""
    out: list[str] = []
    for c in CAPABILITIES:
        if c.id in ids:
            out += [s for s in c.stages if s not in out]
    return out
