"""Cây lỗi dùng chung cho toàn bộ hệ automation.

Phân loại lỗi là quyết định kiến trúc quan trọng nhất ở đây: lớp điều phối
(module) KHÔNG được đoán xem có nên thử lại hay không -- backend phải nói rõ
bằng loại exception nó ném ra.

    AutomationError
    ├── ConfigError          -> cấu hình sai. Dừng cả run, không retry.
    └── BackendError
        ├── RetryableError   -> lỗi tạm thời. Retry theo policy.
        │   ├── SelectorNotFound   (UI chưa render kịp / đổi DOM)
        │   ├── GenerationTimeout  (chờ render quá lâu)
        │   └── TransientNetwork
        └── FatalError       -> lỗi vĩnh viễn với spec/tài khoản này.
            │                   Bỏ qua spec, KHÔNG retry, chạy tiếp spec sau.
            ├── AuthError          (chưa đăng nhập / session hết hạn)
            ├── ContentBlocked     (prompt bị chặn bởi policy)
            └── QuotaExhausted     (hết credit/quota)

Quy tắc: mọi backend PHẢI ném lỗi thuộc nhánh Retryable/Fatal.
Nếu bạn thấy một `Exception` trần thoát ra khỏi backend, đó là bug của backend.
"""

from __future__ import annotations


class AutomationError(Exception):
    """Gốc của mọi lỗi do hệ automation chủ động sinh ra."""


class ConfigError(AutomationError):
    """Cấu hình thiếu, sai kiểu, hoặc mâu thuẫn. Không thể chạy tiếp."""


class BackendError(AutomationError):
    """Gốc của mọi lỗi phát sinh trong lúc backend làm việc."""


# --------------------------------------------------------------------------
# Nhánh RETRY ĐƯỢC
# --------------------------------------------------------------------------
class RetryableError(BackendError):
    """Lỗi tạm thời -- thử lại có khả năng thành công."""


class SelectorNotFound(RetryableError):
    """Không tìm thấy phần tử UI nào khớp danh sách selector ứng viên.

    Xếp vào nhóm retry vì nguyên nhân phổ biến nhất là UI chưa render kịp.
    Nếu retry vẫn hỏng -> gần như chắc chắn Google đã đổi DOM, hãy sửa
    `config/flow_selectors.yaml` (xem README, mục "Khi Google đổi giao diện").
    """

    def __init__(self, key: str, candidates: list[str], debug_dir: str | None = None):
        self.key = key
        self.candidates = candidates
        self.debug_dir = debug_dir
        detail = "\n".join(f"    - {c}" for c in candidates)
        msg = f"Không tìm thấy phần tử '{key}'. Đã thử {len(candidates)} selector:\n{detail}"
        if debug_dir:
            msg += f"\n  Ảnh chụp + HTML để debug: {debug_dir}"
        super().__init__(msg)


class GenerationTimeout(RetryableError):
    """Chờ video render xong quá thời gian cho phép."""


class TransientNetwork(RetryableError):
    """Lỗi mạng / 5xx / rate-limit tạm thời."""


# --------------------------------------------------------------------------
# Nhánh KHÔNG retry
# --------------------------------------------------------------------------
class FatalError(BackendError):
    """Lỗi không thể khắc phục bằng cách thử lại."""


class AuthError(FatalError):
    """Chưa đăng nhập hoặc session đã hết hạn.

    Cách xử lý: chạy lại `python scripts/login_flow.py` để đăng nhập thủ công.
    """


class ContentBlocked(FatalError):
    """Prompt bị từ chối bởi bộ lọc nội dung. Retry cũng vô ích -- sửa prompt."""


class QuotaExhausted(FatalError):
    """Hết credit / hết quota. Dừng toàn bộ run vì các spec sau cũng sẽ hỏng."""
