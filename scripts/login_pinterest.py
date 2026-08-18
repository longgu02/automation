"""Đăng nhập Pinterest MỘT LẦN cho module image_crawl.

    python scripts/login_pinterest.py

Mở Chrome với đúng profile mà module sẽ dùng, rồi đứng chờ bạn đăng nhập thủ
công. Nhấn Enter là phiên được giữ lại trong profile.

VÌ SAO NÊN ĐĂNG NHẬP: Pinterest chặn khách vãng lai sau vài nhịp cuộn (hiện
tường mời đăng ký). Chưa đăng nhập thì module thường chỉ gom được vài chục pin
rồi phải dừng -- không đủ để "top 10" có ý nghĩa.

VÌ SAO ĐĂNG NHẬP BẰNG TAY: giống hệt lý do bên Google Flow -- tự động điền mật
khẩu là đường ngắn nhất tới CAPTCHA, xác minh hai bước và khoá tài khoản. Script
này KHÔNG đọc, không lưu, không đụng tới mật khẩu của bạn.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.paths import CONFIG_DIR, PROJECT_ROOT, ensure_dir  # noqa: E402
from modules.image_crawl.config import ImageCrawlConfig  # noqa: E402


def main() -> int:
    setup_logging("INFO")
    cfg = load_config(CONFIG_DIR / "image_crawl.yaml", ImageCrawlConfig)

    profile = cfg.browser.profile_dir
    profile = profile if profile.is_absolute() else PROJECT_ROOT / profile

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Chưa cài Playwright. Chạy:  pip install playwright")
        return 1

    print("=" * 70)
    print("ĐĂNG NHẬP PINTEREST")
    print("=" * 70)
    print(f"Profile : {profile}")
    print()
    print("Các bước:")
    print("  1. Cửa sổ Chrome sắp mở ra.")
    print("  2. Đăng nhập tài khoản Pinterest của bạn.")
    print("  3. Chờ tới khi thấy trang chủ đã đăng nhập.")
    print("  4. Quay lại đây và nhấn Enter.")
    print()
    print("Profile này TÁCH RIÊNG khỏi profile Google Flow, nên hai thứ không")
    print("ảnh hưởng lẫn nhau.")
    print()

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(ensure_dir(profile)),
            channel=cfg.browser.browser_channel,
            headless=False,  # luôn hiện cửa sổ: cả điểm mấu chốt của script này
            locale=cfg.browser.locale,
            viewport={
                "width": cfg.browser.viewport_width,
                "height": cfg.browser.viewport_height,
            },
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.pinterest.com/login/", wait_until="domcontentloaded")

        input(">>> Đăng nhập xong thì nhấn Enter tại đây để lưu phiên... ")

        final_url = page.url
        context.close()

    print()
    print(f"Đã lưu phiên. URL cuối: {final_url}")
    print()
    print("Bước tiếp theo:")
    print('    python scripts/run_image_crawl.py --query "từ khoá của bạn" --dry-run')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
