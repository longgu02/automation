"""Lớp bọc ffmpeg / ffprobe -- toàn bộ phần chạm tới hệ thống của module này.

Mọi thứ gọi tiến trình con, đọc ghi đĩa, dò file thực thi đều nằm ở đây. Phần
quyết định (`plan.py`) tuyệt đối không import file này -- nhờ vậy nó kiểm thử
được mà không cần cài ffmpeg.

BA ĐIỀU CẦN BIẾT KHI SỬA FILE NÀY:

1. ffmpeg ghi tiến trình ra *stderr*, không phải stdout. Đó là hành vi bình
   thường, không phải lỗi. Chỉ mã thoát mới cho biết thành hay bại.
2. Thông báo lỗi thật của ffmpeg thường nằm ở vài dòng CUỐI của stderr, còn
   phần đầu là mấy chục dòng khoe cấu hình biên dịch. Nên khi hỏng, ta chỉ trích
   phần đuôi ra cho người đọc.
3. Đường dẫn Windows có dấu `\\` và tên thư mục tiếng Việt có dấu. Danh sách
   ghép của ffmpeg cần dấu `/` và phải thoát dấu nháy đơn -- xem `write_concat_list`.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from core.errors import ConfigError, FatalError
from core.paths import ensure_dir
from modules.video_export.models import ClipInfo

log = logging.getLogger(__name__)


class FfmpegRunner:
    """Chạy ffmpeg/ffprobe và dịch kết quả sang kiểu dữ liệu của ta."""

    def __init__(
        self,
        binary: str = "ffmpeg",
        probe_binary: str = "ffprobe",
        timeout_s: int = 3600,
        log_command: bool = True,
    ):
        self.binary = _resolve_tool(binary, "ffmpeg")
        self.probe_binary = _resolve_tool(probe_binary, "ffprobe")
        self.timeout_s = timeout_s
        self.log_command = log_command

    def version(self) -> str:
        """Dòng phiên bản đầu tiên của ffmpeg, để ghi vào log lúc khởi động."""
        result = subprocess.run(
            [self.binary, "-version"], capture_output=True, text=True, timeout=30
        )
        return result.stdout.splitlines()[0] if result.stdout else "không xác định"

    # ------------------------------------------------------------------ đo
    def probe(self, path: Path) -> ClipInfo:
        """Đo một file video bằng ffprobe.

        Ném FatalError nếu file hỏng hoặc không có luồng hình -- đó là lỗi của
        dữ liệu đầu vào, thử lại cũng không đổi gì.
        """
        if not path.exists():
            raise FatalError(f"Không tìm thấy file video: {path}")

        command = [
            self.probe_binary,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=120, encoding="utf-8"
            )
        except subprocess.TimeoutExpired as exc:
            raise FatalError(f"ffprobe treo khi đọc {path.name}") from exc

        if result.returncode != 0:
            raise FatalError(f"ffprobe không đọc được {path.name}: {_tail(result.stderr)}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FatalError(f"ffprobe trả về dữ liệu không đọc được cho {path.name}") from exc

        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if video is None:
            raise FatalError(f"{path.name} không có luồng hình -- đây có phải file video không?")

        duration = _to_float(data.get("format", {}).get("duration")) or _to_float(
            video.get("duration")
        ) or 0.0

        return ClipInfo(
            path=path,
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            fps=_parse_fps(video.get("r_frame_rate")),
            duration_s=duration,
            video_codec=str(video.get("codec_name") or "?"),
            pix_fmt=str(video.get("pix_fmt") or "?"),
            has_audio=audio is not None,
            audio_codec=str(audio.get("codec_name")) if audio else None,
            sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
            channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        )

    # ------------------------------------------------------------- thi hành
    def run(self, args: list[str], label: str) -> None:
        """Chạy một lệnh ffmpeg. Ném FatalError kèm thông báo có ích nếu hỏng."""
        command = [self.binary, *args]
        if self.log_command:
            # In nguyên lệnh để bạn chép ra chạy tay khi cần soi.
            log.info("ffmpeg %s", " ".join(_quote(a) for a in args))

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise FatalError(f"{label}: ffmpeg quá {self.timeout_s}s chưa xong.") from exc

        if result.returncode != 0:
            raise FatalError(f"{label} thất bại (mã {result.returncode}):\n{_tail(result.stderr)}")


# ==========================================================================
# Tiện ích
# ==========================================================================
def _resolve_tool(binary: str, name: str) -> str:
    """Tìm file thực thi, báo lỗi rõ ràng nếu không có."""
    found = shutil.which(binary)
    if found:
        return found
    if Path(binary).exists():
        return str(Path(binary).resolve())
    raise ConfigError(
        f"Không tìm thấy {name} (đã thử '{binary}').\n"
        f"  - Cài đặt:  winget install Gyan.FFmpeg\n"
        f"  - Hoặc khai đường dẫn đầy đủ trong config/video_export.yaml -> ffmpeg.binary\n"
        f"  - Cài xong nhớ mở lại terminal để PATH được cập nhật."
    )


def write_concat_list(paths: list[Path], dest: Path) -> Path:
    """Viết file danh sách cho bộ ghép `concat` của ffmpeg.

    Định dạng mỗi dòng:  file 'đường/dẫn'

    Hai bẫy trên Windows, cả hai đều xử lý ở đây:
      - Dấu `\\` bị ffmpeg hiểu là ký tự thoát -> đổi hết sang `/`.
      - Dấu nháy đơn trong tên file phải viết thành `'\\''`.
    """
    ensure_dir(dest.parent)
    lines = []
    for path in paths:
        text = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{text}'")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def _parse_fps(value: str | None) -> float:
    """Đọc fps dạng phân số của ffprobe ('24000/1001' -> 23.976)."""
    if not value:
        return 0.0
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denom = float(denominator)
            return float(numerator) / denom if denom else 0.0
        return float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _tail(text: str | None, lines: int = 12) -> str:
    """Vài dòng cuối của stderr -- nơi ffmpeg để thông báo lỗi thật."""
    if not text:
        return "(ffmpeg không nói gì thêm)"
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(f"    {line}" for line in kept[-lines:])


def _quote(arg: str) -> str:
    """Bọc nháy khi tham số có khoảng trắng, để lệnh in ra chép chạy được ngay."""
    return f'"{arg}"' if " " in arg else arg
