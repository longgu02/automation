"""Retry với exponential backoff + jitter.

Tự viết (~40 dòng) thay vì kéo thêm thư viện: luồng điều khiển hiện rõ ngay
trước mắt, và nó gắn chặt với cây lỗi ở `core/errors.py` -- chỉ retry
`RetryableError`, còn `FatalError` cho nổ ngay lập tức.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from core.errors import RetryableError

T = TypeVar("T")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """Chính sách thử lại.

    attempts: tổng số lần THỬ (không phải số lần retry). attempts=3 -> 1 lần
              chạy đầu + tối đa 2 lần thử lại.
    """

    attempts: int = 3
    base_delay_s: float = 5.0
    max_delay_s: float = 120.0
    multiplier: float = 2.0
    jitter: float = 0.25  # +/- 25% để nhiều tiến trình không cùng đập lại một lúc

    def delay_for(self, attempt_index: int) -> float:
        """Thời gian chờ trước lần thử thứ `attempt_index` (0-based)."""
        raw = self.base_delay_s * (self.multiplier**attempt_index)
        capped = min(raw, self.max_delay_s)
        spread = capped * self.jitter
        return max(0.0, capped + random.uniform(-spread, spread))


def retry_call(
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    label: str = "thao tác",
    logger: logging.Logger | None = None,
) -> T:
    """Gọi `func()`, thử lại khi gặp RetryableError.

    Ném lại lỗi cuối cùng nếu hết lượt. FatalError/ConfigError bay thẳng ra
    ngoài, không tiêu tốn lượt thử nào.
    """
    lg = logger or log
    last_error: RetryableError | None = None

    for attempt in range(policy.attempts):
        try:
            return func()
        except RetryableError as exc:
            last_error = exc
            remaining = policy.attempts - attempt - 1
            if remaining == 0:
                lg.error("%s thất bại sau %d lần thử: %s", label, policy.attempts, exc)
                break
            wait = policy.delay_for(attempt)
            lg.warning(
                "%s lỗi (lần %d/%d): %s -- chờ %.1fs rồi thử lại",
                label,
                attempt + 1,
                policy.attempts,
                exc,
                wait,
            )
            time.sleep(wait)

    assert last_error is not None  # chỉ tới đây khi vòng lặp đã bắt được lỗi
    raise last_error
