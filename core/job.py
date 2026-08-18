"""Định nghĩa job và bộ chạy job -- biến sơ đồ thành việc thật.

MÔ HÌNH: một job là một ĐỒ THỊ. Mỗi nút là một module, mỗi cạnh là quan hệ
"chạy sau". Bộ chạy sắp thứ tự bằng sắp xếp tô-pô rồi chạy TUẦN TỰ.

    ┌────────────┐      ┌──────────────┐
    │ video_gen  │─────>│ video_export │
    └────────────┘      └──────────────┘
          ▲
    ┌────────────┐            cạnh = "chạy sau"
    │image_crawl │            (không phải "truyền dữ liệu")
    └────────────┘

HAI THỨ KHÁC NHAU, ĐỪNG LẪN:

  * *Cạnh* quyết định THỨ TỰ chạy.
  * *Dữ liệu* đi qua `ctx.shared` -- một cái từ điển dùng chung cho cả job.
    Module ghi vào đó bằng khoá của nó (`videos`, `final_video`, `images`),
    module sau đọc ra.

Tách hai thứ này giúp bạn nối tuỳ ý mà ngữ nghĩa vẫn rõ: kéo một cạnh chỉ có
nghĩa "chạy sau cái kia", còn dữ liệu tự tìm thấy nhau qua khoá đã đặt tên.

VÌ SAO CHẠY TUẦN TỰ chứ không song song các nhánh: `video_gen` đã tự lo phần
song song ở bên trong nó (nhiều tài khoản). Chạy song song thêm ở tầng job chỉ
làm hai module cùng tranh trình duyệt và tranh credit, mà chẳng nhanh hơn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import deep_merge, expand_env, load_yaml
from core.errors import ConfigError
from core.module import ModuleContext, ModuleResult, ModuleStatus
from core.paths import PROJECT_ROOT, ensure_dir
from core.registry import get_spec

log = logging.getLogger(__name__)


# ==========================================================================
# Định nghĩa job
# ==========================================================================
class NodePosition(BaseModel):
    """Toạ độ trên sơ đồ. Bộ chạy hoàn toàn bỏ qua, chỉ UI dùng."""

    model_config = ConfigDict(extra="forbid")
    x: float = 0
    y: float = 0


class JobNode(BaseModel):
    """Một module trong job."""

    model_config = ConfigDict(extra="forbid")

    #: Định danh duy nhất trong job. Cạnh tham chiếu tới nó.
    id: str
    #: Tên module trong sổ đăng ký.
    module: str
    #: Nhãn hiển thị. Bỏ trống -> lấy tiêu đề của module.
    label: str = ""
    #: File cấu hình. Bỏ trống -> dùng file mặc định của module.
    config_file: str | None = None
    #: Ghi đè lên file cấu hình, khoá dùng dấu chấm: {"browser.headless": true}
    overrides: dict[str, Any] = Field(default_factory=dict)
    #: Tắt tạm một nút mà không cần xoá nó khỏi sơ đồ.
    enabled: bool = True
    position: NodePosition = Field(default_factory=NodePosition)


class JobEdge(BaseModel):
    """Một cạnh: `target` chạy sau `source`."""

    model_config = ConfigDict(extra="forbid")
    source: str
    target: str


class JobDefinition(BaseModel):
    """Toàn bộ một job."""

    model_config = ConfigDict(extra="forbid")

    name: str = "job-khong-ten"
    description: str = ""
    nodes: list[JobNode] = Field(default_factory=list)
    edges: list[JobEdge] = Field(default_factory=list)
    #: Một module hỏng thì dừng cả job hay chạy tiếp các nút còn lại?
    continue_on_error: bool = False

    # ------------------------------------------------------------ kiểm tra
    def validate_graph(self) -> list[str]:
        """Soát sơ đồ, trả về danh sách vấn đề bằng tiếng Việt.

        Danh sách RỖNG nghĩa là chạy được. Đây là thứ nút "Kiểm tra" trong UI
        gọi tới, và bộ chạy cũng gọi trước khi bắt đầu.
        """
        problems: list[str] = []
        ids = [n.id for n in self.nodes]

        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            problems.append(f"Id nút bị trùng: {', '.join(sorted(duplicates))}")

        known = set(ids)
        for edge in self.edges:
            if edge.source not in known:
                problems.append(f"Cạnh trỏ tới nút không tồn tại: '{edge.source}'")
            if edge.target not in known:
                problems.append(f"Cạnh trỏ tới nút không tồn tại: '{edge.target}'")
            if edge.source == edge.target:
                problems.append(f"Nút '{edge.source}' nối vào chính nó")

        for node in self.nodes:
            try:
                get_spec(node.module)
            except ConfigError as exc:
                problems.append(str(exc))

        cycle = self._find_cycle()
        if cycle:
            problems.append("Sơ đồ có vòng lặp: " + " → ".join(cycle))

        problems.extend(self._check_data_flow())
        return problems

    def _check_data_flow(self) -> list[str]:
        """Cảnh báo khi một module cần dữ liệu mà không nút nào trước nó tạo ra.

        Đây là CẢNH BÁO chứ không phải lỗi chặn: module như `video_export` vẫn
        có đường lui (tự quét đĩa) khi không nhận được gì từ module trước.
        """
        warnings: list[str] = []
        try:
            order = self.execution_order()
        except ConfigError:
            return warnings  # có vòng lặp rồi, đã báo ở chỗ khác

        available: set[str] = set()
        for node in order:
            if not node.enabled:
                continue
            try:
                spec = get_spec(node.module)
            except ConfigError:
                # Module lạ đã được báo ở vòng kiểm tra phía trên. Ở đây chỉ bỏ
                # qua -- hàm soát sơ đồ phải LUÔN trả về danh sách vấn đề, không
                # bao giờ được ném lỗi, nếu không nút "Kiểm tra" trong UI sẽ hiện
                # lỗi máy chủ thay vì nói cho người dùng biết cái gì sai.
                continue
            missing = [key for key in spec.reads if key not in available]
            if missing:
                warnings.append(
                    f"'{node.label or node.id}' cần {missing} nhưng chưa nút nào "
                    f"trước nó tạo ra. Module sẽ dùng đường lui của nó (nếu có)."
                )
            available.update(spec.writes)
        return warnings

    def _find_cycle(self) -> list[str]:
        """Tìm một vòng lặp bất kỳ, trả về đường đi để hiện cho người dùng."""
        outgoing: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for edge in self.edges:
            if edge.source in outgoing and edge.target in outgoing:
                outgoing[edge.source].append(edge.target)

        WHITE, GREY, BLACK = 0, 1, 2
        colour = {node_id: WHITE for node_id in outgoing}
        path: list[str] = []

        def visit(node_id: str) -> list[str]:
            colour[node_id] = GREY
            path.append(node_id)
            for nxt in outgoing[node_id]:
                if colour[nxt] == GREY:  # gặp lại nút đang trên đường đi -> vòng
                    return path[path.index(nxt):] + [nxt]
                if colour[nxt] == WHITE:
                    found = visit(nxt)
                    if found:
                        return found
            path.pop()
            colour[node_id] = BLACK
            return []

        for node_id in outgoing:
            if colour[node_id] == WHITE:
                found = visit(node_id)
                if found:
                    return found
        return []

    # ------------------------------------------------------------ thứ tự
    def execution_order(self) -> list[JobNode]:
        """Sắp xếp tô-pô: nút nào cũng chạy sau mọi nút trỏ vào nó.

        Nút không nối gì cả vẫn chạy -- một sơ đồ gồm ba nút rời rạc là ba việc
        độc lập, không phải lỗi. Thứ tự giữa các nút ngang hàng bám theo thứ tự
        khai báo, để hai lần chạy giống nhau cho kết quả giống nhau.
        """
        by_id = {n.id: n for n in self.nodes}
        incoming = {n.id: 0 for n in self.nodes}
        outgoing: dict[str, list[str]] = {n.id: [] for n in self.nodes}

        for edge in self.edges:
            if edge.source in by_id and edge.target in by_id:
                outgoing[edge.source].append(edge.target)
                incoming[edge.target] += 1

        ready = [n.id for n in self.nodes if incoming[n.id] == 0]
        order: list[JobNode] = []

        while ready:
            current = ready.pop(0)
            order.append(by_id[current])
            for nxt in outgoing[current]:
                incoming[nxt] -= 1
                if incoming[nxt] == 0:
                    ready.append(nxt)

        if len(order) != len(self.nodes):
            cycle = self._find_cycle()
            raise ConfigError(
                "Sơ đồ có vòng lặp nên không xác định được thứ tự chạy: "
                + (" → ".join(cycle) if cycle else "(không xác định được vòng)")
            )
        return order

    # ------------------------------------------------------------ đọc/ghi
    @classmethod
    def load(cls, path: Path) -> JobDefinition:
        raw = expand_env(load_yaml(path))
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            details = "\n".join(
                f"  - {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ConfigError(f"File job không hợp lệ ({path}):\n{details}") from exc

    def save(self, path: Path) -> Path:
        ensure_dir(path.parent)
        payload = self.model_dump(mode="json")
        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path


# ==========================================================================
# Bộ chạy job
# ==========================================================================
class JobRunner:
    """Chạy các nút theo thứ tự tô-pô, dùng chung một `ModuleContext`."""

    def __init__(self, logger: logging.Logger | None = None):
        self.log = logger or log

    def run(self, job: JobDefinition, ctx: ModuleContext) -> dict[str, ModuleResult]:
        """Chạy cả job. Trả về kết quả từng nút, khoá là id nút."""
        problems = [p for p in job.validate_graph() if not p.startswith("'")]
        if problems:
            raise ConfigError("Sơ đồ job không chạy được:\n  - " + "\n  - ".join(problems))

        order = [n for n in job.execution_order() if n.enabled]
        skipped = len(job.nodes) - len(order)

        self.log.info("=" * 66)
        self.log.info("JOB: %s -- %d nút%s", job.name, len(order), f" ({skipped} bị tắt)" if skipped else "")
        self.log.info("Thứ tự chạy: %s", " → ".join(n.label or n.id for n in order))
        self.log.info("=" * 66)

        results: dict[str, ModuleResult] = {}

        for position, node in enumerate(order, start=1):
            label = node.label or node.id
            self.log.info("")
            self.log.info("--- [%d/%d] %s (%s) ---", position, len(order), label, node.module)

            try:
                module = self._build_module(node)
                result = module.execute(ctx)
            except Exception as exc:  # noqa: BLE001
                # Một module hỏng không được làm mất kết quả của những module
                # đã chạy xong trước nó.
                self.log.error("[%s] hỏng: %s", label, exc)
                results[node.id] = ModuleResult(
                    status=ModuleStatus.FAILED, errors=[str(exc)]
                )
                if not job.continue_on_error:
                    self.log.error("Dừng job. Đặt `continue_on_error: true` để chạy tiếp.")
                    break
                continue

            results[node.id] = result
            if result.status is ModuleStatus.FAILED and not job.continue_on_error:
                self.log.error("[%s] thất bại -- dừng job.", label)
                break

        self._log_summary(job, results)
        return results

    # ------------------------------------------------------------------
    def _build_module(self, node: JobNode):
        """Nạp cấu hình của một nút rồi dựng module.

        Thứ tự ưu tiên: file cấu hình <- ghi đè khai trong nút.
        """
        spec = get_spec(node.module)
        config_file = Path(node.config_file or spec.default_config_file)
        if not config_file.is_absolute():
            config_file = PROJECT_ROOT / config_file

        raw = expand_env(load_yaml(config_file))
        if node.overrides:
            raw = deep_merge(raw, _expand_dotted(node.overrides))

        config_class = spec.load_config_class()
        try:
            config = config_class.model_validate(raw)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ConfigError(f"Nút '{node.id}': cấu hình sai -- {details}") from exc

        return spec.load_module_class()(config)

    def _log_summary(self, job: JobDefinition, results: dict[str, ModuleResult]) -> None:
        self.log.info("")
        self.log.info("=" * 66)
        self.log.info("TỔNG KẾT JOB: %s", job.name)
        self.log.info("=" * 66)
        by_id = {n.id: n for n in job.nodes}
        for node_id, result in results.items():
            label = by_id[node_id].label or node_id if node_id in by_id else node_id
            self.log.info("  %-24s %-8s %s", label, result.status.value, result.stats or "")


def _expand_dotted(flat: dict[str, Any]) -> dict[str, Any]:
    """Biến {"browser.headless": true} thành {"browser": {"headless": True}}.

    UI ghi đè bằng khoá phẳng có dấu chấm vì nó dễ hiển thị và dễ sửa; còn
    Pydantic cần dict lồng nhau. Chỗ chuyển đổi nằm ở đây, một chỗ duy nhất.
    """
    nested: dict[str, Any] = {}
    for dotted, value in flat.items():
        parts = dotted.split(".")
        cursor = nested
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                raise ConfigError(f"Ghi đè '{dotted}' xung đột với một khoá khác.")
        cursor[parts[-1]] = value
    return nested
