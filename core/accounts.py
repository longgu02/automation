"""Kho tài khoản và bộ cấp phát tài khoản (thread-safe).

BÀI TOÁN: bạn có nhiều tài khoản Google AI Pro. Cần hai thứ:

  1. *Dự phòng*  -- một tài khoản hết credit thì tự chuyển sang tài khoản khác,
                    và prompt đang dở được đưa trở lại hàng đợi để làm bằng
                    tài khoản mới, không mất.
  2. *Song song* -- nhiều tài khoản chạy cùng lúc để nhân đôi/nhân ba tốc độ.

TRỤC SONG SONG LÀ TÀI KHOẢN, KHÔNG PHẢI PROMPT. Đây là điểm cốt lõi cần hiểu:
phía Google, mỗi tài khoản có MỘT hàng đợi render. Bắn nhiều prompt cùng lúc vào
một tài khoản không làm nó nhanh hơn, chỉ khiến việc ghép "clip nào của prompt
nào" trở nên bất định. Nhưng hai tài khoản khác nhau là hai hàng đợi thật sự
độc lập -- đó mới là chỗ song song hoá vừa an toàn vừa có lợi.

Hệ quả thiết kế: **mỗi tài khoản được đúng một worker giữ tại một thời điểm**.
Số worker chạy thật = min(max_parallel, số tài khoản còn khoẻ).

Vòng đời một tài khoản trong một lần chạy:

    READY  --lease()-->  IN_USE  --release()-->  READY
                            |
                            +--mark_exhausted()--> EXHAUSTED  (hết credit)
                            +--mark_failed()----->  FAILED     (chưa đăng nhập...)

EXHAUSTED và FAILED là trạng thái CUỐI trong phạm vi một lần chạy: đã hết credit
thì 20 phút nữa cũng vẫn hết. Lần chạy sau bắt đầu lại từ đầu với bảng sạch.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.errors import ConfigError
from core.paths import PROJECT_ROOT

log = logging.getLogger(__name__)


class Account(BaseModel):
    """Một tài khoản dùng để sinh nội dung."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Định danh ngắn, duy nhất. Hiện trong log và tham số --accounts.")
    label: str = Field(default="", description="Ghi chú cho người đọc, ví dụ địa chỉ email.")
    enabled: bool = True

    #: Thư mục profile Chrome riêng của tài khoản này (backend trình duyệt).
    #: Bỏ trống -> tự suy ra .secrets/browser_profile/<id>.
    #: MỖI TÀI KHOẢN PHẢI CÓ THƯ MỤC RIÊNG -- Chrome khoá profile khi đang mở,
    #: và dùng chung thư mục nghĩa là dùng chung phiên đăng nhập.
    profile_dir: Path | None = None

    #: Ghi đè URL project riêng cho tài khoản này. Bỏ trống -> dùng
    #: browser.workspace_url chung. Mỗi tài khoản có project riêng của nó, nên
    #: khi bạn dùng URL project cụ thể thì phải khai báo ở đây.
    workspace_url: str | None = None

    #: Chỉ dùng cho backend gemini_api. Nên viết ${TEN_BIEN} và để key trong .env.
    api_key: str = ""

    notes: str = ""

    @field_validator("id")
    @classmethod
    def _id_safe(cls, value: str) -> str:
        if not value or set(value) & set('\\/:*?"<>| '):
            raise ValueError("id tài khoản chỉ được dùng chữ, số, gạch ngang, gạch dưới.")
        return value

    def resolved_profile_dir(self, root: Path) -> Path:
        """Thư mục profile thật sự dùng cho tài khoản này.

        Đường dẫn tương đối trong YAML luôn tính từ gốc project, KHÔNG phải từ
        thư mục hiện hành -- nếu không thì chạy script từ chỗ khác sẽ lặng lẽ
        tạo ra một profile trắng và bắt bạn đăng nhập lại.
        """
        if self.profile_dir is None:
            return root / self.id
        if self.profile_dir.is_absolute():
            return self.profile_dir
        return PROJECT_ROOT / self.profile_dir

    def describe(self) -> str:
        return f"{self.id} ({self.label})" if self.label else self.id


class AccountHealth(str, Enum):
    READY = "ready"  # rảnh, dùng được
    IN_USE = "in_use"  # đang có worker giữ
    EXHAUSTED = "exhausted"  # hết credit -- không dùng lại trong lần chạy này
    FAILED = "failed"  # chưa đăng nhập / lỗi cấu hình


@dataclass
class AccountRecord:
    account: Account
    health: AccountHealth = AccountHealth.READY
    completed: int = 0  # số prompt sinh thành công bằng tài khoản này
    reason: str = ""  # lý do khi EXHAUSTED/FAILED
    _lease_count: int = field(default=0, repr=False)


class AccountPool:
    """Bộ cấp phát tài khoản dùng chung cho nhiều luồng.

    Mọi thao tác đổi trạng thái đều nằm dưới một khoá duy nhất. Bộ này nhỏ và
    ít bị gọi (mỗi worker gọi vài lần cho cả lần chạy), nên một khoá thô là đủ
    -- không cần tối ưu gì thêm.
    """

    def __init__(self, accounts: list[Account]):
        usable = [a for a in accounts if a.enabled]
        if not usable:
            raise ConfigError(
                "Không có tài khoản nào đang bật. Kiểm tra config/accounts.yaml "
                "(trường `enabled`)."
            )
        self._lock = threading.Lock()
        self._records: dict[str, AccountRecord] = {a.id: AccountRecord(a) for a in usable}

    # ------------------------------------------------------------ cấp phát
    def lease(self) -> Account | None:
        """Nhận một tài khoản rảnh, hoặc None nếu không còn cái nào dùng được.

        Không chờ đợi (non-blocking): worker gọi hàm này khi khởi động và khi
        tài khoản đang giữ bị chết. Trả None nghĩa là worker nên dừng hẳn.
        """
        with self._lock:
            for record in self._records.values():
                if record.health is AccountHealth.READY:
                    record.health = AccountHealth.IN_USE
                    record._lease_count += 1
                    log.debug("Cấp tài khoản %s cho worker.", record.account.id)
                    return record.account
            return None

    def release(self, account: Account) -> None:
        """Trả tài khoản còn khoẻ về kho."""
        with self._lock:
            record = self._records.get(account.id)
            if record and record.health is AccountHealth.IN_USE:
                record.health = AccountHealth.READY

    # -------------------------------------------------------- đổi trạng thái
    def mark_exhausted(self, account: Account, reason: str) -> None:
        self._retire(account, AccountHealth.EXHAUSTED, reason)
        log.warning("Tài khoản %s hết credit -- loại khỏi lần chạy này. %s", account.id, reason)

    def mark_failed(self, account: Account, reason: str) -> None:
        self._retire(account, AccountHealth.FAILED, reason)
        log.error("Tài khoản %s không dùng được -- loại khỏi lần chạy này. %s", account.id, reason)

    def _retire(self, account: Account, health: AccountHealth, reason: str) -> None:
        with self._lock:
            record = self._records.get(account.id)
            if record:
                record.health = health
                record.reason = reason

    def record_success(self, account: Account) -> None:
        with self._lock:
            record = self._records.get(account.id)
            if record:
                record.completed += 1

    # ------------------------------------------------------------- tra cứu
    def available_count(self) -> int:
        """Số tài khoản đang rảnh và dùng được."""
        with self._lock:
            return sum(1 for r in self._records.values() if r.health is AccountHealth.READY)

    def alive_count(self) -> int:
        """Số tài khoản chưa bị loại (kể cả đang được worker giữ)."""
        with self._lock:
            return sum(
                1
                for r in self._records.values()
                if r.health in (AccountHealth.READY, AccountHealth.IN_USE)
            )

    def total_count(self) -> int:
        return len(self._records)

    def summary(self) -> dict[str, dict[str, object]]:
        """Bảng tổng kết để in ra cuối lần chạy."""
        with self._lock:
            return {
                account_id: {
                    "label": record.account.label,
                    "health": record.health.value,
                    "completed": record.completed,
                    "reason": record.reason,
                }
                for account_id, record in self._records.items()
            }


# ==========================================================================
# Nạp danh sách tài khoản từ YAML
# ==========================================================================
def load_accounts(
    path: Path,
    *,
    fallback_profile_dir: Path,
    fallback_workspace_url: str,
    only: list[str] | None = None,
) -> list[Account]:
    """Đọc `config/accounts.yaml`.

    Không có file -> tự dựng một tài khoản 'default' trỏ vào profile cũ. Nhờ vậy
    thiết lập một-tài-khoản đang chạy vẫn hoạt động y nguyên, không cần sửa gì.
    """
    from core.config import expand_env, load_yaml  # nhập tại chỗ để tránh vòng lặp import

    if not path.exists():
        log.info("Không thấy %s -- dùng một tài khoản mặc định với profile sẵn có.", path)
        return [
            Account(
                id="default",
                label="tài khoản duy nhất",
                profile_dir=fallback_profile_dir,
                workspace_url=fallback_workspace_url,
            )
        ]

    raw = expand_env(load_yaml(path))
    entries = raw.get("accounts")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: thiếu danh sách 'accounts' hoặc danh sách rỗng.")

    accounts: list[Account] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path} (mục #{position}): mỗi tài khoản phải là mapping.")
        try:
            accounts.append(Account.model_validate(entry))
        except Exception as exc:
            raise ConfigError(f"{path} (mục #{position}): {exc}") from exc

    _assert_unique(accounts, path, fallback_profile_dir)

    if only:
        wanted = set(only)
        missing = wanted - {a.id for a in accounts}
        if missing:
            raise ConfigError(f"--accounts tham chiếu id không có trong {path}: {sorted(missing)}")
        accounts = [a for a in accounts if a.id in wanted]

    return accounts


def _assert_unique(accounts: list[Account], path: Path, profile_root: Path) -> None:
    """Chặn hai lỗi cấu hình gây hỏng ngầm và rất khó lần ra."""
    seen_ids: set[str] = set()
    for account in accounts:
        if account.id in seen_ids:
            raise ConfigError(f"{path}: id tài khoản bị trùng: '{account.id}'.")
        seen_ids.add(account.id)

    # Hai tài khoản dùng chung thư mục profile = dùng chung phiên đăng nhập, và
    # Chrome sẽ từ chối mở cái thứ hai vì profile đang bị khoá. Triệu chứng rất
    # khó hiểu nếu không chặn từ đây.
    seen_dirs: dict[Path, str] = {}
    for account in accounts:
        if not account.enabled:
            continue
        folder = account.resolved_profile_dir(profile_root).resolve()
        if folder in seen_dirs:
            raise ConfigError(
                f"{path}: tài khoản '{account.id}' và '{seen_dirs[folder]}' dùng chung "
                f"thư mục profile ({folder}). Mỗi tài khoản phải có thư mục riêng."
            )
        seen_dirs[folder] = account.id
