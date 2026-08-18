"""Kiểm thử phần bóc dữ liệu và xếp hạng của image_crawl.

`extract.py` là hàm thuần tuý -- vào JSON, ra PinCandidate. Nên phần dễ vỡ nhất
của module (hình dạng JSON của Pinterest, vốn hay đổi) kiểm được bằng dữ liệu
mẫu, không cần mở trình duyệt lần nào.

Trọng tâm test là hai câu hỏi:
  1. Có bóc đúng pin ra không, kể cả khi Pinterest đổi chỗ dữ liệu?
  2. Cơ sở xếp hạng báo cáo ra có TRUNG THỰC không? -- quan trọng nhất, vì đây
     là chỗ dễ âm thầm nói dối người dùng nhất.

    python -m pytest tests/test_image_extract.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.image_crawl.extract import (  # noqa: E402
    extract_pins,
    looks_like_pin,
    merge_pins,
    pick_image,
    rank_pins,
    upgrade_pinimg_url,
)
from modules.image_crawl.models import PinCandidate, RankingBasis  # noqa: E402


def _pin_json(pin_id: str, saves: int = 0, **extra) -> dict:
    """Một pin dạng JSON như Pinterest trả về."""
    data = {
        "id": pin_id,
        "grid_title": f"Pin {pin_id}",
        "description": "mô tả",
        "repin_count": saves,
        "images": {
            "236x": {"url": f"https://i.pinimg.com/236x/{pin_id}.jpg", "width": 236, "height": 300},
            "orig": {"url": f"https://i.pinimg.com/originals/{pin_id}.jpg", "width": 1200, "height": 1500},
        },
    }
    data.update(extra)
    return data


# ===========================================================================
# Nhận diện pin
# ===========================================================================
def test_nhan_dien_pin_can_co_id_va_anh() -> None:
    assert looks_like_pin(_pin_json("a")) is True
    assert looks_like_pin({"id": "a"}) is False, "thiếu images"
    assert looks_like_pin({"images": {"orig": {"url": "x"}}}) is False, "thiếu id"
    assert looks_like_pin({"id": "a", "images": {}}) is False, "images rỗng"
    assert looks_like_pin({"id": "a", "images": {"orig": {}}}) is False, "ảnh không có url"
    assert looks_like_pin("chuỗi") is False


# ===========================================================================
# Duyệt cây JSON
# ===========================================================================
def test_tim_thay_pin_du_nam_o_dau_trong_cay() -> None:
    """Pinterest đổi tên khoá lồng nhau khá thường xuyên -- bám đường dẫn cứng
    là gãy sau vài tháng, nên ta duyệt khắp cây."""
    shapes = [
        {"resource_response": {"data": {"results": [_pin_json("a")]}}},
        {"data": {"results": [_pin_json("a")]}},
        {"props": {"initialReduxState": {"pins": {"a": _pin_json("a")}}}},
        [{"whatever": [{"deeply": {"nested": [_pin_json("a")]}}]}],
    ]
    for payload in shapes:
        pins = extract_pins(payload)
        assert len(pins) == 1, f"không tìm thấy pin trong {payload}"
        assert pins[0].id == "a"


def test_khong_lap_pin_trong_cung_mot_phan_hoi() -> None:
    payload = {"a": [_pin_json("x")], "b": [_pin_json("x")]}
    assert len(extract_pins(payload)) == 1


def test_giu_nguyen_thu_tu_pinterest_tra_ve() -> None:
    """Thứ tự này chính là xếp hạng của Pinterest, và là phương án dự phòng."""
    payload = {"results": [_pin_json("a"), _pin_json("b"), _pin_json("c")]}
    pins = extract_pins(payload)
    assert [p.id for p in pins] == ["a", "b", "c"]
    assert [p.discovery_index for p in pins] == [0, 1, 2]


def test_bo_qua_du_lieu_rac_khong_no() -> None:
    for junk in ({}, [], None, "chuỗi", {"a": None}, {"results": [None, 5, "x"]}):
        assert extract_pins(junk) == []


def test_cay_long_qua_sau_khong_lam_treo() -> None:
    """Chặn độ sâu để một phản hồi lạ không làm chương trình đứng."""
    deep: dict = {"level": {}}
    node = deep["level"]
    for _ in range(200):
        node["level"] = {}
        node = node["level"]
    node["pin"] = _pin_json("sau-tit-mu")
    assert extract_pins(deep) == []  # quá sâu -> bỏ qua, không nổ


# ===========================================================================
# Đọc số lượt lưu -- Pinterest để nó ở nhiều chỗ khác nhau
# ===========================================================================
def test_doc_luot_luu_tu_repin_count() -> None:
    assert extract_pins({"r": [_pin_json("a", saves=500)]})[0].saves == 500


def test_doc_luot_luu_tu_aggregated_stats() -> None:
    pin = _pin_json("a")
    pin["aggregated_pin_data"] = {"aggregated_stats": {"saves": 999, "done": 3}}
    assert extract_pins({"r": [pin]})[0].saves == 999


def test_lay_gia_tri_lon_nhat_khi_co_nhieu_nguon() -> None:
    pin = _pin_json("a", saves=100)
    pin["save_count"] = 250
    pin["aggregated_pin_data"] = {"aggregated_stats": {"saves": 175}}
    assert extract_pins({"r": [pin]})[0].saves == 250


def test_doc_so_bieu_cam() -> None:
    pin = _pin_json("a")
    pin["reaction_counts"] = {"1": 10, "2": 5}
    assert extract_pins({"r": [pin]})[0].reactions == 15


def test_so_lieu_sai_kieu_khong_lam_no() -> None:
    pin = _pin_json("a")
    pin["repin_count"] = "rất nhiều"
    assert extract_pins({"r": [pin]})[0].saves == 0


# ===========================================================================
# Chọn kích thước ảnh
# ===========================================================================
def test_uu_tien_anh_goc() -> None:
    url, width, _ = pick_image(_pin_json("a")["images"])
    assert "originals" in url and width == 1200


def test_ton_trong_thu_tu_uu_tien_tuy_chinh() -> None:
    url, _, _ = pick_image(_pin_json("a")["images"], preference=["236x"])
    assert "236x" in url


def test_khong_khop_uu_tien_nao_thi_lay_anh_to_nhat() -> None:
    """Pinterest hay thêm tên kích thước mới -- 'to nhất' luôn là ý định đúng."""
    images = {
        "kich-thuoc-la": {"url": "https://x/nho.jpg", "width": 100},
        "kich-thuoc-la-2": {"url": "https://x/to.jpg", "width": 2000},
    }
    url, width, _ = pick_image(images, preference=["orig"])
    assert url == "https://x/to.jpg" and width == 2000


def test_khong_co_anh_nao_dung_duoc() -> None:
    assert pick_image({"orig": {"width": 100}}) == ("", 0, 0)


# ===========================================================================
# Nâng cấp URL ảnh thu nhỏ
# ===========================================================================
def test_nang_url_thu_nho_len_ban_lon() -> None:
    small = "https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg"
    assert upgrade_pinimg_url(small) == "https://i.pinimg.com/736x/ab/cd/ef/abcdef.jpg"


def test_url_da_dung_kich_thuoc_thi_giu_nguyen() -> None:
    already = "https://i.pinimg.com/736x/ab/cd/ef/abcdef.jpg"
    assert upgrade_pinimg_url(already) == already


def test_url_khong_phai_pinterest_thi_khong_dung_toi() -> None:
    other = "https://example.com/236x/anh.jpg"
    assert upgrade_pinimg_url(other) == other
    assert upgrade_pinimg_url("") == ""


# ===========================================================================
# Gộp pin
# ===========================================================================
def test_gop_giu_so_lieu_cua_lan_thay_day_du_hon() -> None:
    """Pinterest trả cùng một pin ở nhiều phản hồi; lần sau có thể mới kèm số liệu."""
    collected: dict[str, PinCandidate] = {}
    merge_pins(collected, extract_pins({"r": [_pin_json("a", saves=0)]}))
    merge_pins(collected, extract_pins({"r": [_pin_json("a", saves=750)]}))

    assert len(collected) == 1
    assert collected["a"].saves == 750


def test_gop_giu_thu_hang_som_nhat() -> None:
    """Thứ hạng đầu tiên Pinterest xếp cho pin mới là thứ hạng thật của nó."""
    early = PinCandidate(id="a", image_url="u", discovery_index=2)
    late = PinCandidate(id="a", image_url="u", discovery_index=57)
    collected = {"a": early}
    merge_pins(collected, [late])
    assert collected["a"].discovery_index == 2


# ===========================================================================
# XẾP HẠNG -- phần quan trọng nhất: có trung thực không?
# ===========================================================================
def test_co_luot_luu_thi_xep_theo_luot_luu() -> None:
    pins = [
        PinCandidate(id="it", image_url="u", saves=10, discovery_index=0),
        PinCandidate(id="nhieu", image_url="u", saves=900, discovery_index=1),
        PinCandidate(id="vua", image_url="u", saves=400, discovery_index=2),
    ]
    top, basis = rank_pins(pins, top_n=2)

    assert basis is RankingBasis.SAVES
    assert [p.id for p in top] == ["nhieu", "vua"]


def test_khong_co_luot_luu_thi_bao_ro_la_theo_thu_tu_tim_kiem() -> None:
    """Đây là test quan trọng nhất trong file.

    Không có số liệu mà vẫn báo là 'top theo lượt thích' là đưa cho người dùng
    một con số không có thật. Phải trả về SEARCH_ORDER.
    """
    pins = [
        PinCandidate(id="b", image_url="u", saves=0, discovery_index=1),
        PinCandidate(id="a", image_url="u", saves=0, discovery_index=0),
    ]
    top, basis = rank_pins(pins, top_n=2)

    assert basis is RankingBasis.SEARCH_ORDER
    assert [p.id for p in top] == ["a", "b"], "phải giữ thứ tự Pinterest trả về"
    assert "THỨ TỰ TÌM KIẾM" in basis.describe()


def test_chi_co_bieu_cam_thi_bao_la_bieu_cam() -> None:
    pins = [
        PinCandidate(id="a", image_url="u", reactions=5, discovery_index=0),
        PinCandidate(id="b", image_url="u", reactions=50, discovery_index=1),
    ]
    top, basis = rank_pins(pins, top_n=2)

    assert basis is RankingBasis.REACTIONS
    assert [p.id for p in top] == ["b", "a"]


def test_luot_luu_duoc_uu_tien_hon_bieu_cam() -> None:
    """Lượt lưu sát nghĩa 'yêu thích' hơn, nên nó thắng khi có cả hai."""
    pins = [
        PinCandidate(id="a", image_url="u", saves=100, reactions=1, discovery_index=0),
        PinCandidate(id="b", image_url="u", saves=0, reactions=9999, discovery_index=1),
    ]
    top, basis = rank_pins(pins, top_n=2)

    assert basis is RankingBasis.SAVES
    assert top[0].id == "a"


def test_bang_diem_thi_giai_theo_thu_tu_pinterest() -> None:
    pins = [
        PinCandidate(id="sau", image_url="u", saves=50, discovery_index=9),
        PinCandidate(id="truoc", image_url="u", saves=50, discovery_index=1),
    ]
    top, _ = rank_pins(pins, top_n=2)
    assert [p.id for p in top] == ["truoc", "sau"]


def test_it_pin_hon_top_n_thi_tra_ve_tat_ca() -> None:
    pins = [PinCandidate(id="a", image_url="u", saves=5)]
    top, _ = rank_pins(pins, top_n=10)
    assert len(top) == 1


def test_khong_co_pin_nao() -> None:
    top, basis = rank_pins([], top_n=10)
    assert top == [] and basis is RankingBasis.SEARCH_ORDER


@pytest.mark.parametrize("basis", list(RankingBasis))
def test_moi_co_so_xep_hang_deu_giai_thich_duoc_bang_tieng_viet(basis) -> None:
    """Cơ sở xếp hạng đi tới tận log và manifest, nên phải đọc hiểu được."""
    assert len(basis.describe()) > 10
