"""Sổ đăng ký backend.

Import ĐỘ TRỄ (lazy) là có chủ đích: chỉ dùng backend API thì không cần cài
Playwright, và ngược lại. Import ở đầu file sẽ bắt mọi người cài mọi thứ.

Mỗi lần gọi `create_backend()` tạo ra một thực thể MỚI gắn với MỘT tài khoản.
Chạy song song nhiều tài khoản = mỗi luồng gọi hàm này một lần cho riêng nó
(bắt buộc: Playwright bản đồng bộ không dùng chung được giữa các luồng).

Thêm backend mới:
    1. Viết lớp con của `VideoBackend` trong thư mục này.
    2. Thêm một nhánh trong `create_backend()` bên dưới.
    3. Thêm tên vào `BackendName` ở `modules/video_gen/config.py`.
"""

from __future__ import annotations

import logging

from core.accounts import Account
from core.errors import ConfigError
from modules.video_gen.backends.base import VideoBackend
from modules.video_gen.config import VideoGenConfig

__all__ = ["VideoBackend", "create_backend"]


def create_backend(
    cfg: VideoGenConfig, account: Account, logger: logging.Logger | None = None
) -> VideoBackend:
    """Tạo backend theo `cfg.backend`, gắn với `account` và khối cấu hình của nó."""
    if cfg.backend == "flow_browser":
        try:
            from modules.video_gen.backends.flow_browser import FlowBrowserBackend
        except ImportError as exc:
            raise ConfigError(
                "Backend flow_browser cần Playwright. Cài bằng:\n"
                "    pip install playwright\n"
                "    playwright install chromium"
            ) from exc
        return FlowBrowserBackend(cfg.browser, account, logger)

    if cfg.backend == "gemini_api":
        from modules.video_gen.backends.gemini_api import GeminiApiBackend

        return GeminiApiBackend(cfg.gemini_api, account, logger)

    raise ConfigError(f"Backend không tồn tại: '{cfg.backend}'. Chọn: flow_browser | gemini_api")
