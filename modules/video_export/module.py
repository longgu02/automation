"""VideoExportModule -- gom các clip rời thành MỘT file video hoàn chỉnh.

Vị trí trong hệ thống: đây là module thứ hai, chạy sau `video_gen`.

    video_gen  ──> ctx.shared["video_gen"]["videos"]  ──> video_export ──> 1 file mp4

Nó chạy được ở cả hai tình huống, và đó là một yêu cầu thiết kế chứ không phải
tiện thể:

  * TRONG MỘT JOB -- nhận danh sách clip từ `ctx.shared`, giữ nguyên thứ tự
    module trước đưa sang (tức thứ tự bạn khai prompt = thứ tự cảnh).
  * CHẠY ĐỘC LẬP -- quét thư mục theo mẫu glob trong config. Dùng khi bạn chỉ
    muốn ghép lại đống video đã có sẵn.

Năm bước, đúng theo thứ tự:

    1. Gom clip     -- từ ctx.shared hoặc quét đĩa, rồi sắp thứ tự
    2. Đo           -- ffprobe từng file (`ffmpeg.py`)
    3. Lập kế hoạch -- quyết nối byte hay chuẩn hoá (`plan.py`, hàm thuần tuý)
    4. Thi hành     -- chạy ffmpeg, dọn file tạm
    5. Bàn giao     -- trả đường dẫn file cuối qua ctx.shared cho module sau
"""

from __future__ import annotations

import datetime as _dt
import glob
import shutil
import time
from pathlib import Path

from core.errors import ConfigError, FatalError
from core.module import BaseModule, ModuleContext, ModuleResult, ModuleStatus
from core.paths import PROJECT_ROOT, ensure_dir
from modules.video_export.config import VideoExportConfig
from modules.video_export.ffmpeg import FfmpegRunner, write_concat_list
from modules.video_export.models import ClipInfo, ExportPlan
from modules.video_export.plan import build_plan


class VideoExportModule(BaseModule):
    name = "video_export"

    def __init__(self, cfg: VideoExportConfig, *, output_override: str | None = None):
        self.cfg = cfg
        self.output_override = output_override

    # ===================================================================
    def run(self, ctx: ModuleContext) -> ModuleResult:
        log = ctx.logger

        # --- 1. Gom clip --------------------------------------------------
        clip_paths = self._collect(ctx)
        if len(clip_paths) < self.cfg.min_clips:
            log.warning(
                "Chỉ tìm được %d clip, cần tối thiểu %d -- không có gì để xuất.",
                len(clip_paths), self.cfg.min_clips,
            )
            return ModuleResult(
                status=ModuleStatus.SKIPPED, stats={"clips": len(clip_paths)}
            )
        log.info("Sẽ ghép %d clip theo thứ tự:", len(clip_paths))
        for index, path in enumerate(clip_paths, start=1):
            log.info("  %2d. %s", index, path.name)

        # --- 2. Đo --------------------------------------------------------
        runner = FfmpegRunner(
            binary=self.cfg.ffmpeg.binary,
            probe_binary=self.cfg.ffmpeg.ffprobe_binary,
            timeout_s=self.cfg.ffmpeg.timeout_s,
            log_command=self.cfg.ffmpeg.log_command,
        )
        log.debug("ffmpeg: %s", runner.version())

        clips: list[ClipInfo] = []
        for path in clip_paths:
            info = runner.probe(path)
            log.debug("  %s", info.describe())
            clips.append(info)

        # --- 3. Lập kế hoạch ----------------------------------------------
        output = self._resolve_output(ctx, len(clips))
        temp_dir = _abs(self.cfg.output.temp_dir) / ctx.run_id
        plan = build_plan(clips, self.cfg, output, temp_dir)

        log.info("Kế hoạch: %s", plan.reason)
        log.info(
            "Đầu ra: %s  (~%.1fs, %d clip)",
            output, plan.total_duration_s, len(clips),
        )

        if ctx.dry_run:
            return self._dry_run_report(ctx, plan, clips)

        # --- 4. Thi hành --------------------------------------------------
        started = time.monotonic()
        try:
            self._execute(plan, runner, temp_dir, ctx)
        finally:
            if not self.cfg.output.keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                log.info("Giữ lại file tạm: %s", temp_dir)

        elapsed = time.monotonic() - started
        size_mb = output.stat().st_size / (1024 * 1024)
        log.info("Xong sau %.0fs -- %s (%.1f MB)", elapsed, output, size_mb)

        # --- 5. Bàn giao --------------------------------------------------
        outputs = {"final_video": str(output), "clip_count": len(clips)}
        ctx.shared.setdefault("video_export", {}).update(outputs)

        return ModuleResult(
            status=ModuleStatus.SUCCESS,
            outputs=outputs,
            stats={
                "clips": len(clips),
                "duration_s": round(plan.total_duration_s, 1),
                "size_mb": round(size_mb, 1),
                "reencoded": plan.needs_reencode,
                "elapsed_s": round(elapsed),
            },
        )

    # ===================================================================
    # 1. Gom clip
    # ===================================================================
    def _collect(self, ctx: ModuleContext) -> list[Path]:
        """Lấy danh sách clip và sắp đúng thứ tự ghép."""
        from_pipeline = self._from_shared(ctx)

        if from_pipeline:
            ctx.logger.info("Nguồn clip: module trước trong job (%d file).", len(from_pipeline))
            paths = from_pipeline
            pipeline_order = True
        else:
            paths = self._from_scan(ctx)
            if not paths:
                return []
            ctx.logger.info("Nguồn clip: quét đĩa theo %s (%d file).", self.cfg.sources.scan, len(paths))
            pipeline_order = False

        paths = [p for p in paths if not self._excluded(p)]
        return self._apply_order(paths, pipeline_order, ctx)

    def _from_shared(self, ctx: ModuleContext) -> list[Path]:
        """Danh sách clip do module trước đưa qua, nếu có."""
        if not self.cfg.sources.from_shared:
            return []
        videos = (ctx.shared.get("video_gen") or {}).get("videos") or []
        # File có thể đã bị xoá tay giữa hai module -- lọc bỏ, đừng để ffprobe
        # báo lỗi khó hiểu ở tận bước sau.
        existing = [Path(v) for v in videos if Path(v).exists()]
        if len(existing) < len(videos):
            ctx.logger.warning(
                "%d/%d clip module trước báo có nhưng không còn trên đĩa -- bỏ qua.",
                len(videos) - len(existing), len(videos),
            )
        return existing

    def _from_scan(self, ctx: ModuleContext) -> list[Path]:
        """Quét đĩa theo các mẫu glob trong config.

        Mẫu tương đối tính từ gốc project; mẫu tuyệt đối (kể cả ổ đĩa khác) cũng
        dùng được. Phải tách hai nhánh vì `Path.glob()` của Python 3.12 từ chối
        mẫu tuyệt đối.
        """
        found: list[Path] = []
        seen: set[Path] = set()
        for pattern in self.cfg.sources.scan:
            if Path(pattern).is_absolute():
                matches = [Path(p) for p in glob.glob(pattern, recursive=True)]
            else:
                matches = list(PROJECT_ROOT.glob(pattern))
            for path in sorted(matches):
                resolved = path.resolve()
                if path.is_file() and resolved not in seen:
                    seen.add(resolved)
                    found.append(path)
        if not found:
            # Trong lúc chạy thử, "chưa có clip nào" là chuyện BÌNH THƯỜNG: module
            # trước cũng đang chạy thử nên chưa sinh ra file thật. Cho nổ ở đây
            # khiến `--dry-run` trên một job nối chuỗi luôn hỏng ở bước thứ hai,
            # tức là biến tính năng xem trước thành vô dụng.
            if ctx.dry_run:
                ctx.logger.info(
                    "Chưa có clip nào khớp %s -- bình thường khi chạy thử, "
                    "vì module trước cũng chưa sinh file thật.",
                    self.cfg.sources.scan,
                )
                return []

            # Chỉ nói "tính từ gốc project" khi điều đó thật sự đúng -- mẫu tuyệt
            # đối không liên quan gì tới gốc project, nhắc vào chỉ gây hiểu lầm.
            has_relative = any(not Path(p).is_absolute() for p in self.cfg.sources.scan)
            note = f" (mẫu tương đối tính từ {PROJECT_ROOT})" if has_relative else ""
            raise ConfigError(
                f"Không tìm thấy clip nào khớp {self.cfg.sources.scan}{note}.\n"
                "  - Chạy video_gen trước, hoặc sửa `sources.scan` trong config/video_export.yaml."
            )
        return found

    def _excluded(self, path: Path) -> bool:
        text = str(path).replace("\\", "/")
        return any(token in text for token in self.cfg.sources.exclude)

    def _apply_order(
        self, paths: list[Path], pipeline_order: bool, ctx: ModuleContext
    ) -> list[Path]:
        """Sắp thứ tự ghép -- đây chính là thứ tự cảnh trong video cuối."""
        order = self.cfg.sources.order

        if order == "explicit":
            return self._order_explicit(paths, ctx)

        if order == "filename":
            return sorted(paths, key=lambda p: p.name.lower())

        # order == "pipeline"
        if pipeline_order:
            return paths  # giữ nguyên thứ tự module trước đưa sang
        # Quét đĩa thì không có "thứ tự pipeline" nào cả -- rơi về tên file, và
        # nói rõ ra thay vì im lặng đổi hành vi.
        ctx.logger.info("order = 'pipeline' nhưng đang quét đĩa -> sắp theo tên file.")
        return sorted(paths, key=lambda p: p.name.lower())

    def _order_explicit(self, paths: list[Path], ctx: ModuleContext) -> list[Path]:
        """Sắp theo danh sách khai tay. Mỗi mục khớp theo chuỗi con của tên file."""
        remaining = list(paths)
        ordered: list[Path] = []

        for token in self.cfg.sources.order_explicit:
            matched = [p for p in remaining if token.lower() in p.name.lower()]
            if not matched:
                ctx.logger.warning("order_explicit: '%s' không khớp clip nào.", token)
                continue
            for path in matched:
                ordered.append(path)
                remaining.remove(path)

        if remaining:
            ctx.logger.warning(
                "%d clip không có trong order_explicit nên bị loại: %s",
                len(remaining), ", ".join(p.name for p in remaining),
            )
        if not ordered:
            raise ConfigError("order_explicit không khớp được clip nào.")
        return ordered

    # ===================================================================
    # 4. Thi hành
    # ===================================================================
    def _execute(
        self, plan: ExportPlan, runner: FfmpegRunner, temp_dir: Path, ctx: ModuleContext
    ) -> None:
        log = ctx.logger
        ensure_dir(plan.output.parent)

        for index, job in enumerate(plan.normalize, start=1):
            log.info(
                "Chuẩn hoá %d/%d: %s (%s)",
                index, len(plan.normalize), job.source.name, job.reason,
            )
            ensure_dir(job.dest.parent)
            runner.run(job.args, label=f"chuẩn hoá {job.source.name}")

        list_file = write_concat_list(plan.concat_inputs, temp_dir / "concat_list.txt")
        # Kế hoạch giữ chỗ bằng {list}/{output} để nó vẫn là dữ liệu thuần tuý,
        # không phụ thuộc đường dẫn thật -- nhờ vậy `plan.py` test được dễ dàng.
        args = [
            str(list_file) if a == "{list}" else str(plan.output) if a == "{output}" else a
            for a in plan.concat_args
        ]
        log.info("Ghép %d clip thành một file...", len(plan.concat_inputs))
        runner.run(args, label="ghép clip")

        if not plan.output.exists() or plan.output.stat().st_size == 0:
            raise FatalError(f"ffmpeg báo thành công nhưng {plan.output} rỗng hoặc không có.")

    # ===================================================================
    # Tiện ích
    # ===================================================================
    def _resolve_output(self, ctx: ModuleContext, clip_count: int) -> Path:
        """Tính đường dẫn file đích, xử lý trường hợp file đã tồn tại."""
        template = self.output_override or self.cfg.output.path
        text = template.format(
            date=_dt.datetime.now().strftime("%Y%m%d"),
            run_id=ctx.run_id,
            count=clip_count,
        )
        path = _abs(Path(text))

        if not path.exists():
            return path

        mode = self.cfg.output.on_exists
        if mode == "overwrite":
            ctx.logger.warning("Ghi đè file đã có: %s", path)
            return path
        if mode == "error":
            raise ConfigError(
                f"File đích đã tồn tại: {path}\n"
                "  Đổi `output.on_exists` thành 'suffix' hoặc 'overwrite', hoặc xoá file cũ."
            )

        # mode == "suffix" -- mặc định, không bao giờ làm mất dữ liệu cũ
        for counter in range(2, 1000):
            candidate = path.with_name(f"{path.stem}_{counter:02d}{path.suffix}")
            if not candidate.exists():
                ctx.logger.info("File đích đã có -> ghi ra %s", candidate.name)
                return candidate
        raise ConfigError(f"Quá nhiều file trùng tên quanh {path}. Dọn bớt thư mục đích.")

    def _dry_run_report(
        self, ctx: ModuleContext, plan: ExportPlan, clips: list[ClipInfo]
    ) -> ModuleResult:
        """In ra đúng những lệnh ffmpeg SẼ chạy, không chạy cái nào."""
        log = ctx.logger
        log.info("Chạy thử (dry-run) -- không gọi ffmpeg:")
        for clip in clips:
            log.info("  đo được: %s", clip.describe())

        if plan.normalize:
            log.info("  %d lệnh chuẩn hoá, ví dụ lệnh đầu tiên:", len(plan.normalize))
            log.info("    ffmpeg %s", " ".join(plan.normalize[0].args))
        else:
            log.info("  Không cần chuẩn hoá -- nối byte thẳng.")
        log.info("  Lệnh ghép: ffmpeg %s", " ".join(plan.concat_args))

        return ModuleResult(
            status=ModuleStatus.SKIPPED,
            stats={
                "clips": len(clips),
                "would_reencode": plan.needs_reencode,
                "canvas": str(plan.canvas),
                "duration_s": round(plan.total_duration_s, 1),
            },
        )


def _abs(path: Path) -> Path:
    """Đường dẫn tương đối trong config luôn tính từ gốc project."""
    return path if path.is_absolute() else PROJECT_ROOT / path
