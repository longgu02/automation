"""Hợp đồng chung cho MỌI module trong hệ automation.

Đây là mảnh ghép then chốt cho mục tiêu "một job = nhiều module chạy tuần tự":
job runner sau này chỉ cần biết ba thứ dưới đây, hoàn toàn không cần biết bên
trong module là Playwright, gọi API, ffmpeg hay upload YouTube.

    ModuleContext  -- thứ job runner ĐƯA VÀO module (run_id, thư mục, dữ liệu
                      do các module trước sinh ra)
    BaseModule     -- việc module phải làm  (setup -> run -> teardown)
    ModuleResult   -- thứ module TRẢ RA, để module kế tiếp dùng tiếp

Quy ước quan trọng: module KHÔNG tự đọc file config từ đĩa và KHÔNG tự gọi
`setup_logging`. Nó nhận config đã validate qua hàm khởi tạo, nhận context qua
`run()`. Nhờ vậy module test được, và job runner nắm toàn quyền điều phối.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ModuleStatus(str, Enum):
    """Kết cục của một module. Job runner dựa vào đây để quyết định chạy tiếp."""

    SUCCESS = "success"  # mọi việc xong xuôi
    PARTIAL = "partial"  # có việc xong, có việc hỏng -- job runner tự quyết
    FAILED = "failed"  # không sinh ra được gì dùng được
    SKIPPED = "skipped"  # không có việc gì để làm (đã xong từ lần chạy trước)


@dataclass
class ModuleContext:
    """Bối cảnh một lần chạy, do job runner (hoặc CLI) tạo ra."""

    run_id: str
    workdir: Path
    logger: logging.Logger
    dry_run: bool = False

    # Kênh truyền dữ liệu giữa các module trong cùng một job.
    # Ví dụ: video_gen ghi shared["videos"], module upload sau đó đọc ra dùng.
    shared: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModuleResult:
    """Kết quả một module trả về."""

    status: ModuleStatus
    outputs: dict[str, Any] = field(default_factory=dict)  # dữ liệu cho module sau
    stats: dict[str, Any] = field(default_factory=dict)  # số liệu để báo cáo/log
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (ModuleStatus.SUCCESS, ModuleStatus.SKIPPED)


class BaseModule(ABC):
    """Lớp cha của mọi module.

    Vòng đời do job runner gọi theo đúng thứ tự:

        module.setup(ctx)      # chuẩn bị: tạo thư mục, kiểm tra điều kiện
        result = module.run(ctx)
        module.teardown(ctx)   # LUÔN chạy, kể cả khi run() ném lỗi

    Chỉ `run()` là bắt buộc phải cài đặt.
    """

    #: Tên định danh, dùng trong log và trong file định nghĩa job.
    name: str = "unnamed"

    def setup(self, ctx: ModuleContext) -> None:  # noqa: B027 - hook tuỳ chọn
        """Chuẩn bị trước khi chạy. Mặc định không làm gì."""

    @abstractmethod
    def run(self, ctx: ModuleContext) -> ModuleResult:
        """Làm phần việc chính. Bắt buộc cài đặt ở lớp con."""

    def teardown(self, ctx: ModuleContext) -> None:  # noqa: B027 - hook tuỳ chọn
        """Dọn dẹp tài nguyên. Mặc định không làm gì."""

    def execute(self, ctx: ModuleContext) -> ModuleResult:
        """Chạy trọn vòng đời với bảo đảm teardown luôn được gọi.

        Job runner nên gọi hàm này thay vì gọi lẻ setup/run/teardown.
        """
        ctx.logger.info("[%s] bắt đầu (run_id=%s)", self.name, ctx.run_id)
        self.setup(ctx)
        try:
            result = self.run(ctx)
        finally:
            self.teardown(ctx)
        ctx.logger.info("[%s] kết thúc: %s | %s", self.name, result.status.value, result.stats)
        return result
