"""Kiểm thử phần logic thuần tuý: chín hoá prompt và vân tay resume.

Cố ý KHÔNG test backend: chúng cần trình duyệt thật và tài khoản thật. Thứ đáng
test tự động là những chỗ dễ âm thầm sai mà chạy vẫn "trông như đúng" -- thứ tự
ưu tiên khi trộn tham số, và tính ổn định của vân tay (quyết định có tốn credit
render lại hay không).

    python -m pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.errors import ConfigError  # noqa: E402
from modules.video_gen.config import VideoGenConfig  # noqa: E402
from modules.video_gen.models import VideoSpec  # noqa: E402
from modules.video_gen.specs import build_specs, filter_specs  # noqa: E402


@pytest.fixture()
def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """
defaults:
  aspect_ratio: "9:16"
  prompt_suffix: "cinematic"

prompts:
  - id: alpha
    prompt: "Sóng biển lúc bình minh"
  - id: beta
    prompt: "Phố đêm trong mưa"
    aspect_ratio: "1:1"
    resolution: "720p"
""",
        encoding="utf-8",
    )
    return path


def _cfg(prompt_file: Path, **kwargs) -> VideoGenConfig:
    return VideoGenConfig(prompt_files=[prompt_file], **kwargs)


# --------------------------------------------------------------- trộn tham số
def test_thu_tu_uu_tien_khi_tron_tham_so(prompt_file: Path) -> None:
    """Mục prompt > defaults của file > defaults toàn cục."""
    specs = {s.id: s for s in build_specs(_cfg(prompt_file))}

    # alpha: lấy 9:16 từ defaults của file (đè lên 16:9 toàn cục)
    assert specs["alpha"].aspect_ratio == "9:16"
    # beta: mục prompt thắng cả hai cấp trên
    assert specs["beta"].aspect_ratio == "1:1"
    assert specs["beta"].resolution == "720p"
    # resolution của alpha rơi về mặc định toàn cục
    assert specs["alpha"].resolution == "1080p"


def test_prompt_suffix_duoc_noi_vao(prompt_file: Path) -> None:
    specs = {s.id: s for s in build_specs(_cfg(prompt_file))}
    assert specs["alpha"].prompt == "Sóng biển lúc bình minh cinematic"


def test_go_sai_ten_khoa_bi_bao_loi_ngay(tmp_path: Path) -> None:
    """Khoá lạ phải nổ, không được nuốt lặng lẽ rồi sinh video sai tham số."""
    path = tmp_path / "typo.yaml"
    path.write_text(
        'prompts:\n  - id: a\n    prompt: "x"\n    aspect_ration: "16:9"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="aspect_ration"):
        build_specs(_cfg(path))


def test_id_chua_ky_tu_khong_hop_le_bi_chan(tmp_path: Path) -> None:
    path = tmp_path / "badid.yaml"
    path.write_text('prompts:\n  - id: "a/b"\n    prompt: "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        build_specs(_cfg(path))


# ------------------------------------------------------------------- vân tay
def _spec(**kwargs) -> VideoSpec:
    base = {"id": "x", "prompt": "một con mèo"}
    return VideoSpec(**{**base, **kwargs})


def test_van_tay_khong_doi_khi_sua_ghi_chu() -> None:
    """Sửa notes/tags KHÔNG được làm mất kết quả cũ -- render lại là tốn tiền."""
    a = _spec(notes="phiên bản một", tags=["x"])
    b = _spec(notes="ghi chú hoàn toàn khác", tags=["y", "z"])
    assert a.fingerprint("flow_browser") == b.fingerprint("flow_browser")


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", "một con chó"),
        ("resolution", "720p"),
        ("aspect_ratio", "9:16"),
        ("duration_seconds", 12),
        ("outputs_per_prompt", 2),
        ("seed", 42),
        ("negative_prompt", "mờ"),
    ],
)
def test_van_tay_doi_khi_sua_tham_so_anh_huong_dau_ra(field: str, value) -> None:
    before = _spec().fingerprint("flow_browser")
    after = _spec(**{field: value}).fingerprint("flow_browser")
    assert before != after, f"Sửa '{field}' phải làm vân tay đổi"


def test_van_tay_khac_nhau_giua_hai_backend() -> None:
    """Cùng prompt nhưng chạy backend khác -> phải sinh lại, không dùng lại."""
    spec = _spec()
    assert spec.fingerprint("flow_browser") != spec.fingerprint("gemini_api")


# --------------------------------------------------------------------- lọc
def test_loc_theo_only_va_limit(prompt_file: Path) -> None:
    specs = build_specs(_cfg(prompt_file))
    assert [s.id for s in filter_specs(specs, only=["beta"])] == ["beta"]
    assert [s.id for s in filter_specs(specs, limit=1)] == ["alpha"]


def test_only_voi_id_khong_ton_tai_bao_loi(prompt_file: Path) -> None:
    specs = build_specs(_cfg(prompt_file))
    with pytest.raises(ConfigError, match="khong-co-that|không tồn tại"):
        filter_specs(specs, only=["khong-co-that"])
