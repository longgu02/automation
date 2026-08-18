"""Mở giao diện kéo thả để dựng job bằng sơ đồ.

    python scripts/run_ui.py

Trình duyệt sẽ tự mở. Kéo module từ cột trái vào khung, nối chúng lại, đặt tên
job rồi bấm Lưu -- job được ghi ra `config/jobs/<tên>.yaml`.

Chạy job thật thì dùng dòng lệnh:

    python scripts/run_job.py <tên>

VÌ SAO CHẠY THẬT KHÔNG NẰM TRONG GIAO DIỆN: bấm một nút trên trang web mà đốt
credit Gemini hoặc mở phiên cào Pinterest là cái bẫy. Ở dòng lệnh bạn thấy log
trực tiếp và Ctrl+C được bất cứ lúc nào. Giao diện chỉ chạy THỬ -- an toàn tuyệt
đối vì mọi module đều tôn trọng cờ dry_run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging_setup import setup_logging  # noqa: E402
from ui.server import serve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Giao diện sơ đồ module.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Đổi giá trị này là mở máy chủ ra ngoài máy bạn. Đừng làm vậy trừ khi "
             "bạn hiểu rõ: nó đọc ghi file trong project và chạy được module.",
    )
    parser.add_argument("--no-open", action="store_true", help="Không tự mở trình duyệt.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()

    setup_logging(args.log_level)
    serve(host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
