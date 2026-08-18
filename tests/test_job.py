"""Kiểm thử định nghĩa job và bộ chạy job.

Đây là tầng mà giao diện kéo thả ghi ra và bộ chạy đọc vào, nên sai ở đây là
sai cả hai đầu. Trọng tâm test:

  1. Thứ tự tô-pô có đúng không (sai = module chạy trước khi có dữ liệu)
  2. Vòng lặp có bị bắt không (sai = treo hoặc bỏ sót nút)
  3. Ghi đè từ UI có THẬT SỰ tới được config của module không
     -- dễ hỏng lặng lẽ nhất: UI hiện là đã đặt, mà module vẫn chạy giá trị cũ.

    python -m pytest tests/test_job.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.errors import ConfigError  # noqa: E402
from core.job import JobDefinition, JobEdge, JobNode, JobRunner, _expand_dotted  # noqa: E402
from core.registry import REGISTRY, describe_fields, get_spec  # noqa: E402


def _node(node_id: str, module: str = "video_gen", **kwargs) -> JobNode:
    return JobNode(id=node_id, module=module, **kwargs)


def _job(nodes, edges=(), **kwargs) -> JobDefinition:
    return JobDefinition(
        name="test",
        nodes=list(nodes),
        edges=[JobEdge(source=s, target=t) for s, t in edges],
        **kwargs,
    )


# ===========================================================================
# Thứ tự chạy
# ===========================================================================
def test_chuoi_thang_chay_dung_thu_tu() -> None:
    job = _job(
        [_node("a"), _node("b", "video_export"), _node("c", "image_crawl")],
        [("a", "b"), ("b", "c")],
    )
    assert [n.id for n in job.execution_order()] == ["a", "b", "c"]


def test_thu_tu_khai_bao_khong_quyet_dinh_thu_tu_chay() -> None:
    """Kéo nút vào sơ đồ theo thứ tự nào cũng được -- cạnh mới quyết định."""
    job = _job([_node("cuoi"), _node("dau")], [("dau", "cuoi")])
    assert [n.id for n in job.execution_order()] == ["dau", "cuoi"]


def test_nut_roi_rac_van_duoc_chay() -> None:
    """Ba nút không nối gì là ba việc độc lập, không phải lỗi."""
    job = _job([_node("a"), _node("b"), _node("c")])
    assert len(job.execution_order()) == 3


def test_nhieu_nhanh_gop_lai_mot_nut() -> None:
    """a và b cùng chạy trước c -- c phải nằm sau CẢ HAI."""
    job = _job(
        [_node("a"), _node("b"), _node("c")],
        [("a", "c"), ("b", "c")],
    )
    order = [n.id for n in job.execution_order()]
    assert order.index("c") > order.index("a")
    assert order.index("c") > order.index("b")


def test_thu_tu_on_dinh_giua_cac_lan_chay() -> None:
    """Hai lần chạy cùng một job phải cho cùng thứ tự -- nếu không thì không
    tái lập được sự cố."""
    job = _job([_node("a"), _node("b"), _node("c")])
    assert [n.id for n in job.execution_order()] == [n.id for n in job.execution_order()]


# ===========================================================================
# Vòng lặp
# ===========================================================================
def test_vong_lap_bi_bat_va_chi_ro_duong_di() -> None:
    job = _job([_node("a"), _node("b")], [("a", "b"), ("b", "a")])

    problems = job.validate_graph()
    assert any("vòng lặp" in p for p in problems)
    assert any("a → b → a" in p for p in problems)

    with pytest.raises(ConfigError, match="vòng lặp"):
        job.execution_order()


def test_vong_lap_dai_cung_bi_bat() -> None:
    job = _job(
        [_node("a"), _node("b"), _node("c")],
        [("a", "b"), ("b", "c"), ("c", "a")],
    )
    assert any("vòng lặp" in p for p in job.validate_graph())


def test_nut_tu_noi_vao_chinh_no() -> None:
    job = _job([_node("a")], [("a", "a")])
    assert any("nối vào chính nó" in p for p in job.validate_graph())


# ===========================================================================
# Soát sơ đồ
# ===========================================================================
def test_id_trung_bi_bat() -> None:
    job = _job([_node("a"), _node("a")])
    assert any("trùng" in p for p in job.validate_graph())


def test_canh_tro_toi_nut_khong_ton_tai() -> None:
    job = _job([_node("a")], [("a", "khong-co")])
    assert any("không tồn tại" in p for p in job.validate_graph())


def test_module_khong_co_trong_so_dang_ky() -> None:
    job = _job([JobNode(id="a", module="module-ma")])
    assert any("sổ đăng ký" in p for p in job.validate_graph())


def test_canh_bao_khi_thieu_du_lieu_dau_vao() -> None:
    """video_export cần `videos` mà không nút nào trước nó tạo ra."""
    job = _job([_node("x", "video_export")])
    problems = job.validate_graph()
    assert any("videos" in p and "chưa nút nào" in p for p in problems)


def test_noi_dung_nguon_thi_het_canh_bao() -> None:
    job = _job([_node("gen"), _node("exp", "video_export")], [("gen", "exp")])
    assert job.validate_graph() == []


def test_nut_bi_tat_khong_duoc_tinh_la_nguon_du_lieu() -> None:
    """Tắt video_gen thì video_export lại thành thiếu đầu vào -- phải cảnh báo."""
    job = _job(
        [_node("gen", enabled=False), _node("exp", "video_export")],
        [("gen", "exp")],
    )
    assert any("videos" in p for p in job.validate_graph())


# ===========================================================================
# GHI ĐÈ -- chỗ dễ hỏng lặng lẽ nhất
# ===========================================================================
def test_khoa_cham_thanh_dict_long_nhau() -> None:
    assert _expand_dotted({"a.b.c": 1, "a.b.d": 2, "x": 3}) == {
        "a": {"b": {"c": 1, "d": 2}}, "x": 3
    }


def test_ghi_de_thuc_su_toi_duoc_config_cua_module() -> None:
    """Test quan trọng nhất trong file.

    UI hiện "đã đặt aspect = 9:16" mà module vẫn chạy 16:9 là kiểu hỏng tệ nhất:
    không báo lỗi, chỉ ra kết quả sai. Test này dựng module thật từ một nút và
    kiểm giá trị trong config của nó.
    """
    node = JobNode(
        id="exp",
        module="video_export",
        overrides={
            "video.target_aspect_ratio": "9:16",
            "video.target_resolution": "720p",
            "encode.crf": 18,
        },
    )
    module = JobRunner()._build_module(node)

    assert module.cfg.video.target_aspect_ratio == "9:16"
    assert module.cfg.video.target_resolution == "720p"
    assert module.cfg.encode.crf == 18


def test_khong_ghi_de_thi_giu_nguyen_gia_tri_trong_file() -> None:
    module = JobRunner()._build_module(JobNode(id="exp", module="video_export"))
    assert module.cfg.video.target_aspect_ratio == "16:9"  # giá trị trong YAML


def test_ghi_de_sai_gia_tri_bi_bat_kem_ten_nut() -> None:
    """Báo lỗi phải nói rõ nút nào, vì một job có nhiều nút cùng loại module."""
    node = JobNode(id="nut-hong", module="video_export",
                   overrides={"video.target_aspect_ratio": "tỉ lệ bịa"})
    with pytest.raises(ConfigError, match="nut-hong"):
        JobRunner()._build_module(node)


# ===========================================================================
# Đọc / ghi file
# ===========================================================================
def test_luu_roi_doc_lai_khong_mat_gi(tmp_path: Path) -> None:
    original = _job(
        [
            _node("a", label="Nhãn tiếng Việt", overrides={"execution.max_parallel": 3}),
            _node("b", "video_export", position={"x": 420.0, "y": 88.0}),
        ],
        [("a", "b")],
    )
    path = original.save(tmp_path / "j.yaml")
    loaded = JobDefinition.load(path)

    assert loaded.model_dump() == original.model_dump()
    assert loaded.nodes[0].label == "Nhãn tiếng Việt"
    assert loaded.nodes[0].overrides == {"execution.max_parallel": 3}
    assert loaded.nodes[1].position.x == 420.0


def test_file_job_sai_bi_bao_loi_ro_rang(tmp_path: Path) -> None:
    path = tmp_path / "hong.yaml"
    path.write_text("name: x\nnodes:\n  - module: video_gen\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="không hợp lệ"):
        JobDefinition.load(path)


def test_khoa_la_trong_file_job_bi_chan(tmp_path: Path) -> None:
    path = tmp_path / "la.yaml"
    path.write_text("name: x\nnodes: []\nedges: []\nkhoa_bia_dat: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        JobDefinition.load(path)


# ===========================================================================
# Sổ đăng ký
# ===========================================================================
def test_moi_module_trong_so_deu_nap_duoc_lop_cau_hinh() -> None:
    """Sổ giữ chuỗi đường dẫn, nên gõ sai chỉ lộ ra lúc chạy. Test này chốt lại."""
    for name, spec in REGISTRY.items():
        config_class = spec.load_config_class()
        assert config_class.model_fields, f"{name}: lớp cấu hình rỗng?"


def test_moi_module_deu_bao_duoc_truong_chinh_duoc() -> None:
    for name, spec in REGISTRY.items():
        fields = describe_fields(spec.load_config_class())
        assert fields, f"{name}: không bóc được trường nào cho UI"
        assert all("path" in f and "type" in f for f in fields)


def test_module_khong_ton_tai_bao_loi_kem_goi_y() -> None:
    with pytest.raises(ConfigError, match="video_gen"):
        get_spec("khong-co-that")


def test_hop_dong_noi_day_giua_hai_module_khop_nhau() -> None:
    """video_export đọc đúng khoá mà video_gen ghi ra -- nếu lệch thì sơ đồ nối
    được nhưng dữ liệu không chảy, và không ai báo gì."""
    assert set(get_spec("video_export").reads) <= set(get_spec("video_gen").writes)
