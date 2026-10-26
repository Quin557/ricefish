"""日志初始化：控制台 + 按大小滚动的 UTF-8 文件。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time


class _JsonFormatter(logging.Formatter):
    """一行一个 JSON，方便云上采集。"""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    log_dir: str = "data/logs",
    level: str = "INFO",
    filename: str = "bot.log",
    max_bytes: int = 5 * 1024 * 1024,
    backups: int = 5,
) -> logging.Logger:
    """配置根 logger，重复调用是安全的。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if getattr(setup_logging, "_done", False):
        return logging.getLogger("ricefish")
    setup_logging._done = True  # type: ignore[attr-defined]

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)-22s %(message)s", datefmt="%m-%d %H:%M:%S"
    )
    if os.environ.get("RICEFISH_LOG_JSON", "").strip().lower() in ("1", "true", "yes"):
        fmt = _JsonFormatter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, filename),
            maxBytes=max_bytes,
            backupCount=backups,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        root.warning("无法创建日志文件，仅输出到控制台：%s", log_dir)

    # 第三方库降噪
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    return logging.getLogger("ricefish")
