"""Cấu hình đọc từ biến môi trường (xem README / .env.example)."""
import os
import sys
from pathlib import Path

# True khi chạy trong trình duyệt bằng Pyodide (bản GitHub Pages).
IS_BROWSER = sys.platform == "emscripten"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'inventory.db'}")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# OCR: auto | anthropic | openai | demo
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "auto").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Đặt APP_PASSWORD để bật đăng nhập (nên bật khi deploy công khai).
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

SEED_DEMO = os.getenv("SEED_DEMO", "1") == "1"
CURRENCY = os.getenv("CURRENCY", "€")
EXPIRY_WARNING_DAYS = int(os.getenv("EXPIRY_WARNING_DAYS", "3"))

# Cảnh báo hàng sắp hết qua email (SMTP). Với Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587,
# SMTP_USER=<gmail của bạn>, SMTP_PASSWORD=<App Password 16 ký tự>.
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "dothanhdatdo@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER
SMTP_SSL = os.getenv("SMTP_SSL", "0") == "1"  # 1 = SMTPS cổng 465, 0 = STARTTLS
APP_URL = os.getenv("APP_URL", "")
