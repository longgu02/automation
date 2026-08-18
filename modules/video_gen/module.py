"""VideoGenModule -- lớp điều phối của module sinh video.

Nó KHÔNG biết Playwright là gì, không biết HTTP là gì, và không biết chuyện
song song hoá diễn ra thế nào. Việc của nó gồm đúng năm thứ:

    1. Chín hoá prompt   -- YAML -> danh sách VideoSpec đầy đủ tham số
    2. Bỏ việc thừa      -- spec đã xong ở lần chạy trước thì không làm lại
    3. Chuẩn bị tài nguyên -- nạp danh sách tài khoản, dựng kho tài khoản
    4. Giao việc         -- đưa toàn bộ cho `runner.MultiAccountRunner`
    5. Bàn giao          -- xuất manifest + đẩy dữ liệu vào ctx.shared cho
                            module kế tiếp trong job dùng

Phần "chạy thế nào" (worker, đổi tài khoản khi hết credit, retry, khoá đa
luồng) nằm trọn trong `runner.py`. Tách như vậy để file này luôn đọc được như
một bản mô tả quy trình, không lẫn cơ chế.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from core.accounts import AccountPool, load_accounts
from core.module import BaseModule, ModuleContext, ModuleResult, ModuleStatus
from core.paths import BROWSER_PROFILE_DIR, PROJECT_ROOT, ensure_dir
from modules.video_gen.config import VideoGenConfig
from modules.video_gen.models import VideoArtifact, VideoSpec
from modules.video_gen.runner import MultiAccountRunner, RunReport
from modules.video_gen.specs import build_specs, filter_specs
from modules.video_gen.state import StateStore


class VideoGenModule(BaseModule):
    name = "video_gen"

    def __init__(
        self,
        cfg: VideoGenConfig,
        *,
        only: list[str] | None = None,
        limit: int | None = None,
        force: bool = False,
        accounts: list[str] | None = None,
    ):
        self.cfg = cfg
        self.only = only
        self.limit = limit
        self.force = force  # bỏ qua sổ trạng thái, sinh lại tất cả
        self.accounts = accounts  # chỉ dùng những tài khoản này

    # ===================================================================
    def run(self, ctx: ModuleContext) -> ModuleResult:
        log = ctx.logger
        output_root = _abs(self.cfg.output.root)
        state = StateStore(_abs(self.cfg.output.state_file))

        # --- 1. Chín hoá prompt -----------------------------------------
        specs = filter_specs(build_specs(self.cfg), only=self.only, limit=self.limit)
        if not specs:
            log.warning("Không có prompt nào để chạy.")
            return ModuleResult(status=ModuleStatus.SKIPPED, stats={"total": 0})

        # --- 2. Bỏ việc thừa --------------------------------------------
        pending, reused = self._split_pending(specs, state, log)
        log.info(
            "Tổng %d prompt: %d cần sinh, %d dùng lại kết quả cũ.",
            len(specs), len(pending), len(specs) - len(pending),
        )

        # --- 3. Chuẩn bị tài khoản --------------------------------------
        # Nạp trước cả khi dry-run, để lỗi cấu hình tài khoản lộ ra ngay lúc
        # chạy thử chứ không đợi tới lúc chạy thật.
        account_list = load_accounts(
            _abs(self.cfg.execution.accounts_file),
            fallback_profile_dir=BROWSER_PROFILE_DIR,
            fallback_workspace_url=self.cfg.browser.workspace_url,
            only=self.accounts,
        )
        log.info(
            "Tài khoản dùng được: %s",
            ", ".join(a.describe() for a in account_list if a.enabled) or "(không có)",
        )

        if ctx.dry_run:
            return self._dry_run_report(ctx, specs, pending, account_list)

        if not pending:
            # Không mở trình duyệt khi chẳng có gì để làm.
            log.info("Mọi prompt đã hoàn thành từ trước. Không cần khởi động backend.")
            return self._finish(ctx, reused, RunReport(), output_root)

        # --- 4. Giao việc ------------------------------------------------
        pool = AccountPool(account_list)
        runner = MultiAccountRunner(self.cfg, pool, state, output_root, log)
        report = runner.run(pending)

        # --- 5. Bàn giao -------------------------------------------------
        return self._finish(ctx, reused, report, output_root)

    # ===================================================================
    def _split_pending(
        self, specs: list[VideoSpec], state: StateStore, log
    ) -> tuple[list[VideoSpec], list[VideoArtifact]]:
        """Chia prompt thành 'cần sinh' và 'đã có sẵn từ lần chạy trước'."""
        pending: list[VideoSpec] = []
        reused: list[VideoArtifact] = []

        for spec in specs:
            fingerprint = spec.fingerprint(self.cfg.backend)
            if self.cfg.resume and not self.force and state.is_done(fingerprint):
                paths = state.artifacts_of(fingerprint)
                log.info("[%s] bỏ qua -- đã có sẵn %d file.", spec.id, len(paths))
                reused += [
                    VideoArtifact(
                        spec_id=spec.id, index=i, path=p,
                        backend=self.cfg.backend, model=spec.model,
                        size_bytes=p.stat().st_size if p.exists() else 0,
                    )
                    for i, p in enumerate(paths, start=1)
                ]
            else:
                pending.append(spec)

        return pending, reused

    # ===================================================================
    def _finish(
        self,
        ctx: ModuleContext,
        reused: list[VideoArtifact],
        report: RunReport,
        output_root: Path,
    ) -> ModuleResult:
        all_artifacts = reused + report.created
        manifest_path = self._write_manifest(ctx, all_artifacts, report, output_root)

        if report.failures and report.created:
            status = ModuleStatus.PARTIAL
        elif report.failures:
            status = ModuleStatus.FAILED
        elif not report.created and reused:
            status = ModuleStatus.SKIPPED
        else:
            status = ModuleStatus.SUCCESS

        # Hợp đồng bàn giao cho module kế tiếp trong job (ví dụ: ghép, upload).
        outputs = {
            "videos": [str(a.path) for a in all_artifacts],
            "manifest": str(manifest_path),
        }
        ctx.shared.setdefault("video_gen", {}).update(outputs)

        stats = {
            "created": len(report.created),
            "reused": len(reused),
            "failed": len(report.failures),
            "total_mb": round(sum(a.size_bytes for a in all_artifacts) / (1024 * 1024), 1),
        }
        if report.workers_used:
            stats["workers"] = report.workers_used

        return ModuleResult(
            status=status,
            outputs={**outputs, "accounts": report.accounts},
            stats=stats,
            errors=report.failures,
        )

    def _write_manifest(
        self,
        ctx: ModuleContext,
        artifacts: list[VideoArtifact],
        report: RunReport,
        output_root: Path,
    ) -> Path:
        """Ghi biên bản đầy đủ của lần chạy này.

        Khác với `_state.json` (sổ cái tích luỹ qua mọi lần chạy), manifest là
        ảnh chụp của MỘT lần chạy -- để đối chiếu và bàn giao.
        """
        folder = ensure_dir(output_root / "_runs")
        path = folder / f"{ctx.run_id}.json"
        payload = {
            "run_id": ctx.run_id,
            "finished_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "backend": self.cfg.backend,
            "accounts": report.accounts,
            "artifacts": [
                {**a.model_dump(mode="json"), "path": str(a.path)} for a in artifacts
            ],
            "failures": report.failures,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        ctx.logger.info("Biên bản lần chạy: %s", path)
        return path

    def _dry_run_report(
        self,
        ctx: ModuleContext,
        specs: list[VideoSpec],
        pending: list[VideoSpec],
        accounts: list,
    ) -> ModuleResult:
        """In ra chính xác những gì SẼ chạy, không đụng tới trình duyệt hay API."""
        log = ctx.logger
        pending_ids = {s.id for s in pending}
        enabled = [a for a in accounts if a.enabled]
        workers = min(self.cfg.execution.max_parallel, len(enabled))

        log.info("Chạy thử (dry-run) -- không sinh video nào:")
        log.info(
            "  Sẽ dùng %d worker trên %d tài khoản: %s",
            workers, len(enabled), ", ".join(a.id for a in enabled),
        )
        for spec in specs:
            mark = "SẼ SINH " if spec.id in pending_ids else "bỏ qua  "
            log.info(
                "  %s %-24s %s %s %ss x%d",
                mark, spec.id, spec.model, spec.aspect_ratio,
                spec.duration_seconds, spec.outputs_per_prompt,
            )
            log.info("            prompt: %s", spec.prompt)
        return ModuleResult(
            status=ModuleStatus.SKIPPED,
            stats={
                "total": len(specs),
                "would_generate": len(pending),
                "workers": workers,
                "accounts": len(enabled),
            },
        )


def _abs(path: Path) -> Path:
    """Đường dẫn tương đối trong config luôn tính từ gốc project."""
    return path if path.is_absolute() else PROJECT_ROOT / path
