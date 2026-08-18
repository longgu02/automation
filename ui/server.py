"""Máy chủ nhỏ phục vụ giao diện kéo thả.

Chỉ dùng thư viện chuẩn của Python -- không Flask, không FastAPI. Đây là công cụ
chạy tại máy bạn, phục vụ một trang HTML và bảy đường dẫn API; kéo thêm một
framework web vào chỉ để làm chừng đó việc là không đáng.

CHỈ NGHE TRÊN 127.0.0.1. Máy chủ này đọc ghi file trong project và chạy được
module -- tuyệt đối không mở ra mạng ngoài.

API:
    GET  /api/modules              danh mục module (từ core/registry.py)
    GET  /api/jobs                 danh sách file job đã lưu
    GET  /api/jobs/<tên>           một job
    POST /api/jobs/<tên>           lưu job (JSON -> YAML)
    DELETE /api/jobs/<tên>         xoá job
    POST /api/validate             soát sơ đồ, trả về danh sách vấn đề
    POST /api/dryrun               chạy thử job, trả về log

VÌ SAO KHÔNG CÓ NÚT "CHẠY THẬT": bấm một nút trên trang web mà đốt credit
Gemini hoặc mở phiên cào Pinterest là cái bẫy. Chạy thật luôn đi qua dòng lệnh,
nơi bạn thấy log trực tiếp và Ctrl+C được. UI hiện sẵn câu lệnh để bạn chép.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from core.errors import AutomationError, ConfigError
from core.job import JobDefinition, JobRunner
from core.module import ModuleContext
from core.paths import CONFIG_DIR, OUTPUT_DIR, ensure_dir, new_run_id
from core.registry import registry_as_json

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
JOBS_DIR = CONFIG_DIR / "jobs"

_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class UIHandler(BaseHTTPRequestHandler):
    """Xử lý yêu cầu. Mỗi phương thức ngắn và làm đúng một việc."""

    server_version = "AutomationUI/1.0"

    # ------------------------------------------------------------ tiện ích
    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Thân yêu cầu không phải JSON hợp lệ: {exc}") from exc

    def log_message(self, fmt: str, *args) -> None:
        # Cho log của máy chủ đi vào hệ thống log chung, đừng in thẳng ra stderr.
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        path = urlparse(self.path).path

        try:
            if path == "/api/modules":
                return self._send_json({"ok": True, "modules": registry_as_json()})
            if path == "/api/jobs":
                return self._send_json({"ok": True, "jobs": self._list_jobs()})
            if path.startswith("/api/jobs/"):
                return self._get_job(unquote(path[len("/api/jobs/"):]))
            return self._serve_static(path)
        except AutomationError as exc:
            self._send_error_json(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Lỗi khi xử lý GET %s", path)
            self._send_error_json(f"Lỗi máy chủ: {exc}", 500)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()

        # Chặn thoát khỏi thư mục static (../../ trong URL).
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_error(404, "Not found")
            return

        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # sửa file là F5 thấy ngay
        self.end_headers()
        self.wfile.write(body)

    def _list_jobs(self) -> list[dict]:
        ensure_dir(JOBS_DIR)
        jobs = []
        for path in sorted(JOBS_DIR.glob("*.yaml")):
            try:
                job = JobDefinition.load(path)
                jobs.append({
                    "file": path.stem,
                    "name": job.name,
                    "description": job.description,
                    "nodes": len(job.nodes),
                })
            except AutomationError as exc:
                jobs.append({"file": path.stem, "name": path.stem, "error": str(exc), "nodes": 0})
        return jobs

    def _get_job(self, name: str) -> None:
        path = self._job_path(name)
        if not path.exists():
            return self._send_error_json(f"Không có job tên '{name}'", 404)
        job = JobDefinition.load(path)
        self._send_json({"ok": True, "job": job.model_dump(mode="json")})

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/validate":
                return self._validate(payload)
            if path == "/api/dryrun":
                return self._dry_run(payload)
            if path.startswith("/api/jobs/"):
                return self._save_job(unquote(path[len("/api/jobs/"):]), payload)
            self._send_error_json(f"Không có đường dẫn {path}", 404)
        except AutomationError as exc:
            self._send_error_json(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Lỗi khi xử lý POST %s", path)
            self._send_error_json(f"Lỗi máy chủ: {exc}", 500)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/jobs/"):
            return self._send_error_json("Không hỗ trợ", 404)
        target = self._job_path(unquote(path[len("/api/jobs/"):]))
        if target.exists():
            target.unlink()
        self._send_json({"ok": True})

    def _parse_job(self, payload: dict) -> JobDefinition:
        try:
            return JobDefinition.model_validate(payload.get("job") or payload)
        except Exception as exc:
            raise ConfigError(f"Sơ đồ không hợp lệ: {exc}") from exc

    def _validate(self, payload: dict) -> None:
        job = self._parse_job(payload)
        problems = job.validate_graph()
        order: list[str] = []
        if not problems:
            order = [n.label or n.id for n in job.execution_order() if n.enabled]
        self._send_json({"ok": True, "problems": problems, "order": order})

    def _save_job(self, name: str, payload: dict) -> None:
        job = self._parse_job(payload)
        path = job.save(self._job_path(name))
        log.info("Đã lưu job '%s' -> %s", name, path)
        self._send_json({"ok": True, "path": str(path), "file": path.stem})

    def _dry_run(self, payload: dict) -> None:
        """Chạy thử cả job, thu lại log rồi trả về cho UI hiển thị.

        Chạy thử là an toàn theo thiết kế: mọi module đều tôn trọng
        `ctx.dry_run` và không mở trình duyệt, không gọi ffmpeg, không tốn credit.
        """
        job = self._parse_job(payload)
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        handler.setLevel(logging.INFO)

        root = logging.getLogger()
        root.addHandler(handler)
        try:
            ctx = ModuleContext(
                run_id=new_run_id(),
                workdir=OUTPUT_DIR,
                logger=logging.getLogger("job"),
                dry_run=True,
            )
            results = JobRunner().run(job, ctx)
            summary = {node_id: r.status.value for node_id, r in results.items()}
            ok = True
        except AutomationError as exc:
            buffer.write(f"\nLỖI: {exc}\n")
            summary, ok = {}, False
        finally:
            root.removeHandler(handler)

        self._send_json({"ok": ok, "log": buffer.getvalue(), "results": summary})

    @staticmethod
    def _job_path(name: str) -> Path:
        """Đường dẫn file job, chặn mọi mưu toan thoát ra khỏi thư mục jobs."""
        safe = Path(name).name.removesuffix(".yaml")
        if not safe or safe.startswith("."):
            raise ConfigError(f"Tên job không hợp lệ: '{name}'")
        return ensure_dir(JOBS_DIR) / f"{safe}.yaml"


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Khởi động máy chủ. Chặn tới khi Ctrl+C."""
    ensure_dir(JOBS_DIR)
    httpd = ThreadingHTTPServer((host, port), UIHandler)
    url = f"http://{host}:{port}/"

    print("=" * 66)
    print("GIAO DIỆN SƠ ĐỒ MODULE")
    print("=" * 66)
    print(f"  Địa chỉ    : {url}")
    print(f"  Job lưu ở  : {JOBS_DIR}")
    print("  Dừng       : Ctrl+C")
    print()
    print("  Chỉ nghe trên máy này (127.0.0.1), không mở ra mạng ngoài.")
    print()

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng máy chủ.")
    finally:
        httpd.server_close()
