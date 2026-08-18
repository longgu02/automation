"""Dựng kế hoạch xuất: quyết định làm gì, và dựng đúng lệnh ffmpeg.

FILE NÀY KHÔNG CHẠM VÀO GÌ CẢ. Không tiến trình con, không đọc ghi đĩa, không
mạng. Vào là `list[ClipInfo]` + config, ra là một `ExportPlan`. Đó là lý do
`--dry-run` in ra được đúng từng lệnh sẽ chạy, và vì sao phần logic khó nhất của
module kiểm thử được mà máy không cần có ffmpeg.

HAI CON ĐƯỜNG:

  A. NỐI BYTE (`-c copy`) -- khi mọi clip đã đồng nhất *và* đúng khung hình đích.
     Không giải mã, không mã hoá. Vài giây, và không mất một chút chất lượng nào.

  B. CHUẨN HOÁ RỒI NỐI -- khi có clip lệch. Mỗi clip được đưa về đúng khung hình
     đích thành một file tạm, sau đó nối byte các file tạm đó.

Vì sao con đường B chuẩn hoá từng clip riêng thay vì gộp hết vào một lệnh
`filter_complex` khổng lồ:

  - Số clip tăng thì lệnh không dài ra (Windows chặn dòng lệnh ở ~32k ký tự).
  - Báo tiến độ được theo từng clip, thay vì ngồi nhìn một tiến trình im lìm.
  - Chi phí y hệt: vẫn đúng một lần mã hoá cho mỗi clip.
  - Đổi lại: tốn thêm chỗ trống trên đĩa cho file tạm, và xoá sau khi xong.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.errors import FatalError
from modules.video_export.config import VideoExportConfig
from modules.video_export.models import Canvas, ClipInfo, ExportPlan, NormalizeJob

log = logging.getLogger(__name__)


def build_plan(
    clips: list[ClipInfo],
    cfg: VideoExportConfig,
    output: Path,
    temp_dir: Path,
) -> ExportPlan:
    """Quyết định cách ghép và dựng sẵn mọi lệnh ffmpeg cần chạy."""
    if not clips:
        raise FatalError("Không có clip nào để ghép.")

    canvas = cfg.video.canvas(fallback_fps=_default_fps(clips))
    total_duration = sum(clip.duration_s for clip in clips)

    uniform, mismatch_reason = _is_uniform(clips, canvas, cfg)

    # --- Con đường A: nối byte ------------------------------------------
    if uniform and cfg.encode.mode != "always":
        return ExportPlan(
            canvas=canvas,
            needs_reencode=False,
            reason=(
                f"{len(clips)} clip đã đồng nhất và đúng khung {canvas} "
                "-> nối byte, không mã hoá lại."
            ),
            concat_inputs=[clip.path for clip in clips],
            concat_args=_concat_args(copy_streams=True),
            output=output,
            total_duration_s=total_duration,
        )

    if cfg.encode.mode == "never":
        # Người dùng đã cấm mã hoá lại -> phải nói rõ vì sao không nối byte được,
        # thay vì âm thầm làm điều họ vừa cấm.
        raise FatalError(
            "encode.mode = 'never' nhưng các clip không nối byte được:\n"
            f"    {mismatch_reason}\n"
            "  Đổi thành 'auto' để hệ thống tự chuẩn hoá, hoặc sinh lại video "
            "với cùng tỉ lệ và độ phân giải."
        )

    # --- Con đường B: chuẩn hoá rồi nối ---------------------------------
    forced = cfg.encode.mode == "always"
    reason = (
        f"encode.mode = 'always' -> mã hoá lại toàn bộ {len(clips)} clip về {canvas}."
        if forced
        else f"Các clip không đồng nhất ({mismatch_reason}) -> chuẩn hoá về {canvas}."
    )

    jobs: list[NormalizeJob] = []
    for index, clip in enumerate(clips, start=1):
        dest = temp_dir / f"{index:03d}_{clip.path.stem}.mp4"
        jobs.append(
            NormalizeJob(
                source=clip.path,
                dest=dest,
                args=_normalize_args(clip, canvas, cfg, dest),
                reason=_why_normalize(clip, canvas, cfg),
            )
        )

    return ExportPlan(
        canvas=canvas,
        needs_reencode=True,
        reason=reason,
        normalize=jobs,
        concat_inputs=[job.dest for job in jobs],
        concat_args=_concat_args(copy_streams=True),
        output=output,
        total_duration_s=total_duration,
    )


# ==========================================================================
# Quyết định
# ==========================================================================
def _default_fps(clips: list[ClipInfo]) -> float:
    """fps dùng khi config không chỉ định: lấy của clip đầu tiên.

    Rơi về 24 nếu ffprobe không đọc được -- thà có một giá trị hợp lý còn hơn
    đưa fps = 0 cho ffmpeg rồi nhận lỗi khó hiểu.
    """
    first = clips[0].fps
    return first if first > 0 else 24.0


def _is_uniform(
    clips: list[ClipInfo], canvas: Canvas, cfg: VideoExportConfig
) -> tuple[bool, str]:
    """Mọi clip có nối byte thẳng được không? Trả về (được/không, lý do)."""
    signatures = {clip.stream_signature() for clip in clips}
    if len(signatures) > 1:
        return False, _describe_mismatch(clips)

    reference = clips[0]
    if not reference.matches_canvas(canvas):
        return False, (
            f"clip là {reference.width}x{reference.height}@{reference.fps:g}fps "
            f"nhưng khung đích là {canvas}"
        )

    if cfg.encode.audio == "strip" and reference.has_audio:
        return False, "cần bỏ âm thanh (encode.audio = 'strip')"

    return True, ""


def _describe_mismatch(clips: list[ClipInfo]) -> str:
    """Nói CHÍNH XÁC thuộc tính nào lệch, để log có ích thay vì chung chung."""
    parts: list[str] = []

    sizes = {(c.width, c.height) for c in clips}
    if len(sizes) > 1:
        parts.append("kích thước khác nhau: " + ", ".join(f"{w}x{h}" for w, h in sorted(sizes)))

    rates = {round(c.fps, 2) for c in clips}
    if len(rates) > 1:
        parts.append("fps khác nhau: " + ", ".join(f"{r:g}" for r in sorted(rates)))

    codecs = {c.video_codec for c in clips}
    if len(codecs) > 1:
        parts.append("codec hình khác nhau: " + ", ".join(sorted(codecs)))

    formats = {c.pix_fmt for c in clips}
    if len(formats) > 1:
        parts.append("định dạng màu khác nhau: " + ", ".join(sorted(formats)))

    with_audio = sum(1 for c in clips if c.has_audio)
    if 0 < with_audio < len(clips):
        parts.append(f"{with_audio}/{len(clips)} clip có âm thanh, số còn lại không")

    audio_rates = {c.sample_rate for c in clips if c.has_audio}
    if len(audio_rates) > 1:
        parts.append("tần số lấy mẫu khác nhau: " + ", ".join(str(r) for r in sorted(audio_rates)))

    return "; ".join(parts) or "thuộc tính luồng không khớp"


def _why_normalize(clip: ClipInfo, canvas: Canvas, cfg: VideoExportConfig) -> str:
    """Lý do riêng của từng clip -- ghi vào log để bạn biết chi phí từ đâu ra."""
    reasons = []
    if (clip.width, clip.height) != (canvas.width, canvas.height):
        reasons.append(f"{clip.width}x{clip.height} -> {canvas.width}x{canvas.height}")
    if abs(clip.fps - canvas.fps) > 0.02:
        reasons.append(f"{clip.fps:g}fps -> {canvas.fps:g}fps")
    if cfg.encode.audio == "keep" and not clip.has_audio:
        reasons.append("chèn khoảng lặng")
    return ", ".join(reasons) or "đồng bộ luồng"


# ==========================================================================
# Dựng lệnh
# ==========================================================================
def _normalize_args(
    clip: ClipInfo, canvas: Canvas, cfg: VideoExportConfig, dest: Path
) -> list[str]:
    """Lệnh ffmpeg đưa MỘT clip về đúng khung hình đích."""
    args = ["-y", "-i", str(clip.path)]

    keep_audio = cfg.encode.audio == "keep"
    needs_silence = keep_audio and not clip.has_audio

    if needs_silence:
        # Bộ ghép của ffmpeg đòi mọi đoạn phải có cùng bộ luồng. Clip câm được
        # cấp một dải im lặng dài đúng bằng nó, để bước nối byte sau không vỡ.
        args += [
            "-f", "lavfi",
            "-t", f"{max(clip.duration_s, 0.1):.3f}",
            "-i",
            f"anullsrc=channel_layout={_channel_layout(cfg.encode.audio_channels)}"
            f":sample_rate={cfg.encode.audio_sample_rate}",
        ]

    args += ["-vf", build_video_filter(clip, canvas, cfg)]

    if needs_silence:
        args += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    elif keep_audio:
        args += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        args += ["-map", "0:v:0", "-an"]

    args += [
        "-c:v", cfg.encode.video_codec,
        "-preset", cfg.encode.preset,
        "-crf", str(cfg.encode.crf),
        "-pix_fmt", cfg.encode.pix_fmt,
    ]

    if keep_audio:
        args += [
            "-c:a", cfg.encode.audio_codec,
            "-b:a", cfg.encode.audio_bitrate,
            "-ar", str(cfg.encode.audio_sample_rate),
            "-ac", str(cfg.encode.audio_channels),
        ]

    # Đưa chỉ mục moov lên đầu file -> phát được ngay khi vừa tải, không phải
    # chờ tải xong. Gần như miễn phí, nên luôn bật.
    args += ["-movflags", "+faststart", str(dest)]
    return args


def build_video_filter(clip: ClipInfo, canvas: Canvas, cfg: VideoExportConfig) -> str:
    """Chuỗi bộ lọc hình đưa clip về đúng khung đích.

    letterbox: thu nhỏ cho VỪA trong khung (`decrease`) rồi thêm viền cho đủ
               kích thước. Không mất một phần hình nào, đổi lại có viền.
    crop:      phóng cho PHỦ KÍN khung (`increase`) rồi cắt phần thừa. Kín khung,
               đổi lại mất phần rìa.

    `setsar=1` chỉnh tỉ lệ điểm ảnh về vuông -- thiếu nó thì một số trình phát
    hiển thị video bị kéo giãn dù kích thước đã đúng.
    """
    width, height = canvas.width, canvas.height

    if cfg.video.fit == "crop":
        geometry = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    else:
        geometry = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={cfg.video.pad_color}"
        )

    return f"{geometry},setsar=1,fps={canvas.fps:g}"


def _concat_args(copy_streams: bool) -> list[str]:
    """Tham số cho bước ghép cuối.

    `-safe 0` cho phép đường dẫn tuyệt đối trong danh sách ghép -- bắt buộc, vì
    ta luôn dùng đường dẫn tuyệt đối để không phụ thuộc thư mục hiện hành.
    """
    args = ["-y", "-f", "concat", "-safe", "0", "-i", "{list}"]
    if copy_streams:
        args += ["-c", "copy"]
    args += ["-movflags", "+faststart", "{output}"]
    return args


def _channel_layout(channels: int) -> str:
    return {1: "mono", 2: "stereo", 6: "5.1"}.get(channels, "stereo")
