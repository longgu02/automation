"""Hợp đồng cấu hình của module video_gen.

File này là *nguồn sự thật duy nhất* mô tả `config/video_gen.yaml` được phép
chứa gì. Muốn thêm một tuỳ chọn: thêm trường ở đây trước, rồi mới dùng trong
code. Sai chính tả key trong YAML sẽ bị bắt ngay lúc nạp (extra="forbid").
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.retry import RetryPolicy
from modules.video_gen.models import AspectRatio, Resolution

BackendName = Literal["flow_browser", "gemini_api"]


class _Strict(BaseModel):
    """Chặn key lạ -> lỗi chính tả trong YAML không bị nuốt lặng lẽ."""

    model_config = ConfigDict(extra="forbid")


class VideoDefaults(_Strict):
    """Tham số mặc định áp cho mọi prompt. Từng prompt có thể ghi đè."""

    model: str = "veo-3.1"
    aspect_ratio: AspectRatio = "16:9"
    resolution: Resolution = "1080p"
    duration_seconds: int = Field(default=8, ge=2, le=60)
    outputs_per_prompt: int = Field(default=1, ge=1, le=4)
    negative_prompt: str | None = None
    seed: int | None = None

    #: Đoạn văn nối vào ĐẦU mọi prompt -- nơi đặt phong cách chung của cả batch.
    prompt_prefix: str = ""
    #: Đoạn văn nối vào CUỐI mọi prompt (ví dụ: "cinematic, 35mm film grain").
    prompt_suffix: str = ""


class RetryConfig(_Strict):
    attempts: int = Field(default=3, ge=1, le=10)
    base_delay_s: float = Field(default=5.0, ge=0)
    max_delay_s: float = Field(default=120.0, ge=0)

    def to_policy(self) -> RetryPolicy:
        return RetryPolicy(
            attempts=self.attempts,
            base_delay_s=self.base_delay_s,
            max_delay_s=self.max_delay_s,
        )


class BrowserConfig(_Strict):
    """Cấu hình riêng cho backend `flow_browser`."""

    #: URL nơi gõ prompt. Xem README để biết vì sao nên trỏ vào MỘT project sẵn có.
    workspace_url: str = "https://labs.google/fx/tools/flow"

    #: 'chrome' = dùng Google Chrome thật đã cài trên máy. Khuyến nghị mạnh:
    #: Chromium mặc định của Playwright hay bị Google chặn đăng nhập với thông
    #: báo "This browser or app may not be secure".
    browser_channel: Literal["chrome", "msedge", "chromium"] = "chrome"

    headless: bool = False
    slow_mo_ms: int = Field(default=0, ge=0, description="Làm chậm thao tác, để gỡ lỗi.")
    viewport_width: int = 1600
    viewport_height: int = 1000
    locale: str = "en-US"

    #: Thời gian tối đa chờ MỘT lần render xong (Veo thường 1-3 phút/clip).
    generation_timeout_s: int = Field(default=900, ge=60)
    #: Nhịp kiểm tra xem đã render xong chưa.
    poll_interval_s: float = Field(default=3.0, ge=0.5)
    #: Thời gian tối đa chờ một phần tử UI xuất hiện.
    element_timeout_ms: int = Field(default=15000, ge=1000)

    #: Video mới xuất hiện ở đầu hay cuối danh sách kết quả?
    #: Đặt sai -> tải nhầm clip cũ. Kiểm tra bằng `--headful` ở lần chạy đầu.
    results_order: Literal["newest_first", "newest_last"] = "newest_last"

    #: Có cố chỉnh model/tỉ lệ khung hình qua bảng settings không.
    #: False (mặc định) = bạn chỉnh tay một lần trên UI, Flow nhớ theo project.
    #: Đây là lựa chọn ĐÁNG TIN CẬY hơn hẳn; xem README.
    apply_settings_in_ui: bool = False

    #: Lưu ảnh chụp màn hình + HTML khi lỗi, để sửa selector.
    save_debug_artifacts: bool = True

    selectors_file: Path = Path("config/flow_selectors.yaml")


class GeminiApiConfig(_Strict):
    """Cấu hình riêng cho backend `gemini_api` (Veo qua Gemini API)."""

    #: Đừng viết thẳng key vào YAML -- dùng ${GEMINI_API_KEY} đọc từ .env.
    api_key: str = ""
    #: Tên model đầy đủ phía API, khác với tên thân thiện ở `defaults.model`.
    model_id: str = "veo-3.1-generate-preview"
    poll_interval_s: float = Field(default=10.0, ge=1)
    generation_timeout_s: int = Field(default=900, ge=60)
    person_generation: Literal["allow_all", "allow_adult", "dont_allow"] = "allow_all"


class ExecutionConfig(_Strict):
    """Chạy bằng bao nhiêu tài khoản, và chạy song song tới đâu."""

    accounts_file: Path = Path("config/accounts.yaml")

    #: Số tài khoản chạy đồng thời.
    #:   1  = tuần tự, một tài khoản một lúc (vẫn tự chuyển tài khoản khi hết credit)
    #:   >1 = song song thật sự, mỗi worker giữ một tài khoản riêng
    #:
    #: Số worker chạy thật = min(max_parallel, số tài khoản đang bật).
    #: Đặt lớn hơn số tài khoản không giúp nhanh hơn: phía Google mỗi tài khoản
    #: chỉ có MỘT hàng đợi render, nên trục song song hoá là tài khoản.
    max_parallel: int = Field(default=1, ge=1, le=8)

    #: Nghỉ giữa hai prompt trong CÙNG một worker, tránh đập liên tục.
    delay_between_specs_s: float = Field(default=2.0, ge=0)

    #: Một prompt được phép chuyển sang tài khoản khác bao nhiêu lần khi gặp
    #: lỗi hết credit. Bỏ trống -> đúng bằng số tài khoản (thử hết một lượt).
    max_account_switches_per_spec: int | None = Field(default=None, ge=1)


class OutputConfig(_Strict):
    root: Path = Path("output/video_gen")
    #: Mẫu tên file. Biến dùng được: {spec_id} {index} {run_id} {date}
    filename_template: str = "{spec_id}_{index:02d}.mp4"
    #: File trạng thái phục vụ resume. Đặt cạnh root để xoá là reset sạch.
    state_file: Path = Path("output/video_gen/_state.json")


class VideoGenConfig(_Strict):
    """Toàn bộ cấu hình module video_gen."""

    backend: BackendName = "flow_browser"

    #: Các file YAML chứa prompt. Nạp theo thứ tự; id trùng -> file sau thắng.
    prompt_files: list[Path] = Field(default_factory=lambda: [Path("config/prompts/demo.yaml")])

    defaults: VideoDefaults = Field(default_factory=VideoDefaults)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    gemini_api: GeminiApiConfig = Field(default_factory=GeminiApiConfig)

    #: Bỏ qua spec đã hoàn thành ở lần chạy trước (đối chiếu theo vân tay).
    #: Đây là cơ chế tiết kiệm credit quan trọng nhất -- đừng tắt trừ khi cần.
    resume: bool = True
