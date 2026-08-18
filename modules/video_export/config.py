"""Hợp đồng cấu hình của module video_export.

Nguồn sự thật duy nhất mô tả `config/video_export.yaml` được phép chứa gì.
`extra="forbid"` nên gõ sai tên khoá là báo lỗi ngay, không âm thầm bỏ qua.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.errors import ConfigError
from modules.video_export.models import Canvas

AspectRatio = Literal["16:9", "9:16", "1:1", "4:5", "4:3"]
Resolution = Literal["480p", "720p", "1080p", "1440p", "2160p"]

#: Tỉ lệ khung hình -> (rộng, cao) dạng tối giản.
_ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
    "4:3": (4, 3),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourcesConfig(_Strict):
    """Lấy clip ở đâu, và ghép theo thứ tự nào."""

    #: True = khi chạy trong một job, dùng danh sách video do module trước
    #: (video_gen) đưa qua `ctx.shared`. Đây là đường đi chính khi ghép module.
    from_shared: bool = True

    #: Dùng khi chạy độc lập, hoặc khi `ctx.shared` không có gì.
    #: Mẫu glob, tính từ gốc project.
    scan: list[str] = Field(default_factory=lambda: ["output/video_gen/*/*.mp4"])

    #: Thứ tự ghép -- rất quan trọng, vì đây là thứ tự cảnh trong video cuối.
    #:   pipeline : giữ nguyên thứ tự module trước đưa sang (= thứ tự khai prompt).
    #:              Chỉ dùng được khi có ctx.shared. Đây là mặc định hợp lý nhất
    #:              vì thứ tự bạn viết prompt thường chính là thứ tự kịch bản.
    #:   filename : sắp theo tên file (a-z). Ổn định, dễ đoán khi quét thư mục.
    #:   explicit : theo đúng danh sách `order_explicit` bên dưới.
    order: Literal["pipeline", "filename", "explicit"] = "pipeline"

    #: Dùng khi order = explicit. Mỗi phần tử là spec_id hoặc một phần tên file.
    #: Clip không khớp phần tử nào sẽ bị loại (kèm cảnh báo trong log).
    order_explicit: list[str] = Field(default_factory=list)

    #: Loại bỏ clip có đường dẫn chứa bất kỳ chuỗi nào trong đây.
    exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_explicit(self) -> SourcesConfig:
        if self.order == "explicit" and not self.order_explicit:
            raise ValueError("order = 'explicit' thì phải khai danh sách 'order_explicit'.")
        return self


class VideoConfig(_Strict):
    """Khung hình đích. Mọi clip lệch tỉ lệ đều bị đưa về đây."""

    target_aspect_ratio: AspectRatio = "16:9"
    target_resolution: Resolution = "1080p"

    #: Ghi đè trực tiếp kích thước, dạng "1920x1080". Bỏ trống -> suy từ hai
    #: trường trên. Dùng khi bạn cần kích thước lạ.
    target_size: str | None = None

    #: Xử lý clip có tỉ lệ khác khung đích:
    #:   letterbox = thu nhỏ vừa khung rồi thêm viền -> KHÔNG mất hình, có viền đen
    #:   crop      = phóng cho kín khung rồi cắt phần thừa -> kín khung, MẤT rìa hình
    fit: Literal["letterbox", "crop"] = "letterbox"

    #: Màu viền khi letterbox. Tên màu của ffmpeg hoặc mã hex ("black", "#101010").
    pad_color: str = "black"

    #: fps của video xuất ra. Bỏ trống -> lấy fps của clip đầu tiên.
    fps: float | None = None

    def canvas(self, fallback_fps: float) -> Canvas:
        """Tính khung hình đích. `fallback_fps` dùng khi config không chỉ định fps."""
        if self.target_size:
            width, height = _parse_size(self.target_size)
        else:
            width, height = _size_from_aspect(self.target_aspect_ratio, self.target_resolution)
        return Canvas(width=width, height=height, fps=self.fps or fallback_fps)


class EncodeConfig(_Strict):
    """Tham số mã hoá, chỉ dùng khi phải mã hoá lại."""

    #: auto   = nối byte nếu mọi clip đã đồng nhất và đúng khung đích,
    #:          ngược lại mã hoá lại. Gần như luôn là lựa chọn bạn muốn.
    #: always = luôn mã hoá lại (dùng khi cần chắc chắn đồng nhất tuyệt đối).
    #: never  = chỉ nối byte; clip lệch nhau -> báo lỗi thay vì âm thầm mã hoá lại.
    mode: Literal["auto", "always", "never"] = "auto"

    video_codec: str = "libx264"
    #: Chất lượng, thang CRF: nhỏ hơn = nét hơn, file to hơn. 18-23 là khoảng dùng được.
    crf: int = Field(default=20, ge=0, le=51)
    preset: str = "medium"
    pix_fmt: str = "yuv420p"

    #: keep  = giữ âm thanh. Clip nào không có tiếng sẽ được chèn khoảng lặng,
    #:         vì bộ ghép của ffmpeg đòi mọi đoạn phải có cùng bộ luồng.
    #: strip = bỏ hẳn âm thanh.
    audio: Literal["keep", "strip"] = "keep"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    audio_channels: int = 2


class OutputConfig(_Strict):
    #: Biến dùng được: {date} {run_id} {count}
    path: str = "output/export/final_{date}.mp4"

    #: File đích đã tồn tại thì làm gì:
    #:   suffix    = thêm _02, _03... KHÔNG ghi đè, không mất dữ liệu (mặc định)
    #:   overwrite = ghi đè
    #:   error     = dừng, báo lỗi
    on_exists: Literal["suffix", "overwrite", "error"] = "suffix"

    #: Giữ lại các file tạm đã chuẩn hoá (để soi khi kết quả trông lạ).
    keep_temp: bool = False
    temp_dir: Path = Path("output/export/_temp")


class FfmpegConfig(_Strict):
    #: Tên lệnh (tìm trong PATH) hoặc đường dẫn đầy đủ tới file thực thi.
    binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    timeout_s: int = Field(default=3600, ge=30)
    #: Ghi nguyên lệnh ffmpeg vào log. Rất nên bật -- bạn chép ra chạy tay được.
    log_command: bool = True


class VideoExportConfig(_Strict):
    """Toàn bộ cấu hình module video_export."""

    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    encode: EncodeConfig = Field(default_factory=EncodeConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    ffmpeg: FfmpegConfig = Field(default_factory=FfmpegConfig)

    #: Số clip tối thiểu để chạy. Đặt 1 nếu bạn muốn xuất cả khi chỉ có một clip.
    min_clips: int = Field(default=1, ge=1)


# ==========================================================================
# Tính kích thước khung hình
# ==========================================================================
def _round_even(value: float) -> int:
    """Làm tròn về số chẵn gần nhất.

    Bắt buộc: định dạng màu yuv420p (mọi trình phát đều đọc được) đòi cả chiều
    rộng và chiều cao phải chẵn. Số lẻ -> ffmpeg báo lỗi.
    """
    return max(2, int(round(value / 2)) * 2)


def _size_from_aspect(aspect: str, resolution: str) -> tuple[int, int]:
    """Suy kích thước từ tỉ lệ + độ phân giải.

    Quy ước: con số trong "1080p" là CẠNH NGẮN. Nhờ vậy một quy tắc phủ cả ba
    hướng khung hình:
        16:9 1080p -> 1920x1080     9:16 1080p -> 1080x1920     1:1 1080p -> 1080x1080
    """
    if aspect not in _ASPECT_RATIOS:
        raise ConfigError(f"Tỉ lệ khung hình không hỗ trợ: {aspect}")
    ratio_w, ratio_h = _ASPECT_RATIOS[aspect]
    short_edge = int(resolution.rstrip("p"))

    if ratio_w >= ratio_h:  # ngang hoặc vuông -> cạnh ngắn là chiều cao
        height = short_edge
        width = _round_even(short_edge * ratio_w / ratio_h)
    else:  # dọc -> cạnh ngắn là chiều rộng
        width = short_edge
        height = _round_even(short_edge * ratio_h / ratio_w)
    return _round_even(width), _round_even(height)


def _parse_size(text: str) -> tuple[int, int]:
    """Đọc chuỗi '1920x1080'."""
    try:
        width_text, height_text = text.lower().replace(" ", "").split("x")
        width, height = int(width_text), int(height_text)
    except ValueError as exc:
        raise ConfigError(f"target_size phải có dạng '1920x1080', nhận được: '{text}'") from exc
    if width <= 0 or height <= 0:
        raise ConfigError(f"target_size không hợp lệ: '{text}'")
    return _round_even(width), _round_even(height)
