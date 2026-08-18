"""Kiểm thử bộ điều tiết nhịp độ.

Nhịp độ là thứ dễ tưởng là "đã có" nhưng thực ra hỏng lặng lẽ: đặt nhầm một số
trong config và module chạy nhanh như máy mà không báo gì. Vài test rẻ tiền ở
đây chốt lại những tính chất phải luôn đúng.

Thời gian ngủ thật được thay bằng hàm giả, nên bộ test chạy trong tích tắc.

    python -m pytest tests/test_humanize.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.image_crawl.humanize import Pacer, PacingProfile  # noqa: E402


@pytest.fixture()
def no_real_sleep(monkeypatch):
    """Thay time.sleep bằng hàm ghi sổ, để test không mất 15 phút."""
    recorded: list[float] = []
    monkeypatch.setattr("modules.image_crawl.humanize.time.sleep", recorded.append)
    return recorded


class FakePage:
    """Trang giả, chỉ ghi lại các thao tác được gọi."""

    def __init__(self):
        self.wheel_calls: list[int] = []
        self.mouse_moves: list[tuple] = []
        self.viewport_size = {"width": 1440, "height": 900}
        self.mouse = self

    def wheel(self, dx: int, dy: int) -> None:
        self.wheel_calls.append(dy)

    def move(self, x: int, y: int, steps: int = 1) -> None:
        self.mouse_moves.append((x, y, steps))


def _pacer(seed: int = 1, **overrides) -> Pacer:
    return Pacer(PacingProfile(**overrides), seed=seed)


# ===========================================================================
# Khoảng nghỉ
# ===========================================================================
def test_nghi_nam_trong_khoang_cau_hinh(no_real_sleep) -> None:
    pacer = _pacer(min_action_delay_s=2.0, max_action_delay_s=5.0)
    for _ in range(30):
        pacer.pause()
    assert all(2.0 <= s <= 5.0 for s in no_real_sleep)


def test_khoang_nghi_khong_deu_tam_tap(no_real_sleep) -> None:
    """Nghỉ đúng 2.000s mỗi lần còn lộ liễu hơn là không nghỉ."""
    pacer = _pacer(min_action_delay_s=1.0, max_action_delay_s=4.0)
    for _ in range(20):
        pacer.pause()
    assert len(set(no_real_sleep)) > 15, "khoảng nghỉ phải ngẫu nhiên, không lặp lại"


def test_nghi_dai_lau_hon_nghi_thuong(no_real_sleep) -> None:
    pacer = _pacer(
        min_action_delay_s=1.0, max_action_delay_s=2.0,
        min_long_pause_s=5.0, max_long_pause_s=9.0,
    )
    pacer.pause()
    pacer.think()
    assert no_real_sleep[0] < no_real_sleep[1]
    assert no_real_sleep[1] >= 5.0


def test_cung_seed_thi_cung_ket_qua(no_real_sleep) -> None:
    """Tái lập được một phiên là điều kiện để gỡ lỗi nghiêm túc."""
    a, b = _pacer(seed=7), _pacer(seed=7)
    assert [a.pause() for _ in range(5)] == [b.pause() for _ in range(5)]


def test_seed_khac_nhau_cho_nhip_khac_nhau(no_real_sleep) -> None:
    a, b = _pacer(seed=1), _pacer(seed=2)
    assert [a.pause() for _ in range(5)] != [b.pause() for _ in range(5)]


# ===========================================================================
# Cuộn
# ===========================================================================
def test_cuon_tung_nac_ngan_khong_nhay_xuong_day(no_real_sleep) -> None:
    page = FakePage()
    pacer = _pacer(min_scroll_px=300, max_scroll_px=800, backscroll_chance=0.0)
    for _ in range(15):
        pacer.scroll_once(page)

    assert len(page.wheel_calls) == 15
    assert all(300 <= d <= 800 for d in page.wheel_calls)


def test_thinh_thoang_cuon_nguoc_len(no_real_sleep) -> None:
    """Người thật hay lướt quá rồi kéo lại; chuỗi cuộn xuống đều tăm tắp rất máy."""
    page = FakePage()
    pacer = _pacer(seed=3, backscroll_chance=1.0)
    for _ in range(5):
        pacer.scroll_once(page)
    assert all(d < 0 for d in page.wheel_calls)


def test_moi_nhip_cuon_deu_co_nghi(no_real_sleep) -> None:
    page = FakePage()
    pacer = _pacer(long_pause_every=0)
    for _ in range(10):
        pacer.scroll_once(page)
    assert len(no_real_sleep) == 10, "cuộn mà không nghỉ là hành vi lộ nhất"


def test_cu_may_nhip_lai_nghi_dai(no_real_sleep) -> None:
    page = FakePage()
    pacer = _pacer(
        long_pause_every=3,
        min_scroll_pause_s=0.5, max_scroll_pause_s=0.6,
        min_long_pause_s=8.0, max_long_pause_s=9.0,
    )
    for _ in range(6):
        pacer.scroll_once(page)

    long_pauses = [s for s in no_real_sleep if s >= 8.0]
    assert len(long_pauses) == 2, "6 nhịp, cứ 3 nhịp nghỉ dài -> đúng 2 lần"


# ===========================================================================
# Chuột
# ===========================================================================
def test_chuot_di_chuyen_theo_duong_chu_khong_nhay_coc(no_real_sleep) -> None:
    """Chuột dịch chuyển tức thời rồi bấm là dấu hiệu máy móc rõ nhất."""
    page = FakePage()
    _pacer(mouse_moves=4).move_mouse(page)

    assert len(page.mouse_moves) == 4
    assert all(steps > 1 for _, _, steps in page.mouse_moves), "phải nội suy đường đi"
    assert all(0 < x < 1440 and 0 < y < 900 for x, y, _ in page.mouse_moves)


def test_loi_khi_re_chuot_khong_lam_hong_phien(no_real_sleep) -> None:
    """Rê chuột chỉ là trang trí -- hỏng thì bỏ qua, đừng làm chết cả run."""

    class BrokenPage(FakePage):
        def move(self, *args, **kwargs):
            raise RuntimeError("trang đã đóng")

    _pacer().move_mouse(BrokenPage())  # không được ném ra ngoài


# ===========================================================================
# Ngân sách phiên -- cái phanh
# ===========================================================================
def test_ngan_sach_chua_can_thi_chua_dung(no_real_sleep) -> None:
    pacer = _pacer(max_session_s=600)
    assert pacer.budget_exhausted() is False
    assert pacer.remaining_s() > 0


def test_ngan_sach_can_thi_bao_dung(no_real_sleep, monkeypatch) -> None:
    """Trần thời lượng ngăn một cấu hình sai biến thành phiên cào hàng giờ."""
    pacer = _pacer(max_session_s=10)
    monkeypatch.setattr(
        "modules.image_crawl.humanize.time.monotonic", lambda: pacer.started + 11
    )
    assert pacer.budget_exhausted() is True
    assert pacer.remaining_s() == 0
