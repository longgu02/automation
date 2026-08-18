"""Kiểm thử bộ chạy nhiều tài khoản, dùng backend giả.

VÌ SAO ĐÁNG TEST: cơ chế "hết credit thì chuyển tài khoản" chỉ lộ ra khi tài
khoản thật sự hết credit -- thứ bạn không thể chờ để kiểm chứng bằng tay, và
lúc nó xảy ra thật thì đã đang chạy dở một batch lớn. Backend giả cho phép dựng
đúng tình huống đó trong 0,1 giây.

Backend giả tuân thủ nguyên hợp đồng `VideoBackend`, nên những gì test ở đây là
logic điều phối thật, không phải một bản mô phỏng.

    python -m pytest tests/test_runner.py -v
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.accounts import Account, AccountHealth, AccountPool  # noqa: E402
from core.errors import AuthError, ContentBlocked, QuotaExhausted  # noqa: E402
from modules.video_gen.backends.base import VideoBackend  # noqa: E402
from modules.video_gen.config import VideoGenConfig  # noqa: E402
from modules.video_gen.models import VideoArtifact, VideoSpec  # noqa: E402
from modules.video_gen import runner as runner_module  # noqa: E402
from modules.video_gen.runner import MultiAccountRunner  # noqa: E402
from modules.video_gen.state import StateStore  # noqa: E402


# ===========================================================================
# Backend giả
# ===========================================================================
class FakeBackend(VideoBackend):
    """Sinh file mp4 giả, và hết credit sau đúng N prompt.

    `quota` = số prompt tài khoản này làm được trước khi hết credit.
    """

    name = "fake"

    def __init__(self, account: Account, quota: int, fail_open: bool = False, blocked: set | None = None):
        super().__init__(account, logging.getLogger("test"))
        self.quota = quota
        self.fail_open = fail_open
        self.blocked = blocked or set()
        self.done = 0

    def open(self) -> None:
        if self.fail_open:
            raise AuthError(f"{self.account.id} chưa đăng nhập")

    def generate(self, spec: VideoSpec, dest_dir: Path) -> list[VideoArtifact]:
        if spec.id in self.blocked:
            raise ContentBlocked(f"prompt '{spec.id}' bị chặn")
        if self.done >= self.quota:
            raise QuotaExhausted(f"{self.account.id} hết credit")
        self.done += 1
        path = dest_dir / f"{spec.id}_01.mp4"
        path.write_bytes(b"fake mp4 data")
        return [VideoArtifact.from_file(spec, 1, path, self.name, account=self.account.id)]


@pytest.fixture()
def wire_fake_backend(monkeypatch):
    """Thay `create_backend` bằng nhà máy sinh backend giả.

    Trả về một hàm nhận bảng cấu hình theo từng tài khoản:
        wire({"acc1": {"quota": 1}, "acc2": {"quota": 99}})
    """

    def wire(plan: dict[str, dict]) -> dict[str, FakeBackend]:
        created: dict[str, FakeBackend] = {}

        def factory(cfg, account, logger=None):
            settings = plan.get(account.id, {})
            backend = FakeBackend(
                account,
                quota=settings.get("quota", 99),
                fail_open=settings.get("fail_open", False),
                blocked=settings.get("blocked"),
            )
            created[account.id] = backend
            return backend

        monkeypatch.setattr(runner_module, "create_backend", factory)
        return created

    return wire


def _cfg(tmp_path: Path, max_parallel: int = 1) -> VideoGenConfig:
    cfg = VideoGenConfig()
    cfg.execution.max_parallel = max_parallel
    cfg.execution.delay_between_specs_s = 0  # test không cần nghỉ
    cfg.retry.attempts = 1  # không retry: test đo logic điều phối, không đo backoff
    return cfg


def _run(tmp_path: Path, accounts: list[Account], specs: list[VideoSpec], cfg: VideoGenConfig):
    pool = AccountPool(accounts)
    state = StateStore(tmp_path / "_state.json")
    runner = MultiAccountRunner(cfg, pool, state, tmp_path / "out", logging.getLogger("test"))
    return runner.run(specs), pool


def _specs(*ids: str) -> list[VideoSpec]:
    return [VideoSpec(id=i, prompt=f"prompt {i}") for i in ids]


# ===========================================================================
# Dự phòng khi hết credit -- lý do chính của cả tính năng này
# ===========================================================================
def test_het_credit_thi_prompt_duoc_tai_khoan_khac_lam_tiep(tmp_path, wire_fake_backend) -> None:
    """acc1 chỉ làm được 1 prompt. 3 prompt vẫn phải xong đủ nhờ acc2."""
    backends = wire_fake_backend({"acc1": {"quota": 1}, "acc2": {"quota": 99}})
    accounts = [Account(id="acc1"), Account(id="acc2")]

    report, pool = _run(tmp_path, accounts, _specs("p1", "p2", "p3"), _cfg(tmp_path))

    assert len(report.created) == 3, "Không được mất prompt nào khi đổi tài khoản"
    assert report.failures == []
    assert backends["acc1"].done == 1
    assert backends["acc2"].done == 2
    assert pool.summary()["acc1"]["health"] == AccountHealth.EXHAUSTED.value
    assert pool.summary()["acc2"]["health"] in ("ready", "in_use")


def test_prompt_dang_do_khong_bi_tinh_la_that_bai(tmp_path, wire_fake_backend) -> None:
    """Prompt đúng lúc gặp tường credit phải được làm lại, không phải bị bỏ."""
    wire_fake_backend({"acc1": {"quota": 0}, "acc2": {"quota": 99}})
    accounts = [Account(id="acc1"), Account(id="acc2")]

    report, _ = _run(tmp_path, accounts, _specs("p1"), _cfg(tmp_path))

    assert report.failures == []
    assert len(report.created) == 1
    assert report.created[0].spec_id == "p1"


def test_het_sach_tai_khoan_thi_bao_cao_trung_thuc(tmp_path, wire_fake_backend) -> None:
    """Không còn tài khoản nào: phần chưa làm phải được nêu rõ, không im lặng."""
    wire_fake_backend({"acc1": {"quota": 1}, "acc2": {"quota": 1}})
    accounts = [Account(id="acc1"), Account(id="acc2")]

    report, pool = _run(tmp_path, accounts, _specs("p1", "p2", "p3", "p4"), _cfg(tmp_path))

    assert len(report.created) == 2
    assert len(report.failures) == 2, "Hai prompt còn lại phải xuất hiện trong báo cáo"
    assert all("tài khoản" in f for f in report.failures)
    assert pool.alive_count() == 0


def test_tai_khoan_chua_dang_nhap_bi_loai_khong_lam_hong_ca_run(tmp_path, wire_fake_backend) -> None:
    wire_fake_backend({"acc1": {"fail_open": True}, "acc2": {"quota": 99}})
    accounts = [Account(id="acc1"), Account(id="acc2")]

    report, pool = _run(tmp_path, accounts, _specs("p1", "p2"), _cfg(tmp_path))

    assert len(report.created) == 2
    assert report.failures == []
    assert pool.summary()["acc1"]["health"] == AccountHealth.FAILED.value


def test_prompt_bi_chan_khong_lam_chet_tai_khoan(tmp_path, wire_fake_backend) -> None:
    """Phân biệt 'prompt hỏng' với 'tài khoản hỏng' -- hai xử lý khác hẳn nhau."""
    backends = wire_fake_backend({"acc1": {"quota": 99, "blocked": {"p2"}}})
    accounts = [Account(id="acc1")]

    report, pool = _run(tmp_path, accounts, _specs("p1", "p2", "p3"), _cfg(tmp_path))

    assert len(report.created) == 2, "p1 và p3 vẫn phải xong"
    assert len(report.failures) == 1
    assert "p2" in report.failures[0]
    assert pool.summary()["acc1"]["health"] in ("ready", "in_use"), "Tài khoản vẫn phải còn dùng được"
    assert backends["acc1"].done == 2


# ===========================================================================
# Chạy song song
# ===========================================================================
def test_song_song_chia_viec_cho_nhieu_tai_khoan(tmp_path, wire_fake_backend) -> None:
    backends = wire_fake_backend({"acc1": {"quota": 99}, "acc2": {"quota": 99}})
    accounts = [Account(id="acc1"), Account(id="acc2")]

    report, _ = _run(tmp_path, accounts, _specs("p1", "p2", "p3", "p4"), _cfg(tmp_path, max_parallel=2))

    assert len(report.created) == 4
    assert report.failures == []
    assert report.workers_used == 2
    # Cả hai tài khoản đều phải có phần việc -- nếu một cái làm hết thì việc
    # song song hoá đã không xảy ra.
    assert backends["acc1"].done > 0 and backends["acc2"].done > 0
    assert backends["acc1"].done + backends["acc2"].done == 4


def test_so_worker_khong_vuot_qua_so_tai_khoan(tmp_path, wire_fake_backend) -> None:
    """Xin 5 worker nhưng chỉ có 2 tài khoản -> chạy 2. Thêm worker không giúp
    gì vì phía Google mỗi tài khoản chỉ có một hàng đợi render."""
    wire_fake_backend({"acc1": {"quota": 99}, "acc2": {"quota": 99}})
    accounts = [Account(id="acc1"), Account(id="acc2")]

    report, _ = _run(tmp_path, accounts, _specs("p1", "p2"), _cfg(tmp_path, max_parallel=5))

    assert report.workers_used == 2


def test_song_song_ghi_so_trang_thai_khong_dam_len_nhau(tmp_path, wire_fake_backend) -> None:
    """Nhiều luồng cùng ghi một file trạng thái -- không được mất bản ghi nào."""
    wire_fake_backend({f"acc{i}": {"quota": 99} for i in range(1, 4)})
    accounts = [Account(id=f"acc{i}") for i in range(1, 4)]
    specs = _specs(*[f"p{i}" for i in range(1, 13)])
    cfg = _cfg(tmp_path, max_parallel=3)

    pool = AccountPool(accounts)
    state = StateStore(tmp_path / "_state.json")
    runner = MultiAccountRunner(cfg, pool, state, tmp_path / "out", logging.getLogger("test"))
    report = runner.run(specs)

    assert len(report.created) == 12
    reloaded = StateStore(tmp_path / "_state.json")
    done = [s for s in specs if reloaded.is_done(s.fingerprint(cfg.backend))]
    assert len(done) == 12, "Mọi prompt phải có mặt trong sổ trạng thái sau khi chạy song song"
