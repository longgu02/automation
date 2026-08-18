"""Bóc pin ra khỏi dữ liệu JSON của Pinterest, và xếp hạng.

FILE NÀY KHÔNG CHẠM VÀO GÌ CẢ -- không mạng, không trình duyệt, không đĩa. Vào
là dữ liệu JSON đã có sẵn, ra là `list[PinCandidate]`. Nhờ vậy toàn bộ phần dễ
vỡ nhất (hình dạng JSON của Pinterest) kiểm thử được bằng dữ liệu mẫu, không cần
mở trình duyệt lần nào.

CÁCH LẤY DỮ LIỆU -- đáng đọc kỹ vì nó là lựa chọn thiết kế chính:

Ta KHÔNG gọi thẳng vào API nào của Pinterest. Ta mở trang như một người dùng
bình thường, rồi đọc lại chính những phản hồi JSON mà trình duyệt đã tải về.
Nghĩa là không tạo thêm một lượt truy cập nào ngoài những gì việc xem trang vốn
đã sinh ra -- vừa nhẹ cho Pinterest, vừa đúng tinh thần "thao tác như người dùng".

VÌ SAO DUYỆT ĐỆ QUY THAY VÌ ĐI THEO ĐƯỜNG DẪN CỐ ĐỊNH: Pinterest đổi tên endpoint
và tên khoá lồng nhau khá thường xuyên (`resource_response.data.results`,
`data.results`, `...data.pins`...). Bám vào một đường dẫn cứng là gãy sau vài
tháng. Thay vào đó ta lùng khắp cây JSON tìm những dict *trông giống một pin* --
cách này miễn nhiễm với việc họ đổi chỗ dữ liệu.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.image_crawl.models import PinCandidate, RankingBasis

log = logging.getLogger(__name__)

#: Thứ tự ưu tiên khi chọn kích thước ảnh. `orig` là bản gốc do người đăng tải lên.
DEFAULT_SIZE_PREFERENCE = ["orig", "originals", "736x", "564x", "474x", "236x"]

#: Độ sâu tối đa khi duyệt cây JSON. Chặn để một phản hồi lạ không làm treo.
_MAX_DEPTH = 12


def looks_like_pin(node: Any) -> bool:
    """Dict này có phải một pin không?

    Điều kiện: có id, và có khối `images` chứa ít nhất một ảnh có `url`. Đó là
    hai thứ ổn định nhất qua các phiên bản của Pinterest -- các trường khác
    (repin_count, description...) có thể vắng mặt tuỳ ngữ cảnh.
    """
    if not isinstance(node, dict):
        return False
    if not node.get("id"):
        return False
    images = node.get("images")
    if not isinstance(images, dict) or not images:
        return False
    return any(isinstance(v, dict) and v.get("url") for v in images.values())


def pick_image(images: dict, preference: list[str] | None = None) -> tuple[str, int, int]:
    """Chọn ảnh to nhất/đúng ý nhất trong khối `images` của pin.

    Trả về (url, rộng, cao). Không có ảnh nào dùng được -> ("", 0, 0).
    Hết danh sách ưu tiên thì rơi về ảnh có chiều rộng lớn nhất -- Pinterest hay
    thêm tên kích thước mới, và "to nhất" luôn là ý định đúng.
    """
    order = preference or DEFAULT_SIZE_PREFERENCE

    for key in order:
        entry = images.get(key)
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"]), int(entry.get("width") or 0), int(entry.get("height") or 0)

    best: tuple[str, int, int] = ("", 0, 0)
    for entry in images.values():
        if isinstance(entry, dict) and entry.get("url"):
            width = int(entry.get("width") or 0)
            if width >= best[1]:
                best = (str(entry["url"]), width, int(entry.get("height") or 0))
    return best


def _read_saves(node: dict) -> int:
    """Đọc số lượt lưu, thử mọi chỗ Pinterest từng đặt nó.

    Nhiều nguồn vì cùng một con số xuất hiện dưới các tên khác nhau tuỳ endpoint.
    Lấy giá trị lớn nhất tìm được -- các trường vắng mặt trả 0 nên không ảnh hưởng.
    """
    values = [_as_int(node.get("repin_count")), _as_int(node.get("save_count"))]

    aggregated = node.get("aggregated_pin_data")
    if isinstance(aggregated, dict):
        stats = aggregated.get("aggregated_stats")
        if isinstance(stats, dict):
            values.append(_as_int(stats.get("saves")))

    return max(values)


def _read_reactions(node: dict) -> int:
    """Đọc tổng số biểu cảm, nếu Pinterest có trả về."""
    total = _as_int(node.get("total_reaction_count"))
    counts = node.get("reaction_counts")
    if isinstance(counts, dict):
        total = max(total, sum(_as_int(v) for v in counts.values()))
    return total


def extract_pins(
    payload: Any, size_preference: list[str] | None = None, start_index: int = 0
) -> list[PinCandidate]:
    """Duyệt khắp một phản hồi JSON, gom mọi pin tìm thấy.

    Giữ nguyên thứ tự gặp -- đó chính là thứ tự xếp hạng của Pinterest, và là
    phương án dự phòng khi không có số liệu tương tác nào.
    """
    found: list[PinCandidate] = []
    seen: set[str] = set()

    def walk(node: Any, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return

        if looks_like_pin(node):
            pin_id = str(node["id"])
            if pin_id not in seen:
                url, width, height = pick_image(node["images"], size_preference)
                if url:
                    seen.add(pin_id)
                    found.append(
                        PinCandidate(
                            id=pin_id,
                            image_url=url,
                            pin_url=f"https://www.pinterest.com/pin/{pin_id}/",
                            title=_clean(node.get("grid_title") or node.get("title")),
                            description=_clean(node.get("description")),
                            saves=_read_saves(node),
                            reactions=_read_reactions(node),
                            width=width,
                            height=height,
                            discovery_index=start_index + len(found),
                        )
                    )
            # Không dừng ở đây: pin có thể lồng pin khác (gợi ý liên quan).

        for value in node.values():
            walk(value, depth + 1)

    walk(payload, 0)
    return found


def merge_pins(
    collected: dict[str, PinCandidate], new_pins: list[PinCandidate]
) -> dict[str, PinCandidate]:
    """Gộp pin mới vào kho đã có, khử trùng lặp theo id.

    Sửa `collected` tại chỗ và trả lại nó. Cùng một pin xuất hiện ở nhiều phản
    hồi là chuyện thường, và lần sau có thể kèm số liệu mà lần đầu chưa có.
    """
    for pin in new_pins:
        existing = collected.get(pin.id)
        collected[pin.id] = existing.merged_with(pin) if existing else pin
    return collected


def rank_pins(
    pins: list[PinCandidate], top_n: int
) -> tuple[list[PinCandidate], RankingBasis]:
    """Xếp hạng và lấy `top_n` pin đầu.

    Trả kèm CƠ SỞ xếp hạng đã dùng thật sự. Không bao giờ giả vờ có số liệu khi
    không có -- gọi một danh sách theo thứ tự tìm kiếm là "top theo lượt thích"
    là nói sai với người dùng.
    """
    if not pins:
        return [], RankingBasis.SEARCH_ORDER

    if any(p.saves > 0 for p in pins):
        basis = RankingBasis.SAVES
        ordered = sorted(pins, key=lambda p: (-p.saves, p.discovery_index))
    elif any(p.reactions > 0 for p in pins):
        basis = RankingBasis.REACTIONS
        ordered = sorted(pins, key=lambda p: (-p.reactions, p.discovery_index))
    else:
        basis = RankingBasis.SEARCH_ORDER
        ordered = sorted(pins, key=lambda p: p.discovery_index)

    return ordered[:top_n], basis


#: Các đoạn kích thước xuất hiện trong URL ảnh của Pinterest (i.pinimg.com).
_PINIMG_SIZES = ("/236x/", "/474x/", "/564x/", "/736x/", "/originals/")


def upgrade_pinimg_url(url: str, target: str = "736x") -> str:
    """Nâng URL ảnh thu nhỏ của Pinterest lên bản lớn hơn.

    Ảnh trong DOM thường là bản 236x (ảnh xem trước trong lưới). URL của Pinterest
    nhúng thẳng kích thước vào đường dẫn, nên đổi đoạn đó là lấy được bản lớn:

        .../236x/ab/cd/ef/abcdef.jpg   ->   .../736x/ab/cd/ef/abcdef.jpg

    Cố ý dùng 736x chứ không phải `originals`: bản gốc không phải lúc nào cũng
    tồn tại và sẽ trả về 404, còn 736x thì gần như luôn có. Đường JSON đã cho
    URL chuẩn rồi, hàm này chỉ phục vụ phương án bóc từ DOM.
    """
    if not url or "pinimg.com" not in url:
        return url
    for size in _PINIMG_SIZES:
        if size in url:
            if size.strip("/") == target:
                return url
            return url.replace(size, f"/{target}/", 1)
    return url


def _as_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _clean(value: Any) -> str:
    return " ".join(str(value).split()) if value else ""
