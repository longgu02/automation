"""Backend sinh video bằng cách điều khiển trình duyệt trên Google Flow.

Dùng chính tài khoản Google AI Pro của bạn, tiêu credit của gói thuê bao (không
cần bật billing API).

BỐN QUYẾT ĐỊNH THIẾT KẾ CẦN NẮM TRƯỚC KHI SỬA FILE NÀY
------------------------------------------------------

1. *Profile Chrome bền vững thay vì lưu cookie.*
   Ta khởi chạy Chrome thật với một thư mục profile riêng (`.secrets/browser_profile`).
   Đăng nhập MỘT LẦN bằng `scripts/login_flow.py`, sau đó mọi lần chạy đều dùng
   lại phiên đó. Không đụng tới mật khẩu, không tự động hoá màn hình đăng nhập,
   không phải xử lý 2FA -- những thứ vừa mong manh vừa rủi ro.

2. *Không có selector nào nằm trong file này.* Tất cả ở
   `config/flow_selectors.yaml`. Xem `backends/locators.py`.

3. *Nhận biết clip mới bằng cách ĐẾM.* Không dựa vào id phần tử (chúng sinh
   ngẫu nhiên). Trước khi gửi prompt, đếm số clip đã render xong; sau khi gửi,
   chờ tới khi con số đó tăng thêm đúng bằng số output mong đợi. Cách này miễn
   nhiễm với việc Flow đổi cách đánh dấu clip.

4. *Tuần tự, không chạy song song.* Một tài khoản, một hàng đợi render. Bắn
   nhiều prompt cùng lúc chỉ khiến việc ghép "clip nào của prompt nào" trở nên
   bất định -- đổi lấy chút tốc độ bằng rủi ro tải nhầm file là không đáng.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from core.accounts import Account
from core.errors import (
    AuthError,
    ContentBlocked,
    FatalError,
    GenerationTimeout,
    QuotaExhausted,
    RetryableError,
)
from core.paths import BROWSER_PROFILE_ROOT, OUTPUT_DIR, ensure_dir
from modules.video_gen.backends.base import VideoBackend
from modules.video_gen.backends.locators import LocatorBook
from modules.video_gen.config import BrowserConfig
from modules.video_gen.models import VideoArtifact, VideoSpec

log = logging.getLogger(__name__)

#: Từ khoá trong thông báo lỗi của Flow -> loại lỗi tương ứng. Khớp không phân
#: biệt hoa thường. Thêm dòng mới khi bạn gặp thông báo lạ trong thực tế.
_ERROR_SIGNATURES: list[tuple[re.Pattern[str], type[FatalError]]] = [
    (re.compile(r"quota|limit reached|out of credits|no credits|ran out", re.I), QuotaExhausted),
    (re.compile(r"blocked|policy|not allowed|violat|unsafe|can't generate", re.I), ContentBlocked),
]


class FlowBrowserBackend(VideoBackend):
    name = "flow_browser"

    def __init__(
        self, cfg: BrowserConfig, account: Account, logger: logging.Logger | None = None
    ):
        super().__init__(account, logger)
        self.cfg = cfg
        # Mỗi tài khoản có profile Chrome riêng và (thường là) project Flow riêng.
        self.profile_dir = account.resolved_profile_dir(BROWSER_PROFILE_ROOT)
        self.workspace_url = account.workspace_url or cfg.workspace_url
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._loc: LocatorBook | None = None

    def describe(self) -> str:
        mode = "ẩn" if self.cfg.headless else "hiện"
        return (
            f"[{self.account.describe()}] Flow qua trình duyệt "
            f"({self.cfg.browser_channel}, cửa sổ {mode}) @ {self.workspace_url}"
        )

    # ===================================================================
    # Vòng đời
    # ===================================================================
    def open(self) -> None:
        self.log.info("Khởi động trình duyệt: %s", self.describe())
        self._playwright = sync_playwright().start()

        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(ensure_dir(self.profile_dir)),
                channel=self.cfg.browser_channel,
                headless=self.cfg.headless,
                slow_mo=self.cfg.slow_mo_ms,
                accept_downloads=True,
                locale=self.cfg.locale,
                viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as exc:
            self.close()
            if "executable doesn't exist" in str(exc).lower() or "channel" in str(exc).lower():
                raise FatalError(
                    f"Không mở được trình duyệt kênh '{self.cfg.browser_channel}'.\n"
                    "  - Nếu chưa cài Google Chrome: cài đặt, hoặc đổi "
                    "`browser.browser_channel: chromium` trong config/video_gen.yaml.\n"
                    "  - Nếu thiếu bản Chromium của Playwright: chạy `playwright install chromium`.\n"
                    f"Lỗi gốc: {exc}"
                ) from exc
            raise FatalError(f"Không khởi động được trình duyệt: {exc}") from exc

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self.cfg.element_timeout_ms)

        # Thư mục gỡ lỗi tách theo tài khoản: chạy song song mà dùng chung thì
        # ảnh chụp của các worker đè lên nhau.
        debug_dir = (
            ensure_dir(OUTPUT_DIR / "_debug" / self.account.id)
            if self.cfg.save_debug_artifacts
            else None
        )
        self._loc = LocatorBook(
            self.cfg.selectors_file,
            self._page,
            default_timeout_ms=self.cfg.element_timeout_ms,
            debug_dir=debug_dir,
        )

        self._open_workspace()
        self._assert_logged_in()
        self.log.info("Trình duyệt sẵn sàng, đã đăng nhập.")

    def close(self) -> None:
        # Đóng theo thứ tự ngược, mỗi bước tự chịu lỗi: dọn dẹp không được phép
        # che mất lỗi thật đang lan ra ngoài.
        for label, closer in (
            ("context", lambda: self._context.close() if self._context else None),
            ("playwright", lambda: self._playwright.stop() if self._playwright else None),
        ):
            try:
                closer()
            except Exception as exc:
                self.log.debug("Bỏ qua lỗi khi đóng %s: %s", label, exc)
        self._context = self._page = self._playwright = self._loc = None

    # ===================================================================
    # Chuẩn bị trang
    # ===================================================================
    def _open_workspace(self) -> None:
        assert self._page and self._loc
        self.log.debug("Mở %s", self.workspace_url)
        try:
            self._page.goto(self.workspace_url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeout as exc:
            raise RetryableError(f"Không tải được {self.workspace_url}: {exc}") from exc

        # Các hộp thoại chào mừng / cookie: có thì đóng, không có thì thôi.
        for optional_key in ("dismiss_dialog_button", "accept_cookies_button"):
            button = self._loc.find(optional_key, required=False, timeout_ms=2500)
            if button:
                self.log.debug("Đóng hộp thoại: %s", optional_key)
                _safe_click(button)

    def _assert_logged_in(self) -> None:
        """Phân biệt 'chưa đăng nhập' với 'UI đổi' -- hai lỗi cần hai cách xử lý."""
        assert self._loc
        if self._loc.find("prompt_input", required=False, timeout_ms=self.cfg.element_timeout_ms):
            return

        if self._loc.find("signin_button", required=False, timeout_ms=3000):
            raise AuthError(
                f"Tài khoản '{self.account.id}' chưa đăng nhập vào Google Flow.\n"
                f"  Chạy:  python scripts/login_flow.py --account {self.account.id}\n"
                f"  rồi đăng nhập bằng tài khoản Google AI Pro tương ứng. Phiên đăng nhập "
                f"được giữ trong {self.profile_dir} nên chỉ cần làm một lần."
            )

        # Không thấy ô prompt mà cũng không thấy nút đăng nhập -> DOM đã đổi.
        self._loc.dump_debug("workspace_unrecognized")
        raise RetryableError(
            "Đã mở được trang nhưng không nhận ra giao diện (không thấy ô nhập prompt "
            "lẫn nút đăng nhập). Nhiều khả năng Flow đã đổi giao diện -- xem ảnh chụp "
            f"trong {OUTPUT_DIR / '_debug'} rồi cập nhật config/flow_selectors.yaml."
        )

    # ===================================================================
    # Việc chính
    # ===================================================================
    def generate(self, spec: VideoSpec, dest_dir: Path) -> list[VideoArtifact]:
        assert self._page and self._loc, "Phải gọi open() trước generate()"
        page, loc = self._page, self._loc
        ensure_dir(dest_dir)

        if self.cfg.apply_settings_in_ui:
            self._apply_settings(spec)

        # Mốc so sánh: số clip ĐÃ RENDER XONG trước khi ta gửi prompt.
        baseline = loc.count("result_card_ready")
        self.log.debug("[%s] số clip sẵn có trước khi gửi: %d", spec.id, baseline)

        if spec.reference_image:
            self._attach_reference_image(spec)

        self._submit_prompt(spec)

        new_cards = self._wait_for_new_results(
            baseline=baseline,
            expected=spec.outputs_per_prompt,
            spec_id=spec.id,
        )

        artifacts: list[VideoArtifact] = []
        for index, card in enumerate(new_cards, start=1):
            path = dest_dir / f"{spec.id}_{index:02d}.mp4"
            self._download_card(card, path, spec_id=spec.id, index=index)
            artifacts.append(
                VideoArtifact.from_file(
                    spec, index, path, self.name, workspace_url=page.url
                )
            )
        return artifacts

    # ------------------------------------------------------------------
    def _submit_prompt(self, spec: VideoSpec) -> None:
        assert self._page and self._loc
        page, loc = self._page, self._loc

        box = loc.find("prompt_input")
        assert box is not None
        box.click()
        box.fill("")  # fill("") xoá sạch nội dung cũ, kể cả prompt lần trước
        box.fill(spec.prompt)

        preview = spec.prompt if len(spec.prompt) <= 90 else spec.prompt[:87] + "..."
        self.log.info("[%s] gửi prompt: %s", spec.id, preview)

        submit = loc.find("submit_button", required=False, timeout_ms=3000)
        if submit and _safe_click(submit):
            return
        # Nhiều phiên bản UI của Flow gửi bằng Enter. Đây là đường lui hợp lệ,
        # không phải chắp vá.
        self.log.debug("[%s] không thấy nút gửi, dùng phím Enter.", spec.id)
        box.press("Enter")

    def _attach_reference_image(self, spec: VideoSpec) -> None:
        assert self._loc and spec.reference_image
        image = spec.reference_image
        if not image.exists():
            raise FatalError(f"[{spec.id}] không tìm thấy ảnh tham chiếu: {image}")

        # state="attached": ô tải file hầu như luôn bị CSS giấu, nhưng vẫn nhận
        # được set_input_files() bình thường.
        upload = self._loc.find(
            "reference_image_input", required=False, timeout_ms=5000, state="attached"
        )
        if upload is None:
            raise FatalError(
                f"[{spec.id}] có khai báo reference_image nhưng không tìm thấy ô tải ảnh lên. "
                "Bổ sung selector 'reference_image_input' trong config/flow_selectors.yaml."
            )
        upload.set_input_files(str(image))
        self.log.info("[%s] đã đính kèm ảnh tham chiếu %s", spec.id, image.name)

    # ------------------------------------------------------------------
    def _wait_for_new_results(self, *, baseline: int, expected: int, spec_id: str) -> list[Locator]:
        """Chờ tới khi có thêm đúng `expected` clip render xong.

        Vòng lặp chỉ đếm phần tử -- rẻ và ổn định. Mỗi vòng còn ngó qua thông
        báo lỗi để bỏ cuộc sớm thay vì ngồi chờ hết 15 phút vô ích.
        """
        assert self._loc and self._page
        loc = self._loc
        target = baseline + expected
        deadline = time.monotonic() + self.cfg.generation_timeout_s
        started = time.monotonic()
        last_report = 0.0
        last_seen = baseline

        while time.monotonic() < deadline:
            self._raise_if_error_shown(spec_id)

            ready = loc.count("result_card_ready")
            if ready >= target:
                elapsed = time.monotonic() - started
                self.log.info("[%s] %d clip đã render xong sau %.0fs.", spec_id, expected, elapsed)
                return self._pick_new_cards(baseline=baseline, expected=expected)

            elapsed = time.monotonic() - started
            if ready != last_seen or elapsed - last_report >= 30:
                self.log.info(
                    "[%s] đang render... %d/%d clip (%.0fs / tối đa %ds)",
                    spec_id, ready - baseline, expected, elapsed, self.cfg.generation_timeout_s,
                )
                last_seen, last_report = ready, elapsed

            self._page.wait_for_timeout(int(self.cfg.poll_interval_s * 1000))

        loc.dump_debug(f"timeout_{spec_id}")
        raise GenerationTimeout(
            f"[{spec_id}] quá {self.cfg.generation_timeout_s}s mà chỉ thấy "
            f"{loc.count('result_card_ready') - baseline}/{expected} clip render xong."
        )

    def _pick_new_cards(self, *, baseline: int, expected: int) -> list[Locator]:
        """Chọn ra đúng những thẻ clip vừa xuất hiện.

        `browser.results_order` quyết định clip mới nằm đầu hay cuối danh sách.
        Đặt sai giá trị này = tải nhầm clip cũ, nên hãy xác nhận bằng mắt ở lần
        chạy đầu tiên với `--headful`.
        """
        assert self._loc
        cards = self._loc.all("result_card_ready")
        total = cards.count()

        if self.cfg.results_order == "newest_first":
            indexes = list(range(expected))
        else:
            indexes = list(range(baseline, min(total, baseline + expected)))

        selected = [cards.nth(i) for i in indexes]
        if len(selected) < expected:
            raise RetryableError(
                f"Cần {expected} clip mới nhưng chỉ khoanh vùng được {len(selected)} "
                f"(tổng {total}, mốc {baseline}). Kiểm tra `browser.results_order`."
            )
        return selected

    # ------------------------------------------------------------------
    def _download_card(self, card: Locator, dest: Path, *, spec_id: str, index: int) -> None:
        """Tải một clip về `dest` qua menu ngữ cảnh của thẻ clip.

        Playwright bắt sự kiện download của trình duyệt, nên ta lấy đúng file
        gốc Flow phục vụ -- không phải bản re-encode hay stream cắt xén.
        """
        assert self._page and self._loc
        page, loc = self._page, self._loc

        # Nhiều UI chỉ hiện nút menu khi rê chuột vào thẻ.
        try:
            card.scroll_into_view_if_needed(timeout=5000)
            card.hover(timeout=5000)
        except PlaywrightTimeout:
            self.log.debug("[%s] không rê chuột được vào thẻ #%d, vẫn thử tiếp.", spec_id, index)

        menu = loc.find("card_menu_button", root=card, timeout_ms=8000, required=False)
        if menu:
            _safe_click(menu)

        try:
            with page.expect_download(timeout=self.cfg.element_timeout_ms * 4) as download_info:
                item = loc.find("download_menu_item", timeout_ms=8000)
                assert item is not None
                item.click()
                # Một số bản UI hỏi thêm chất lượng ("Original size" / "720p").
                quality = loc.find("download_quality_option", required=False, timeout_ms=4000)
                if quality:
                    _safe_click(quality)
            download = download_info.value
        except PlaywrightTimeout as exc:
            loc.dump_debug(f"download_failed_{spec_id}_{index}")
            raise RetryableError(f"[{spec_id}] không tải được clip #{index}: {exc}") from exc

        ensure_dir(dest.parent)
        download.save_as(str(dest))

        size_mb = dest.stat().st_size / (1024 * 1024)
        if size_mb < 0.01:
            raise RetryableError(f"[{spec_id}] clip #{index} tải về rỗng ({dest}).")
        self.log.info("[%s] đã lưu clip #%d -> %s (%.1f MB)", spec_id, index, dest, size_mb)

    # ------------------------------------------------------------------
    def _apply_settings(self, spec: VideoSpec) -> None:
        """Chỉnh model / tỉ lệ khung hình / số output qua bảng cài đặt.

        Cố ý ở chế độ *best-effort*: thiếu selector chỉ ghi cảnh báo chứ không
        làm hỏng cả run. Flow ghi nhớ cài đặt theo từng project, nên cách dùng
        được khuyến nghị là chỉnh tay một lần rồi để `apply_settings_in_ui: false`
        (xem README, mục "Vì sao mặc định không tự chỉnh cài đặt").
        """
        assert self._loc
        loc = self._loc

        panel = loc.find("settings_button", required=False, timeout_ms=5000)
        if panel is None:
            self.log.warning("[%s] không mở được bảng cài đặt -- dùng cài đặt hiện có trên UI.", spec.id)
            return
        _safe_click(panel)

        wanted = {
            "model": spec.model,
            "aspect_ratio": spec.aspect_ratio,
            "outputs_per_prompt": str(spec.outputs_per_prompt),
        }
        for field, value in wanted.items():
            opener = loc.find(f"{field}_selector", required=False, timeout_ms=4000)
            if opener is None:
                self.log.warning("[%s] bỏ qua cài đặt '%s': không thấy điều khiển.", spec.id, field)
                continue
            _safe_click(opener)
            option = loc.find(f"{field}_option", value=value, required=False, timeout_ms=4000)
            if option is None:
                self.log.warning("[%s] không thấy lựa chọn '%s' cho '%s'.", spec.id, value, field)
                continue
            _safe_click(option)
            self.log.debug("[%s] đặt %s = %s", spec.id, field, value)

        closer = loc.find("settings_close_button", required=False, timeout_ms=3000)
        if closer:
            _safe_click(closer)

    # ------------------------------------------------------------------
    def _raise_if_error_shown(self, spec_id: str) -> None:
        """Đọc thông báo lỗi trên UI và quy về đúng loại exception.

        Phân loại đúng ở đây quyết định hành vi phía trên: hết credit thì dừng
        cả run, prompt bị chặn thì bỏ qua prompt đó, lỗi lạ thì thử lại.
        """
        assert self._loc
        toast = self._loc.find("error_message", required=False, timeout_ms=500)
        if toast is None:
            return

        try:
            text = (toast.inner_text(timeout=2000) or "").strip()
        except PlaywrightTimeout:
            return
        if not text:
            return

        self.log.warning("[%s] Flow báo lỗi: %s", spec_id, text)
        for pattern, error_type in _ERROR_SIGNATURES:
            if pattern.search(text):
                raise error_type(f"[{spec_id}] {text}")
        raise RetryableError(f"[{spec_id}] Flow báo lỗi: {text}")


def _safe_click(locator: Locator, timeout_ms: int = 8000) -> bool:
    """Click, trả về False nếu không click được thay vì ném lỗi.

    Dùng cho các thao tác *không thiết yếu* (đóng banner, mở menu tuỳ chọn) --
    nơi thất bại là chuyện bình thường và luồng chính vẫn đi tiếp được.
    """
    try:
        locator.click(timeout=timeout_ms)
        return True
    except PlaywrightError as exc:
        log.debug("Click không thành công: %s", exc)
        return False
