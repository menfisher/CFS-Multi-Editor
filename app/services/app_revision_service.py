from __future__ import annotations

import json
from datetime import datetime
from urllib.error import HTTPError, URLError

from app.config import PROJECT_ROOT
from app.database import fetch_all, get_connection
from app.services.app_update_service import get_app_update_status
from app.services.google_sync_service import (
    export_app_revisions_to_google_drive,
    get_google_sync_summary,
    import_app_revisions_from_google_drive,
)


DEFAULT_APP_REVISIONS = [
    {
        "date_display": "4/20/2026",
        "version": "1.0",
        "description": "made changes to menu items under ContactsFreeShare",
    }
]
APP_REVISION_DISPLAY_LIMIT = 30
PUBLISHED_REVISIONS_PATH = PROJECT_ROOT / "app" / "data" / "published_app_revisions.json"


def ensure_app_revisions() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_revisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sort_order INTEGER NOT NULL,
              date_display TEXT NOT NULL DEFAULT '',
              version_text TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_app_revisions_sort_order
            ON app_revisions (sort_order, id)
            """
        )
        existing = conn.execute("SELECT COUNT(*) AS count FROM app_revisions").fetchone()
        if int(existing["count"]) == 0:
            for index, revision in enumerate(DEFAULT_APP_REVISIONS, start=1):
                conn.execute(
                    """
                    INSERT INTO app_revisions (
                      sort_order,
                      date_display,
                      version_text,
                      description
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        index,
                        str(revision["date_display"]),
                        str(revision["version"]),
                        str(revision["description"]),
                    ),
                )
        conn.commit()


def list_app_revisions(*, limit: int | None = None) -> list[dict]:
    ensure_app_revisions()
    rows = fetch_all(
        """
        SELECT id, sort_order, date_display, version_text, description
        FROM app_revisions
        ORDER BY sort_order DESC, id DESC
        """
    )
    revisions = [
        {
            "id": int(row["id"]),
            "date_display": str(row["date_display"] or ""),
            "version": str(row["version_text"] or ""),
            "description": str(row["description"] or ""),
        }
        for row in rows
    ]
    if limit is not None and limit >= 0:
        revisions = revisions[:limit]
    return revisions


def _normalize_revision(revision: dict) -> dict | None:
    date_display = str(revision.get("date_display") or "").strip()
    version = str(revision.get("version") or "").strip()
    description = str(revision.get("description") or "").strip()
    if not any((date_display, version, description)):
        return None
    return {
        "date_display": date_display,
        "version": version,
        "description": description,
    }


def _sort_revisions_desc(revisions: list[dict]) -> list[dict]:
    def sort_key(revision: dict) -> tuple:
        version = str(revision.get("version") or "").strip()
        if version:
            parts: list[object] = []
            for chunk in version.replace("-", ".").split("."):
                if chunk.isdigit():
                    parts.append(int(chunk))
                elif chunk:
                    parts.append(chunk)
            return (0, tuple(parts), version.lower())
        date_display = str(revision.get("date_display") or "")
        return (1, date_display, "")

    return sorted(revisions, key=sort_key, reverse=True)


def load_bundled_published_revisions() -> list[dict]:
    try:
        payload = json.loads(PUBLISHED_REVISIONS_PATH.read_text(encoding="utf-8"))
    except OSError:
        return []
    except json.JSONDecodeError:
        return []
    revisions = payload.get("revisions") if isinstance(payload, dict) else []
    if not isinstance(revisions, list):
        return []
    normalized: list[dict] = []
    for revision in revisions:
        if not isinstance(revision, dict):
            continue
        item = _normalize_revision(revision)
        if item:
            normalized.append(item)
    return _sort_revisions_desc(normalized)


def append_published_revision_to_bundle(
    *,
    version: str,
    description: str,
    date_display: str,
) -> list[dict]:
    version_text = str(version or "").strip()
    description_text = str(description or "").strip()
    date_text = str(date_display or "").strip()
    if not version_text:
        raise ValueError("version is required")

    existing = load_bundled_published_revisions()
    filtered = [
        revision
        for revision in existing
        if str(revision.get("version") or "").strip() != version_text
    ]
    latest = _sort_revisions_desc(
        [
            {
                "version": version_text,
                "date_display": date_text,
                "description": description_text,
            },
            *filtered,
        ]
    )
    payload = {
        "format": "contactsfreeshare.published_revisions.v1",
        "revisions": latest,
    }
    PUBLISHED_REVISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED_REVISIONS_PATH.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return latest


def _format_manifest_release_date(release_date: str) -> str:
    date_text = str(release_date or "").strip()
    if not date_text:
        return ""
    try:
        parsed = datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return date_text
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


def _manifest_revision() -> dict | None:
    try:
        update_status = get_app_update_status()
    except (RuntimeError, HTTPError, URLError, OSError, ValueError):
        return None
    if not bool(update_status.get("ok")):
        return None
    version = str(update_status.get("latest_version") or "").strip()
    notes = str(update_status.get("notes") or "").strip()
    if not version or not notes:
        return None
    return {
        "date_display": _format_manifest_release_date(str(update_status.get("release_date") or "")),
        "version": version,
        "description": notes,
    }


def merge_revision_sources(*sources: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen_versions: set[str] = set()
    for source in sources:
        for revision in source:
            normalized = _normalize_revision(revision)
            if not normalized:
                continue
            version = normalized["version"]
            if version:
                key = version.lower()
                if key in seen_versions:
                    continue
                seen_versions.add(key)
            merged.append(normalized)
    return _sort_revisions_desc(merged)


def get_display_app_revisions() -> list[dict]:
    bundled = load_bundled_published_revisions()
    local = list_app_revisions(limit=None)
    manifest_revision = _manifest_revision()
    manifest_list = [manifest_revision] if manifest_revision else []
    merged = merge_revision_sources(bundled, manifest_list, local)
    return merged[:APP_REVISION_DISPLAY_LIMIT]


def merge_public_update_revision(revisions: list[dict]) -> list[dict]:
    manifest_revision = _manifest_revision()
    if not manifest_revision:
        return revisions
    version = manifest_revision["version"]
    notes = manifest_revision["description"]
    existing_revision = next(
        (
            revision
            for revision in revisions
            if str(revision.get("version") or "").strip() == version
        ),
        None,
    )
    if existing_revision:
        existing_notes = str(existing_revision.get("description") or "").strip()
        existing_date = str(existing_revision.get("date_display") or "").strip()
        if existing_notes:
            notes = existing_notes
        release_date_display = existing_date or manifest_revision["date_display"]
    else:
        release_date_display = manifest_revision["date_display"]

    update_revision = {
        "date_display": release_date_display,
        "version": version,
        "description": notes,
    }
    filtered_revisions = [
        revision
        for revision in revisions
        if str(revision.get("version") or "").strip() != version
    ]
    return _sort_revisions_desc([update_revision, *filtered_revisions])


def _revision_rows_equal(left: list[dict], right: list[dict]) -> bool:
    def signature(rows: list[dict]) -> list[tuple[str, str, str]]:
        normalized = []
        for revision in rows:
            item = _normalize_revision(revision)
            if item:
                normalized.append(
                    (item["version"], item["date_display"], item["description"])
                )
        return normalized[:APP_REVISION_DISPLAY_LIMIT]

    return signature(left) == signature(right)


def sync_public_update_revision_to_local() -> list[dict]:
    return reconcile_app_revisions_storage(export_to_drive=False)


def reconcile_app_revisions_storage(*, export_to_drive: bool = False) -> list[dict]:
    display_revisions = get_display_app_revisions()
    current_revisions = list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)
    if not _revision_rows_equal(display_revisions, current_revisions):
        display_revisions = _replace_app_revisions(display_revisions)
    if export_to_drive:
        try:
            export_app_revisions_to_google_drive(display_revisions)
        except (RuntimeError, HTTPError, URLError):
            pass
    return display_revisions


def _replace_app_revisions(revisions: list[dict]) -> list[dict]:
    normalized_revisions: list[dict] = []
    for revision in revisions:
        item = _normalize_revision(revision)
        if item:
            normalized_revisions.append(item)

    normalized_revisions = _sort_revisions_desc(normalized_revisions)[:APP_REVISION_DISPLAY_LIMIT]
    if not normalized_revisions:
        normalized_revisions = [dict(DEFAULT_APP_REVISIONS[0])]

    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_revisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sort_order INTEGER NOT NULL,
              date_display TEXT NOT NULL DEFAULT '',
              version_text TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("DELETE FROM app_revisions")
        for index, revision in enumerate(reversed(normalized_revisions), start=1):
            conn.execute(
                """
                INSERT INTO app_revisions (
                  sort_order,
                  date_display,
                  version_text,
                  description
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    index,
                    revision["date_display"],
                    revision["version"],
                    revision["description"],
                ),
            )
        conn.commit()
    return list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)


def sync_app_revisions_from_google_drive() -> list[dict]:
    drive_revisions = import_app_revisions_from_google_drive()
    bundled = load_bundled_published_revisions()
    local_revisions = list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)
    manifest_revision = _manifest_revision()
    manifest_list = [manifest_revision] if manifest_revision else []
    merged_revisions = merge_revision_sources(
        bundled,
        manifest_list,
        local_revisions,
        drive_revisions,
    )
    merged_revisions = merge_public_update_revision(merged_revisions)
    merged_revisions = merged_revisions[:APP_REVISION_DISPLAY_LIMIT]
    latest_revisions = _replace_app_revisions(merged_revisions)
    if not _revision_rows_equal(latest_revisions, drive_revisions):
        try:
            export_app_revisions_to_google_drive(latest_revisions)
        except (RuntimeError, HTTPError, URLError):
            pass
    return latest_revisions


def save_app_revisions(revisions: list[dict]) -> list[dict]:
    normalized_revisions: list[dict] = []
    for revision in revisions:
        item = _normalize_revision(revision)
        if item:
            normalized_revisions.append(item)

    if not normalized_revisions:
        return list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)

    existing_revisions_desc = list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)
    bundled = load_bundled_published_revisions()
    combined_revisions = merge_revision_sources(
        normalized_revisions,
        bundled,
        existing_revisions_desc,
    )
    latest_revisions = _replace_app_revisions(combined_revisions)

    google_summary = get_google_sync_summary()
    account = google_summary.get("account") or {}
    if str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready")):
        try:
            export_app_revisions_to_google_drive(latest_revisions)
        except (RuntimeError, HTTPError, URLError):
            pass
    return latest_revisions


def record_app_revision(
    *,
    version: str,
    description: str,
    date_display: str,
    export_to_drive: bool = True,
) -> list[dict]:
    version_text = str(version or "").strip()
    description_text = str(description or "").strip()
    date_text = str(date_display or "").strip()
    if not version_text and not description_text:
        return list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)

    append_published_revision_to_bundle(
        version=version_text,
        description=description_text,
        date_display=date_text,
    )
    return reconcile_app_revisions_storage(export_to_drive=export_to_drive)


def load_app_revisions_for_page(*, sync_drive: bool = False) -> list[dict]:
    if sync_drive:
        try:
            return sync_app_revisions_from_google_drive()
        except (RuntimeError, HTTPError, URLError):
            pass
    return reconcile_app_revisions_storage(export_to_drive=False)
