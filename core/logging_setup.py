"""Cấu hình logging thống nhất cho mọi module.

Chỉ dùng stdlib để giảm phụ thuộc. Mỗi module tự lấy logger bằng
`logging.getLogger(__name__)`; hàm dưới đây chỉ gọi MỘT LẦN ở entrypoint (CLI).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from core.paths import LOG_DIR, ensure_dir

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-24s  %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str = "INFO", run_id: str | None = None) -> Path | None:
    """Bật log ra console + (tuỳ chọn) ra file logs/<run_id>.log.

    Trả về đường dẫn file log, hoặc None nếu không ghi file.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handler mới là nơi quyết định mức hiển thị

    # Xoá handler cũ để gọi lại hàm này không bị log trùng dòng.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(console)

    log_path: Path | None = None
    if run_id:
        ensure_dir(LOG_DIR)
        log_path = LOG_DIR / f"{run_id}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        # File luôn ghi DEBUG để mổ xẻ sự cố về sau; console thì gọn gàng.
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(file_handler)

    # Vài thư viện rất "nhiều lời" ở mức DEBUG -- ghìm lại cho log đọc được.
    for noisy in ("playwright", "asyncio", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log_path
