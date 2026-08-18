"""ImageCrawlModule -- tìm trên Pinterest và tải về N ảnh được yêu thích nhất.

Bốn bước:

    1. Gom pin      -- mở trang, cuộn với nhịp người dùng, thu thập pin
    2. Xếp hạng     -- theo số lượt lưu; không có số liệu thì nói rõ ra
    3. Tải ảnh      -- từng tấm một, có nghỉ giữa các lượt
    4. Bàn giao     -- ghi file kê khai + đẩy dữ liệu vào ctx.shared

MỘT ĐIỂM TRUNG THỰC ĐƯỢC THIẾT KẾ SẴN: `RankingBasis` đi kèm kết quả tới tận
log, file kê khai và `ctx.shared`. Khi Pinterest không trả về số lượt lưu, module
vẫn đưa ra 10 ảnh -- nhưng nói thẳng rằng đó là "theo thứ tự tìm kiếm", không
phải "nhiều lượt thích nhất". Gọi nhầm hai thứ này là đưa cho bạn một con số
không có thật.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path
from urllib.parse import quote_plus

from core.errors import FatalError
from core.module import BaseModule, ModuleContext, ModuleResult, ModuleStatus
from core.paths import PROJECT_ROOT, ensure_dir, slugify
from modules.image_crawl.browser import PinterestBrowser
from modules.image_crawl.config import ImageCrawlConfig
from modules.image_crawl.extract import rank_pins
from modules.image_crawl.humanize import Pacer
from modules.image_crawl.models import CrawlReport, DownloadedImage, RankingBasis


class ImageCrawlModule(BaseModule):
    name = "image_crawl"

    def __init__(self, cfg: ImageCrawlConfig, *, query: str | None = None):
        self.cfg = cfg
        self.query = query or cfg.search.query

    # ===================================================================
    def run(self, ctx: ModuleContext) -> ModuleResult:
        log = ctx.logger
        started = time.monotonic()

        dest_dir = self._dest_dir()
        pacer = Pacer(self.cfg.pacing.to_profile(), seed=self.cfg.pacing.seed)

        log.info("Từ khoá: '%s' -- lấy top %d ảnh", self.query, self.cfg.search.top_n)
        log.info(
            "Nhịp độ: nghỉ %.1f-%.1fs giữa các thao tác, trần phiên %.0fs",
            self.cfg.pacing.min_action_delay_s,
            self.cfg.pacing.max_action_delay_s,
            self.cfg.pacing.max_session_s,
        )

        if ctx.dry_run:
            return self._dry_run_report(ctx, dest_dir)

        report = CrawlReport(query=self.query, basis=RankingBasis.SEARCH_ORDER)

        with PinterestBrowser(self.cfg, pacer, log) as browser:
            if not browser.is_logged_in():
                # Cảnh báo chứ không chặn: Pinterest vẫn cho xem một phần khi
                # chưa đăng nhập, và phép đoán này có thể sai.
                log.warning(
                    "Có vẻ chưa đăng nhập Pinterest. Nếu gom được ít pin, chạy: "
                    "python scripts/login_pinterest.py"
                )

            # --- 1. Gom pin ---------------------------------------------
            pins, scrolls = browser.collect(self.query)
            report.discovered = len(pins)
            report.scrolls = scrolls

            # --- 2. Xếp hạng --------------------------------------------
            top, basis = rank_pins(pins, self.cfg.search.top_n)
            report.basis = basis
            self._log_ranking(log, top, basis, len(pins))

            # --- 3. Tải ảnh ---------------------------------------------
            for rank, pin in enumerate(top, start=1):
                if pin.width and pin.width < self.cfg.download.min_width:
                    log.info("[%02d] bỏ qua ảnh nhỏ (%dpx): %s", rank, pin.width, pin.id)
                    continue

                path = dest_dir / self._filename(rank, pin)
                if self.cfg.download.skip_existing and path.exists():
                    log.info("[%02d] đã có sẵn, bỏ qua: %s", rank, path.name)
                    report.downloaded.append(
                        DownloadedImage(pin, path, path.stat().st_size, rank)
                    )
                    continue

                try:
                    size = browser.download(pin, path)
                except Exception as exc:  # noqa: BLE001
                    # Một ảnh hỏng không được làm mất chín ảnh còn lại.
                    log.error("[%02d] tải thất bại (%s): %s", rank, pin.id, exc)
                    report.failures.append(f"{pin.id}: {exc}")
                    continue

                log.info("[%02d] %s -> %s (%.0f KB)", rank, pin.describe(), path.name, size / 1024)
                report.downloaded.append(DownloadedImage(pin, path, size, rank))

                if rank < len(top):
                    browser.wait_between_downloads()

            log.info("Nhịp độ phiên: %s", pacer.summary())

        report.elapsed_s = time.monotonic() - started
        manifest = self._write_manifest(report, dest_dir) if self.cfg.download.write_manifest else None

        # --- 4. Bàn giao -------------------------------------------------
        outputs = {
            "images": [str(d.path) for d in report.downloaded],
            "ranking_basis": report.basis.value,
            "query": self.query,
        }
        if manifest:
            outputs["manifest"] = str(manifest)
        ctx.shared.setdefault("image_crawl", {}).update(outputs)

        if not report.downloaded:
            status = ModuleStatus.FAILED
        elif report.failures:
            status = ModuleStatus.PARTIAL
        else:
            status = ModuleStatus.SUCCESS

        return ModuleResult(
            status=status,
            outputs=outputs,
            stats={
                "discovered": report.discovered,
                "downloaded": len(report.downloaded),
                "failed": len(report.failures),
                "basis": report.basis.value,
                "scrolls": report.scrolls,
                "elapsed_s": round(report.elapsed_s),
            },
            errors=report.failures,
        )

    # ===================================================================
    def _log_ranking(self, log, top, basis: RankingBasis, pool: int) -> None:
        """In bảng xếp hạng và nói rõ nó dựa trên cái gì."""
        log.info("Xếp hạng %d pin, lấy %d đầu.", pool, len(top))
        log.info("Cơ sở xếp hạng: %s", basis.describe())

        if basis is RankingBasis.SEARCH_ORDER:
            log.warning(
                "LƯU Ý: không lấy được số lượt lưu nào, nên đây KHÔNG phải "
                "'top theo lượt thích'. Đây là thứ tự Pinterest tự xếp cho từ "
                "khoá này -- vốn có tính tới mức độ tương tác, nhưng ta không đo được."
            )

        for rank, pin in enumerate(top, start=1):
            log.info("  %2d. %s", rank, pin.describe())

    def _dest_dir(self) -> Path:
        text = str(self.cfg.download.dest).format(query_slug=slugify(self.query))
        path = Path(text)
        return ensure_dir(path if path.is_absolute() else PROJECT_ROOT / path)

    def _filename(self, rank: int, pin) -> str:
        """Tên file mang sẵn thứ hạng để sắp xếp trong thư mục là ra đúng thứ tự."""
        suffix = Path(pin.image_url.split("?")[0]).suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            suffix = ".jpg"
        return f"{rank:02d}_{pin.id}{suffix}"

    def _write_manifest(self, report: CrawlReport, dest_dir: Path) -> Path:
        """Ghi kê khai nguồn gốc từng tấm ảnh.

        Đây là dấu vết ghi công tác giả: ảnh tải về thuộc bản quyền người đăng,
        và file này giữ đường dẫn tới pin gốc để bạn truy lại được.
        """
        path = dest_dir / "manifest.json"
        payload = {
            "query": report.query,
            "ranking_basis": report.basis.value,
            "ranking_basis_note": report.basis.describe(),
            "crawled_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "discovered": report.discovered,
            "images": [
                {
                    "rank": item.rank,
                    "file": item.path.name,
                    "pin_id": item.pin.id,
                    "pin_url": item.pin.pin_url,
                    "image_url": item.pin.image_url,
                    "saves": item.pin.saves,
                    "reactions": item.pin.reactions,
                    "title": item.pin.title,
                    "size_bytes": item.size_bytes,
                }
                for item in report.downloaded
            ],
            "failures": report.failures,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _dry_run_report(self, ctx: ModuleContext, dest_dir: Path) -> ModuleResult:
        """In ra kế hoạch mà không mở trình duyệt."""
        log = ctx.logger
        cfg = self.cfg
        estimate = cfg.search.max_scrolls * (
            (cfg.pacing.min_scroll_pause_s + cfg.pacing.max_scroll_pause_s) / 2
        )

        log.info("Chạy thử (dry-run) -- không mở trình duyệt:")
        log.info("  Từ khoá        : %s", self.query)
        # Mã hoá y hệt lúc chạy thật -- dry-run mà hiện URL khác thì vô nghĩa.
        log.info(
            "  URL            : %s",
            cfg.search.url_template.format(query=quote_plus(self.query)),
        )
        log.info("  Gom tối đa     : %d pin, tối đa %d nhịp cuộn", cfg.search.candidate_pool, cfg.search.max_scrolls)
        log.info("  Lấy            : top %d", cfg.search.top_n)
        log.info("  Lưu vào        : %s", dest_dir)
        log.info("  Ước tính cuộn  : ~%.0fs (trần phiên %.0fs)", estimate, cfg.pacing.max_session_s)
        return ModuleResult(
            status=ModuleStatus.SKIPPED,
            stats={"query": self.query, "top_n": cfg.search.top_n, "dest": str(dest_dir)},
        )
