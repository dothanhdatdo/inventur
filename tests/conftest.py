import os
import sys
import tempfile
from pathlib import Path

# Mỗi lần chạy test dùng database tạm, không có dữ liệu mẫu, không SMTP/API thật.
_tmp = tempfile.mkdtemp(prefix="inv-test-")
os.environ.update(
    DATA_DIR=_tmp,
    SEED_DEMO="0",
    APPLY_IMPORTS="0",
    OCR_PROVIDER="demo",
    SMTP_HOST="",
    APP_PASSWORD="",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c
