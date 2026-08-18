"""Bộ chạy nhiều tài khoản: dự phòng khi hết credit + chạy song song.

MÔ HÌNH: một hàng đợi prompt dùng chung, N worker. Mỗi worker giữ **đúng một
tài khoản** và một trình duyệt riêng của nó, rút prompt từ hàng đợi cho tới khi
hết việc.

    hàng đợi prompt  ──┬──> worker 1  (tài khoản acc1, Chrome #1)
    [p1 p2 p3 p4 …]    ├──> worker 2  (tài khoản acc2, Chrome #2)
                       └──> worker 3  (tài khoản acc3, Chrome #3)

VÌ SAO SONG SONG THEO TÀI KHOẢN, KHÔNG PHẢI THEO PROMPT: phía Google mỗi tài
khoản chỉ có MỘT hàng đợi render. Bắn nhiều prompt cùng lúc vào một tài khoản
không nhanh hơn, mà còn khiến việc ghép "clip nào của prompt nào" trở nên bất
định. Hai tài khoản khác nhau mới là hai hàng đợi thật sự độc lập.

KHI MỘT TÀI KHOẢN HẾT CREDIT -- đây là phần đáng đọc kỹ nhất:

    1. Tài khoản bị đánh dấu EXHAUSTED, loại khỏi lần chạy này.
    2. Prompt đang dở được **trả lại hàng đợi**, không mất, không tính là hỏng.
    3. Worker đóng trình duyệt, xin một tài khoản khác rồi chạy tiếp.
    4. Không còn tài khoản nào -> worker dừng. Prompt còn sót lại được báo cáo
       trung thực là chưa làm được, kèm lý do.

Một prompt chỉ được chuyển tài khoản tối đa `max_account_switches_per_spec` lần
(mặc định: bằng số tài khoản) -- để một prompt hỏng vì lý do riêng của nó không
đi vòng quanh mọi tài khoản mãi mãi.

VỀ LUỒNG VÀ PLAYWRIGHT: bản đồng bộ của Playwright không dùng chung được giữa
các luồng. Nên mỗi worker tự tạo backend, tự khởi động Playwright của riêng nó,
và không bao giờ chạm vào đối tượng của worker khác. Ba thứ dùng chung duy nhất
-- hàng đợi, kho tài khoản, sổ trạng thái -- đều có khoá riêng.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from core.accounts import Account, AccountPool
from core.errors import AuthError, ConfigError, QuotaExhausted
from core.paths import ensure_dir
from core.retry import retry_call
from modules.video_gen.backends import VideoBackend, create_backend
from modules.video_gen.config import VideoGenConfig
from modules.video_gen.models import VideoArtifact, VideoSpec
from modules.video_gen.state import StateStore

log = logging.getLogger(__name__)


# ==========================================================================
# Hàng đợi prompt dùng chung
# ==========================================================================
class SpecQueue:
    """Hàng đợi prompt an toàn đa luồng, cho phép trả lại việc chưa làm được."""

    def __init__(self, specs: list[VideoSpec], max_switches_per_spec: int):
        self._pending: deque[VideoSpec] = deque(specs)
        self._switches: dict[str, int] = defaultdict(int)
        self._max_switches = max_switches_per_spec
        self._lock = threading.Lock()

    def take(self) -> VideoSpec | None:
        """Lấy prompt kế tiếp, hoặc None khi hết việc."""
        with self._lock:
            return self._pending.popleft() if self._pending else None

    def requeue(self, spec: VideoSpec) -> bool:
        """Trả prompt về hàng đợi để tài khoản khác làm.

        Trả False nếu prompt này đã đổi tài khoản quá số lần cho phép -- lúc đó
        lớp gọi phải ghi nhận nó là thất bại thay vì đẩy đi vòng nữa.
        """
        with self._lock:
            self._switches[spec.id] += 1
            if self._switches[spec.id] > self._max_switches:
                return False
            self._pending.append(spec)
            return True

    def leftovers(self) -> list[VideoSpec]:
        """Những prompt còn nằm lại khi mọi worker đã dừng."""
        with self._lock:
            return list(self._pending)


# ==========================================================================
class _Verdict(Enum):
    """Kết cục của một prompt, dưới góc nhìn của worker."""

    DONE = auto()  # xong -- tiếp tục với cùng tài khoản
    SPEC_FAILED = auto()  # prompt này hỏng, tài khoản vẫn tốt -- chạy tiếp
    ACCOUNT_DEAD = auto()  # tài khoản chết -- phải đổi tài khoản


@dataclass
class RunReport:
    """Kết quả của cả lần chạy, trả về cho module."""

    created: list[VideoArtifact] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    accounts: dict = field(default_factory=dict)
    workers_used: int = 0


# ==========================================================================
class MultiAccountRunner:
    """Điều phối worker, tài khoản và hàng đợi cho một lần chạy."""

    def __init__(
        self,
        cfg: VideoGenConfig,
        pool: AccountPool,
        state: StateStore,
        output_root: Path,
        logger: logging.Logger,
    ):
        self.cfg = cfg
        self.pool = pool
        self.state = state
        self.output_root = output_root
        self.log = logger
        self.policy = cfg.retry.to_policy()

        self.queue = SpecQueue([], 1)  # thay bằng hàng đợi thật trong run()
        self._results_lock = threading.Lock()
        self._created: list[VideoArtifact] = []
        self._failures: list[str] = []
        self._stop = threading.Event()  # bật khi người dùng nhấn Ctrl+C

    # ------------------------------------------------------------------
    def run(self, specs: list[VideoSpec]) -> RunReport:
        max_switches = (
            self.cfg.execution.max_account_switches_per_spec or self.pool.total_count()
        )
        self.queue = SpecQueue(specs, max_switches)

        workers = min(self.cfg.execution.max_parallel, self.pool.total_count())
        self.log.info(
            "Chạy %d prompt bằng %d worker / %d tài khoản.",
            len(specs), workers, self.pool.total_count(),
        )

        try:
            if workers == 1:
                # Một worker thì chạy thẳng ở luồng chính: Ctrl+C phản hồi ngay,
                # traceback sạch, không thêm tầng thread nào cho không.
                self._worker(1)
            else:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vgen") as pool:
                    futures = [pool.submit(self._worker, i + 1) for i in range(workers)]
                    for future in futures:
                        future.result()  # ném lại lỗi bất ngờ của worker (nếu có)
        except KeyboardInterrupt:
            self._stop.set()
            self.log.warning("Bị ngắt -- đang dừng các worker. Phần đã xong vẫn được giữ.")
            raise

        self._report_leftovers()
        return RunReport(
            created=list(self._created),
            failures=list(self._failures),
            accounts=self.pool.summary(),
            workers_used=workers,
        )

    # ------------------------------------------------------------------
    def _worker(self, index: int) -> None:
        """Vòng đời một worker: giữ một tài khoản, làm tới khi hết việc.

        Đổi tài khoản CHỈ xảy ra khi tài khoản đang giữ chết -- mở trình duyệt
        tốn khoảng 5-10 giây nên ta không đổi vô cớ.
        """
        wlog = logging.getLogger(f"video_gen.w{index}")
        account = self.pool.lease()
        backend: VideoBackend | None = None

        if account is None:
            wlog.debug("Không còn tài khoản rảnh -- worker %d không khởi động.", index)
            return

        while account is not None and not self._stop.is_set():
            if backend is None:
                backend = self._open_backend(account, wlog)
                if backend is None:  # tài khoản chết ngay lúc mở
                    account = self.pool.lease()
                    continue

            spec = self.queue.take()
            if spec is None:
                break  # hết việc

            verdict = self._process(spec, backend, account, wlog)

            if verdict is _Verdict.ACCOUNT_DEAD:
                self._close(backend, wlog)
                backend = None
                account = self.pool.lease()
                if account is None:
                    wlog.error("Không còn tài khoản nào dùng được -- worker %d dừng.", index)
                else:
                    wlog.info("Worker %d chuyển sang tài khoản %s.", index, account.id)
                continue

            delay = self.cfg.execution.delay_between_specs_s
            if delay > 0 and not self._stop.is_set():
                time.sleep(delay)

        if backend is not None:
            self._close(backend, wlog)
        if account is not None:
            self.pool.release(account)

    # ------------------------------------------------------------------
    def _open_backend(self, account: Account, wlog: logging.Logger) -> VideoBackend | None:
        """Mở backend cho một tài khoản. None = tài khoản này không dùng được."""
        backend = create_backend(self.cfg, account, logging.getLogger(f"video_gen.{account.id}"))
        try:
            backend.open()
        except (AuthError, ConfigError) as exc:
            # Chưa đăng nhập / thiếu API key: hỏng vĩnh viễn với tài khoản này,
            # nhưng các tài khoản khác vẫn chạy bình thường.
            self.pool.mark_failed(account, str(exc))
            self._close(backend, wlog)
            return None
        except Exception as exc:  # noqa: BLE001
            self.pool.mark_failed(account, f"không mở được backend: {exc}")
            self._close(backend, wlog)
            return None

        wlog.info("Sẵn sàng: %s", backend.describe())
        return backend

    def _close(self, backend: VideoBackend, wlog: logging.Logger) -> None:
        try:
            backend.close()
        except Exception as exc:  # noqa: BLE001
            wlog.debug("Bỏ qua lỗi khi đóng backend: %s", exc)

    # ------------------------------------------------------------------
    def _process(
        self, spec: VideoSpec, backend: VideoBackend, account: Account, wlog: logging.Logger
    ) -> _Verdict:
        """Sinh video cho một prompt và quyết định điều gì xảy ra tiếp theo."""
        fingerprint = spec.fingerprint(self.cfg.backend)
        dest = ensure_dir(self.output_root / spec.id)
        started = time.monotonic()

        try:
            artifacts = retry_call(
                lambda: backend.generate(spec, dest),
                self.policy,
                label=f"sinh video '{spec.id}' bằng {account.id}",
                logger=wlog,
            )
        except QuotaExhausted as exc:
            self.pool.mark_exhausted(account, str(exc))
            return self._hand_back(spec, fingerprint, account, f"hết credit: {exc}", wlog)
        except AuthError as exc:
            self.pool.mark_failed(account, str(exc))
            return self._hand_back(spec, fingerprint, account, f"lỗi đăng nhập: {exc}", wlog)
        except Exception as exc:  # noqa: BLE001
            # Prompt này hỏng nhưng tài khoản vẫn tốt (prompt bị chặn, hết giờ
            # render, selector vỡ...). Ghi nhận rồi chạy tiếp prompt sau.
            self._record_failure(spec, fingerprint, f"[{account.id}] {exc}")
            wlog.error("[%s] thất bại: %s", spec.id, exc, exc_info=wlog.level <= logging.DEBUG)
            return _Verdict.SPEC_FAILED

        self.state.mark_done(fingerprint, spec.id, artifacts)
        self.pool.record_success(account)
        with self._results_lock:
            self._created.extend(artifacts)
        wlog.info(
            "[%s] xong bằng %s: %d file trong %.0fs.",
            spec.id, account.id, len(artifacts), time.monotonic() - started,
        )
        return _Verdict.DONE

    def _hand_back(
        self,
        spec: VideoSpec,
        fingerprint: str,
        account: Account,
        reason: str,
        wlog: logging.Logger,
    ) -> _Verdict:
        """Tài khoản chết: trả prompt lại hàng đợi cho tài khoản khác làm."""
        if self.queue.requeue(spec):
            wlog.warning(
                "[%s] %s không dùng được (%s) -- prompt được trả lại hàng đợi.",
                spec.id, account.id, reason,
            )
        else:
            self._record_failure(
                spec, fingerprint,
                f"đã thử hết số tài khoản cho phép mà vẫn không sinh được. Lỗi cuối: {reason}",
            )
        return _Verdict.ACCOUNT_DEAD

    def _record_failure(self, spec: VideoSpec, fingerprint: str, message: str) -> None:
        self.state.mark_failed(fingerprint, spec.id, message)
        with self._results_lock:
            self._failures.append(f"{spec.id}: {message}")

    def _report_leftovers(self) -> None:
        """Prompt còn nằm trong hàng đợi khi mọi worker đã dừng.

        Xảy ra khi hết sạch tài khoản dùng được. Báo cáo thẳng thay vì im lặng
        -- người dùng cần biết chính xác cái gì chưa làm và vì sao.
        """
        for spec in self.queue.leftovers():
            reason = "không còn tài khoản nào dùng được (hết credit hoặc chưa đăng nhập)"
            self._record_failure(spec, spec.fingerprint(self.cfg.backend), reason)
            self.log.error("[%s] chưa chạy: %s", spec.id, reason)
