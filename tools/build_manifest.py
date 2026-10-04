"""Tạo web/manifest.json: danh sách file Python + template mà bản GitHub Pages tải vào Pyodide.

Chạy lại mỗi khi thêm/xoá/sửa file trong app/:  python tools/build_manifest.py
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "web" / "manifest.json"


def collect() -> list[str]:
    files = sorted(ROOT.glob("app/**/*.py")) + sorted(ROOT.glob("app/templates/*.html"))
    return [f.relative_to(ROOT).as_posix() for f in files if "__pycache__" not in f.parts]


def build() -> dict:
    files = collect()
    digest = hashlib.sha256()
    for rel in files:
        digest.update(rel.encode())
        digest.update((ROOT / rel).read_bytes())
    return {"version": digest.hexdigest()[:12], "files": files}


if __name__ == "__main__":
    MANIFEST.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"Đã ghi {MANIFEST.relative_to(ROOT)}")
