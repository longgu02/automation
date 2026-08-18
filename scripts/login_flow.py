"""Đăng nhập Google Flow MỘT LẦN cho mỗi tài khoản.

Mở Chrome với đúng profile của tài khoản đó, rồi đứng chờ bạn đăng nhập thủ
công. Khi bạn nhấn Enter, phiên đăng nhập đã nằm sẵn trong profile.

    python scripts/login_flow.py                    # tài khoản đầu tiên
    python scripts/login_flow.py --account acc2     # một tài khoản cụ thể
    python scripts/login_flow.py --all              # lần lượt từng tài khoản

VÌ SAO ĐĂNG NHẬP BẰNG TAY: tự động điền mật khẩu Google là con đường ngắn nhất
tới rắc rối -- 2FA, CAPTCHA, cảnh báo bảo mật, và nguy cơ khoá tài khoản. Đăng
nhập tay 30 giây một lần rồi dùng lại phiên đó hàng tháng vừa an toàn hơn vừa
ít vỡ hơn. Script này KHÔNG đọc, không lưu, không đụng tới mật khẩu của bạn.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Cho phép chạy trực tiếp `python scripts/login_flow.py` từ bất kỳ đâu.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.accounts import Account, load_accounts  # noqa: E402
from core.config import load_config  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.paths import (  # noqa: E402
    BROWSER_PROFILE_DIR,
    BROWSER_PROFILE_ROOT,
    CONFIG_DIR,
    PROJECT_ROOT,
    ensure_dir,
)
from modules.video_gen.config import VideoGenConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Đăng nhập Google Flow cho một hoặc nhiều tài khoản.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--account", help="Id tài khoản trong config/accounts.yaml.")
    group.add_argument("--all", action="store_true", help="Đăng nhập lần lượt mọi tài khoản đang bật.")
    parser.add_argument("--list", action="store_true", help="Chỉ liệt kê tài khoản rồi thoát.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging("INFO")

    cfg = load_config(CONFIG_DIR / "video_gen.yaml", VideoGenConfig)
    accounts_file = PROJECT_ROOT / cfg.execution.accounts_file
    accounts = load_accounts(
        accounts_file,
        fallback_profile_dir=BROWSER_PROFILE_DIR,
        fallback_workspace_url=cfg.browser.workspace_url,
    )
    enabled = [a for a in accounts if a.enabled]

    if args.list:
        print(f"Tài khoản khai trong {accounts_file}:\n")
        for account in accounts:
            folder = account.resolved_profile_dir(BROWSER_PROFILE_ROOT)
            mark = "bật " if account.enabled else "tắt "
            state = "đã đăng nhập" if _looks_logged_in(folder) else "CHƯA đăng nhập"
            print(f"  [{mark}] {account.id:<12} {state:<16} {account.label}")
            print(f"           profile: {folder}")
        return 0

    if args.all:
        targets = enabled
    elif args.account:
        targets = [a for a in accounts if a.id == args.account]
        if not targets:
            print(f"Không có tài khoản id '{args.account}' trong {accounts_file}.")
            print(f"Các id hiện có: {', '.join(a.id for a in accounts)}")
            return 2
    else:
        targets = enabled[:1]

    if not targets:
        print("Không có tài khoản nào đang bật trong config/accounts.yaml.")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Chưa cài Playwright. Chạy:  pip install playwright")
        return 1

    for position, account in enumerate(targets, start=1):
        _login_one(account, cfg, sync_playwright, position, len(targets))

    print()
    print("Xong. Kiểm tra lại bằng:  python scripts/login_flow.py --list")
    print("Rồi xem thử không tốn credit:  python scripts/run_video_gen.py --dry-run")
    return 0


def _login_one(account: Account, cfg: VideoGenConfig, sync_playwright, position: int, total: int) -> None:
    profile_dir = account.resolved_profile_dir(BROWSER_PROFILE_ROOT)
    url = account.workspace_url or cfg.browser.workspace_url

    print()
    print("=" * 72)
    print(f"ĐĂNG NHẬP [{position}/{total}] -- tài khoản: {account.describe()}")
    print("=" * 72)
    print(f"Profile : {profile_dir}")
    print(f"Địa chỉ : {url}")
    print()
    print("Các bước:")
    print("  1. Cửa sổ Chrome sắp mở ra.")
    print(f"  2. Đăng nhập bằng tài khoản Google AI Pro dành cho '{account.id}'.")
    print("     QUAN TRỌNG: mỗi tài khoản một profile riêng, nên đừng đăng nhập")
    print("     nhầm cùng một tài khoản vào hai profile khác nhau.")
    print("  3. Chờ tới khi thấy ô nhập prompt của Flow.")
    print("  4. Nếu dùng project cố định: mở project, chỉnh model và tỉ lệ khung")
    print(f"     hình, rồi chép URL vào accounts.yaml -> {account.id}.workspace_url")
    print("  5. Quay lại đây và nhấn Enter.")
    print()

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(ensure_dir(profile_dir)),
            channel=cfg.browser.browser_channel,
            headless=False,  # luôn hiện cửa sổ: cả điểm mấu chốt của script này
            accept_downloads=True,
            locale=cfg.browser.locale,
            viewport={"width": cfg.browser.viewport_width, "height": cfg.browser.viewport_height},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        input(f">>> [{account.id}] Đăng nhập xong thì nhấn Enter để lưu phiên... ")

        final_url = page.url
        context.close()

    print(f"Đã lưu phiên cho '{account.id}'. URL cuối: {final_url}")


def _looks_logged_in(profile_dir: Path) -> bool:
    """Đoán nhanh xem profile đã từng đăng nhập chưa.

    Chỉ kiểm tra sự tồn tại của kho cookie -- đủ để phân biệt 'chưa làm gì' với
    'đã đăng nhập'. Không khẳng định phiên còn hạn; chuyện đó chỉ biết khi mở
    trình duyệt thật.

    Chrome đổi chỗ file này qua các phiên bản, nên dò vài vị trí đã biết.
    """
    candidates = (
        profile_dir / "Default" / "Network" / "Cookies",  # Chrome hiện nay
        profile_dir / "Default" / "Cookies",  # bản cũ hơn
        profile_dir / "Cookies",
    )
    return any(path.exists() for path in candidates)


if __name__ == "__main__":
    raise SystemExit(main())
