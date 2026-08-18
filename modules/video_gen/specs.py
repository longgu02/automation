"""Nạp prompt từ YAML và "chín hoá" thành danh sách VideoSpec.

Tách riêng khỏi module.py vì đây là phần logic thuần tuý, không đụng mạng,
không đụng đĩa (ngoài việc đọc YAML) -- nên rất dễ viết test.

Định dạng file prompt (xem `config/prompts/demo.yaml`):

    defaults:            # tuỳ chọn -- ghi đè mặc định toàn cục cho riêng file này
      aspect_ratio: "9:16"

    prompts:
      - id: bien-hoang-hon
        prompt: "Sóng vỗ bờ đá lúc hoàng hôn..."
        resolution: 1080p       # ghi đè cho riêng prompt này

Thứ tự ưu tiên khi trộn tham số (sau thắng trước):

    VideoDefaults trong video_gen.yaml
      -> khối `defaults:` trong file prompt
        -> từng mục trong `prompts:`
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.config import deep_merge, expand_env, load_yaml
from core.errors import ConfigError
from core.paths import PROJECT_ROOT
from modules.video_gen.config import VideoGenConfig
from modules.video_gen.models import VideoSpec

log = logging.getLogger(__name__)

#: Các khoá được phép xuất hiện ở mỗi mục prompt.
_ALLOWED_KEYS = set(VideoSpec.model_fields.keys())


def _resolve(path: Path) -> Path:
    """Đường dẫn tương đối luôn tính từ gốc project, không phải thư mục hiện tại."""
    return path if path.is_absolute() else PROJECT_ROOT / path


def build_specs(cfg: VideoGenConfig) -> list[VideoSpec]:
    """Đọc mọi file prompt trong config và trả về danh sách spec đã hoàn chỉnh.

    Id trùng nhau: mục nạp sau ghi đè mục trước (kèm cảnh báo trong log), nhờ
    vậy bạn có thể để một file "override" nhỏ đè lên bộ prompt lớn.
    """
    global_defaults = cfg.defaults.model_dump()
    prefix = global_defaults.pop("prompt_prefix", "") or ""
    suffix = global_defaults.pop("prompt_suffix", "") or ""

    by_id: dict[str, VideoSpec] = {}

    for rel_path in cfg.prompt_files:
        path = _resolve(rel_path)
        raw = expand_env(load_yaml(path))

        file_defaults = raw.get("defaults") or {}
        if not isinstance(file_defaults, dict):
            raise ConfigError(f"{path}: khối 'defaults' phải là mapping.")

        entries = raw.get("prompts")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: thiếu danh sách 'prompts' (hoặc danh sách rỗng).")

        merged_defaults = deep_merge(global_defaults, file_defaults)
        # prompt_prefix/suffix cũng có thể ghi đè ở cấp file.
        file_prefix = merged_defaults.pop("prompt_prefix", prefix) or ""
        file_suffix = merged_defaults.pop("prompt_suffix", suffix) or ""

        for position, entry in enumerate(entries, start=1):
            spec = _build_one(
                entry=entry,
                defaults=merged_defaults,
                prefix=file_prefix,
                suffix=file_suffix,
                source=path,
                position=position,
            )
            if spec.id in by_id:
                log.warning("Prompt id '%s' bị định nghĩa lại ở %s -- dùng bản mới nhất.", spec.id, path)
            by_id[spec.id] = spec

    specs = list(by_id.values())
    log.info("Đã nạp %d prompt từ %d file.", len(specs), len(cfg.prompt_files))
    return specs


def _build_one(
    *,
    entry: Any,
    defaults: dict[str, Any],
    prefix: str,
    suffix: str,
    source: Path,
    position: int,
) -> VideoSpec:
    where = f"{source.name} (mục #{position})"

    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: mỗi mục trong 'prompts' phải là mapping có ít nhất 'id' và 'prompt'.")

    unknown = set(entry) - _ALLOWED_KEYS
    if unknown:
        raise ConfigError(
            f"{where}: khoá không nhận diện được {sorted(unknown)}. "
            f"Các khoá hợp lệ: {sorted(_ALLOWED_KEYS)}"
        )

    merged = deep_merge(defaults, entry)

    body = str(merged.get("prompt", "")).strip()
    if not body:
        raise ConfigError(f"{where}: thiếu trường 'prompt' hoặc để rỗng.")
    merged["prompt"] = " ".join(part for part in (prefix.strip(), body, suffix.strip()) if part)

    if "id" not in merged:
        raise ConfigError(f"{where}: thiếu trường 'id'.")

    try:
        return VideoSpec.model_validate(merged)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"{where}: {details}") from exc


def filter_specs(
    specs: list[VideoSpec], only: list[str] | None = None, limit: int | None = None
) -> list[VideoSpec]:
    """Lọc theo tham số CLI (`--only`, `--limit`), giữ nguyên thứ tự khai báo."""
    result = specs
    if only:
        wanted = set(only)
        missing = wanted - {s.id for s in specs}
        if missing:
            raise ConfigError(f"--only tham chiếu id không tồn tại: {sorted(missing)}")
        result = [s for s in result if s.id in wanted]
    if limit is not None:
        result = result[:limit]
    return result
