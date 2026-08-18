"""Kiểm thử kho tài khoản và hàng đợi prompt.

Đây là phần dễ sai nhất trong cả module -- nhiều luồng, trạng thái dùng chung,
và chuyện đổi tài khoản giữa chừng. Nhưng cũng là phần test được sạch sẽ vì nó
hoàn toàn không đụng tới trình duyệt.

    python -m pytest tests/test_accounts.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.accounts import Account, AccountHealth, AccountPool, load_accounts  # noqa: E402
from core.errors import ConfigError  # noqa: E402
from modules.video_gen.models import VideoSpec  # noqa: E402
from modules.video_gen.runner import SpecQueue  # noqa: E402


def _acc(account_id: str, **kwargs) -> Account:
    return Account(id=account_id, **kwargs)


# ===========================================================================
# AccountPool
# ===========================================================================
def test_moi_tai_khoan_chi_duoc_mot_worker_giu() -> None:
    """Bất biến cốt lõi: một tài khoản = một hàng đợi render = một worker."""
    pool = AccountPool([_acc("a"), _acc("b")])

    first = pool.lease()
    second = pool.lease()
    third = pool.lease()

    assert {first.id, second.id} == {"a", "b"}
    assert third is None, "Không được cấp cùng một tài khoản cho hai worker"


def test_tra_tai_khoan_ve_thi_muon_lai_duoc() -> None:
    pool = AccountPool([_acc("a")])
    account = pool.lease()
    assert pool.lease() is None

    pool.release(account)
    assert pool.lease().id == "a"


def test_het_credit_thi_loai_khoi_lan_chay() -> None:
    pool = AccountPool([_acc("a"), _acc("b")])
    account = pool.lease()

    pool.mark_exhausted(account, "hết credit")
    pool.release(account)  # release sau khi loại KHÔNG được hồi sinh nó

    remaining = [pool.lease()]
    assert pool.lease() is None
    assert remaining[0].id != account.id
    assert pool.summary()[account.id]["health"] == AccountHealth.EXHAUSTED.value


def test_dem_tai_khoan_con_song() -> None:
    pool = AccountPool([_acc("a"), _acc("b"), _acc("c")])
    assert pool.alive_count() == 3

    pool.mark_exhausted(pool.lease(), "hết credit")
    pool.mark_failed(pool.lease(), "chưa đăng nhập")
    assert pool.alive_count() == 1


def test_tai_khoan_bi_tat_khong_duoc_dua_vao_kho() -> None:
    pool = AccountPool([_acc("a"), _acc("b", enabled=False)])
    assert pool.total_count() == 1


def test_khong_co_tai_khoan_nao_bat_thi_bao_loi() -> None:
    with pytest.raises(ConfigError, match="Không có tài khoản nào đang bật"):
        AccountPool([_acc("a", enabled=False)])


# ===========================================================================
# load_accounts
# ===========================================================================
def test_khong_co_file_thi_dung_profile_cu(tmp_path: Path) -> None:
    """Thiết lập một-tài-khoản đang chạy phải tiếp tục hoạt động y nguyên."""
    legacy = tmp_path / "browser_profile"
    accounts = load_accounts(
        tmp_path / "khong-ton-tai.yaml",
        fallback_profile_dir=legacy,
        fallback_workspace_url="https://example.com/flow",
    )
    assert len(accounts) == 1
    assert accounts[0].id == "default"
    assert accounts[0].resolved_profile_dir(tmp_path) == legacy


def test_hai_tai_khoan_dung_chung_profile_bi_chan(tmp_path: Path) -> None:
    """Dùng chung thư mục profile = dùng chung phiên đăng nhập, và Chrome khoá
    thư mục khi đang mở. Phải chặn từ lúc nạp config."""
    path = tmp_path / "accounts.yaml"
    path.write_text(
        "accounts:\n"
        "  - id: a\n"
        "    profile_dir: /tmp/chung\n"
        "  - id: b\n"
        "    profile_dir: /tmp/chung\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="dùng chung"):
        load_accounts(path, fallback_profile_dir=tmp_path, fallback_workspace_url="x")


def test_id_tai_khoan_trung_bi_chan(tmp_path: Path) -> None:
    path = tmp_path / "accounts.yaml"
    path.write_text("accounts:\n  - id: a\n  - id: a\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="trùng"):
        load_accounts(path, fallback_profile_dir=tmp_path, fallback_workspace_url="x")


def test_loc_tai_khoan_theo_tham_so(tmp_path: Path) -> None:
    path = tmp_path / "accounts.yaml"
    path.write_text("accounts:\n  - id: a\n  - id: b\n  - id: c\n", encoding="utf-8")

    accounts = load_accounts(
        path, fallback_profile_dir=tmp_path, fallback_workspace_url="x", only=["a", "c"]
    )
    assert [a.id for a in accounts] == ["a", "c"]

    with pytest.raises(ConfigError, match="khong-co"):
        load_accounts(
            path, fallback_profile_dir=tmp_path, fallback_workspace_url="x", only=["khong-co"]
        )


def test_profile_tuong_doi_neo_vao_goc_project(tmp_path: Path) -> None:
    """Đường dẫn tương đối không được phụ thuộc thư mục hiện hành."""
    from core.paths import PROJECT_ROOT

    account = Account(id="a", profile_dir=Path(".secrets/browser_profile"))
    assert account.resolved_profile_dir(tmp_path) == PROJECT_ROOT / ".secrets/browser_profile"


# ===========================================================================
# SpecQueue
# ===========================================================================
def _spec(spec_id: str) -> VideoSpec:
    return VideoSpec(id=spec_id, prompt="x")


def test_hang_doi_lay_dung_thu_tu_roi_het() -> None:
    queue = SpecQueue([_spec("a"), _spec("b")], max_switches_per_spec=2)
    assert queue.take().id == "a"
    assert queue.take().id == "b"
    assert queue.take() is None


def test_tra_lai_hang_doi_de_tai_khoan_khac_lam() -> None:
    queue = SpecQueue([_spec("a")], max_switches_per_spec=2)
    spec = queue.take()

    assert queue.requeue(spec) is True
    assert queue.take().id == "a", "Prompt bị hết credit phải quay lại hàng đợi, không được mất"


def test_prompt_khong_di_vong_quanh_mai_mai() -> None:
    """Trần số lần đổi tài khoản: một prompt hỏng vì lý do riêng của nó không
    được phép đi hết tài khoản này tới tài khoản khác vô hạn."""
    queue = SpecQueue([_spec("a")], max_switches_per_spec=2)
    spec = queue.take()

    assert queue.requeue(spec) is True
    assert queue.requeue(spec) is True
    assert queue.requeue(spec) is False, "Quá trần thì phải từ chối, để lớp gọi ghi nhận thất bại"


def test_bao_cao_phan_con_sot() -> None:
    queue = SpecQueue([_spec("a"), _spec("b")], max_switches_per_spec=1)
    queue.take()
    assert [s.id for s in queue.leftovers()] == ["b"]
