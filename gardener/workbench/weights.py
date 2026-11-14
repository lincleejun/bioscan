"""The model weights 自动归类 needs, fetched when a person first asks for it and not at install (ADR 0005: the album
runs without them). `status()` says what is on disk; `start()` runs the one download script (tests/models/download.py)
in a child process, because this service keeps Hugging Face offline, and its output goes to logs/models-download.log.
Sizes are what a fresh download took on 2026-11-13: progress is bytes on disk against them."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[2] / "tests" / "models" / "download.py"
_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_last: dict[str, Any] = {"state": "idle", "error": None}


def _items() -> list[dict[str, Any]]:
    from huggingface_hub import constants, try_to_load_from_cache

    from gardener.service import names
    from gardener.service.adapters import bioclip, owlv2, siglip2
    from gardener.workbench import faces

    hub = Path(constants.HF_HUB_CACHE)

    def hf(key: str, name: str, repo: str, filename: str, revision: str, size: int) -> dict[str, Any]:
        d = hub / ("models--" + repo.replace("/", "--")) / "blobs"
        have = sum(f.stat().st_size for f in d.iterdir()) if d.is_dir() else 0      # part files count: it is progress
        return {"id": key, "name": name, "bytes": size, "have_bytes": min(have, size),
                "present": isinstance(try_to_load_from_cache(repo, filename, revision=revision), str)}

    taxa = names.all_taxa_path()
    part = taxa.with_suffix(".part")
    return [hf("bioclip", "物种识别", bioclip.MODEL_ID.removeprefix("hf-hub:"), "open_clip_model.safetensors", bioclip.REVISION, 3_949_567_227),
            hf("siglip2", "画面理解", siglip2.MODEL_ID, "model.safetensors", siglip2.REVISION, 1_539_458_338),
            hf("owlv2", "动物检测", owlv2.MODEL_ID, "model.safetensors", owlv2.REVISION, 621_511_889),
            hf("faces", "人物", faces.SFACE[0], faces.SFACE[1], faces.SFACE[2], 38_696_353),
            {"id": "all_taxa", "name": "其他动物名录", "bytes": 799_878_148, "present": taxa.is_file(),
             "have_bytes": taxa.stat().st_size if taxa.is_file() else part.stat().st_size if part.is_file() else 0}]


def status() -> dict[str, Any]:
    global _proc
    items = _items()
    with _lock:
        if _proc is not None and _proc.poll() is not None:
            _last.update(state="done" if _proc.returncode == 0 else "failed",
                         error=None if _proc.returncode == 0 else f"下载脚本退出码 {_proc.returncode}，详见日志")
            _proc = None
        state = dict(_last, state="running") if _proc is not None else dict(_last)
    return {"ready": all(i["present"] for i in items), "items": items, "bytes": sum(i["bytes"] for i in items),
            "have_bytes": sum(i["bytes"] if i["present"] else i["have_bytes"] for i in items), "download": state}


def start(log_dir: Path) -> dict[str, Any]:
    """Begin the download unless one runs or nothing is missing."""
    global _proc
    if status()["ready"]:
        return status()
    with _lock:
        if _proc is None:
            env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
            log = open(Path(log_dir) / "models-download.log", "ab")  # noqa: SIM115 - the child owns it until it exits
            _proc = subprocess.Popen([sys.executable, str(SCRIPT)], stdout=log, stderr=subprocess.STDOUT, env=env,
                                     cwd=SCRIPT.parents[2])
            _last.update(state="running", error=None, log=str(Path(log_dir) / "models-download.log"))
    return status()
