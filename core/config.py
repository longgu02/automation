"""Nạp cấu hình: YAML -> thay biến môi trường -> validate bằng Pydantic.

Triết lý: file YAML là *dữ liệu thô*, model Pydantic là *hợp đồng*. Sai kiểu,
thiếu trường, giá trị ngoài danh sách cho phép -> nổ ngay lúc nạp config, chứ
không phải nổ giữa chừng sau khi đã đốt 20 phút render video.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from core.errors import ConfigError

T = TypeVar("T", bound=BaseModel)

# Khớp ${TEN_BIEN} và ${TEN_BIEN:-giá trị mặc định}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_yaml(path: Path) -> dict[str, Any]:
    """Đọc một file YAML thành dict. File rỗng -> dict rỗng."""
    if not path.exists():
        raise ConfigError(f"Không tìm thấy file cấu hình: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML sai cú pháp tại {path}:\n{exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"File {path} phải là một mapping (key: value) ở cấp cao nhất.")
    return data


def expand_env(node: Any) -> Any:
    """Thay ${BIEN_MOI_TRUONG} trong mọi chuỗi của cấu trúc lồng nhau.

    Cho phép để secret (API key...) trong .env thay vì viết thẳng vào YAML.
    Biến không tồn tại và không có giá trị mặc định -> ConfigError.
    """
    if isinstance(node, dict):
        return {k: expand_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [expand_env(v) for v in node]
    if not isinstance(node, str):
        return node

    def _sub(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = os.environ.get(name)
        if value is not None:
            return value
        if default is not None:
            return default
        raise ConfigError(
            f"Biến môi trường '{name}' được tham chiếu trong config nhưng chưa được đặt. "
            f"Thêm nó vào file .env, hoặc dùng cú pháp ${{{name}:-giá_trị_mặc_định}}."
        )

    return _ENV_PATTERN.sub(_sub, node)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Trộn `override` lên `base`, đệ quy vào dict con. Không sửa đầu vào.

    Dùng cho: mặc định toàn cục <- ghi đè theo từng prompt <- ghi đè từ CLI.
    List KHÔNG trộn mà thay thế hoàn toàn (tránh hành vi khó đoán).
    """
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        elif value is not None:
            result[key] = value
    return result


def load_config(path: Path, model: type[T], cli_overrides: dict[str, Any] | None = None) -> T:
    """Nạp file YAML, thay biến môi trường, áp ghi đè CLI, rồi validate.

    Args:
        path: đường dẫn file YAML.
        model: lớp Pydantic mô tả hợp đồng cấu hình.
        cli_overrides: dict phẳng/lồng nhau từ tham số dòng lệnh (ưu tiên cao nhất).
    """
    raw = expand_env(load_yaml(path))
    if cli_overrides:
        raw = deep_merge(raw, cli_overrides)

    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        lines = [f"Cấu hình không hợp lệ ({path}):"]
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"]) or "<gốc>"
            lines.append(f"  - {location}: {err['msg']}")
        raise ConfigError("\n".join(lines)) from exc
