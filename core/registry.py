"""Sổ đăng ký module -- danh mục mọi module mà hệ thống biết.

VÌ SAO CẦN: giao diện kéo thả phải biết có những module nào, mỗi cái đọc/ghi gì,
và chỉnh được tham số nào. Trước đây thông tin đó nằm rải rác trong đầu bạn.
Giờ nó nằm ở một chỗ, và cả UI lẫn bộ chạy job đều đọc từ đây.

HAI ĐIỀU QUAN TRỌNG:

1. *Nạp trễ (lazy)*. Sổ này chỉ giữ CHUỖI đường dẫn tới lớp, không import lớp
   thật. Nhờ vậy máy chủ UI khởi động được mà không cần Playwright hay ffmpeg --
   nó chỉ cần biết tên module, chứ chưa chạy cái nào. Lớp thật được import đúng
   lúc job chạy tới nó.

2. *`reads` / `writes` là hợp đồng nối dây*. Đó là các khoá trong `ctx.shared`
   mà module đọc vào và ghi ra. UI dùng chúng để vẽ cổng vào/cổng ra, và để
   cảnh báo khi bạn nối một module vào thứ không cung cấp dữ liệu nó cần.

THÊM MODULE MỚI VÀO SƠ ĐỒ: thêm một `ModuleSpec` vào `REGISTRY` bên dưới. Không
phải sửa UI, không phải sửa bộ chạy job.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from core.errors import ConfigError


@dataclass(frozen=True)
class ModuleSpec:
    """Mô tả một module đủ để UI vẽ nó ra và bộ chạy job gọi được nó."""

    #: Định danh dùng trong file job. Đổi cái này là làm hỏng job đã lưu.
    name: str
    #: Tên hiển thị trên sơ đồ.
    title: str
    description: str

    #: "đường.dẫn.module:TênLớp" -- import khi cần, không import lúc nạp sổ.
    module_path: str
    config_path: str
    default_config_file: str

    #: Khoá trong ctx.shared module này ĐỌC vào. Rỗng = không cần đầu vào nào.
    reads: tuple[str, ...] = ()
    #: Khoá trong ctx.shared module này GHI ra.
    writes: tuple[str, ...] = ()

    #: Màu nhấn trên sơ đồ, để phân biệt nhóm module bằng mắt.
    accent: str = "#6366f1"
    #: Biểu tượng hiển thị trên nút.
    icon: str = "▦"

    #: Cảnh báo hiện trong UI trước khi chạy thật (tốn credit, gọi mạng...).
    warning: str = ""

    def load_module_class(self):
        """Import lớp module thật. Chỉ gọi khi sắp chạy nó."""
        return _import_symbol(self.module_path)

    def load_config_class(self) -> type[BaseModel]:
        """Import lớp cấu hình. Nhẹ hơn nhiều so với lớp module."""
        return _import_symbol(self.config_path)


def _import_symbol(path: str):
    """Nạp 'gói.mô_đun:TênLớp' thành đối tượng thật."""
    if ":" not in path:
        raise ConfigError(f"Đường dẫn lớp phải có dạng 'mô_đun:TênLớp', nhận được: {path}")
    module_name, symbol = path.split(":", 1)
    try:
        return getattr(importlib.import_module(module_name), symbol)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"Không nạp được '{path}': {exc}") from exc


# ==========================================================================
# DANH MỤC MODULE
# ==========================================================================
REGISTRY: dict[str, ModuleSpec] = {
    spec.name: spec
    for spec in [
        ModuleSpec(
            name="video_gen",
            title="Sinh video",
            description=(
                "Sinh video từ prompt bằng tài khoản Google AI Pro. "
                "Tự chuyển tài khoản khi hết credit, chạy song song được."
            ),
            module_path="modules.video_gen.module:VideoGenModule",
            config_path="modules.video_gen.config:VideoGenConfig",
            default_config_file="config/video_gen.yaml",
            writes=("videos",),
            accent="#8b5cf6",
            icon="🎬",
            warning="Tốn credit tài khoản Gemini Pro và mở trình duyệt.",
        ),
        ModuleSpec(
            name="video_export",
            title="Ghép video",
            description=(
                "Ghép nhiều clip thành MỘT file mp4. Tự chuẩn hoá tỉ lệ, "
                "fps và âm thanh khi các clip lệch nhau."
            ),
            module_path="modules.video_export.module:VideoExportModule",
            config_path="modules.video_export.config:VideoExportConfig",
            default_config_file="config/video_export.yaml",
            reads=("videos",),
            writes=("final_video",),
            accent="#0ea5e9",
            icon="🎞",
            warning="Cần ffmpeg. Mã hoá lại có thể mất vài phút.",
        ),
        ModuleSpec(
            name="image_crawl",
            title="Lấy ảnh Pinterest",
            description=(
                "Tìm trên Pinterest và tải về N ảnh nổi bật nhất, "
                "với nhịp thao tác của người dùng thật."
            ),
            module_path="modules.image_crawl.module:ImageCrawlModule",
            config_path="modules.image_crawl.config:ImageCrawlConfig",
            default_config_file="config/image_crawl.yaml",
            writes=("images",),
            accent="#e11d48",
            icon="🖼",
            warning="Mở trình duyệt và truy cập Pinterest. Một phiên có thể kéo dài vài phút.",
        ),
    ]
}


def get_spec(name: str) -> ModuleSpec:
    if name not in REGISTRY:
        raise ConfigError(
            f"Module không có trong sổ đăng ký: '{name}'. "
            f"Hiện có: {', '.join(sorted(REGISTRY))}"
        )
    return REGISTRY[name]


# ==========================================================================
# Mô tả trường cấu hình cho giao diện
# ==========================================================================
#: Kiểu vô hướng UI vẽ được thành một ô nhập. Cố ý bỏ qua list và dict lồng
#: nhau: chúng cần trình soạn riêng, và sửa trong file YAML vẫn dễ hơn nhiều.
_SIMPLE_TYPES = {str: "text", int: "number", float: "number", bool: "checkbox"}


def describe_fields(model: type[BaseModel], prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """Trải phẳng các trường của một lớp cấu hình Pydantic cho UI vẽ.

    Trả về danh sách {path, label, type, default, choices, help}. `path` dùng
    dấu chấm ("browser.headless") và chính là khoá ghi đè trong file job.

    Chỉ lấy trường vô hướng. Trường phức tạp (danh sách prompt, danh sách
    selector...) vẫn sửa trong file YAML -- UI không cố thay thế điều đó.
    """
    if depth > 3:
        return []

    fields: list[dict[str, Any]] = []
    for field_name, info in model.model_fields.items():
        path = f"{prefix}{field_name}"
        annotation = _unwrap_optional(info.annotation)

        # Nhóm lồng nhau -> đệ quy xuống.
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            fields.extend(describe_fields(annotation, f"{path}.", depth + 1))
            continue

        entry = _describe_one(path, field_name, annotation, info)
        if entry:
            fields.append(entry)
    return fields


def _describe_one(path: str, name: str, annotation: Any, info) -> dict[str, Any] | None:
    choices: list[str] = []
    kind: str | None = None

    if get_origin(annotation) is Literal:
        choices = [str(v) for v in get_args(annotation)]
        kind = "select"
    elif annotation in _SIMPLE_TYPES:
        kind = _SIMPLE_TYPES[annotation]
    elif annotation is not None and getattr(annotation, "__name__", "") == "Path":
        kind = "text"

    if kind is None:
        return None  # danh sách, dict, kiểu lạ -> để yên trong YAML

    default = info.default
    if default is not None and not isinstance(default, (str, int, float, bool)):
        default = str(default)
    if repr(default) == "PydanticUndefined":
        default = None

    return {
        "path": path,
        "label": name.replace("_", " "),
        "type": kind,
        "default": default,
        "choices": choices,
        "help": (info.description or "").strip(),
    }


def _unwrap_optional(annotation: Any) -> Any:
    """Bóc `X | None` thành `X`. Ô nhập trống trong UI nghĩa là None."""
    if get_origin(annotation) is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def registry_as_json() -> list[dict[str, Any]]:
    """Toàn bộ sổ đăng ký ở dạng UI dùng được.

    Đây là thứ duy nhất giao diện biết về hệ thống -- nên thêm module mới vào
    `REGISTRY` là nó tự xuất hiện trong bảng chọn, không phải sửa một dòng
    JavaScript nào.
    """
    output = []
    for spec in REGISTRY.values():
        try:
            fields = describe_fields(spec.load_config_class())
        except ConfigError:
            # Không nạp được lớp cấu hình thì vẫn hiện module ra, chỉ là không
            # chỉnh được tham số. Thà thiếu một phần còn hơn sập cả giao diện.
            fields = []
        output.append(
            {
                "name": spec.name,
                "title": spec.title,
                "description": spec.description,
                "reads": list(spec.reads),
                "writes": list(spec.writes),
                "accent": spec.accent,
                "icon": spec.icon,
                "warning": spec.warning,
                "default_config_file": spec.default_config_file,
                "fields": fields,
            }
        )
    return output
