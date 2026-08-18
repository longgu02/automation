"""Bộ định vị phần tử UI, nạp từ YAML.

VÌ SAO PHẢI TÁCH RA: Google đổi giao diện Flow khá thường xuyên. Khi đó, thứ
duy nhất hỏng là chuỗi selector. Tách hết ra `config/flow_selectors.yaml` nghĩa
là bạn sửa một file dữ liệu, không phải đọc lại logic Python.

HAI CƠ CHẾ CHỐNG VỠ:

1. *Danh sách ứng viên*: mỗi phần tử khai báo NHIỀU selector, thử lần lượt tới
   khi có cái nhìn thấy được. Xếp theo độ bền: ưu tiên `role=`/`text=` (bám vào
   ngữ nghĩa và nhãn hiển thị) trước `css=` bám vào class sinh tự động.

2. *Chẩn đoán khi hỏng*: không tìm được thì chụp màn hình + đổ HTML ra đĩa, và
   in ra đúng những selector đã thử.

Chuỗi selector dùng thẳng cú pháp Playwright, trộn engine thoải mái:
    role=textbox[name="Prompt"]      -- theo vai trò ARIA (bền nhất)
    text=Download                    -- theo chữ hiển thị
    textarea[placeholder*="video"]   -- CSS thuần
    div[data-testid="clip"]:has(video)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout

from core.config import load_yaml
from core.errors import ConfigError, SelectorNotFound
from core.paths import PROJECT_ROOT, ensure_dir

log = logging.getLogger(__name__)


class LocatorBook:
    """Ánh xạ tên-nghiệp-vụ -> danh sách selector ứng viên."""

    def __init__(
        self,
        selectors_file: Path,
        page: Page,
        *,
        default_timeout_ms: int = 15000,
        debug_dir: Path | None = None,
    ):
        path = selectors_file if selectors_file.is_absolute() else PROJECT_ROOT / selectors_file
        raw = load_yaml(path)
        self.source = path
        self.page = page
        self.default_timeout_ms = default_timeout_ms
        self.debug_dir = debug_dir
        self._book: dict[str, list[str]] = {}

        for key, value in raw.items():
            if isinstance(value, str):
                self._book[key] = [value]
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                self._book[key] = value
            else:
                raise ConfigError(
                    f"{path}: '{key}' phải là một chuỗi selector hoặc danh sách chuỗi."
                )

    # ------------------------------------------------------------------ tra
    def candidates(self, key: str, **fmt: Any) -> list[str]:
        """Danh sách selector của `key`, đã thay chỗ trống `{...}` nếu có.

        Ví dụ: `candidates("model_option", value="Veo 3.1")` sẽ biến
        `text={value}` thành `text=Veo 3.1`.
        """
        if key not in self._book:
            raise ConfigError(
                f"Selector '{key}' chưa được định nghĩa trong {self.source}. "
                f"Các khoá hiện có: {sorted(self._book)}"
            )
        if not fmt:
            return list(self._book[key])
        return [sel.format(**fmt) for sel in self._book[key]]

    def find(
        self,
        key: str,
        *,
        root: Locator | Page | None = None,
        timeout_ms: int | None = None,
        required: bool = True,
        state: Literal["visible", "attached"] = "visible",
        **fmt: Any,
    ) -> Locator | None:
        """Trả về locator ĐẦU TIÊN khớp trong danh sách ứng viên.

        required=False -> trả None thay vì ném lỗi. Dùng cho phần tử có thể
        không tồn tại (banner cookie, hộp thoại chào mừng...).

        state="attached" -> chấp nhận phần tử có trong DOM nhưng bị ẩn. Cần cho
        `input[type=file]`, thứ gần như luôn được CSS giấu đi nhưng vẫn nhận
        được `set_input_files()`.
        """
        scope = root if root is not None else self.page
        options = self.candidates(key, **fmt)
        budget = timeout_ms if timeout_ms is not None else self.default_timeout_ms
        # Chia đều thời gian cho các ứng viên: tổng thời gian chờ không đổi dù
        # bạn khai báo 2 hay 8 ứng viên.
        per_candidate = max(500, budget // max(1, len(options)))

        for selector in options:
            try:
                locator = scope.locator(selector).first
                locator.wait_for(state=state, timeout=per_candidate)
                log.debug("Selector khớp: %s -> %s", key, selector)
                return locator
            except PlaywrightTimeout:
                continue
            except Exception as exc:  # selector sai cú pháp -> báo rõ, đừng nuốt
                log.debug("Selector '%s' của '%s' lỗi: %s", selector, key, exc)
                continue

        if not required:
            return None

        debug_path = self.dump_debug(f"missing_{key}") if self.debug_dir else None
        raise SelectorNotFound(key, options, str(debug_path) if debug_path else None)

    def count(self, key: str, *, root: Locator | Page | None = None, **fmt: Any) -> int:
        """Đếm phần tử khớp ứng viên ĐẦU TIÊN có kết quả > 0.

        Dùng để theo dõi số clip đã render -- rẻ, không chờ đợi, gọi trong vòng
        lặp poll rất thoải mái.
        """
        scope = root if root is not None else self.page
        for selector in self.candidates(key, **fmt):
            try:
                n = scope.locator(selector).count()
                if n > 0:
                    return n
            except Exception:
                continue
        return 0

    def all(self, key: str, *, root: Locator | Page | None = None, **fmt: Any) -> Locator:
        """Locator trỏ tới TẤT CẢ phần tử khớp ứng viên đầu tiên có kết quả."""
        scope = root if root is not None else self.page
        options = self.candidates(key, **fmt)
        for selector in options:
            try:
                locator = scope.locator(selector)
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        debug_path = self.dump_debug(f"missing_{key}") if self.debug_dir else None
        raise SelectorNotFound(key, options, str(debug_path) if debug_path else None)

    # ------------------------------------------------------------- chẩn đoán
    def dump_debug(self, label: str) -> Path | None:
        """Chụp màn hình + đổ HTML để soi khi selector không khớp.

        Không bao giờ ném lỗi: đây là đường dẫn xử lý sự cố, nó mà hỏng nữa thì
        thông tin lỗi gốc bị che mất.
        """
        if not self.debug_dir:
            return None
        try:
            folder = ensure_dir(self.debug_dir)
            png = folder / f"{label}.png"
            html = folder / f"{label}.html"
            self.page.screenshot(path=str(png), full_page=True)
            html.write_text(self.page.content(), encoding="utf-8")
            log.info("Đã lưu ảnh chụp và HTML để gỡ lỗi: %s", folder)
            return folder
        except Exception as exc:
            log.debug("Không lưu được dữ liệu gỡ lỗi: %s", exc)
            return None
