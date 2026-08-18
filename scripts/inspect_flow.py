"""Công cụ dò selector -- dùng khi Google đổi giao diện Flow.

Mở Flow bằng profile đã đăng nhập, rồi làm hai việc:

  1. Kiểm tra từng khoá trong `config/flow_selectors.yaml` xem còn khớp không,
     in ra bảng ĐƯỢC / HỎNG.
  2. Bật chế độ soi: bạn bấm chuột vào phần tử nào trên trang, nó in ra ngay
     một selector dùng được cho phần tử đó, sẵn sàng chép vào file YAML.

    python scripts/inspect_flow.py                 # tài khoản đầu tiên
    python scripts/inspect_flow.py --account acc2  # một tài khoản cụ thể

Đây chính là quy trình bảo trì của module: chạy script này, chép selector mới
vào `config/flow_selectors.yaml`, xong. Không phải đụng một dòng Python nào.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.accounts import load_accounts  # noqa: E402
from core.config import load_config, load_yaml  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.paths import (  # noqa: E402
    BROWSER_PROFILE_DIR,
    BROWSER_PROFILE_ROOT,
    CONFIG_DIR,
    PROJECT_ROOT,
    ensure_dir,
)
from modules.video_gen.config import VideoGenConfig  # noqa: E402

# Script JS gắn vào trang: bắt sự kiện click và dựng selector cho phần tử.
# Ưu tiên đúng thứ tự bền vững như trong flow_selectors.yaml.
_PROBE_SCRIPT = """
() => {
  window.__picked = [];
  const describe = (el) => {
    const out = [];
    const tag = el.tagName.toLowerCase();
    const aria = el.getAttribute('aria-label');
    const testid = el.getAttribute('data-testid');
    const role = el.getAttribute('role');
    const text = (el.innerText || '').trim().split('\\n')[0].slice(0, 40);
    if (aria)   out.push(`${tag}[aria-label="${aria}"i]`);
    if (testid) out.push(`${tag}[data-testid="${testid}"]`);
    if (role && text) out.push(`[role="${role}"]:has-text("${text}")`);
    if (text)   out.push(`${tag}:has-text("${text}")`);
    if (el.id)  out.push(`#${el.id}`);
    if (el.placeholder) out.push(`${tag}[placeholder*="${el.placeholder.slice(0,25)}"i]`);
    const cls = [...el.classList].filter(c => c.length < 25 && !/^[a-z]{1,3}$/.test(c));
    if (cls.length) out.push(`${tag}.${cls[0]}`);
    return { tag, text, selectors: out };
  };
  document.addEventListener('click', (ev) => {
    const info = describe(ev.target);
    window.__picked.push(info);
    console.log('[PICKED]', JSON.stringify(info));
  }, true);
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Dò selector cho giao diện Google Flow.")
    parser.add_argument("--account", help="Id tài khoản dùng để soi (mặc định: cái đầu tiên).")
    args = parser.parse_args()

    setup_logging("INFO")
    cfg = load_config(CONFIG_DIR / "video_gen.yaml", VideoGenConfig)
    selectors_path = PROJECT_ROOT / cfg.browser.selectors_file
    book = load_yaml(selectors_path)

    accounts = load_accounts(
        PROJECT_ROOT / cfg.execution.accounts_file,
        fallback_profile_dir=BROWSER_PROFILE_DIR,
        fallback_workspace_url=cfg.browser.workspace_url,
        only=[args.account] if args.account else None,
    )
    enabled = [a for a in accounts if a.enabled]
    if not enabled:
        print("Không có tài khoản nào đang bật.")
        return 2
    account = enabled[0]
    url = account.workspace_url or cfg.browser.workspace_url
    print(f"Soi bằng tài khoản: {account.describe()}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(ensure_dir(account.resolved_profile_dir(BROWSER_PROFILE_ROOT))),
            channel=cfg.browser.browser_channel,
            headless=False,
            locale=cfg.browser.locale,
            viewport={"width": cfg.browser.viewport_width, "height": cfg.browser.viewport_height},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # ---- 1. Kiểm tra sức khoẻ của bản đồ selector hiện tại ----------
        print()
        print("=" * 78)
        print("KIỂM TRA SELECTOR HIỆN CÓ")
        print("=" * 78)
        for key, value in book.items():
            candidates = [value] if isinstance(value, str) else value
            # Bỏ qua các selector có chỗ trống {value}: chúng cần dữ liệu lúc chạy.
            if any("{" in c for c in candidates):
                print(f"  {'BỎ QUA':<8} {key}  (có tham số {{value}})")
                continue
            matched = None
            for selector in candidates:
                try:
                    if page.locator(selector).count() > 0:
                        matched = selector
                        break
                except Exception:
                    continue
            if matched:
                count = page.locator(matched).count()
                print(f"  {'ĐƯỢC':<8} {key:<28} -> {matched}  ({count} phần tử)")
            else:
                print(f"  {'HỎNG':<8} {key:<28} -> không ứng viên nào khớp")

        # ---- 2. Chế độ soi bằng cách bấm chuột --------------------------
        page.evaluate(_PROBE_SCRIPT)
        print()
        print("=" * 78)
        print("CHẾ ĐỘ SOI PHẦN TỬ")
        print("=" * 78)
        print("Bấm chuột vào các phần tử trên trang (ô nhập prompt, nút gửi,")
        print("thẻ clip, nút tải về...). Xong thì quay lại đây nhấn Enter.")
        print()
        input(">>> Nhấn Enter khi đã bấm xong... ")

        picked = page.evaluate("() => window.__picked || []")
        print()
        print("=" * 78)
        print(f"CÁC PHẦN TỬ BẠN ĐÃ BẤM ({len(picked)})")
        print("=" * 78)
        for i, item in enumerate(picked, start=1):
            label = item.get("text") or "(không có chữ)"
            print(f"\n[{i}] <{item['tag']}>  {label}")
            for selector in item["selectors"]:
                print(f"      - '{selector}'")
        print()
        print(f"Chép selector phù hợp vào: {selectors_path}")
        print("Đặt cái bền nhất lên ĐẦU danh sách của khoá tương ứng.")

        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
