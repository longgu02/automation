"""Chạy một job đã dựng bằng giao diện sơ đồ.

    python scripts/run_job.py <tên-job>

Ví dụ:

    python scripts/run_job.py --list            # xem có những job nào
    python scripts/run_job.py demo --dry-run    # xem sẽ chạy gì, không tốn gì
    python scripts/run_job.py demo              # chạy thật

Job là một đồ thị: mỗi nút một module, mỗi cạnh nghĩa là "chạy sau". Bộ chạy
sắp thứ tự bằng sắp xếp tô-pô rồi chạy tuần tự, dùng chung một `ctx.shared` để
các module truyền dữ liệu cho nhau.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.errors import AutomationError  # noqa: E402
from core.job import JobDefinition, JobRunner  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.module import ModuleContext, ModuleStatus  # noqa: E402
from core.paths import CONFIG_DIR, OUTPUT_DIR, new_run_id  # noqa: E402

JOBS_DIR = CONFIG_DIR / "jobs"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chạy một job gồm nhiều module.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("job", nargs="?", help="Tên job trong config/jobs/ (không cần .yaml)")
    parser.add_argument("--list", action="store_true", help="Liệt kê job đã có rồi thoát.")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in ra sẽ chạy gì.")
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="Một module hỏng vẫn chạy tiếp các nút còn lại.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    if args.list:
        return _list_jobs()

    if not args.job:
        parser.print_help()
        print()
        return _list_jobs()

    run_id = new_run_id()
    log_path = setup_logging(args.log_level, run_id=run_id)
    log = logging.getLogger("job")
    log.info("Lần chạy %s -- nhật ký: %s", run_id, log_path)

    path = JOBS_DIR / f"{Path(args.job).stem}.yaml"
    if not path.exists():
        log.error("Không có job '%s' (đã tìm ở %s)", args.job, path)
        _list_jobs()
        return 2

    try:
        job = JobDefinition.load(path)
        if args.continue_on_error:
            job.continue_on_error = True

        ctx = ModuleContext(
            run_id=run_id, workdir=OUTPUT_DIR, logger=log, dry_run=args.dry_run
        )
        results = JobRunner(log).run(job, ctx)
    except AutomationError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Bị ngắt. Những module đã xong vẫn giữ nguyên kết quả.")
        return 130

    failed = [i for i, r in results.items() if r.status is ModuleStatus.FAILED]
    _print_summary(job, results, args.dry_run)
    return 1 if failed else 0


def _list_jobs() -> int:
    if not JOBS_DIR.exists() or not any(JOBS_DIR.glob("*.yaml")):
        print(f"Chưa có job nào trong {JOBS_DIR}.")
        print("Dựng một cái bằng giao diện sơ đồ:  python scripts/run_ui.py")
        return 0

    print(f"Job có trong {JOBS_DIR}:\n")
    for path in sorted(JOBS_DIR.glob("*.yaml")):
        try:
            job = JobDefinition.load(path)
            order = " → ".join(n.label or n.id for n in job.execution_order() if n.enabled)
            print(f"  {path.stem:<24} {len(job.nodes)} nút")
            if order:
                print(f"  {'':<24} {order}")
        except AutomationError as exc:
            print(f"  {path.stem:<24} (hỏng: {exc})")
    print()
    return 0


def _print_summary(job: JobDefinition, results: dict, dry_run: bool) -> None:
    by_id = {n.id: n for n in job.nodes}
    print()
    print("=" * 70)
    print(f"JOB: {job.name}")
    print("=" * 70)
    for node_id, result in results.items():
        label = by_id[node_id].label or node_id if node_id in by_id else node_id
        print(f"  {label:<26} {result.status.value:<9} {result.stats or ''}")
        for err in result.errors[:3]:
            print(f"  {'':<26} └─ {err[:90]}")
    if dry_run:
        print("\n  Đây là chạy thử. Bỏ --dry-run để chạy thật.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
