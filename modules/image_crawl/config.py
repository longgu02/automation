"""Hợp đồng cấu hình của module image_crawl.

Nguồn sự thật duy nhất mô tả `config/image_crawl.yaml`. `extra="forbid"` nên gõ
sai tên khoá là báo lỗi ngay lúc nạp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from modules.image_crawl.humanize import PacingProfile


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchConfig(_Strict):
    """Tìm gì trên Pinterest."""

    #: Từ khoá tìm kiếm. Có thể ghi đè bằng `--query` trên dòng lệnh.
    query: str = "minimalist interior design"

    #: Lấy bao nhiêu ảnh cuối cùng.
    top_n: int = Field(default=10, ge=1, le=100)

    #: Cần gom được ít nhất bao nhiêu pin trước khi xếp hạng.
    #: Phải LỚN HƠN top_n khá nhiều thì bảng xếp hạng mới có ý nghĩa: chọn 10
    #: trong 12 gần như chỉ là lấy tất cả, chọn 10 trong 120 mới là chọn lọc.
    candidate_pool: int = Field(default=120, ge=1)

    #: Trần số nhịp cuộn, phòng khi không bao giờ gom đủ candidate_pool.
    max_scrolls: int = Field(default=40, ge=1, le=300)

    #: Dùng thẳng URL này thay cho tìm kiếm theo từ khoá (ví dụ URL một bảng).
    #: {query} sẽ được thay bằng từ khoá đã mã hoá URL.
    url_template: str = "https://www.pinterest.com/search/pins/?q={query}&rs=typed"


class BrowserConfig(_Strict):
    """Trình duyệt dùng để duyệt Pinterest."""

    #: Profile riêng cho Pinterest, tách khỏi profile của Google Flow.
    profile_dir: Path = Path(".secrets/profiles/pinterest")

    browser_channel: Literal["chrome", "msedge", "chromium"] = "chrome"

    #: Mặc định HIỆN cửa sổ. Trang chạy ẩn dễ bị nhận ra hơn, và bạn cũng nên
    #: nhìn thấy nó đang làm gì trong vài lần chạy đầu.
    headless: bool = False

    viewport_width: int = 1440
    viewport_height: int = 900
    locale: str = "en-US"

    #: Thời gian tối đa chờ trang tải xong.
    page_timeout_ms: int = Field(default=45000, ge=5000)

    save_debug_artifacts: bool = True


class PacingConfig(_Strict):
    """Nhịp thao tác. Xem `modules/image_crawl/humanize.py` để hiểu từng thứ.

    Số nhỏ hơn = nhanh hơn = giống máy hơn = dễ bị chặn hơn. Bộ mặc định là
    nhịp của một người đang lướt Pinterest bình thường.
    """

    min_action_delay_s: float = Field(default=1.5, ge=0)
    max_action_delay_s: float = Field(default=4.0, ge=0)

    min_scroll_px: int = Field(default=300, ge=50)
    max_scroll_px: int = Field(default=800, ge=50)
    min_scroll_pause_s: float = Field(default=0.8, ge=0)
    max_scroll_pause_s: float = Field(default=2.5, ge=0)

    #: Cứ bao nhiêu nhịp cuộn thì dừng lâu một lần. 0 = tắt.
    long_pause_every: int = Field(default=6, ge=0)
    min_long_pause_s: float = Field(default=4.0, ge=0)
    max_long_pause_s: float = Field(default=10.0, ge=0)

    #: Xác suất cuộn ngược lên một đoạn (người thật hay lướt quá rồi kéo lại).
    backscroll_chance: float = Field(default=0.15, ge=0, le=1)

    #: Số nhịp rê chuột trước các thao tác quan trọng.
    mouse_moves: int = Field(default=3, ge=0, le=20)

    #: Trần thời lượng một phiên. Cái phanh chống việc cấu hình sai biến thành
    #: phiên cào kéo dài hàng giờ.
    max_session_s: float = Field(default=900.0, ge=30)

    #: Cố định để tái lập được khi gỡ lỗi. null = mỗi phiên một khác.
    seed: int | None = None

    def to_profile(self) -> PacingProfile:
        data = self.model_dump(exclude={"seed"})
        return PacingProfile(**data)


class DownloadConfig(_Strict):
    """Tải ảnh về."""

    dest: Path = Path("output/image_crawl/{query_slug}")

    #: Thứ tự ưu tiên kích thước ảnh. `orig` là bản gốc người đăng tải lên.
    size_preference: list[str] = Field(
        default_factory=lambda: ["orig", "originals", "736x", "564x"]
    )

    #: Nghỉ giữa hai lượt tải. Tải liên tiếp không nghỉ là hành vi lộ liễu nhất.
    min_delay_s: float = Field(default=1.0, ge=0)
    max_delay_s: float = Field(default=3.0, ge=0)

    timeout_ms: int = Field(default=30000, ge=1000)

    #: Bỏ qua ảnh nhỏ hơn mức này (thường là biểu tượng, ảnh đại diện).
    min_width: int = Field(default=200, ge=0)

    #: Ghi kèm file kê khai: nguồn, số lượt lưu, đường dẫn pin gốc.
    #: Nên để true -- đây là dấu vết ghi công tác giả của từng tấm ảnh.
    write_manifest: bool = True

    #: File đã có thì bỏ qua, không tải lại.
    skip_existing: bool = True


class ImageCrawlConfig(_Strict):
    """Toàn bộ cấu hình module image_crawl."""

    search: SearchConfig = Field(default_factory=SearchConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    pacing: PacingConfig = Field(default_factory=PacingConfig)
    download: DownloadConfig = Field(default_factory=DownloadConfig)
