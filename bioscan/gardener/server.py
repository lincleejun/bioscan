"""`gardener serve`: the Gardener service (API + web UI + background worker) on this machine only.

uvicorn runs in this process with one worker; the BioScan decoder pool is spawned processes, so the
app is built inside main(), never at import time (a child re-importing this module must not serve)."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from bioscan.gardener.api import LOCAL_HOSTS

HOST = "127.0.0.1"
PORT = 8770


def default_data_dir() -> Path:
    return Path(os.environ.get("GARDENER_HOME") or "~/.gardener").expanduser()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gardener serve", description="启动 Gardener 本机服务")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="数据库、预览缓存、默认导出与日志所在目录（默认 $GARDENER_HOME，否则 ~/.gardener）")
    ap.add_argument("--host", default=HOST, help=f"只允许本机地址（默认 {HOST}）")
    ap.add_argument("--port", type=int, default=PORT, help=f"默认 {PORT}")
    ap.add_argument("--lightroom-dir", type=Path, default=None,
                    help="Lightroom 交接文件目录（插件读取；默认 ~/Library/Application Support/bioscan/lightroom）")
    ap.add_argument("--allow-root", action="append", default=[],
                    help="任务来源必须位于这些目录之内（可重复；默认不限制）")
    a = ap.parse_args(argv)
    if a.host not in LOCAL_HOSTS:
        ap.error(f"--host 只能是本机地址 {LOCAL_HOSTS}：v0.1 没有局域网认证")
    data_dir = (a.data_dir or default_data_dir()).expanduser().resolve()
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(data_dir / "logs" / "gardener.log")])

    import uvicorn

    from bioscan.gardener.api import create_gardener_app

    app = create_gardener_app(data_dir, allow_roots=a.allow_root, lightroom_dir=a.lightroom_dir)
    logging.getLogger("gardener").info("data_dir=%s url=http://%s:%d/", data_dir, a.host, a.port)
    uvicorn.run(app, host=a.host, port=a.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
