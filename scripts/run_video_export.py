"""Cửa vào dòng lệnh cho module video_export.

    python scripts/run_video_export.py [tuỳ chọn]

Script này CỐ Ý mỏng: đọc tham số, nạp cấu hình, bật log, gọi module. Toàn bộ
logic nằm trong `modules/video_export/`, nhờ vậy job runner sau này chạy được
module y hệt mà không cần đi qua dòng lệnh.

Ví dụ hay dùng:

    # Xem trước từng lệnh ffmpeg sẽ chạy, không mã hoá gì
    python scripts/run_video_export.py --dry-run

    # Ghép mọi clip đã sinh thành một video ngang 1080p
    python scripts/run_video_export.py

    # Xuất bản dọc cho TikTok/Shorts, cắt cho kín khung thay vì thêm viền
    python scripts/run_video_export.py --aspect 9:16 --fit crop

    # Chỉ ghép vài clip, theo đúng thứ tự bạn muốn
    python scripts/run_video_export.py --clips bien-hoang-hon,san-pham-xoay

    # Chỉ định file đích
    python scripts/run_video_export.py --output output/export/gioi-thieu.mp4
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config  # noqa: E402
from core.errors import AutomationError  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.module import ModuleContext  # noqa: E402
from core.paths import CONFIG_DIR, OUTPUT_DIR, new_run_id  # noqa: E402
from modules.video_export import VideoExportConfig, VideoExportModule  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ghép các clip đã sinh thành một file video hoàn chỉnh.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=Path, default=CONFIG_DIR / "video_export.yaml",
        help="File cấu hình (mặc định: config/video_export.yaml)",
    )
    parser.add_argument(
        "--scan", type=str, nargs="+", default=None, metavar="GLOB",
        help="Ghi đè mẫu quét clip. VD: --scan 'output/video_gen/*/*.mp4'",
    )
    parser.add_argument(
        "--clips", type=str, default=None,
        help="Chỉ ghép các clip này, ĐÚNG THỨ TỰ liệt kê. VD: --clips a,b,c",
    )
    parser.add_argument("--output", "-o", type=str, default=None, help="Đường dẫn file đích.")
    parser.add_argument(
        "--aspect", choices=["16:9", "9:16", "1:1", "4:5", "4:3"], default=None,
        help="Tỉ lệ khung hình đích.",
    )
    parser.add_argument(
        "--resolution", choices=["480p", "720p", "1080p", "1440p", "2160p"], default=None,
        help="Độ phân giải đích (con số là cạnh ngắn).",
    )
    parser.add_argument(
        "--fit", choices=["letterbox", "crop"], default=None,
        help="Clip lệch tỉ lệ: thêm viền (letterbox) hay cắt rìa (crop).",
    )
    parser.add_argument(
        "--reencode", choices=["auto", "always", "never"], default=None,
        help="Chế độ mã hoá lại.",
    )
    parser.add_argument("--no-audio", action="store_true", help="Bỏ hẳn âm thanh.")
    parser.add_argument(
        "--overwrite", action="store_true", help="Ghi đè nếu file đích đã tồn tại."
    )
    parser.add_argument("--keep-temp", action="store_true", help="Giữ lại file tạm để soi.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Chỉ in ra các lệnh ffmpeg sẽ chạy. Không mã hoá gì.",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


def build_overrides(args: argparse.Namespace) -> dict:
    """Chuyển tham số dòng lệnh thành dict ghi đè lên YAML.

    Chỉ đưa vào khoá người dùng thực sự chỉ định -- `deep_merge` bỏ qua None,
    nên cấu hình trong file không bị ghi đè oan.
    """
    sources: dict = {}
    if args.scan:
        sources["scan"] = args.scan
        # Quét tay thì bỏ qua danh sách của module trước, nếu không tham số này
        # sẽ bị lờ đi một cách khó hiểu khi chạy trong job.
        sources["from_shared"] = False
    if args.clips:
        sources["order"] = "explicit"
        sources["order_explicit"] = [s.strip() for s in args.clips.split(",") if s.strip()]

    video: dict = {}
    if args.aspect:
        video["target_aspect_ratio"] = args.aspect
    if args.resolution:
        video["target_resolution"] = args.resolution
    if args.fit:
        video["fit"] = args.fit

    encode: dict = {}
    if args.reencode:
        encode["mode"] = args.reencode
    if args.no_audio:
        encode["audio"] = "strip"

    output: dict = {}
    if args.overwrite:
        output["on_exists"] = "overwrite"
    if args.keep_temp:
        output["keep_temp"] = True

    overrides: dict = {}
    for key, value in (
        ("sources", sources), ("video", video), ("encode", encode), ("output", output)
    ):
        if value:
            overrides[key] = value
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    run_id = new_run_id()
    log_path = setup_logging(args.log_level, run_id=run_id)
    log = logging.getLogger("video_export")
    log.info("Lần chạy %s -- nhật ký: %s", run_id, log_path)

    try:
        cfg = load_config(args.config, VideoExportConfig, build_overrides(args))
        module = VideoExportModule(cfg, output_override=args.output)
        ctx = ModuleContext(
            run_id=run_id, workdir=OUTPUT_DIR, logger=log, dry_run=args.dry_run
        )
        result = module.execute(ctx)
    except AutomationError as exc:
        # Lỗi "biết trước": thiếu ffmpeg, không tìm thấy clip, cấu hình sai.
        # In gọn gàng, không đổ traceback ra làm rối mắt.
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Bị ngắt bởi người dùng.")
        return 130

    _print_summary(result, args.dry_run)
    return 0 if result.ok else 1


def _print_summary(result, dry_run: bool) -> None:
    print()
    print("=" * 70)
    print(f"KẾT QUẢ: {result.status.value.upper()}")
    print("=" * 70)
    for key, value in result.stats.items():
        print(f"  {key:<16} {value}")
    if final := result.outputs.get("final_video"):
        print(f"\n  Video: {final}")
    if dry_run:
        print("\n  Đây là chạy thử. Bỏ --dry-run để ghép thật.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
