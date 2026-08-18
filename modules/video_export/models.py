"""Kiểu dữ liệu của module video_export.

    Canvas    -- khung hình đích: rộng, cao, fps. Mọi clip đều bị đưa về đây.
    ClipInfo  -- kết quả đo một file video bằng ffprobe (sự thật về file đó)
    ExportPlan-- kế hoạch đã quyết: copy trực tiếp hay chuẩn hoá, và chạy lệnh gì

Tách `ClipInfo` (đo được) khỏi `Canvas` (mong muốn) là có chủ đích: toàn bộ quyết
định của module nằm ở chỗ so hai thứ này với nhau, nên chúng phải là hai kiểu
riêng biệt chứ không lẫn vào nhau.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Canvas:
    """Khung hình đích của video xuất ra."""

    width: int
    height: int
    fps: float

    def __str__(self) -> str:
        return f"{self.width}x{self.height}@{self.fps:g}fps"

    @property
    def aspect(self) -> float:
        return self.width / self.height


@dataclass(frozen=True)
class ClipInfo:
    """Số đo thật của một file video, do ffprobe trả về.

    Đây là *sự thật*, không phải mong muốn. Mọi thứ trong đây đọc từ file.
    """

    path: Path
    width: int
    height: int
    fps: float
    duration_s: float
    video_codec: str
    pix_fmt: str
    has_audio: bool
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    def matches_canvas(self, canvas: Canvas, fps_tolerance: float = 0.02) -> bool:
        """Clip này đã đúng khung hình đích chưa (kể cả fps)?"""
        return (
            self.width == canvas.width
            and self.height == canvas.height
            and abs(self.fps - canvas.fps) <= fps_tolerance
        )

    def stream_signature(self) -> tuple:
        """Vân tay các thuộc tính phải TRÙNG NHAU giữa mọi clip để ghép được
        bằng cách nối byte (`-c copy`) mà không cần giải mã lại.

        Lệch bất kỳ thứ nào trong đây -> buộc phải mã hoá lại. Đây chính là điều
        kiện của bộ ghép `concat` trong ffmpeg.
        """
        return (
            self.width,
            self.height,
            round(self.fps, 2),
            self.video_codec,
            self.pix_fmt,
            self.has_audio,
            self.audio_codec,
            self.sample_rate,
            self.channels,
        )

    def describe(self) -> str:
        audio = f"{self.audio_codec} {self.sample_rate}Hz" if self.has_audio else "không âm thanh"
        return (
            f"{self.path.name}: {self.width}x{self.height} @{self.fps:g}fps "
            f"{self.duration_s:.1f}s {self.video_codec}/{self.pix_fmt}, {audio}"
        )


@dataclass
class NormalizeJob:
    """Một lệnh ffmpeg đưa MỘT clip về đúng khung hình đích."""

    source: Path
    dest: Path
    args: list[str]
    #: Lý do clip này phải mã hoá lại -- ghi vào log để bạn hiểu chi phí từ đâu ra.
    reason: str = ""


@dataclass
class ExportPlan:
    """Kế hoạch xuất, đã quyết xong nhưng CHƯA chạy.

    Dựng kế hoạch là hàm thuần tuý (`plan.py`), thi hành là việc của `module.py`.
    Nhờ tách vậy mà `--dry-run` in ra được đúng từng lệnh ffmpeg sẽ chạy, và
    phần quyết định khó nhất kiểm thử được mà không cần có ffmpeg.
    """

    canvas: Canvas
    #: True = phải mã hoá lại. False = chỉ nối byte, nhanh và không mất chất lượng.
    needs_reencode: bool
    #: Vì sao lại chọn con đường đó. Luôn ghi ra log.
    reason: str
    #: Rỗng khi đi đường nối byte.
    normalize: list[NormalizeJob] = field(default_factory=list)
    #: Các file đưa vào bước ghép cuối (clip gốc, hoặc file tạm đã chuẩn hoá).
    concat_inputs: list[Path] = field(default_factory=list)
    #: Tham số cho bước ghép cuối.
    concat_args: list[str] = field(default_factory=list)
    output: Path = Path()
    total_duration_s: float = 0.0
