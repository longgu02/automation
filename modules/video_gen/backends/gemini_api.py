"""Backend sinh video qua Gemini API (model Veo).

TÌNH TRẠNG: đã cài đặt đầy đủ, nhưng CHƯA CHẠY THỬ THỰC TẾ vì bạn chọn đi
đường trình duyệt trước. Coi đây là bản nháp tin cậy được -- khi nào bật billing
thì kiểm chứng lại. Nó tồn tại ngay từ đầu để chứng minh interface `VideoBackend`
đủ tổng quát cho cả hai kiểu truy cập, chứ không phải bị uốn cong quanh Playwright.

LƯU Ý VỀ THANH TOÁN: gói thuê bao Google AI Pro KHÔNG cấp quota Veo cho API key.
Đây là hai túi tiền khác nhau -- backend này cần một dự án Google Cloud/AI Studio
đã bật thanh toán. Đó chính là lý do backend trình duyệt được làm trước.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from core.accounts import Account
from core.errors import (
    ConfigError,
    ContentBlocked,
    FatalError,
    GenerationTimeout,
    QuotaExhausted,
    TransientNetwork,
)
from core.paths import ensure_dir
from modules.video_gen.backends.base import VideoBackend
from modules.video_gen.config import GeminiApiConfig
from modules.video_gen.models import VideoArtifact, VideoSpec

log = logging.getLogger(__name__)


class GeminiApiBackend(VideoBackend):
    name = "gemini_api"

    def __init__(
        self, cfg: GeminiApiConfig, account: Account, logger: logging.Logger | None = None
    ):
        super().__init__(account, logger)
        self.cfg = cfg
        # "Tài khoản" ở đây là một API key. Key khai trong accounts.yaml được ưu
        # tiên; không có thì rơi về key chung trong video_gen.yaml.
        self.api_key = account.api_key or cfg.api_key
        self._client = None
        self._types = None

    def describe(self) -> str:
        return f"[{self.account.describe()}] Gemini API, model {self.cfg.model_id}"

    # ===================================================================
    def open(self) -> None:
        if not self.api_key:
            raise ConfigError(
                f"Tài khoản '{self.account.id}' không có API key. Khai `api_key` cho nó "
                "trong config/accounts.yaml (nên dùng ${TEN_BIEN} và để key thật trong "
                ".env), hoặc đặt gemini_api.api_key dùng chung trong config/video_gen.yaml."
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ConfigError(
                "Thiếu thư viện google-genai. Cài bằng: pip install google-genai"
            ) from exc

        self._types = types
        self._client = genai.Client(api_key=self.api_key)
        self.log.info(
            "Đã tạo client Gemini API cho '%s' (model %s).", self.account.id, self.cfg.model_id
        )

    def close(self) -> None:
        self._client = None

    # ===================================================================
    def generate(self, spec: VideoSpec, dest_dir: Path) -> list[VideoArtifact]:
        assert self._client and self._types, "Phải gọi open() trước generate()"
        client, types = self._client, self._types
        ensure_dir(dest_dir)

        # Chỉ gửi tham số có giá trị: SDK từ chối None, và không phải model nào
        # cũng nhận đủ mọi tham số.
        options = {
            "aspect_ratio": spec.aspect_ratio,
            "resolution": spec.resolution,
            "number_of_videos": spec.outputs_per_prompt,
            "duration_seconds": spec.duration_seconds,
            "negative_prompt": spec.negative_prompt,
            "seed": spec.seed,
            "person_generation": self.cfg.person_generation,
        }
        config = types.GenerateVideosConfig(**{k: v for k, v in options.items() if v is not None})

        self.log.info("[%s] gửi yêu cầu tới %s", spec.id, self.cfg.model_id)
        try:
            operation = client.models.generate_videos(
                model=self.cfg.model_id, prompt=spec.prompt, config=config
            )
        except Exception as exc:
            raise _classify(exc, spec.id) from exc

        operation = self._await_operation(operation, spec.id)

        videos = getattr(operation.response, "generated_videos", None) or []
        if not videos:
            raise ContentBlocked(
                f"[{spec.id}] API hoàn tất nhưng không trả về video nào -- "
                "thường là do prompt bị bộ lọc nội dung chặn."
            )

        artifacts: list[VideoArtifact] = []
        for index, generated in enumerate(videos, start=1):
            path = dest_dir / f"{spec.id}_{index:02d}.mp4"
            try:
                client.files.download(file=generated.video)
                generated.video.save(str(path))
            except Exception as exc:
                raise _classify(exc, spec.id) from exc
            self.log.info("[%s] đã lưu clip #%d -> %s", spec.id, index, path)
            artifacts.append(VideoArtifact.from_file(spec, index, path, self.name))
        return artifacts

    # ------------------------------------------------------------------
    def _await_operation(self, operation, spec_id: str):
        """Hỏi trạng thái tác vụ dài cho tới khi xong hoặc hết giờ."""
        assert self._client
        deadline = time.monotonic() + self.cfg.generation_timeout_s
        started = time.monotonic()

        while not operation.done:
            if time.monotonic() > deadline:
                raise GenerationTimeout(
                    f"[{spec_id}] quá {self.cfg.generation_timeout_s}s mà tác vụ chưa xong."
                )
            time.sleep(self.cfg.poll_interval_s)
            try:
                operation = self._client.operations.get(operation)
            except Exception as exc:
                raise _classify(exc, spec_id) from exc
            self.log.debug("[%s] đang chờ... %.0fs", spec_id, time.monotonic() - started)

        if getattr(operation, "error", None):
            raise _classify(RuntimeError(str(operation.error)), spec_id)
        return operation


def _classify(exc: Exception, spec_id: str) -> Exception:
    """Quy lỗi từ SDK về đúng nhánh trong cây lỗi của ta (retry được hay không)."""
    text = str(exc).lower()
    if any(k in text for k in ("quota", "resource_exhausted", "billing", "exceeded")):
        return QuotaExhausted(f"[{spec_id}] {exc}")
    if any(k in text for k in ("safety", "blocked", "prohibited", "policy")):
        return ContentBlocked(f"[{spec_id}] {exc}")
    if any(k in text for k in ("permission", "unauthenticated", "api key", "invalid_argument")):
        return FatalError(f"[{spec_id}] {exc}")
    # Còn lại (mạng chập chờn, 5xx, deadline) -> đáng thử lại.
    return TransientNetwork(f"[{spec_id}] {exc}")
