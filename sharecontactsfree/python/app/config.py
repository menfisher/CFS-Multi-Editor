from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(os.getenv("SHARE_PROJECT_ROOT", str(BASE_DIR.parent)))


def _resolve_frontend_dir() -> Path:
    configured = os.getenv("SHARE_FRONTEND_DIR")
    if configured:
        return Path(configured)
    for candidate in (
        PROJECT_ROOT / "frontend" / "public",
        BASE_DIR / "frontend" / "public",
    ):
        if candidate.exists():
            return candidate
    return PROJECT_ROOT / "frontend" / "public"


FRONTEND_DIR = _resolve_frontend_dir()

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-only-change-me")
# CFS desktop calls POST /api/cfs/sync-contact-changes with header X-CFS-API-Key.
CFS_API_KEY = os.getenv("CFS_API_KEY", APP_SECRET_KEY).strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

# URL recipients see in invite emails — must be HTTPS in production (Cloud Run).
PUBLIC_WEB_URL = os.getenv("PUBLIC_WEB_URL", APP_BASE_URL).rstrip("/")

# Bumped when frontend import/progress UI changes — visible in footer and browser console.
UI_BUILD_TAG = "card-repair-status-2026-10-27"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

DATABASE_PATH = Path(
    os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "sharecontacts.db"))
)
SHARE_DB_GCS_URI = os.getenv("SHARE_DB_GCS_URI", "").strip()
SHARE_DB_LOCAL_PATH = os.getenv(
    "SHARE_DB_LOCAL_PATH", "/tmp/sharecontacts.db"
).strip()

SEND_SHARE_EMAILS = os.getenv("SEND_SHARE_EMAILS", "true").lower() in {
    "1",
    "true",
    "yes",
}

_BASE_OWNER_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/contacts",
]

# Owner login (web app user) — gmail.send only when invite emails are enabled
OWNER_SCOPES = list(_BASE_OWNER_SCOPES)
if SEND_SHARE_EMAILS:
    OWNER_SCOPES.append("https://www.googleapis.com/auth/gmail.send")

# Recipient connect (import into their account)
RECIPIENT_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/contacts",
]

# GAS used 25 ops / 25s to stay under Apps Script quotas. Self-hosted Python can go faster.
RECIPIENT_IMPORT_BATCH_SIZE = int(os.getenv("RECIPIENT_IMPORT_BATCH_SIZE", "200"))
RECIPIENT_IMPORT_CREATE_BATCH_SIZE = int(
    os.getenv("RECIPIENT_IMPORT_CREATE_BATCH_SIZE", "200")
)
# Smaller create chunks for small groups so progress polls see importCursor advance.
RECIPIENT_IMPORT_PROGRESS_BATCH_SIZE = int(
    os.getenv("RECIPIENT_IMPORT_PROGRESS_BATCH_SIZE", "5")
)
# Groups larger than this use full create batch size (up to 200), not progress chunks.
RECIPIENT_IMPORT_LARGE_GROUP_THRESHOLD = int(
    os.getenv("RECIPIENT_IMPORT_LARGE_GROUP_THRESHOLD", "100")
)
RECIPIENT_IMPORT_MAX_OPS_PER_LOAD = int(
    os.getenv("RECIPIENT_IMPORT_MAX_OPS_PER_LOAD", "200")
)
# One import pass may use the remaining kick slice. Keep under Cloud Run 300s
# and the 240s scheduler deadline; the 4-minute kick spacing leaves slack.
RECIPIENT_IMPORT_TIME_BUDGET_MS = int(
    os.getenv("RECIPIENT_IMPORT_TIME_BUDGET_MS", "180000")
)
RECIPIENT_IMPORT_MAX_RETRIES = 5
RECIPIENT_IMPORT_INCLUDE_PHOTOS = (
    str(os.getenv("RECIPIENT_IMPORT_INCLUDE_PHOTOS", "true")).strip().lower()
    not in ("0", "false", "no", "off")
)
RECIPIENT_IMPORT_PHOTO_BATCH_SIZE = int(
    os.getenv("RECIPIENT_IMPORT_PHOTO_BATCH_SIZE", "20")
)
RECIPIENT_IMPORT_PHOTO_FETCH_WORKERS = int(
    os.getenv("RECIPIENT_IMPORT_PHOTO_FETCH_WORKERS", "16")
)
RECIPIENT_IMPORT_PHOTO_HTTP_BATCH_SIZE = int(
    os.getenv("RECIPIENT_IMPORT_PHOTO_HTTP_BATCH_SIZE", "20")
)
# Hard socket timeout (seconds) for every Google People API call. Without this the
# underlying httplib2 transport has no timeout, so a single stalled connection (e.g.
# a hung updateContactPhoto) blocks the background push worker thread forever.
PEOPLE_API_HTTP_TIMEOUT = int(os.getenv("PEOPLE_API_HTTP_TIMEOUT", "90"))
OWNER_SYNC_BATCH_SIZE = int(os.getenv("OWNER_SYNC_BATCH_SIZE", "200"))

# Max seconds the scheduled /api/cfs/kick endpoint spends draining the push queue
# *within the request*. With request-based (CPU-throttled) billing the instance is
# only billed while a request is active, so doing the work inside the kick request
# lets the instance scale to zero between pings instead of staying always-on.
# Kept comfortably under Cloud Run's default 300s request timeout.
KICK_DRAIN_SECONDS = int(os.getenv("SHARE_KICK_DRAIN_SECONDS", "200"))
# When set, claim this recipient's queued jobs before anyone else's.
SHARE_JOB_PRIORITY_EMAIL = str(os.getenv("SHARE_JOB_PRIORITY_EMAIL", "")).strip().lower()
