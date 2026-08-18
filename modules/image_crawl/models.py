"""Kiểu dữ liệu của module image_crawl.

    PinCandidate  -- một pin đã tìm thấy, kèm số liệu tương tác nếu có
    RankingBasis  -- cơ sở xếp hạng đã dùng THẬT SỰ (rất quan trọng, đọc bên dưới)
    CrawlReport   -- kết quả cả lần chạy

VÌ SAO CÓ `RankingBasis`: Pinterest không phải lúc nào cũng trả về số lượt lưu.
Khi thiếu, module vẫn xếp được thứ tự -- nhưng bằng thứ tự tìm kiếm của chính
Pinterest, chứ không phải bằng số liệu yêu thích. Hai thứ đó KHÁC NHAU, và người
dùng có quyền biết mình đang cầm cái nào. Nên cơ sở xếp hạng là dữ liệu hạng
nhất, được ghi vào log và vào file kê khai, chứ không bị giấu đi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RankingBasis(str, Enum):
    """Thứ hạng "top 10" thực sự dựa trên cái gì."""

    #: Có số lượt lưu thật từ Pinterest -> đúng nghĩa "nhiều yêu thích nhất".
    SAVES = "saves"
    #: Không có số lượt lưu, dùng số biểu cảm (reaction) thay thế.
    REACTIONS = "reactions"
    #: Không có số liệu nào -> giữ thứ tự Pinterest trả về. Thứ tự này vốn đã
    #: được Pinterest xếp theo mức độ tương tác, nhưng ta KHÔNG đo được nó,
    #: nên đừng gọi kết quả là "top theo lượt thích".
    SEARCH_ORDER = "search_order"

    def describe(self) -> str:
        return {
            RankingBasis.SAVES: "số lượt lưu thật từ Pinterest",
            RankingBasis.REACTIONS: "số biểu cảm (không có số lượt lưu)",
            RankingBasis.SEARCH_ORDER: (
                "THỨ TỰ TÌM KIẾM của Pinterest -- không lấy được số liệu tương tác nào"
            ),
        }[self]


@dataclass
class PinCandidate:
    """Một pin tìm thấy trong lúc duyệt."""

    id: str
    image_url: str
    pin_url: str = ""
    title: str = ""
    description: str = ""

    #: Số lượt lưu (repin). Đây là thứ gần "yêu thích" nhất mà Pinterest công bố.
    saves: int = 0
    #: Số biểu cảm, khi có.
    reactions: int = 0

    width: int = 0
    height: int = 0
    #: Thứ tự Pinterest trả về pin này. Dùng làm phương án xếp hạng dự phòng.
    discovery_index: int = 0

    @property
    def engagement(self) -> int:
        """Chỉ số tương tác dùng để xếp hạng. 0 = không có số liệu."""
        return max(self.saves, self.reactions)

    def merged_with(self, other: PinCandidate) -> PinCandidate:
        """Gộp hai lần thấy cùng một pin, giữ thông tin đầy đủ hơn.

        Cần có vì Pinterest trả cùng một pin ở nhiều phản hồi khác nhau, và lần
        sau có thể kèm số liệu mà lần đầu chưa có.
        """
        return PinCandidate(
            id=self.id,
            image_url=self.image_url or other.image_url,
            pin_url=self.pin_url or other.pin_url,
            title=self.title or other.title,
            description=self.description or other.description,
            saves=max(self.saves, other.saves),
            reactions=max(self.reactions, other.reactions),
            width=max(self.width, other.width),
            height=max(self.height, other.height),
            # Giữ lần thấy SỚM NHẤT -- đó mới là thứ hạng Pinterest xếp cho nó.
            discovery_index=min(self.discovery_index, other.discovery_index),
        )

    def describe(self) -> str:
        stat = f"{self.saves} lượt lưu" if self.saves else (
            f"{self.reactions} biểu cảm" if self.reactions else "không có số liệu"
        )
        label = (self.title or self.description or "(không tiêu đề)")[:50]
        return f"{stat:<20} {label}"


@dataclass
class DownloadedImage:
    """Một ảnh đã tải về đĩa."""

    pin: PinCandidate
    path: Path
    size_bytes: int
    rank: int


@dataclass
class CrawlReport:
    """Kết quả cả lần chạy."""

    query: str
    basis: RankingBasis
    discovered: int = 0
    downloaded: list[DownloadedImage] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    scrolls: int = 0
    elapsed_s: float = 0.0
