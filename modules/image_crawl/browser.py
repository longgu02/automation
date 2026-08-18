"""Điều khiển trình duyệt để duyệt Pinterest và tải ảnh.

BA NGUỒN DỮ LIỆU, xếp theo độ tin cậy giảm dần. Module thử lần lượt và LUÔN ghi
vào log nó đã dùng nguồn nào -- vì chỉ hai nguồn đầu mới có số lượt lưu:

  1. `__PWS_DATA__` -- Pinterest nhúng sẵn trạng thái ban đầu vào chính trang
     HTML. Đọc được ngay sau khi tải xong, không cần chặn bắt gì. Có số lượt lưu.

  2. Phản hồi JSON trong lúc cuộn -- khi bạn cuộn, trang tự gọi thêm dữ liệu.
     Ta đọc lại chính những phản hồi đó. KHÔNG tạo thêm một lượt truy cập nào
     ngoài những gì việc xem trang vốn đã sinh ra. Có số lượt lưu.

  3. Bóc từ DOM -- phương án cuối. Luôn chạy được, nhưng KHÔNG CÓ số lượt lưu,
     nên kết quả chỉ là "theo thứ tự Pinterest xếp", không phải "nhiều lượt
     thích nhất". Module sẽ nói rõ điều đó thay vì lặng lẽ đưa ra danh sách.

VỀ NHỊP ĐỘ: mọi thao tác đều đi qua `Pacer` (xem `humanize.py`). Không có chỗ
nào trong file này cuộn hay bấm mà không qua nó.
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from core.errors import FatalError, RetryableError
from core.paths import OUTPUT_DIR, PROJECT_ROOT, ensure_dir
from modules.image_crawl.config import ImageCrawlConfig
from modules.image_crawl.extract import extract_pins, merge_pins, upgrade_pinimg_url
from modules.image_crawl.humanize import Pacer
from modules.image_crawl.models import PinCandidate

log = logging.getLogger(__name__)

#: Chỉ đọc phản hồi từ những đường dẫn có khả năng chứa pin. Lọc sớm để không
#: phải tải thân của mọi ảnh và mọi file script về bộ nhớ.
_INTERESTING_URL_PARTS = ("/resource/", "/_ngjs/resource/")

#: Các id thẻ script Pinterest từng dùng để nhúng trạng thái ban đầu.
_STATE_SCRIPT_IDS = ("__PWS_DATA__", "__PWS_INITIAL_PROPS__", "initial-state")

#: JS bóc pin từ DOM. Phương án cuối, không lấy được số lượt lưu.
_DOM_EXTRACT_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/pin/"]').forEach(anchor => {
    const match = anchor.getAttribute('href').match(/\\/pin\\/([^/]+)\\//);
    if (!match) return;
    const id = match[1];
    if (seen.has(id)) return;
    const img = anchor.querySelector('img') || anchor.parentElement?.querySelector('img');
    if (!img || !img.src) return;
    seen.add(id);
    out.push({
      id: id,
      url: img.src,
      alt: img.alt || '',
      width: img.naturalWidth || 0,
      height: img.naturalHeight || 0,
    });
  });
  return out;
}
"""


class PinterestBrowser:
    """Mở Pinterest, gom pin, tải ảnh -- với nhịp của người dùng thật."""

    def __init__(self, cfg: ImageCrawlConfig, pacer: Pacer, logger: logging.Logger | None = None):
        self.cfg = cfg
        self.pacer = pacer
        self.log = logger or log
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        #: Thân các phản hồi JSON bắt được, chờ phân tích sau khi cuộn xong.
        self._captured: list[str] = []

    # ===================================================================
    # Vòng đời
    # ===================================================================
    def __enter__(self) -> PinterestBrowser:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        cfg = self.cfg.browser
        profile = cfg.profile_dir
        profile = profile if profile.is_absolute() else PROJECT_ROOT / profile

        self.log.info("Khởi động trình duyệt (%s, profile %s)", cfg.browser_channel, profile)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(ensure_dir(profile)),
                channel=cfg.browser_channel,
                headless=cfg.headless,
                locale=cfg.locale,
                viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as exc:
            self.close()
            raise FatalError(
                f"Không mở được trình duyệt kênh '{cfg.browser_channel}': {exc}\n"
                "  Cài Google Chrome, hoặc đổi browser.browser_channel: chromium "
                "rồi chạy `playwright install chromium`."
            ) from exc

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(cfg.page_timeout_ms)
        self._page.on("response", self._on_response)

    def close(self) -> None:
        for closer in (
            lambda: self._context.close() if self._context else None,
            lambda: self._playwright.stop() if self._playwright else None,
        ):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Bỏ qua lỗi khi đóng trình duyệt: %s", exc)
        self._context = self._page = self._playwright = None

    # ===================================================================
    # Nguồn 2: bắt phản hồi JSON trong lúc cuộn
    # ===================================================================
    def _on_response(self, response) -> None:
        """Ghi lại thân các phản hồi JSON có thể chứa pin.

        Cố ý làm ÍT NHẤT CÓ THỂ trong hàm này: chỉ lấy chuỗi thô rồi cất đi,
        phân tích để sau. Xử lý nặng trong trình xử lý sự kiện của Playwright
        bản đồng bộ rất dễ sinh lỗi khó lần.
        """
        try:
            url = response.url
            if not any(part in url for part in _INTERESTING_URL_PARTS):
                return
            if "json" not in (response.headers.get("content-type") or ""):
                return
            self._captured.append(response.text())
        except Exception as exc:  # noqa: BLE001
            # Thân phản hồi có thể đã bị trình duyệt giải phóng. Không sao --
            # đây là một trong ba nguồn, mất một phản hồi không hỏng cả run.
            self.log.debug("Không đọc được một phản hồi: %s", exc)

    # ===================================================================
    # Gom pin
    # ===================================================================
    def collect(self, query: str) -> tuple[list[PinCandidate], int]:
        """Duyệt Pinterest và gom pin. Trả về (danh sách pin, số nhịp đã cuộn)."""
        assert self._page, "Phải gọi open() trước"
        page = self._page
        search = self.cfg.search

        url = search.url_template.format(query=quote_plus(query))
        self.log.info("Mở %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeout as exc:
            raise RetryableError(f"Không tải được trang tìm kiếm: {exc}") from exc

        self.pacer.pause("chờ trang ổn định")
        self._dismiss_overlays()
        self.pacer.move_mouse(page)

        collected: dict[str, PinCandidate] = {}

        # --- Nguồn 1: trạng thái nhúng sẵn trong trang -------------------
        initial = self._read_embedded_state(page)
        if initial:
            merge_pins(collected, initial)
            self.log.info("Đọc được %d pin từ dữ liệu nhúng trong trang.", len(initial))

        # --- Nguồn 2: cuộn để trang tự tải thêm --------------------------
        scrolls = 0
        stagnant = 0
        while scrolls < search.max_scrolls:
            if len(collected) >= search.candidate_pool:
                self.log.info("Đã gom đủ %d pin.", len(collected))
                break
            if self.pacer.budget_exhausted():
                self.log.warning(
                    "Hết ngân sách thời gian phiên (%.0fs) -- dừng cuộn với %d pin.",
                    self.pacer.profile.max_session_s, len(collected),
                )
                break

            before = len(collected)
            self.pacer.scroll_once(page)
            scrolls += 1

            self._drain_captured(collected)

            gained = len(collected) - before
            if gained == 0:
                stagnant += 1
                # Cuộn thêm mà không ra pin mới nhiều lần liên tiếp -> hết nội
                # dung, hoặc gặp tường đăng nhập. Dừng thay vì cuộn vô ích.
                if stagnant >= 5:
                    self.log.info("Cuộn %d nhịp không ra thêm pin mới -- dừng.", stagnant)
                    break
            else:
                stagnant = 0
                self.log.debug("Nhịp %d: +%d pin (tổng %d)", scrolls, gained, len(collected))

        self._drain_captured(collected)

        # --- Nguồn 3: bóc từ DOM khi hai nguồn trên không ra gì ----------
        if not collected:
            self.log.warning("Không lấy được dữ liệu JSON nào -- chuyển sang bóc từ DOM.")
            merge_pins(collected, self._extract_from_dom(page))

        pins = list(collected.values())
        self.log.info("Tổng cộng %d pin sau %d nhịp cuộn.", len(pins), scrolls)
        if not pins:
            self._save_debug(page, "khong-tim-thay-pin")
            raise RetryableError(
                "Không tìm thấy pin nào. Thường là do gặp tường đăng nhập.\n"
                "  Chạy:  python scripts/login_pinterest.py"
            )
        return pins, scrolls

    def _drain_captured(self, collected: dict[str, PinCandidate]) -> None:
        """Phân tích các phản hồi đã bắt được, gộp vào kho pin."""
        if not self._captured:
            return
        batch, self._captured = self._captured, []
        for raw in batch:
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            merge_pins(
                collected,
                extract_pins(
                    payload,
                    size_preference=self.cfg.download.size_preference,
                    start_index=len(collected),
                ),
            )

    def _read_embedded_state(self, page: Page) -> list[PinCandidate]:
        """Nguồn 1: đọc trạng thái Pinterest nhúng sẵn trong HTML."""
        for script_id in _STATE_SCRIPT_IDS:
            try:
                raw = page.evaluate(
                    "(id) => { const el = document.getElementById(id); "
                    "return el ? el.textContent : null; }",
                    script_id,
                )
            except PlaywrightError:
                continue
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            pins = extract_pins(payload, size_preference=self.cfg.download.size_preference)
            if pins:
                self.log.debug("Trạng thái nhúng đọc từ thẻ #%s", script_id)
                return pins
        return []

    def _extract_from_dom(self, page: Page) -> list[PinCandidate]:
        """Nguồn 3: bóc từ DOM. Không có số lượt lưu."""
        try:
            rows = page.evaluate(_DOM_EXTRACT_JS) or []
        except PlaywrightError as exc:
            self.log.debug("Bóc DOM thất bại: %s", exc)
            return []

        pins: list[PinCandidate] = []
        for index, row in enumerate(rows):
            url = upgrade_pinimg_url(str(row.get("url") or ""))
            if not url:
                continue
            pins.append(
                PinCandidate(
                    id=str(row["id"]),
                    image_url=url,
                    pin_url=f"https://www.pinterest.com/pin/{row['id']}/",
                    title=str(row.get("alt") or ""),
                    width=int(row.get("width") or 0),
                    height=int(row.get("height") or 0),
                    discovery_index=index,
                )
            )
        return pins

    # ===================================================================
    # Tải ảnh
    # ===================================================================
    def download(self, pin: PinCandidate, dest: Path) -> int:
        """Tải một ảnh về `dest`. Trả về số byte đã ghi.

        Dùng phiên của chính trình duyệt (`context.request`) nên mang theo đúng
        cookie và header như khi bạn bấm tải bằng tay.
        """
        assert self._context, "Phải gọi open() trước"
        try:
            response = self._context.request.get(
                pin.image_url, timeout=self.cfg.download.timeout_ms
            )
        except PlaywrightError as exc:
            raise RetryableError(f"Không tải được {pin.image_url}: {exc}") from exc

        if not response.ok:
            raise RetryableError(f"Máy chủ trả về {response.status} cho {pin.image_url}")

        data = response.body()
        if not data:
            raise RetryableError(f"Ảnh rỗng: {pin.image_url}")

        ensure_dir(dest.parent)
        dest.write_bytes(data)
        return len(data)

    def wait_between_downloads(self) -> None:
        """Nghỉ giữa hai lượt tải. Tải liên tiếp không nghỉ là hành vi lộ nhất."""
        cfg = self.cfg.download
        time.sleep(random.uniform(cfg.min_delay_s, cfg.max_delay_s))

    # ===================================================================
    # Tiện ích
    # ===================================================================
    def _dismiss_overlays(self) -> None:
        """Đóng hộp thoại cookie / mời đăng nhập nếu có. Không có cũng không sao."""
        assert self._page
        candidates = [
            'button:has-text("Accept all")',
            'button:has-text("Accept")',
            'button[aria-label="Close"]',
            'button:has-text("Not now")',
        ]
        for selector in candidates:
            try:
                element = self._page.locator(selector).first
                element.wait_for(state="visible", timeout=1500)
                element.click(timeout=3000)
                self.log.debug("Đã đóng lớp phủ: %s", selector)
                self.pacer.pause("sau khi đóng hộp thoại")
                return
            except PlaywrightError:
                continue

    def is_logged_in(self) -> bool:
        """Đoán nhanh xem phiên đã đăng nhập chưa.

        Chỉ để cảnh báo, không dùng để chặn: Pinterest vẫn cho xem một phần khi
        chưa đăng nhập, và ta không nên từ chối chạy chỉ vì đoán sai.
        """
        assert self._page
        try:
            return self._page.locator('div[data-test-id="header-profile"]').count() > 0 or (
                self._page.locator('button:has-text("Log in")').count() == 0
            )
        except PlaywrightError:
            return False

    def _save_debug(self, page: Page, label: str) -> None:
        if not self.cfg.browser.save_debug_artifacts:
            return
        try:
            folder = ensure_dir(OUTPUT_DIR / "_debug" / "pinterest")
            page.screenshot(path=str(folder / f"{label}.png"), full_page=False)
            (folder / f"{label}.html").write_text(page.content(), encoding="utf-8")
            self.log.info("Đã lưu ảnh chụp và HTML để gỡ lỗi: %s", folder)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Không lưu được dữ liệu gỡ lỗi: %s", exc)
