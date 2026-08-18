"""Kiểu dữ liệu của module video_gen.

Hai khái niệm cần phân biệt rạch ròi:

    VideoSpec     -- MỘT ĐƠN VỊ CÔNG VIỆC: một prompt kèm đầy đủ tham số đã
                     được giải quyết (đã trộn mặc định toàn cục + ghi đè riêng).
                     Backend nhận vào cái này.
    VideoArtifact -- MỘT FILE ĐÃ SINH RA: đường dẫn mp4 + metadata kèm theo.
                     Backend trả ra cái này.

Backend không bao giờ nhìn thấy file config; nó chỉ thấy VideoSpec. Nhờ đó thay
backend không cần đụng tới định dạng config, và ngược lại.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AspectRatio = Literal["16:9", "9:16", "1:1"]
Resolution = Literal["720p", "1080p"]


class VideoSpec(BaseModel):
    """Một prompt đã "chín" -- đủ tham số để đưa thẳng cho backend."""

    id: str = Field(description="Định danh ngắn, duy nhất. Dùng làm tên thư mục output.")
    prompt: str = Field(min_length=1)

    negative_prompt: str | None = None
    model: str = "veo-3.1"
    aspect_ratio: AspectRatio = "16:9"
    resolution: Resolution = "1080p"
    duration_seconds: int = Field(default=8, ge=2, le=60)
    outputs_per_prompt: int = Field(default=1, ge=1, le=4)
    seed: int | None = None

    #: Ảnh tham chiếu cho image-to-video. None = text-to-video thuần.
    reference_image: Path | None = None

    #: Ghi chú tự do của bạn, chép nguyên vào manifest. Hệ thống không đọc.
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _id_must_be_path_safe(cls, value: str) -> str:
        bad = set(value) & set('\\/:*?"<>| ')
        if bad:
            raise ValueError(
                f"id chứa ký tự không dùng được cho tên thư mục: {sorted(bad)}. "
                "Chỉ dùng chữ, số, gạch ngang, gạch dưới."
            )
        return value

    def fingerprint(self, backend: str) -> str:
        """Vân tay nội dung -- nền tảng của cơ chế resume.

        Băm mọi trường ẢNH HƯỞNG tới video đầu ra (kèm tên backend). Sửa prompt
        hay đổi độ phân giải -> vân tay đổi -> lần chạy sau sinh lại. Chỉ sửa
        `notes`/`tags` -> vân tay giữ nguyên -> không tốn credit render lại.
        """
        material: dict[str, Any] = {
            "backend": backend,
            "prompt": self.prompt.strip(),
            "negative_prompt": (self.negative_prompt or "").strip(),
            "model": self.model,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_seconds": self.duration_seconds,
            "outputs_per_prompt": self.outputs_per_prompt,
            "seed": self.seed,
            "reference_image": str(self.reference_image) if self.reference_image else None,
        }
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class VideoArtifact(BaseModel):
    """Một file video đã sinh ra thành công."""

    spec_id: str
    index: int = Field(description="Số thứ tự trong cùng một spec, bắt đầu từ 1.")
    path: Path
    backend: str
    model: str
    size_bytes: int = 0
    created_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now())

    #: Thông tin phụ tuỳ backend (id operation, url gốc...). Không có schema cố định.
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_file(
        cls, spec: VideoSpec, index: int, path: Path, backend: str, **meta: Any
    ) -> VideoArtifact:
        """Tạo artifact từ file vừa ghi xuống đĩa, tự đọc kích thước."""
        return cls(
            spec_id=spec.id,
            index=index,
            path=path,
            backend=backend,
            model=spec.model,
            size_bytes=path.stat().st_size if path.exists() else 0,
            meta=meta,
        )
