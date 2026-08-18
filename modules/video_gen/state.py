"""Sổ trạng thái phục vụ resume -- thứ giúp module này "hiệu quả".

Sinh video tốn tiền và tốn thời gian. Đây là cơ chế bảo đảm một prompt đã render
xong thì KHÔNG BAO GIỜ bị render lại, kể cả khi bạn Ctrl+C giữa chừng, mất mạng,
hay chạy lại lệnh lần thứ mười.

Cách hoạt động: một file JSON duy nhất, khoá theo `VideoSpec.fingerprint()`
(băm nội dung prompt + tham số). Ghi ngay sau MỖI spec, không đợi hết run --
mất điện giữa chừng vẫn giữ được thành quả.

AN TOÀN ĐA LUỒNG: khi chạy song song nhiều tài khoản, các worker cùng ghi vào
một sổ. Mọi thao tác đọc/ghi đều nằm dưới một khoá, và việc ghi file là nguyên
tử (ghi ra file tạm rồi thay thế). Sổ này bị chạm vài lần mỗi phút nên một khoá
thô hoàn toàn đủ -- không đáng đánh đổi sự rõ ràng lấy tốc độ ở đây.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Literal

from core.paths import ensure_dir
from modules.video_gen.models import VideoArtifact

log = logging.getLogger(__name__)

SpecState = Literal["done", "failed"]


class StateStore:
    """Đọc/ghi file trạng thái. Hỏng file -> bỏ qua, coi như chạy mới."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()  # RLock: mark_* gọi _flush khi đang giữ khoá
        self._load()

    # ------------------------------------------------------------------ đọc
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Không cho file trạng thái hỏng làm chết cả run: xấu nhất là
            # render lại, chứ không phải mất khả năng chạy.
            log.warning("File trạng thái %s không đọc được (%s) -- bắt đầu lại từ đầu.", self.path, exc)
            self._data = {}

    def is_done(self, fingerprint: str) -> bool:
        """Đã hoàn thành VÀ mọi file sinh ra vẫn còn trên đĩa?

        Kiểm tra sự tồn tại của file là có chủ đích: bạn xoá mp4 đi thì lần chạy
        sau phải sinh lại, chứ không được báo "đã xong" một cách vô nghĩa.
        """
        with self._lock:
            entry = self._data.get(fingerprint)
            if not entry or entry.get("state") != "done":
                return False
            paths = [Path(p) for p in entry.get("artifacts", [])]
        return bool(paths) and all(p.exists() for p in paths)

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(fingerprint)

    def artifacts_of(self, fingerprint: str) -> list[Path]:
        with self._lock:
            entry = self._data.get(fingerprint) or {}
            return [Path(p) for p in entry.get("artifacts", [])]

    # ------------------------------------------------------------------ ghi
    def mark_done(self, fingerprint: str, spec_id: str, artifacts: list[VideoArtifact]) -> None:
        with self._lock:
            self._data[fingerprint] = {
                "spec_id": spec_id,
                "state": "done",
                "artifacts": [str(a.path) for a in artifacts],
                "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            }
            self._flush()

    def mark_failed(self, fingerprint: str, spec_id: str, error: str) -> None:
        with self._lock:
            previous = self._data.get(fingerprint, {})
            self._data[fingerprint] = {
                "spec_id": spec_id,
                "state": "failed",
                "artifacts": [],
                "error": error[:2000],  # cắt bớt để file trạng thái không phình ra
                "attempts": int(previous.get("attempts", 0)) + 1,
                "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            }
            self._flush()

    def _flush(self) -> None:
        """Ghi nguyên tử: ghi ra file tạm rồi thay thế.

        Tránh trường hợp Ctrl+C đúng lúc đang ghi làm hỏng file trạng thái.
        """
        ensure_dir(self.path.parent)
        # Tên file tạm kèm id luồng: hai worker cùng ghi không giẫm lên file tạm
        # của nhau (dù đã có khoá, đây là lớp bảo vệ thứ hai gần như miễn phí).
        tmp = self.path.with_suffix(f"{self.path.suffix}.{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)
