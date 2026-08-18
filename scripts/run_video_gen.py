"""Cửa vào dòng lệnh cho module video_gen.

    python scripts/run_video_gen.py [tuỳ chọn]

Script này CỐ Ý mỏng. Nó chỉ làm bốn việc: đọc tham số, nạp cấu hình, bật log,
gọi module. Toàn bộ logic nằm trong `modules/video_gen/`, nhờ vậy job runner sau
này chạy được module y hệt mà không cần đi qua dòng lệnh.

Ví dụ hay dùng:

    # Xem sẽ chạy gì, không tốn credit nào
    python scripts/run_video_gen.py --dry-run

    # Thử đúng một prompt, mở cửa sổ để nhìn tận mắt
    python scripts/run_video_gen.py --only bien-hoang-hon --headful

    # Chạy cả bộ, giới hạn 5 prompt đầu
    python scripts/run_video_gen.py --limit 5

    # Sinh lại kể cả những prompt đã xong
    python scripts/run_video_gen.py --only pho-dem-mua --force

    # Chạy song song 3 tài khoản
    python scripts/run_video_gen.py --parallel 3

    # Chỉ dùng vài tài khoản nhất định (để dành tài khoản khác cho việc khác)
    python scripts/run_video_gen.py --accounts acc1,acc2
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
from core.module import ModuleContext, ModuleStatus  # noqa: E402
from core.paths import CONFIG_DIR, OUTPUT_DIR, new_run_id  # noqa: E402
from modules.video_gen import VideoGenConfig, VideoGenModule  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sinh video từ prompt bằng Google Flow hoặc Gemini API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=Path, default=CONFIG_DIR / "video_gen.yaml",
        help="File cấu hình (mặc định: config/video_gen.yaml)",
    )
    parser.add_argument(
        "--prompts", type=Path, nargs="+", default=None,
        help="Ghi đè danh sách file prompt trong cấu hình.",
    )
    parser.add_argument(
        "--backend", choices=["flow_browser", "gemini_api"], default=None,
        help="Ghi đè backend trong cấu hình.",
    )
    parser.add_argument(
        "--only", type=str, default=None,
        help="Chỉ chạy các id này, cách nhau bằng dấu phẩy. VD: --only a,b",
    )
    parser.add_argument("--limit", type=int, default=None, help="Chỉ chạy N prompt đầu tiên.")
    parser.add_argument(
        "--accounts", type=str, default=None,
        help="Chỉ dùng các tài khoản này, cách nhau bằng dấu phẩy. VD: --accounts acc1,acc2",
    )
    parser.add_argument(
        "--parallel", type=int, default=None, metavar="N",
        help="Số tài khoản chạy đồng thời (ghi đè execution.max_parallel).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bỏ qua sổ trạng thái, sinh lại kể cả prompt đã xong.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Chỉ in ra những gì sẽ chạy. Không mở trình duyệt, không tốn credit.",
    )

    display = parser.add_mutually_exclusive_group()
    display.add_argument("--headful", action="store_true", help="Hiện cửa sổ trình duyệt.")
    display.add_argument("--headless", action="store_true", help="Ẩn cửa sổ trình duyệt.")

    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def build_overrides(args: argparse.Namespace) -> dict:
    """Chuyển tham số dòng lệnh thành dict ghi đè lên YAML.

    Chỉ đưa vào những khoá người dùng thực sự chỉ định -- giá trị None sẽ bị
    `deep_merge` bỏ qua, nên cấu hình trong file không bị ghi đè oan.
    """
    overrides: dict = {}
    if args.backend:
        overrides["backend"] = args.backend
    if args.prompts:
        overrides["prompt_files"] = [str(p) for p in args.prompts]
    if args.headful:
        overrides["browser"] = {"headless": False}
    elif args.headless:
        overrides["browser"] = {"headless": True}
    if args.parallel:
        overrides["execution"] = {"max_parallel": args.parallel}
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Nạp .env trước khi đọc cấu hình, để ${GEMINI_API_KEY} có giá trị.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # python-dotenv là tuỳ chọn; biến môi trường hệ thống vẫn dùng được

    run_id = new_run_id()
    log_path = setup_logging(args.log_level, run_id=run_id)
    log = logging.getLogger("video_gen")
    log.info("Lần chạy %s -- nhật ký: %s", run_id, log_path)

    try:
        cfg = load_config(args.config, VideoGenConfig, build_overrides(args))
        module = VideoGenModule(
            cfg,
            only=[s.strip() for s in args.only.split(",")] if args.only else None,
            limit=args.limit,
            force=args.force,
            accounts=[s.strip() for s in args.accounts.split(",")] if args.accounts else None,
        )
        ctx = ModuleContext(
            run_id=run_id,
            workdir=OUTPUT_DIR,
            logger=log,
            dry_run=args.dry_run,
        )
        result = module.execute(ctx)
    except AutomationError as exc:
        # Lỗi "biết trước": cấu hình sai, chưa đăng nhập, hết quota. In gọn gàng,
        # không đổ traceback ra làm rối mắt.
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Bị ngắt bởi người dùng. Những prompt đã xong vẫn được giữ nguyên.")
        return 130

    _print_summary(result)
    return 0 if result.ok else 1


def _print_summary(result) -> None:
    print()
    print("=" * 70)
    print(f"KẾT QUẢ: {result.status.value.upper()}")
    print("=" * 70)
    for key, value in result.stats.items():
        print(f"  {key:<16} {value}")

    accounts = result.outputs.get("accounts") or {}
    if accounts:
        print("\n  Tài khoản:")
        for account_id, info in accounts.items():
            line = f"    {account_id:<12} {info['health']:<10} {info['completed']} prompt"
            if info.get("label"):
                line += f"  ({info['label']})"
            print(line)
            # Chỉ nêu lý do khi tài khoản bị loại -- đó là thứ cần hành động.
            if info.get("reason"):
                print(f"                 └─ {str(info['reason']).splitlines()[0][:100]}")

    if result.errors:
        print(f"\n  {len(result.errors)} prompt thất bại:")
        for err in result.errors:
            print(f"    - {err}")
    videos = result.outputs.get("videos", [])
    if videos:
        print(f"\n  {len(videos)} video:")
        for path in videos[:10]:
            print(f"    {path}")
        if len(videos) > 10:
            print(f"    ... và {len(videos) - 10} file nữa")
    if "would_generate" in result.stats:
        print("\n  Đây là chạy thử. Bỏ --dry-run để sinh video thật.")
    elif result.status is ModuleStatus.SKIPPED and not videos:
        print("\n  Không có gì để làm.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
