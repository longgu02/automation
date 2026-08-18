"""Kiểm thử phần quyết định của video_export.

`plan.py` là hàm thuần tuý -- vào ClipInfo, ra lệnh ffmpeg. Nên toàn bộ phần khó
nhất của module (chọn nối byte hay mã hoá lại, dựng chuỗi bộ lọc, tính khung
hình) test được mà máy chạy test không cần có ffmpeg.

    python -m pytest tests/test_export_plan.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.errors import ConfigError, FatalError  # noqa: E402
from modules.video_export.config import VideoExportConfig  # noqa: E402
from modules.video_export.models import ClipInfo  # noqa: E402
from modules.video_export.plan import build_plan, build_video_filter  # noqa: E402


def _clip(
    name: str = "a",
    width: int = 1920,
    height: int = 1080,
    fps: float = 24.0,
    duration: float = 8.0,
    has_audio: bool = True,
    codec: str = "h264",
    pix_fmt: str = "yuv420p",
) -> ClipInfo:
    return ClipInfo(
        path=Path(f"/clips/{name}.mp4"),
        width=width,
        height=height,
        fps=fps,
        duration_s=duration,
        video_codec=codec,
        pix_fmt=pix_fmt,
        has_audio=has_audio,
        audio_codec="aac" if has_audio else None,
        sample_rate=48000 if has_audio else None,
        channels=2 if has_audio else None,
    )


def _cfg(**overrides) -> VideoExportConfig:
    cfg = VideoExportConfig()
    for dotted, value in overrides.items():
        section, _, field = dotted.partition(".")
        setattr(getattr(cfg, section), field, value)
    return cfg


def _plan(clips, cfg=None):
    return build_plan(clips, cfg or _cfg(), Path("/out/final.mp4"), Path("/tmp/x"))


# ===========================================================================
# Tính khung hình đích
# ===========================================================================
@pytest.mark.parametrize(
    "aspect,resolution,expected",
    [
        ("16:9", "1080p", (1920, 1080)),
        ("16:9", "720p", (1280, 720)),
        ("9:16", "1080p", (1080, 1920)),
        ("1:1", "1080p", (1080, 1080)),
        ("4:5", "1080p", (1080, 1350)),
        ("4:3", "720p", (960, 720)),
    ],
)
def test_con_so_trong_1080p_la_canh_ngan(aspect, resolution, expected) -> None:
    """Một quy tắc phủ cả ba hướng khung hình: số trong '1080p' là cạnh ngắn."""
    cfg = _cfg(**{"video.target_aspect_ratio": aspect, "video.target_resolution": resolution})
    canvas = cfg.video.canvas(fallback_fps=24)
    assert (canvas.width, canvas.height) == expected


def test_kich_thuoc_luon_chan() -> None:
    """yuv420p đòi cả hai chiều phải chẵn, số lẻ làm ffmpeg báo lỗi."""
    canvas = _cfg(**{"video.target_size": "1921x1081"}).video.canvas(fallback_fps=24)
    assert canvas.width % 2 == 0 and canvas.height % 2 == 0


def test_target_size_de_be_thang_ti_le() -> None:
    cfg = _cfg(**{"video.target_aspect_ratio": "16:9", "video.target_size": "800x800"})
    canvas = cfg.video.canvas(fallback_fps=24)
    assert (canvas.width, canvas.height) == (800, 800)


def test_target_size_sai_dinh_dang_bi_bao_loi() -> None:
    with pytest.raises(ConfigError, match="1920x1080"):
        _cfg(**{"video.target_size": "to bằng cái nhà"}).video.canvas(fallback_fps=24)


def test_fps_mac_dinh_lay_tu_clip_dau_tien() -> None:
    plan = _plan([_clip("a", fps=30.0), _clip("b", fps=30.0)])
    assert plan.canvas.fps == 30.0


def test_fps_bang_khong_thi_roi_ve_24() -> None:
    """ffprobe đọc không ra fps thì thà dùng 24 còn hơn đưa 0 cho ffmpeg."""
    plan = _plan([_clip("a", fps=0.0)])
    assert plan.canvas.fps == 24.0


# ===========================================================================
# Chọn con đường: nối byte hay mã hoá lại
# ===========================================================================
def test_clip_dong_nhat_dung_khung_thi_noi_byte() -> None:
    """Đường nhanh: không giải mã, không mất chất lượng."""
    plan = _plan([_clip("a"), _clip("b"), _clip("c")])

    assert plan.needs_reencode is False
    assert plan.normalize == []
    assert "-c" in plan.concat_args and "copy" in plan.concat_args
    assert len(plan.concat_inputs) == 3
    assert plan.total_duration_s == pytest.approx(24.0)


def test_ti_le_lech_thi_chuan_hoa() -> None:
    """Chính tình huống của bộ prompt demo: có clip 16:9 và có clip 1:1."""
    plan = _plan([_clip("ngang", 1920, 1080), _clip("vuong", 1080, 1080)])

    assert plan.needs_reencode is True
    assert len(plan.normalize) == 2
    assert "kích thước khác nhau" in plan.reason
    # Đầu vào bước ghép là file tạm, không phải clip gốc.
    assert all("/clips/" not in str(p) for p in plan.concat_inputs)


def test_khung_dich_khac_kich_thuoc_clip_thi_chuan_hoa() -> None:
    """Clip đồng nhất với nhau nhưng không đúng khung đích -> vẫn phải mã hoá lại."""
    cfg = _cfg(**{"video.target_resolution": "720p"})
    plan = _plan([_clip("a", 1920, 1080), _clip("b", 1920, 1080)], cfg)

    assert plan.needs_reencode is True
    assert "1920x1080" in plan.reason and "1280x720" in plan.reason


def test_fps_lech_thi_chuan_hoa() -> None:
    plan = _plan([_clip("a", fps=24.0), _clip("b", fps=30.0)])
    assert plan.needs_reencode is True
    assert "fps khác nhau" in plan.reason


def test_mot_clip_khong_co_tieng_thi_chuan_hoa() -> None:
    """Bộ ghép của ffmpeg đòi mọi đoạn cùng bộ luồng -- có/không tiếng là lệch."""
    plan = _plan([_clip("a", has_audio=True), _clip("b", has_audio=False)])

    assert plan.needs_reencode is True
    assert "có âm thanh" in plan.reason


def test_mode_always_thi_luon_ma_hoa_lai() -> None:
    plan = _plan([_clip("a"), _clip("b")], _cfg(**{"encode.mode": "always"}))
    assert plan.needs_reencode is True
    assert len(plan.normalize) == 2


def test_mode_never_gap_clip_lech_thi_bao_loi_ro_rang() -> None:
    """Người dùng đã cấm mã hoá lại -> phải nói vì sao không nối byte được,
    thay vì âm thầm làm đúng điều họ vừa cấm."""
    cfg = _cfg(**{"encode.mode": "never"})
    with pytest.raises(FatalError, match="kích thước khác nhau"):
        _plan([_clip("a", 1920, 1080), _clip("b", 1080, 1080)], cfg)


def test_mode_never_voi_clip_dong_nhat_thi_chay_binh_thuong() -> None:
    plan = _plan([_clip("a"), _clip("b")], _cfg(**{"encode.mode": "never"}))
    assert plan.needs_reencode is False


def test_khong_co_clip_thi_bao_loi() -> None:
    with pytest.raises(FatalError, match="Không có clip"):
        _plan([])


def test_bo_tieng_thi_khong_the_noi_byte_clip_co_tieng() -> None:
    """Yêu cầu bỏ tiếng mà clip đang có tiếng -> buộc phải xử lý lại."""
    plan = _plan([_clip("a", has_audio=True)], _cfg(**{"encode.audio": "strip"}))
    assert plan.needs_reencode is True


# ===========================================================================
# Chuỗi bộ lọc hình
# ===========================================================================
def test_letterbox_them_vien_khong_cat_hinh() -> None:
    cfg = _cfg(**{"video.fit": "letterbox"})
    canvas = cfg.video.canvas(fallback_fps=24)
    filter_text = build_video_filter(_clip("a", 1080, 1080), canvas, cfg)

    assert "force_original_aspect_ratio=decrease" in filter_text  # thu cho VỪA khung
    assert "pad=1920:1080" in filter_text
    assert "crop" not in filter_text


def test_crop_phu_kin_khung_va_cat_ria() -> None:
    cfg = _cfg(**{"video.fit": "crop"})
    canvas = cfg.video.canvas(fallback_fps=24)
    filter_text = build_video_filter(_clip("a", 1080, 1080), canvas, cfg)

    assert "force_original_aspect_ratio=increase" in filter_text  # phóng cho PHỦ KÍN
    assert "crop=1920:1080" in filter_text
    assert "pad" not in filter_text


def test_luon_dat_lai_ti_le_diem_anh() -> None:
    """Thiếu setsar=1 thì một số trình phát kéo giãn hình dù kích thước đã đúng."""
    cfg = _cfg()
    filter_text = build_video_filter(_clip("a"), cfg.video.canvas(fallback_fps=24), cfg)
    assert "setsar=1" in filter_text


def test_mau_vien_tuy_chinh_duoc() -> None:
    cfg = _cfg(**{"video.pad_color": "#101010"})
    filter_text = build_video_filter(_clip("a", 1080, 1080), cfg.video.canvas(24), cfg)
    assert "color=#101010" in filter_text


# ===========================================================================
# Lệnh chuẩn hoá
# ===========================================================================
def test_clip_cam_duoc_chen_khoang_lang() -> None:
    """Không có bước này, bước nối byte sau sẽ vỡ vì lệch bộ luồng."""
    plan = _plan([_clip("a", has_audio=True), _clip("cam", has_audio=False)])
    silent_job = next(j for j in plan.normalize if "cam" in j.source.name)
    args = " ".join(silent_job.args)

    assert "anullsrc" in args
    assert "-map 0:v:0 -map 1:a:0" in args
    assert "chèn khoảng lặng" in silent_job.reason


def test_clip_co_tieng_thi_khong_chen_gi() -> None:
    plan = _plan([_clip("a", 1080, 1080), _clip("b", 1920, 1080)])
    job = next(j for j in plan.normalize if "a" == j.source.stem)
    args = " ".join(job.args)

    assert "anullsrc" not in args
    assert "-map 0:v:0 -map 0:a:0" in args


def test_bo_tieng_thi_dung_an() -> None:
    cfg = _cfg(**{"encode.audio": "strip"})
    plan = _plan([_clip("a", 1080, 1080), _clip("b", 1920, 1080)], cfg)
    args = " ".join(plan.normalize[0].args)

    assert "-an" in args
    assert "-c:a" not in args


def test_tham_so_ma_hoa_lay_tu_config() -> None:
    cfg = _cfg(**{"encode.crf": 18, "encode.preset": "slow", "encode.video_codec": "libx265"})
    plan = _plan([_clip("a", 1080, 1080), _clip("b", 1920, 1080)], cfg)
    args = plan.normalize[0].args

    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-preset") + 1] == "slow"
    assert args[args.index("-c:v") + 1] == "libx265"


def test_luon_bat_faststart() -> None:
    """Đưa chỉ mục lên đầu file -> phát được ngay khi vừa tải. Gần như miễn phí."""
    plan = _plan([_clip("a", 1080, 1080), _clip("b", 1920, 1080)])
    assert "+faststart" in plan.normalize[0].args
    assert "+faststart" in plan.concat_args


def test_ghep_luon_dung_safe_0() -> None:
    """Bắt buộc, vì ta luôn dùng đường dẫn tuyệt đối trong danh sách ghép."""
    plan = _plan([_clip("a")])
    assert "-safe" in plan.concat_args
    assert plan.concat_args[plan.concat_args.index("-safe") + 1] == "0"


def test_file_tam_giu_dung_thu_tu_ghep() -> None:
    """Tên file tạm có tiền tố số -> thứ tự cảnh không bị lẫn."""
    plan = _plan([_clip("z_dau", 1080, 1080), _clip("a_sau", 1920, 1080)])
    names = [job.dest.name for job in plan.normalize]

    assert names[0].startswith("001_") and "z_dau" in names[0]
    assert names[1].startswith("002_") and "a_sau" in names[1]
