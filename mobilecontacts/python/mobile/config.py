from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
CFS_PROJECT_ROOT = Path(os.getenv("CFS_PROJECT_ROOT", str(BASE_DIR.parent.parent))).resolve()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-only-change-me")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")).strip()
GOOGLE_CLIENT_SECRET = os.getenv(
    "GOOGLE_CLIENT_SECRET",
    os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
).strip()
DATA_ROOT = Path(os.getenv("DATA_ROOT", str(BASE_DIR / "data"))).expanduser().resolve()

OWNER_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/drive.file",
]


def email_slug(email: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(email or "").strip().lower())
    return normalized or "unknown"


def user_db_path(email: str) -> Path:
    return DATA_ROOT / "users" / email_slug(email) / "app.sqlite3"
