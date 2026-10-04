"""Chạy ứng dụng FastAPI ngay trong trình duyệt (Pyodide) cho bản GitHub Pages.

Trình duyệt không có server, nên web/runtime.js chặn mọi cú click/submit, gọi
`handle()` ở đây với request dạng ASGI, rồi hiển thị HTML trả về.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/data")

import anyio.to_thread  # noqa: E402

# Pyodide không có luồng (thread): chạy hàm đồng bộ trực tiếp thay vì đẩy sang threadpool.
async def _run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):  # noqa: ARG001
    return func(*args)


anyio.to_thread.run_sync = _run_sync

from . import config  # noqa: E402
from .db import SessionLocal, init_db  # noqa: E402
from .main import app  # noqa: E402
from .imports import apply_pending as apply_imports  # noqa: E402
from .seed import seed  # noqa: E402
from .services import alerts  # noqa: E402


def startup() -> None:
    init_db()
    if config.SEED_DEMO:
        with SessionLocal() as session:
            seed(session)
    with SessionLocal() as session:
        apply_imports(session)


async def handle(method: str, path: str, query: str, headers: list, body: bytes | None) -> dict:
    """Gọi app như một server ASGI và trả về {status, headers, body}."""
    # Giá trị từ JavaScript tới dạng JsProxy -> đổi sang kiểu Python.
    if hasattr(headers, "to_py"):
        headers = headers.to_py()
    if hasattr(body, "to_bytes"):
        body = body.to_bytes()
    body = bytes(body or b"")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": (query or "").encode(),
        "headers": [(str(k).lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in headers]
        + [(b"host", b"inventur.local"), (b"content-length", str(len(body)).encode())],
        "client": ("127.0.0.1", 0),
        "server": ("inventur.local", 443),
        "state": {},
    }
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    response = {"status": 500, "headers": [], "body": bytearray()}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")

    await app(scope, receive, send)
    response["body"] = bytes(response["body"])
    return response


def set_alert_status(alert_id: int, status: str, detail: str = "") -> None:
    """Được runtime.js gọi sau khi gửi email cảnh báo qua dịch vụ web."""
    alerts.set_status(int(alert_id), status, detail)
