"""Nhịp thao tác giống người dùng thật.

MỤC ĐÍCH THẬT SỰ, nói thẳng để bạn khỏi kỳ vọng sai:

  * Điều file này LÀM ĐƯỢC: giữ lượt truy cập ở mức một người đang xem trang
    thật sự tạo ra. Không dội request, không cuộn hết trang trong một nhịp,
    không đều đặn như máy đếm. Đây trước hết là phép lịch sự với máy chủ người
    ta, và nhờ đó cũng ít bị chặn hơn.
  * Điều file này KHÔNG LÀM ĐƯỢC: bảo đảm không bị chặn. Pinterest còn nhìn
    nhiều thứ khác -- dấu vết trình duyệt, địa chỉ IP, hành vi tài khoản. Chậm
    rãi giúp giảm rủi ro, không xoá được nó.

BỐN THÓI QUEN CỦA NGƯỜI THẬT ĐƯỢC MÔ PHỎNG Ở ĐÂY:

  1. Khoảng nghỉ NGẪU NHIÊN, không phải hằng số. Nghỉ đúng 2,000s mỗi lần còn
     lộ liễu hơn là không nghỉ.
  2. Cuộn từng nấc ngắn rồi dừng, không nhảy thẳng xuống đáy.
  3. Thỉnh thoảng dừng lâu (đang xem một tấm ảnh) và đôi khi cuộn ngược lên
     một chút (nhìn lại thứ vừa lướt qua).
  4. Chuột có di chuyển, chứ không dịch chuyển tức thời rồi bấm.

Mọi con số đều nằm trong config. `seed` cố định giúp tái lập được khi cần gỡ lỗi
và giúp kiểm thử -- để trống thì mỗi phiên một khác.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class PacingProfile:
    """Các thông số nhịp độ. Xem `config/image_crawl.yaml` để biết ý nghĩa."""

    min_action_delay_s: float = 1.5
    max_action_delay_s: float = 4.0
    min_scroll_px: int = 300
    max_scroll_px: int = 800
    min_scroll_pause_s: float = 0.8
    max_scroll_pause_s: float = 2.5
    long_pause_every: int = 6
    min_long_pause_s: float = 4.0
    max_long_pause_s: float = 10.0
    backscroll_chance: float = 0.15
    mouse_moves: int = 3
    max_session_s: float = 900.0


class Pacer:
    """Điều tiết nhịp thao tác và canh chừng ngân sách phiên làm việc."""

    def __init__(self, profile: PacingProfile, seed: int | None = None):
        self.profile = profile
        self.rng = random.Random(seed)
        self.started = time.monotonic()
        self.actions = 0
        self.slept_s = 0.0

    # ------------------------------------------------------------ nghỉ
    def pause(self, reason: str = "") -> float:
        """Nghỉ một khoảng ngẫu nhiên giữa hai thao tác."""
        seconds = self.rng.uniform(
            self.profile.min_action_delay_s, self.profile.max_action_delay_s
        )
        return self._sleep(seconds, reason or "thao tác")

    def think(self, reason: str = "") -> float:
        """Nghỉ lâu hơn, như người đang dừng lại xem một tấm ảnh."""
        seconds = self.rng.uniform(self.profile.min_long_pause_s, self.profile.max_long_pause_s)
        return self._sleep(seconds, reason or "dừng xem")

    def _sleep(self, seconds: float, reason: str) -> float:
        log.debug("Nghỉ %.1fs (%s)", seconds, reason)
        time.sleep(seconds)
        self.slept_s += seconds
        return seconds

    # ---------------------------------------------------------- ngân sách
    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def budget_exhausted(self) -> bool:
        """Phiên đã chạy quá lâu chưa?

        Trần thời lượng là một cái phanh có chủ đích: nó ngăn việc một cấu hình
        sai biến thành phiên cào kéo dài hàng giờ mà bạn không để ý.
        """
        return self.elapsed_s >= self.profile.max_session_s

    def remaining_s(self) -> float:
        return max(0.0, self.profile.max_session_s - self.elapsed_s)

    # ------------------------------------------------------- thao tác trang
    def move_mouse(self, page) -> None:
        """Rê chuột lòng vòng vài nhịp trước khi làm gì đó.

        Chuột nhảy tức thời từ (0,0) tới đúng toạ độ cần bấm là dấu hiệu máy móc
        rõ nhất. `steps` khiến Playwright nội suy đường đi thay vì dịch chuyển.
        """
        try:
            size = page.viewport_size or {"width": 1280, "height": 800}
            for _ in range(self.profile.mouse_moves):
                x = self.rng.randint(int(size["width"] * 0.2), int(size["width"] * 0.8))
                y = self.rng.randint(int(size["height"] * 0.2), int(size["height"] * 0.8))
                page.mouse.move(x, y, steps=self.rng.randint(8, 25))
                time.sleep(self.rng.uniform(0.15, 0.5))
        except Exception as exc:  # noqa: BLE001 - trang trí, hỏng cũng không sao
            log.debug("Bỏ qua lỗi khi rê chuột: %s", exc)

    def scroll_once(self, page) -> int:
        """Cuộn MỘT nấc như người thật. Trả về số điểm ảnh đã cuộn.

        Đôi khi cuộn ngược lên một đoạn ngắn -- người thật hay lướt quá rồi kéo
        lại, và chuỗi cuộn xuống đều tăm tắp trông rất máy.
        """
        self.actions += 1
        profile = self.profile

        if self.rng.random() < profile.backscroll_chance:
            delta = -self.rng.randint(profile.min_scroll_px // 3, profile.min_scroll_px)
            log.debug("Cuộn ngược lên %dpx", -delta)
        else:
            delta = self.rng.randint(profile.min_scroll_px, profile.max_scroll_px)

        page.mouse.wheel(0, delta)
        self._sleep(
            self.rng.uniform(profile.min_scroll_pause_s, profile.max_scroll_pause_s),
            "sau khi cuộn",
        )

        # Cứ vài nhịp lại dừng lâu, như đang thật sự xem thứ gì đó.
        if profile.long_pause_every > 0 and self.actions % profile.long_pause_every == 0:
            self.think("nghỉ định kỳ")

        return delta

    def summary(self) -> dict[str, float | int]:
        return {
            "hành động": self.actions,
            "tổng thời gian (s)": round(self.elapsed_s, 1),
            "thời gian nghỉ (s)": round(self.slept_s, 1),
        }
