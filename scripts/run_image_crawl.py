"""Cửa vào dòng lệnh cho module image_crawl.

    python scripts/run_image_crawl.py [tuỳ chọn]

Ví dụ hay dùng:

    # Xem kế hoạch, không mở trình duyệt
    python scripts/run_image_crawl.py --dry-run

    # Lấy top 10 ảnh cho một từ khoá
    python scripts/run_image_crawl.py --query "vietnamese street food"

    # Lấy nhiều hơn, gom từ kho ứng viên lớn hơn
    python scripts/run_image_crawl.py --query "brutalist architecture" --top 20 --pool 300

    # Chạy chậm hơn nữa (nhịp nghỉ gấp đôi)
    python scripts/run_image_crawl.py --slow

    # Tái lập đúng một phiên đã chạy, để gỡ lỗi
    python scripts/run_image_crawl.py --seed 42
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
from modules.image_crawl import ImageCrawlConfig, ImageCrawlModule  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tìm trên Pinterest và tải về những ảnh được yêu thích nhất.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=Path, default=CONFIG_DIR / "image_crawl.yaml",
        help="File cấu hình (mặc định: config/image_crawl.yaml)",
    )
    parser.add_argument("--query", "-q", type=str, default=None, help="Từ khoá tìm kiếm.")
    parser.add_argument("--top", type=int, default=None, help="Lấy bao nhiêu ảnh.")
    parser.add_argument(
        "--pool", type=int, default=None,
        help="Gom bao nhiêu pin trước khi xếp hạng. Lớn hơn = chọn lọc kỹ hơn, lâu hơn.",
    )
    parser.add_argument("--max-scrolls", type=int, default=None, help="Trần số nhịp cuộn.")
    parser.add_argument("--dest", type=str, default=None, help="Thư mục lưu ảnh.")
    parser.add_argument(
        "--slow", action="store_true",
        help="Nhân đôi mọi khoảng nghỉ. Dùng khi bạn thấy Pinterest phản ứng gắt.",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Cố định chuỗi ngẫu nhiên để tái lập một phiên khi gỡ lỗi.",
    )

    display = parser.add_mutually_exclusive_group()
    display.add_argument("--headful", action="store_true", help="Hiện cửa sổ (mặc định).")
    display.add_argument("--headless", action="store_true", help="Ẩn cửa sổ trình duyệt.")

    parser.add_argument(
        "--dry-run", action="store_true", help="Chỉ in kế hoạch. Không mở trình duyệt."
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


def build_overrides(args: argparse.Namespace) -> dict:
    """Chuyển tham số dòng lệnh thành dict ghi đè lên YAML."""
    search: dict = {}
    if args.query:
        search["query"] = args.query
    if args.top:
        search["top_n"] = args.top
    if args.pool:
        search["candidate_pool"] = args.pool
    if args.max_scrolls:
        search["max_scrolls"] = args.max_scrolls

    pacing: dict = {}
    if args.seed is not None:
        pacing["seed"] = args.seed
    if args.slow:
        # Nhân đôi mọi khoảng nghỉ. Không đụng tới độ dài nhịp cuộn -- cuộn ngắn
        # hơn không làm bạn trông giống người hơn, chỉ khiến phiên dài ra vô ích.
        pacing.update(
            min_action_delay_s=3.0, max_action_delay_s=8.0,
            min_scroll_pause_s=1.6, max_scroll_pause_s=5.0,
            min_long_pause_s=8.0, max_long_pause_s=20.0,
            max_session_s=1800.0,
        )

    browser: dict = {}
    if args.headless:
        browser["headless"] = True
    elif args.headful:
        browser["headless"] = False

    download: dict = {}
    if args.dest:
        download["dest"] = args.dest
    if args.slow:
        download.update(min_delay_s=2.5, max_delay_s=6.0)

    overrides: dict = {}
    for key, value in (
        ("search", search), ("pacing", pacing), ("browser", browser), ("download", download)
    ):
        if value:
            overrides[key] = value
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    run_id = new_run_id()
    log_path = setup_logging(args.log_level, run_id=run_id)
    log = logging.getLogger("image_crawl")
    log.info("Lần chạy %s -- nhật ký: %s", run_id, log_path)

    try:
        cfg = load_config(args.config, ImageCrawlConfig, build_overrides(args))
        module = ImageCrawlModule(cfg, query=args.query)
        ctx = ModuleContext(
            run_id=run_id, workdir=OUTPUT_DIR, logger=log, dry_run=args.dry_run
        )
        result = module.execute(ctx)
    except AutomationError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Bị ngắt bởi người dùng. Ảnh đã tải vẫn được giữ.")
        return 130

    _print_summary(result, args.dry_run)
    return 0 if result.ok else 1


def _print_summary(result, dry_run: bool) -> None:
    print()
    print("=" * 70)
    print(f"KẾT QUẢ: {result.status.value.upper()}")
    print("=" * 70)
    for key, value in result.stats.items():
        print(f"  {key:<14} {value}")

    if basis := result.outputs.get("ranking_basis"):
        if basis == "search_order":
            print()
            print("  ⚠ Không lấy được số lượt lưu nào.")
            print("    Danh sách này theo THỨ TỰ TÌM KIẾM của Pinterest,")
            print("    không phải theo số lượt thích. Xem manifest.json.")

    images = result.outputs.get("images", [])
    if images:
        print(f"\n  {len(images)} ảnh:")
        for path in images[:12]:
            print(f"    {path}")
        if len(images) > 12:
            print(f"    ... và {len(images) - 12} ảnh nữa")
    if result.errors:
        print(f"\n  {len(result.errors)} lỗi:")
        for err in result.errors[:5]:
            print(f"    - {err}")
    if dry_run:
        print("\n  Đây là chạy thử. Bỏ --dry-run để chạy thật.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
