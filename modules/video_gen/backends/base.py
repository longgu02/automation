"""Interface chung cho mọi backend sinh video.

Đây là ranh giới quan trọng nhất của module: mọi thứ phía trên (điều phối,
resume, retry, manifest) chỉ nói chuyện qua interface này. Trình duyệt hay HTTP
API chỉ là chi tiết cài đặt nằm phía dưới.

Thêm backend mới = viết một lớp con + đăng ký một dòng trong `backends/__init__.py`.
Không phải sửa `module.py`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from core.accounts import Account
from modules.video_gen.models import VideoArtifact, VideoSpec


class VideoBackend(ABC):
    """Hợp đồng: đưa vào một VideoSpec, nhận lại các file video.

    Backend được dùng như một context manager, vì tài nguyên nặng (trình duyệt,
    phiên HTTP) phải mở một lần cho cả batch chứ không mở lại theo từng prompt:

        with backend:
            for spec in specs:
                artifacts = backend.generate(spec, dest_dir)

    Ràng buộc bắt buộc với lớp con:

    1. `generate()` chỉ ném lỗi thuộc `RetryableError` hoặc `FatalError`
       (xem `core/errors.py`). Lỗi lạ phải được bọc lại.
    2. `generate()` là ATOMIC theo góc nhìn người gọi: hoặc trả về danh sách
       artifact với đầy đủ file đã nằm trên đĩa, hoặc ném lỗi. Không trả về
       kết quả nửa vời.
    3. `generate()` không được tự retry -- việc đó do `core/retry.py` lo, để
       chính sách retry chỉ tồn tại ở một chỗ duy nhất.
    4. Một thực thể backend chỉ được dùng trong ĐÚNG MỘT luồng. Chạy song song
       nhiều tài khoản = mỗi luồng tạo backend riêng của nó (bắt buộc với
       Playwright bản đồng bộ, xem `runner.py`).
    """

    #: Tên định danh, trùng với giá trị `backend:` trong YAML.
    name: str = "unnamed"

    def __init__(self, account: Account, logger: logging.Logger | None = None):
        #: Tài khoản mà backend này gắn với. Mỗi worker có backend riêng gắn với
        #: đúng một tài khoản, và giữ nguyên cho tới khi tài khoản đó hết credit.
        self.account = account
        self.log = logger or logging.getLogger(f"video_gen.{account.id}")

    # -------------------------------------------------------------- vòng đời
    def open(self) -> None:
        """Mở tài nguyên dùng chung cho cả batch. Mặc định không làm gì."""

    def close(self) -> None:
        """Đóng tài nguyên. Phải chịu được việc gọi khi open() đã hỏng giữa chừng."""

    def __enter__(self) -> VideoBackend:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------ việc chính
    @abstractmethod
    def generate(self, spec: VideoSpec, dest_dir: Path) -> list[VideoArtifact]:
        """Sinh video cho `spec` và lưu file vào `dest_dir`.

        Trả về đúng `spec.outputs_per_prompt` artifact khi thành công.
        """

    def describe(self) -> str:
        """Một dòng mô tả để ghi log lúc khởi động. Lớp con nên ghi đè."""
        return self.name
