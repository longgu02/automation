"""Điểm duy nhất định nghĩa đường dẫn trong project.

Không hardcode đường dẫn ở bất kỳ nơi nào khác -- import từ đây.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

# core/paths.py -> core/ -> <project root>
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

CONFIG_DIR: Path = PROJECT_ROOT / "config"
PROMPTS_DIR: Path = CONFIG_DIR / "prompts"
OUTPUT_DIR: Path = PROJECT_ROOT / "output"
LOG_DIR: Path = PROJECT_ROOT / "logs"

# Nơi chứa dữ liệu nhạy cảm (profile trình duyệt đã đăng nhập, token...).
# Thư mục này BẮT BUỘC nằm trong .gitignore.
SECRETS_DIR: Path = PROJECT_ROOT / ".secrets"

# Profile Chrome của automation. Đăng nhập một lần, dùng mãi.
#
# BROWSER_PROFILE_DIR là thư mục của thời một-tài-khoản. Vẫn giữ nguyên vì đó
# là nơi phiên đăng nhập đầu tiên của bạn đang nằm; config/accounts.yaml trỏ
# tài khoản thứ nhất vào đúng đây.
BROWSER_PROFILE_DIR: Path = SECRETS_DIR / "browser_profile"

# Từ tài khoản thứ hai trở đi, mỗi tài khoản một thư mục con:
#   .secrets/profiles/<account_id>
# Bắt buộc tách riêng: Chrome khoá thư mục profile khi đang mở, và dùng chung
# thư mục nghĩa là dùng chung phiên đăng nhập.
BROWSER_PROFILE_ROOT: Path = SECRETS_DIR / "profiles"


def ensure_dir(path: Path) -> Path:
    """Tạo thư mục (kể cả cha) nếu chưa có, rồi trả lại chính nó."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_run_id() -> str:
    """Mã định danh cho một lần chạy, ví dụ '20260812-153045'.

    Dùng thời gian local để bạn đối chiếu log bằng mắt cho nhanh.
    """
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def slugify(text: str, max_len: int = 60) -> str:
    """Biến chuỗi tuỳ ý thành tên file/thư mục an toàn trên Windows."""
    keep: list[str] = []
    for ch in text.strip().lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -_":
            keep.append("-")
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:max_len] or "untitled"
