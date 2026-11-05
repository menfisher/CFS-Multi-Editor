from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote_plus, urlencode
from urllib.request import HTTPSHandler, Request, build_opener

from app.services.address_format import build_formatted_address, enrich_address_parts
from app.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_REDIRECT_URI,
    GOOGLE_PICKER_API_KEY,
    GOOGLE_PICKER_APP_ID,
    HOST,
    PORT,
    UPLOADS_DIR,
)
from app.database import get_active_uploads_dir, get_connection


_LOGGER = logging.getLogger(__name__)

GOOGLE_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_PEOPLE_BASE_URL = "https://people.googleapis.com/v1"
GOOGLE_DRIVE_BASE_URL = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_UPLOAD_BASE_URL = "https://www.googleapis.com/upload/drive/v3"
GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/drive.file",
]
# drive.file alone covers everything the app needs: it can create/manage its own
# Drive files and folders, and the Google Picker grants per-item access to any
# folder the user selects. drive.readonly was dropped because it is a sensitive
# scope that triggers Google's "app isn't verified" warning without adding needed
# capability.
GOOGLE_DRIVE_VISIBLE_SCOPES = {
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
}
GOOGLE_PICKER_VISIBLE_SCOPES = {
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
}
GOOGLE_DRIVE_ROOT_FOLDER_NAME = "ContactsFreeShare"
GOOGLE_DRIVE_BACKUPS_FOLDER_NAME = "Backups"
GOOGLE_DRIVE_ADDRESS_BOOK_PDFS_FOLDER_NAME = "Address Book PDF's"
GOOGLE_DRIVE_ADDRESS_BOOK_PRINTED_FOLDER_NAME = "Address Book Printed"
GOOGLE_DRIVE_ADDRESS_BOOK_PDF_RETENTION_LIMIT = 15
GOOGLE_DRIVE_BACKUP_RETENTION_LIMIT = 1
GOOGLE_DRIVE_SHARED_ADDRESS_BOOK_FOLDER_NAME = ""
GOOGLE_DRIVE_SHARED_ADDRESS_BOOK_FOLDER_ID = ""
GOOGLE_DRIVE_SHARED_FOLDER_NAME = "Shared"
GOOGLE_DRIVE_SHARED_CONTACT_RECORDS_FOLDER_NAME = "Contacts"
GOOGLE_DRIVE_SHARED_CONTACT_PHOTOS_FOLDER_NAME = "Contact Photos"
GOOGLE_DRIVE_APP_REVISIONS_FOLDER_NAME = "App Revisions"
GOOGLE_DRIVE_APP_REVISIONS_FILE_NAME = "app_revisions.json"
GOOGLE_DRIVE_APP_UPDATES_FOLDER_NAME = "App Updates"
GOOGLE_DRIVE_APP_UPDATE_MANIFEST_FILE_NAME = "app_update_manifest.json"
GOOGLE_DRIVE_EDITOR_LOCK_FILE_NAME = "editor_lock.json"
GOOGLE_DRIVE_CONTACTS_CURRENT_FILE_NAME = "contacts_current.json"
GOOGLE_DRIVE_CONTACTS_FULL_FILE_NAME = "contacts_current_full.json"
GOOGLE_DRIVE_MEETINGDATA_CURRENT_FILE_NAME = "meetingdata_current.json"
GOOGLE_DRIVE_FIELD_LIST_CURRENT_FILE_NAME = "field_list_current.json"
GOOGLE_DRIVE_SETTINGS_CURRENT_FILE_NAME = "settings_current.json"
GOOGLE_DRIVE_BOOK_LAYOUTS_CURRENT_FILE_NAME = "book_layouts_current.json"
GOOGLE_DRIVE_CHANGES_LIST_CURRENT_FILE_NAME = "changes_list_current.json"
GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME = "shared_sync_state.json"
EDITOR_LOCK_TIMEOUT_SECONDS = 60 * 60
EDITOR_LOCK_EXTENSION_MINUTES = 15
DEFAULT_EDITOR_SECRET = "test"
EDITOR_SECRET_ITERATIONS = 260_000
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
DRIVE_UPLOAD_TIMEOUT_SECONDS = 180
DRIVE_JSON_UPLOAD_TIMEOUT_SECONDS = 45
DRIVE_READ_RETRY_ATTEMPTS = 1
DRIVE_READ_RETRY_BASE_SECONDS = 1.5
STALE_UPLOAD_PROGRESS_SECONDS = 5 * 60
STALE_SIGNIN_SYNC_SECONDS = 90
CONTACTS_MANIFEST_CACHE_TTL_SECONDS = 90
DRIVE_BOOTSTRAP_BATCH_SIZE = 25
DRIVE_BOOTSTRAP_VISIBLE_PROGRESS_STEP = 10
ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_INTERVAL_SECONDS = 5
ADDRESS_BOOK_PDF_APP_PROPERTY_KIND = "contactsfreeshare.address_book_pdf"
CONTACT_PHOTO_STATIC_PREFIX = "/static/uploads/contact_photos/"
_CONTACT_ONLY_SHARED_DRIVE_STORAGE_CACHE: dict[str, dict] = {}
_SHARED_DRIVE_STORAGE_CACHE: dict[str, dict] = {}
_CONTACTS_MANIFEST_CACHE: dict[str, tuple[float, dict]] = {}


def get_contact_photo_storage_dir() -> Path:
    return get_active_uploads_dir() / "contact_photos"


CONTACT_PHOTO_STORAGE_DIR = UPLOADS_DIR / "contact_photos"
BOOK_LAYOUT_SETTING_COLUMNS = [
    "id",
    "name",
    "book_title",
    "trim_width_in",
    "trim_height_in",
    "margin_left_in",
    "margin_right_in",
    "margin_top_in",
    "margin_bottom_in",
    "font_family",
    "base_font_size_pt",
    "title_font_size_pt",
    "toc_font_size_pt",
    "index_font_size_pt",
    "line_height",
    "meeting_table_column_count",
    "meeting_table_column_gap_px",
    "screen_preview_scale",
    "meetingdata_title_bar_color",
    "meetingdata_primary_row_color",
    "meetingdata_secondary_row_color",
    "meetingdata_highlight_row_color",
    "contacts_title_bar_color",
    "contacts_primary_row_color",
    "contacts_secondary_row_color",
    "contacts_highlight_row_color",
    "shared_contacts_group_name",
    "is_default",
]
BOOK_LAYOUT_COLOR_COLUMNS = [
    "meetingdata_title_bar_color",
    "meetingdata_primary_row_color",
    "meetingdata_secondary_row_color",
    "meetingdata_highlight_row_color",
    "contacts_title_bar_color",
    "contacts_primary_row_color",
    "contacts_secondary_row_color",
    "contacts_highlight_row_color",
]
GOOGLE_IMPORT_PERSON_FIELDS = ",".join(
    [
        "addresses",
        "biographies",
        "birthdays",
        "emailAddresses",
        "memberships",
        "metadata",
        "names",
        "organizations",
        "phoneNumbers",
        "photos",
        "relations",
        "userDefined",
        "urls",
    ]
)
_PEOPLE_IMPORT_PHASE = "people_import"
_PEOPLE_IMPORT_PAGE_SIZE = 200
_MOBILE_IMPORT_PERSON_FIELDS = ",".join(
    [
        "names",
        "organizations",
        "metadata",
        "phoneNumbers",
        "emailAddresses",
        "addresses",
        "photos",
        "memberships",
    ]
)

SYNC_NOTICE_MESSAGES = {
    "settings_saved": "Google sync settings were saved locally.",
    "oauth_not_configured": "Google OAuth is not configured yet. Add GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET before connecting.",
    "google_connected": "Google account connected successfully.",
    "google_disconnected": "Google account connection was removed from the app.",
    "import_not_connected": "Connect a Google account before importing contacts.",
    "import_success": "Google contacts were imported into the app and replaced the previous local contact set.",
    "import_failed": "Google import failed. Review Google Connect status and try again.",
    "upload_not_connected": "Reconnect Google before uploading local contact changes.",
    "upload_success": "Queued local contact changes and shared app changes were uploaded successfully.",
    "upload_failed": "Upload failed. Local changes remain queued. Reconnect Google if this keeps happening, then click Upload again.",
    "upload_pending": "Upload is not finished. Click Upload again to continue.",
    "upload_queue_empty": "There are no local contact or shared app changes waiting to upload.",
    "drive_not_connected": "Connect a Google account before creating Google Drive folders, exporting, or backing up to Drive.",
    "drive_scope_missing": "Drive file access has not been granted to the current Google connection yet. Reconnect the Google account to approve Google Drive file access.",
    "drive_storage_ready": "Google Drive storage is ready for App Revisions.",
    "drive_export_success": "App Revisions were exported to Google Drive successfully.",
    "drive_export_failed": "Google Drive export failed. Local App Revisions were still saved in the app.",
    "drive_import_success": "App Revisions were refreshed from Google Drive successfully.",
    "drive_import_failed": "Google Drive refresh failed. Local App Revisions were left unchanged.",
    "changes_list_download_success": "Pending Edits were downloaded from Google Drive.",
    "changes_list_download_current": "Pending Edits are already up to date with Google Drive.",
    "changes_list_download_failed": "Pending Edits could not be downloaded from Google Drive.",
    "app_update_available": "A newer ContactsFreeShare app package is available.",
    "app_update_current": "ContactsFreeShare is already up to date.",
    "app_update_installed_newer": "This installed copy is newer than the latest published update package.",
    "app_update_check_failed": "App update check failed. Confirm the public update manifest URL is set and reachable.",
    "app_update_download_failed": "App update download failed. Try checking for updates again.",
    "app_update_install_failed": "Automatic app update failed. Download the update package and open it manually, then check the update install log in the App Revisions screen.",
    "contacts_export_local_failed": "Contacts export to this computer failed or was cancelled before completion.",
    "contacts_export_drive_success": "Contacts were exported to Google Drive successfully.",
    "contacts_export_drive_failed": "Contacts export to Google Drive failed.",
    "contacts_backup_drive_success": "Contacts backup was saved to Google Drive successfully.",
    "contacts_backup_drive_failed": "Contacts backup to Google Drive failed.",
    "meetingdata_backup_drive_success": "MeetingData backup was saved to Google Drive successfully.",
    "meetingdata_backup_drive_failed": "MeetingData backup to Google Drive failed.",
    "backup_local_failed": "Backup to this computer failed or was cancelled before completion.",
    "backup_import_success": "Backup was imported successfully.",
    "backup_import_failed": "Backup import failed. Existing local data was left unchanged.",
    "backup_import_missing_selection": "Choose one backup file to import.",
    "contact_photo_google_success": "Contact photo was uploaded to Google Contacts. Shared accounts will update shortly.",
    "contact_photo_google_failed": "Contact photo upload to Google Contacts failed.",
    "contact_photo_google_missing": "Could not read this contact’s photo. Use “Upload photo from computer” to choose an image file, then try Upload photo to Google Contacts again.",
    "contact_photo_google_not_connected": "Connect this contact to Google before uploading its photo.",
    "contact_photo_pending_applied": "Pending photo was applied and uploaded to Google Contacts. Shared accounts will update shortly.",
    "contact_photo_pending_applied_local": "Pending photo was applied in the app, but Google Contacts upload failed. Use Upload photo to Google Contacts, then try again.",
    "contact_photo_pending_already_applied": "This contact already uses the pending photo. It was re-uploaded to Google Contacts so shared accounts can update.",
    "contact_photo_pending_missing": "No pending photo edit was found for this contact.",
    "contact_photo_pending_not_synced": "Could not download the pending photo from Google Drive. The Pending Edits path is from the mobile device, not proof Drive received the file. Ask the mobile user to tap Upload to Drive again, then click Download and apply pending photo here.",
    "contact_photo_pending_not_connected": "Pending photo was applied in the app, but this contact is not linked to Google Contacts yet.",
    "contact_photo_remove_success": "Contact photo was removed. The Google default letter avatar is now stored in the app.",
    "contact_photo_remove_local_only": "Contact photo was removed locally. Google Contacts photo could not be cleared — try again when connected.",
    "contact_photo_remove_already_clear": "This contact already has no custom photo.",
    "contact_photo_remove_failed": "Could not remove the contact photo.",
    "contact_saved": "Contact was saved.",
    "multi_editor_settings_saved": "ContactsFreeShare settings were saved locally and queued for the next Drive upload.",
    "multi_editor_settings_drive_saved": "ContactsFreeShare settings were saved and synced to Google Drive.",
    "book_layout_settings_drive_saved": "Book layout colors were saved and synced to Google Drive.",
    "app_settings_drive_saved": "ContactsFreeShare settings and book layout colors were saved and synced to Google Drive.",
    "multi_editor_account_locked": "This Google account is already set for multiple Editors. Single-Editor editing is not allowed.",
    "editor_secret_saved": "Editor secret was saved locally and queued for the next Drive upload.",
    "shared_settings_imported": "Shared app settings and book layouts were imported from Drive.",
    "editor_name_required": "Enter an Editor name before signing in.",
    "editor_secret_required": "Enter the Editor password, then click Sign in.",
    "editor_secret_invalid": "Editor password is not correct. The app remains in NO-EDIT mode.",
    "editor_signed_in": "Editor session is active.",
    "editor_signed_in_update_available": (
        "Editor session is active. A newer ContactsFreeShare version is available "
        "(you have {current_version}; latest is {latest_version}). "
        "Open App Revisions to download or install the update."
    ),
    "editor_signed_out": "Editor session ended.",
    "editor_signout_before_disconnect": "Sign out as Editor before disconnecting or switching the Google account.",
    "field_list_saved": "Field List template was saved locally and queued for the next Drive upload.",
    "address_book_settings_saved": "Field List settings were saved locally and queued for the next Drive upload.",
    "address_book_pdf_uploaded": "Address Book PDF was saved to Google Drive.",
    "address_book_pdf_saving_to_drive": "Address Book PDF is ready to preview. Saving a copy to Google Drive now.",
    "address_book_pdf_created": "Address Book PDF was created for preview. Save it to this computer or print it from the preview.",
    "address_book_pdf_failed": "Address Book PDF export failed.",
    "address_book_pdf_preview_drive_failed": "Address Book PDF was created for preview, but Drive upload failed.",
    "address_book_pdf_preview_shared_drive_failed": "Address Book PDF was saved to the app Drive folder, but the shared folder copy failed.",
    "editor_lock_blocked": "This app is in NO-EDIT mode. Sign in as an Editor to make changes.",
    "editor_lock_stale": "The previous Editor session is stale. Confirm takeover to edit.",
    "editor_lock_failed": "Editor sign-in failed. The app is in NO-EDIT mode.",
    "access_name_required": "Enter a name before signing in.",
    "access_signed_in": "Access session is active.",
    "changes_list_connect_google_before_signout": "Connect a Google account before signing out so Changes List edits can be saved to Drive.",
    "signin_sync_blocked": "Editor sign-in is still syncing shared data from Drive. Editing is temporarily unavailable.",
    "signin_sync_failed_blocked": "Editor sign-in sync did not finish. Retry Google Connect sign-in sync before editing.",
    "signin_sync_retry_started": "Shared Drive sync resumed.",
    "bootstrap_editing_blocked": "Drive bootstrap is in progress. Editing is temporarily unavailable.",
    "bootstrap_failed_blocked": "Drive bootstrap did not finish. Resume Upload before editing.",
    "google_sync_changes_success": "Merged {count} Google contact change(s) into the app. Click Upload to push updates to Drive.",
    "google_sync_changes_none": "Google Contacts is already up to date with the app.",
    "google_sync_changes_failed": "Sync from Google Contacts failed. Review Google Connect status and try again.",
    "google_sync_changes_conflicts": "Some Google contact changes were skipped because the app has unsaved local edits for those contacts.",
    "shared_group_renamed": "The shared Google group was renamed in this account and on connected shared accounts. Contacts were not re-uploaded.",
    "shared_group_rename_local_only": "The shared group name was saved locally. Connect Google to rename the Google Contacts label on this account and shared accounts.",
    "shared_group_rename_not_found": "The shared group name was saved. A Google Contacts group with the previous name was not found, so shared account labels were not renamed.",
    "shared_group_rename_collision": "The shared group name was saved, but Google Contacts already has a different group with the new name. The shared label was not renamed in place.",
    "shared_group_rename_google_failed": "The shared group name was saved locally, but renaming the Google Contacts label failed. Try Save Settings again after reconnecting Google.",
    "shared_group_rename_share_skipped": "The shared Google group was renamed in this account. Shared account labels were not updated because Share is not configured.",
    "shared_group_rename_share_failed": "The shared Google group was renamed in this account. Shared account labels could not be updated. Deploy the Share app update, then Save Settings again.",
    "shared_group_label_queued": "{count} Field+Meeting contacts were queued to receive {group_name} in Google Contacts. Click Upload on Contacts or Google Connect to write the labels.",
    "organization_rewrite_queued": "{count} uploaded contacts were queued so Google Job Title keeps Field and Department is cleared. Click Upload to write owner Google. Share will skip recipients if card repair already finished.",
    "organization_rewrite_none": "No uploaded Google contacts were found to queue for Job Title cleanup.",
    "shared_group_label_none": "No Field+Meeting contacts were found to queue for {group_name}.",
    "shared_group_label_sync_off": "Google contact upload is not enabled, so {group_name} was not applied to existing contacts.",
}

GOOGLE_UPLOAD_PERSON_FIELDS = ",".join(
    [
        "addresses",
        "biographies",
        "birthdays",
        "emailAddresses",
        "memberships",
        "metadata",
        "names",
        "organizations",
        "phoneNumbers",
        "relations",
        "urls",
        "userDefined",
    ]
)
GOOGLE_UPLOAD_UPDATE_FIELDS = ",".join(
    [
        "addresses",
        "biographies",
        "birthdays",
        "emailAddresses",
        "names",
        "organizations",
        "phoneNumbers",
        "relations",
        "urls",
        "userDefined",
    ]
)

_UPLOAD_JOB_LOCK = threading.Lock()
_UPLOAD_JOB_THREAD: threading.Thread | None = None
_SIGNIN_SYNC_LOCK = threading.Lock()
_SIGNIN_SYNC_THREAD: threading.Thread | None = None
_SIGNIN_SYNC_CONTACTS_CONTEXT: dict[str, int] = {}
_ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_LOCK = threading.Lock()
_ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_THREAD: threading.Thread | None = None
_ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_STOP = threading.Event()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_local_timestamp(value: str | None) -> datetime | None:
    text = _clean(str(value or ""))
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return None


def _create_connection_prefer_ipv4(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **_kwargs):
    """Connect IPv4 first so a broken IPv6 path cannot stall Google Drive for 30s per call."""
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ordered = [item for item in infos if item[0] == socket.AF_INET] + [
        item for item in infos if item[0] != socket.AF_INET
    ]
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in ordered:
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT and timeout is not None:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"Could not connect to {host}:{port}")


class _PreferIPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        self.sock = _create_connection_prefer_ipv4(
            (self.host, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PreferIPv4HTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PreferIPv4HTTPSConnection, req)


_IPV4_URL_OPENER = build_opener(_PreferIPv4HTTPSHandler())


def _urlopen_prefer_ipv4(request, timeout=None):
    return _IPV4_URL_OPENER.open(request, timeout=timeout)


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    data: dict | None = None,
    json_data: dict | None = None,
    raw_data: bytes | None = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
    retry_attempts: int | None = None,
) -> dict:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    payload = None
    supplied_body_count = sum(1 for item in (data, json_data, raw_data) if item is not None)
    if supplied_body_count > 1:
        raise ValueError("Only one of data, json_data, or raw_data may be supplied.")
    if data is not None:
        payload = urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif json_data is not None:
        payload = json.dumps(json_data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif raw_data is not None:
        payload = raw_data
    request = Request(url, data=payload, headers=request_headers, method=method)
    attempts = max(1, int(retry_attempts or (DRIVE_READ_RETRY_ATTEMPTS if method.upper() == "GET" else 1)))
    for attempt in range(1, attempts + 1):
        try:
            with _urlopen_prefer_ipv4(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as exc:
            if attempt >= attempts or exc.code not in {429, 500, 502, 503, 504}:
                raise
            time.sleep(DRIVE_READ_RETRY_BASE_SECONDS * attempt)
        except (
            TimeoutError,
            socket.timeout,
            URLError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            http.client.BadStatusLine,
        ) as exc:
            if attempt >= attempts:
                raise
            time.sleep(DRIVE_READ_RETRY_BASE_SECONDS * attempt)
    return {}


def _http_error_message(exc: HTTPError) -> str:
    try:
        error_payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        error_payload = {}
    if isinstance(error_payload, dict):
        nested_error = error_payload.get("error")
        if isinstance(nested_error, dict):
            message = _clean(nested_error.get("message"))
            code = _clean(nested_error.get("status") or nested_error.get("code"))
            if message and code:
                return f"{message} ({code})"
            if message:
                return message
        if isinstance(nested_error, str):
            description = _clean(error_payload.get("error_description"))
            return f"{nested_error}: {description}" if description else nested_error
        message = _clean(error_payload.get("message"))
        if message:
            return message
    return f"HTTP {exc.code}: {exc.reason}"


def describe_google_http_error(exc: HTTPError) -> str:
    return _http_error_message(exc)


def _looks_like_google_quota_error(exc: HTTPError, message: str) -> bool:
    normalized = f"{exc.code} {exc.reason} {message}".lower()
    return (
        exc.code in {403, 429}
        and (
            "resource_exhausted" in normalized
            or "quota exceeded" in normalized
            or "rate limit" in normalized
            or "user rate limit" in normalized
        )
    )


def _looks_like_expired_access_token_error(exc: HTTPError, message: str) -> bool:
    normalized = f"{exc.code} {exc.reason} {message}".lower()
    return exc.code == 401 or (
        "unauthenticated" in normalized
        or "invalid authentication" in normalized
        or "invalid_token" in normalized
        or "expected oauth" in normalized
    )


def _google_quota_retry_delay(exc: HTTPError, attempt: int) -> int:
    retry_after = _clean(exc.headers.get("Retry-After") if exc.headers else "")
    if retry_after.isdigit():
        return min(max(int(retry_after), 5), 75)
    return min(20 * attempt, 75)


def _looks_like_reconnect_error(message: str) -> bool:
    normalized = str(message or "").lower()
    return (
        "not connected" in normalized
        or "refresh token" in normalized
        or "reconnect" in normalized
        or "expired" in normalized
        or "revoked" in normalized
        or "invalid_grant" in normalized
        or "unauthenticated" in normalized
        or "invalid authentication" in normalized
    )


def _ensure_google_sync_records_once() -> None:
    with get_connection() as conn:
        account_row = conn.execute("SELECT id FROM google_sync_accounts WHERE id = 1").fetchone()
        state_row = conn.execute("SELECT id FROM google_sync_state WHERE id = 1").fetchone()
        if account_row and state_row:
            _ensure_editor_secret_configured(conn)
            _ensure_public_web_url_configured(conn)
            conn.commit()
            return
        conn.execute(
            """
            INSERT INTO google_sync_accounts (id)
            SELECT 1
            WHERE NOT EXISTS (SELECT 1 FROM google_sync_accounts WHERE id = 1)
            """
        )
        conn.execute(
            """
            INSERT INTO google_sync_state (id)
            SELECT 1
            WHERE NOT EXISTS (SELECT 1 FROM google_sync_state WHERE id = 1)
            """
        )
        _ensure_editor_secret_configured(conn)
        _ensure_public_web_url_configured(conn)
        conn.commit()


def _ensure_public_web_url_configured(conn: sqlite3.Connection) -> None:
    from app.config import PUBLIC_WEB_URL, is_placeholder_share_web_url

    row = conn.execute("SELECT public_web_url FROM google_sync_state WHERE id = 1").fetchone()
    saved = _clean(row["public_web_url"] if row else "")
    if saved and not is_placeholder_share_web_url(saved):
        return
    default_url = PUBLIC_WEB_URL.rstrip("/") if PUBLIC_WEB_URL and not is_placeholder_share_web_url(PUBLIC_WEB_URL) else ""
    if not default_url:
        return
    conn.execute(
        """
        UPDATE google_sync_state
        SET public_web_url = ?, updated_at = ?
        WHERE id = 1
        """,
        (default_url, _now_text()),
    )


def ensure_google_sync_records() -> None:
    try:
        _ensure_google_sync_records_once()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        from app.database import initialize_database

        initialize_database()
        _ensure_google_sync_records_once()


def _hash_editor_secret(secret: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(secret or "").encode("utf-8"),
        str(salt or "").encode("utf-8"),
        EDITOR_SECRET_ITERATIONS,
    ).hex()


def _new_editor_secret_salt() -> str:
    return secrets.token_hex(16)


def _ensure_editor_secret_configured(conn) -> None:
    row = conn.execute(
        """
        SELECT editor_secret_hash, editor_secret_salt
        FROM google_sync_state
        WHERE id = 1
        """
    ).fetchone()
    if not row:
        return
    if _clean(row["editor_secret_hash"]) and _clean(row["editor_secret_salt"]):
        return
    salt = _new_editor_secret_salt()
    conn.execute(
        """
        UPDATE google_sync_state
        SET editor_secret_hash = ?, editor_secret_salt = ?, updated_at = ?
        WHERE id = 1
        """,
        (_hash_editor_secret(DEFAULT_EDITOR_SECRET, salt), salt, _now_text()),
    )


def save_editor_secret(secret: str) -> bool:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        return False
    ensure_google_sync_records()
    salt = _new_editor_secret_salt()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET editor_secret_hash = ?, editor_secret_salt = ?, updated_at = ?
            WHERE id = 1
            """,
            (_hash_editor_secret(normalized_secret, salt), salt, _now_text()),
        )
        conn.commit()
    return True


def verify_editor_secret(secret: str) -> bool:
    submitted_secret = str(secret or "").strip()
    if not submitted_secret:
        return False
    ensure_google_sync_records()
    with get_connection() as conn:
        _ensure_editor_secret_configured(conn)
        row = conn.execute(
            """
            SELECT editor_secret_hash, editor_secret_salt
            FROM google_sync_state
            WHERE id = 1
            """
        ).fetchone()
        conn.commit()
        if not row:
            return False
        expected_hash = _clean(row["editor_secret_hash"])
        salt = _clean(row["editor_secret_salt"])
    if not expected_hash or not salt:
        return False
    return hmac.compare_digest(_hash_editor_secret(submitted_secret, salt), expected_hash)


def _read_pending_upload_count(conn) -> int:
    count_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM google_sync_queue
        WHERE status = 'pending'
        """
    ).fetchone()
    return int(count_row["count"]) if count_row else 0


def _refresh_pending_upload_count(conn) -> int:
    pending_count = _read_pending_upload_count(conn)
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          pending_upload_count = ?,
          needs_upload_reminder = CASE WHEN ? > 0 THEN 1 ELSE 0 END,
          updated_at = ?
        WHERE id = 1
        """,
        (pending_count, pending_count, _now_text()),
    )
    return pending_count


def _google_sync_is_enabled_for_writes(conn) -> bool:
    row = conn.execute(
        """
        SELECT sync_enabled, multi_editor_enabled
        FROM google_sync_state
        WHERE id = 1
        """
    ).fetchone()
    if not row:
        return False
    return bool(row["multi_editor_enabled"] or row["sync_enabled"])


def _queue_unuploaded_local_contacts(conn) -> int:
    rows = conn.execute(
        """
        SELECT id, family_name, given_name, fields_text, meetings_text, group_membership
        FROM contacts
        WHERE COALESCE(google_contact_id, '') = ''
          AND id NOT IN (
            SELECT contact_id
            FROM google_sync_queue
            WHERE status = 'pending' AND contact_id IS NOT NULL
          )
        ORDER BY id
        """
    ).fetchall()
    queued = 0
    for row in rows:
        item = dict(row)
        payload_json = json.dumps(
            {
                "sync_action": "Create",
                "family_name": _clean(item.get("family_name")),
                "given_name": _clean(item.get("given_name")),
                "fields_text": _clean(item.get("fields_text")),
                "meetings_text": _clean(item.get("meetings_text")),
                "group_membership": _clean(item.get("group_membership")),
            },
            sort_keys=True,
        )
        conn.execute(
            """
            INSERT INTO google_sync_queue (
              contact_id,
              google_contact_id,
              operation,
              payload_json,
              status,
              last_error,
              updated_at
            ) VALUES (?, '', 'update', ?, 'pending', '', ?)
            """,
            (int(item["id"]), payload_json, _now_text()),
        )
        queued += 1
    return queued


def _contact_qualifies_for_shared_group_label(contact: dict) -> bool:
    field_name = _clean(contact.get("fields_text"))
    meeting_name = _clean(contact.get("meetings_text"))
    if not field_name or not meeting_name:
        return False
    meetings = {
        part.strip().lower()
        for part in str(contact.get("meetings_text") or "").replace(":::", "\n").replace(";", "\n").splitlines()
        if part.strip()
    }
    fields = {
        part.strip().lower()
        for part in str(contact.get("fields_text") or "").replace(":::", "\n").replace(";", "\n").splitlines()
        if part.strip()
    }
    return "deleted" not in meetings and "deleted" not in fields


def queue_uploaded_contacts_for_organization_rewrite() -> dict:
    """Queue every already-uploaded contact so the next Upload writes Job Title only."""
    ensure_google_sync_records()
    result = {"queued": 0, "sync_enabled": False}
    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            conn.commit()
            return result
        result["sync_enabled"] = True
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, google_contact_id, family_name, given_name, fields_text, meetings_text
                FROM contacts
                WHERE TRIM(COALESCE(google_contact_id, '')) != ''
                ORDER BY id
                """
            ).fetchall()
        ]
        if not rows:
            conn.commit()
            return result
        ids = [int(item["id"]) for item in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""
            DELETE FROM google_sync_queue
            WHERE status = 'pending' AND contact_id IN ({placeholders})
            """,
            ids,
        )
        now = _now_text()
        for item in rows:
            payload_json = json.dumps(
                {
                    "sync_action": "Job Title cleanup",
                    "family_name": _clean(item.get("family_name")),
                    "given_name": _clean(item.get("given_name")),
                    "fields_text": _clean(item.get("fields_text")),
                    "meetings_text": _clean(item.get("meetings_text")),
                    "google_contact_id": _clean(item.get("google_contact_id")),
                },
                sort_keys=True,
            )
            conn.execute(
                """
                INSERT INTO google_sync_queue (
                  contact_id,
                  google_contact_id,
                  operation,
                  payload_json,
                  status,
                  last_error,
                  updated_at
                ) VALUES (?, ?, 'update', ?, 'pending', '', ?)
                """,
                (
                    int(item["id"]),
                    _clean(item.get("google_contact_id")),
                    payload_json,
                    now,
                ),
            )
        result["queued"] = len(rows)
        _refresh_pending_upload_count(conn)
        conn.commit()
    return result


def queue_assigned_contacts_for_shared_group_label() -> dict:
    """Queue a Google membership rewrite for every Field+Meeting contact.

    Settings stores the catch-all shared label, but changing that name does not
    re-upload contacts. This queues them so the next Upload writes the label.
    """
    from app.services.preset_service import get_shared_contacts_group_name

    ensure_google_sync_records()
    group_name = _clean(get_shared_contacts_group_name())
    result = {
        "queued": 0,
        "assigned": 0,
        "group_name": group_name,
        "sync_enabled": False,
    }
    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            conn.commit()
            return result
        result["sync_enabled"] = True
        rows = conn.execute(
            """
            SELECT id, google_contact_id, family_name, given_name, fields_text, meetings_text, group_membership
            FROM contacts
            ORDER BY id
            """
        ).fetchall()
        assigned_items = [
            dict(row)
            for row in rows
            if _contact_qualifies_for_shared_group_label(dict(row))
        ]
        result["assigned"] = len(assigned_items)
        if not assigned_items:
            conn.commit()
            return result
        assigned_ids = [int(item["id"]) for item in assigned_items]
        placeholders = ",".join("?" for _ in assigned_ids)
        conn.execute(
            f"""
            DELETE FROM google_sync_queue
            WHERE status = 'pending' AND contact_id IN ({placeholders})
            """,
            assigned_ids,
        )
        now = _now_text()
        for item in assigned_items:
            payload_json = json.dumps(
                {
                    "sync_action": "Shared group label",
                    "family_name": _clean(item.get("family_name")),
                    "given_name": _clean(item.get("given_name")),
                    "fields_text": _clean(item.get("fields_text")),
                    "meetings_text": _clean(item.get("meetings_text")),
                    "group_membership": _clean(item.get("group_membership")),
                    "google_contact_id": _clean(item.get("google_contact_id")),
                },
                sort_keys=True,
            )
            conn.execute(
                """
                INSERT INTO google_sync_queue (
                  contact_id,
                  google_contact_id,
                  operation,
                  payload_json,
                  status,
                  last_error,
                  updated_at
                ) VALUES (?, ?, 'update', ?, 'pending', '', ?)
                """,
                (
                    int(item["id"]),
                    _clean(item.get("google_contact_id")),
                    payload_json,
                    now,
                ),
            )
        result["queued"] = len(assigned_items)
        _refresh_pending_upload_count(conn)
        conn.commit()
    return result


def _set_upload_progress(
    conn,
    *,
    in_progress: bool,
    phase: str | None = None,
    total_count: int | None = None,
    processed_count: int | None = None,
    current_contact: str | None = None,
    started_at: str | None = None,
) -> None:
    current_state = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
    if not current_state:
        return
    state = dict(current_state)
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          upload_in_progress = ?,
          upload_phase = ?,
          upload_total_count = ?,
          upload_processed_count = ?,
          upload_current_contact = ?,
          upload_started_at = ?,
          updated_at = ?
        WHERE id = 1
        """,
        (
            1 if in_progress else 0,
            str(phase if phase is not None else state.get("upload_phase") or ""),
            int(total_count if total_count is not None else state.get("upload_total_count") or 0),
            int(processed_count if processed_count is not None else state.get("upload_processed_count") or 0),
            str(current_contact if current_contact is not None else state.get("upload_current_contact") or ""),
            str(started_at if started_at is not None else state.get("upload_started_at") or ""),
            _now_text(),
        ),
    )


def _set_drive_upload_step(
    current_contact: str,
    *,
    total_count: int | None = None,
    processed_count: int | None = None,
    started_at: str | None = None,
) -> None:
    with get_connection() as conn:
        _set_upload_progress(
            conn,
            in_progress=True,
            phase="drive_shared_data",
            total_count=total_count,
            processed_count=processed_count,
            current_contact=current_contact,
            started_at=started_at,
        )
        conn.commit()


def _clear_stale_upload_progress_if_needed(conn) -> bool:
    global _UPLOAD_JOB_THREAD
    current_state = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
    if not current_state:
        return False
    state = dict(current_state)
    if not bool(state.get("upload_in_progress") or 0):
        return False
    started_at = _parse_local_timestamp(state.get("upload_started_at")) or _parse_local_timestamp(state.get("updated_at"))
    if started_at is None:
        return False
    age_seconds = max(0, int((_utc_now() - started_at).total_seconds()))
    if age_seconds < STALE_UPLOAD_PROGRESS_SECONDS:
        return False
    if _UPLOAD_JOB_THREAD is not None and _UPLOAD_JOB_THREAD.is_alive():
        return False
    if str(state.get("bootstrap_status") or "") == "running":
        _mark_bootstrap_failed(conn, "Drive bootstrap stopped before it finished. Resume Upload to continue.")
    _set_upload_progress(
        conn,
        in_progress=False,
        phase="",
        total_count=int(state.get("upload_total_count") or 0),
        processed_count=int(state.get("upload_processed_count") or 0),
        current_contact="",
        started_at="",
    )
    return True


def _clear_orphaned_upload_progress_if_needed(conn) -> bool:
    global _UPLOAD_JOB_THREAD
    current_state = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
    if not current_state:
        return False
    state = dict(current_state)
    if not bool(state.get("upload_in_progress") or 0):
        return False
    if _UPLOAD_JOB_THREAD is not None and _UPLOAD_JOB_THREAD.is_alive():
        return False
    pending_count = _read_pending_upload_count(conn)
    has_remaining_work = pending_count > 0 or _state_has_resumable_upload_work(conn, state)
    _set_upload_progress(
        conn,
        in_progress=False,
        phase="",
        total_count=0,
        processed_count=0,
        current_contact="",
        started_at="",
    )
    if has_remaining_work and not str(state.get("last_sync_error") or "").strip():
        _set_last_sync_error(
            conn,
            "Upload stopped before it finished. Click Upload to continue.",
        )
    return True


def _count_pending_shared_contact_exports(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM contacts
        WHERE shared_drive_needs_export = 1
        """
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def _state_has_pending_drive_export(conn, state: dict | None = None) -> bool:
    state = state or dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
    if _count_pending_shared_contact_exports(conn) > 0:
        return True
    return bool(
        state.get("needs_drive_export")
        or state.get("contacts_manifest_needs_drive_export")
        or state.get("book_layouts_needs_drive_export")
        or state.get("meetingdata_needs_drive_export")
        or state.get("field_list_needs_drive_export")
        or state.get("changes_list_needs_drive_export")
    )


def pending_drive_export_work_remaining() -> bool:
    with get_connection() as conn:
        return _state_has_pending_drive_export(conn)


def _state_has_pending_non_editor_drive_export(conn, state: dict | None = None) -> bool:
    state = state or dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
    return bool(
        state.get("book_layouts_needs_drive_export")
        or state.get("field_list_needs_drive_export")
        or state.get("changes_list_needs_drive_export")
    )


def pending_non_editor_drive_export_work_remaining() -> bool:
    with get_connection() as conn:
        return _state_has_pending_non_editor_drive_export(conn)


def export_non_editor_access_data_to_google_drive() -> None:
    """Upload only Field List, Address Book, and Pending Edits artifacts.

    Non-editor / contributor sign-out must not flush leftover Editor contact,
    settings, or meeting-data Drive work.
    """
    with get_connection() as conn:
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        book_layouts_needed = bool(state.get("book_layouts_needs_drive_export") or 0)
        field_list_needed = bool(state.get("field_list_needs_drive_export") or 0)
        changes_list_needed = bool(state.get("changes_list_needs_drive_export") or 0)
        conn.commit()
    if book_layouts_needed:
        export_book_layouts_to_google_drive()
    if field_list_needed:
        export_field_list_to_google_drive()
        with get_connection() as conn:
            _clear_pending_field_list_drive_export(conn)
            conn.commit()
    if changes_list_needed:
        export_changes_list_to_google_drive()


def _count_unbootstrapped_contacts(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM contacts
        WHERE shared_drive_bootstrapped = 0
        """
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def _count_drive_bootstrap_remaining(conn, *, manifest_is_v2: bool) -> int:
    return _count_unbootstrapped_contacts(conn)


def _prepare_drive_bootstrap_run(
    conn,
    *,
    manifest_is_v2: bool,
    total_contacts: int,
    state: dict,
) -> tuple[int, int]:
    """Prepare bootstrap counters for the next Drive upload run."""
    bootstrap_status = str(state.get("bootstrap_status") or "idle")
    remaining_contacts = _count_drive_bootstrap_remaining(conn, manifest_is_v2=manifest_is_v2)

    if not manifest_is_v2:
        stored_total = int(state.get("bootstrap_total_contacts") or 0)
        if bootstrap_status not in {"running", "failed"} or stored_total <= 0:
            conn.execute("UPDATE contacts SET shared_drive_bootstrapped = 0")
            _set_bootstrap_state(
                conn,
                status="running",
                total_contacts=total_contacts,
                processed_contacts=0,
                last_contact_id=0,
                started_at=_now_text(),
                updated_at=_now_text(),
                error="",
            )
            return total_contacts, 0

        work_total = max(stored_total, total_contacts)
        work_processed = max(work_total - _count_unbootstrapped_contacts(conn), int(state.get("bootstrap_processed_contacts") or 0))
        _set_bootstrap_state(
            conn,
            status="running",
            total_contacts=work_total,
            processed_contacts=work_processed,
            updated_at=_now_text(),
            error="",
        )
        return work_total, work_processed

    if remaining_contacts <= 0:
        _set_bootstrap_state(conn, status="running", updated_at=_now_text(), error="")
        return 0, 0

    stored_total = int(state.get("bootstrap_total_contacts") or 0)
    stored_processed = int(state.get("bootstrap_processed_contacts") or 0)
    if bootstrap_status in {"running", "failed"} and stored_total > 0:
        if stored_total > remaining_contacts + stored_processed:
            work_total = remaining_contacts + stored_processed
            work_processed = stored_processed
        else:
            work_total = stored_total
            work_processed = max(stored_total - remaining_contacts, stored_processed, 0)
        _set_bootstrap_state(
            conn,
            status="running",
            total_contacts=work_total,
            processed_contacts=work_processed,
            updated_at=_now_text(),
            error="",
        )
        return work_total, work_processed

    _set_bootstrap_state(
        conn,
        status="running",
        total_contacts=remaining_contacts,
        processed_contacts=0,
        last_contact_id=0,
        started_at=_now_text(),
        updated_at=_now_text(),
        error="",
    )
    return remaining_contacts, 0


def _count_contacts(conn) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM contacts").fetchone()
    return int(row["count"] or 0) if row else 0


def _bootstrap_status_dict(state: dict) -> dict:
    total = int(state.get("bootstrap_total_contacts") or 0)
    processed = int(state.get("bootstrap_processed_contacts") or 0)
    if "bootstrap_remaining_contacts" in state:
        remaining = max(int(state.get("bootstrap_remaining_contacts") or 0), 0)
    else:
        remaining = max(total - processed, 0)
    return {
        "status": str(state.get("bootstrap_status") or "idle"),
        "total": total,
        "processed": processed,
        "remaining": remaining,
        "last_contact_id": int(state.get("bootstrap_last_contact_id") or 0),
        "started_at": str(state.get("bootstrap_started_at") or ""),
        "updated_at": str(state.get("bootstrap_updated_at") or ""),
        "error": str(state.get("bootstrap_error") or ""),
    }


def _is_bootstrap_blocking_state(state: dict) -> bool:
    return str(state.get("bootstrap_status") or "idle") in {"running", "failed"}


def _bootstrap_notice_key(state: dict) -> str:
    return "bootstrap_failed_blocked" if str(state.get("bootstrap_status") or "") == "failed" else "bootstrap_editing_blocked"


def _set_bootstrap_state(
    conn,
    *,
    status: str | None = None,
    total_contacts: int | None = None,
    processed_contacts: int | None = None,
    last_contact_id: int | None = None,
    started_at: str | None = None,
    updated_at: str | None = None,
    error: str | None = None,
) -> None:
    current_state = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
    if not current_state:
        return
    state = dict(current_state)
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          bootstrap_status = ?,
          bootstrap_total_contacts = ?,
          bootstrap_processed_contacts = ?,
          bootstrap_last_contact_id = ?,
          bootstrap_started_at = ?,
          bootstrap_updated_at = ?,
          bootstrap_error = ?,
          updated_at = ?
        WHERE id = 1
        """,
        (
            str(status if status is not None else state.get("bootstrap_status") or "idle"),
            int(total_contacts if total_contacts is not None else state.get("bootstrap_total_contacts") or 0),
            int(processed_contacts if processed_contacts is not None else state.get("bootstrap_processed_contacts") or 0),
            int(last_contact_id if last_contact_id is not None else state.get("bootstrap_last_contact_id") or 0),
            str(started_at if started_at is not None else state.get("bootstrap_started_at") or ""),
            str(updated_at if updated_at is not None else _now_text()),
            str(error if error is not None else state.get("bootstrap_error") or ""),
            _now_text(),
        ),
    )


def _set_signin_sync_state(
    conn,
    *,
    in_progress: bool | None = None,
    phase: str | None = None,
    error: str | None = None,
    total_count: int | None = None,
    processed_count: int | None = None,
    current_item: str | None = None,
) -> None:
    current_state = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
    if not current_state:
        return
    state = dict(current_state)
    next_phase = str(phase if phase is not None else state.get("signin_sync_phase") or "")
    phase_changed = phase is not None and next_phase != str(state.get("signin_sync_phase") or "")
    next_in_progress = bool(in_progress if in_progress is not None else bool(state.get("signin_sync_in_progress") or 0))
    if not next_in_progress:
        total_value = 0
        processed_value = 0
        current_value = ""
    elif total_count is not None or processed_count is not None or current_item is not None:
        total_value = int(total_count if total_count is not None else state.get("signin_sync_total_count") or 0)
        processed_value = int(processed_count if processed_count is not None else state.get("signin_sync_processed_count") or 0)
        current_value = _clean(current_item if current_item is not None else state.get("signin_sync_current_item") or "")
    elif phase_changed:
        total_value = 0
        processed_value = 0
        current_value = ""
    else:
        total_value = int(state.get("signin_sync_total_count") or 0)
        processed_value = int(state.get("signin_sync_processed_count") or 0)
        current_value = _clean(state.get("signin_sync_current_item") or "")
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          signin_sync_in_progress = ?,
          signin_sync_phase = ?,
          signin_sync_error = ?,
          signin_sync_total_count = ?,
          signin_sync_processed_count = ?,
          signin_sync_current_item = ?,
          signin_sync_updated_at = ?,
          updated_at = ?
        WHERE id = 1
        """,
        (
            1 if next_in_progress else 0,
            next_phase,
            str(error if error is not None else state.get("signin_sync_error") or ""),
            total_value,
            processed_value,
            current_value,
            _now_text(),
            _now_text(),
        ),
    )


def _clear_local_contact_data(conn) -> None:
    conn.executescript(
        """
        DELETE FROM relationships;
        DELETE FROM phones;
        DELETE FROM addresses;
        DELETE FROM emails;
        DELETE FROM custom_fields;
        DELETE FROM google_sync_queue;
        DELETE FROM contact_assignment_options;
        DELETE FROM contacts;
        """
    )


def _clear_local_meetingdata_data(conn) -> None:
    conn.executescript(
        """
        DELETE FROM meeting_edit_log_entries;
        DELETE FROM meeting_row_cells_v2;
        DELETE FROM meeting_row_flow_items_v2;
        DELETE FROM meeting_layout_presets_v2;
        DELETE FROM meeting_section_layout_v2;
        DELETE FROM meeting_section_rows_v2;
        DELETE FROM meeting_sections_v2;
        DELETE FROM meeting_section_name_options;
        DELETE FROM meeting_data_rows;
        """
    )


def _clear_transfer_progress_for_account_switch(conn) -> None:
    _clear_local_contact_data(conn)
    _clear_local_meetingdata_data(conn)
    _set_upload_progress(
        conn,
        in_progress=False,
        phase="",
        total_count=0,
        processed_count=0,
        current_contact="",
        started_at="",
    )
    _set_bootstrap_state(
        conn,
        status="idle",
        total_contacts=0,
        processed_contacts=0,
        last_contact_id=0,
        started_at="",
        updated_at=_now_text(),
        error="",
    )
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          pending_upload_count = 0,
          needs_upload_reminder = 0,
          needs_drive_export = 0,
          book_layouts_needs_drive_export = 0,
          contacts_manifest_needs_drive_export = 0,
          meetingdata_needs_drive_export = 0,
          field_list_needs_drive_export = 0,
          changes_list_needs_drive_export = 0,
          contacts_sync_revision = 0,
          meetingdata_sync_revision = 0,
          field_list_sync_revision = 0,
          settings_sync_revision = 0,
          last_import_at = '',
          last_import_account_email = '',
          last_sync_error = '',
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _mark_bootstrap_complete(conn) -> None:
    _set_bootstrap_state(
        conn,
        status="completed",
        error="",
        updated_at=_now_text(),
    )


def _mark_bootstrap_failed(conn, message: str) -> None:
    _set_bootstrap_state(
        conn,
        status="failed",
        error=_clean(message),
        updated_at=_now_text(),
    )


def _visible_bootstrap_processed(processed: int, total: int) -> int:
    if processed <= 0:
        return 0
    if processed >= total:
        return total
    return max((processed // DRIVE_BOOTSTRAP_VISIBLE_PROGRESS_STEP) * DRIVE_BOOTSTRAP_VISIBLE_PROGRESS_STEP, 0)


def _upload_thread_running() -> bool:
    return _UPLOAD_JOB_THREAD is not None and _UPLOAD_JOB_THREAD.is_alive()


def _signin_sync_thread_running() -> bool:
    return _SIGNIN_SYNC_THREAD is not None and _SIGNIN_SYNC_THREAD.is_alive()


def _log_editor_signout_drive_backup_failure(messages: list[str]) -> None:
    from app.config import ERROR_LOG_PATH
    from app.logging_utils import append_log_line

    append_log_line(ERROR_LOG_PATH, "Editor sign-out Drive backup failed: " + "; ".join(messages))


def _auto_backup_contacts_snapshot_to_google_drive() -> None:
    """Push live shared contact records, then write the Import-from-backup snapshot.

    The dataset revision must follow the v2 per-contact files other Editors import.
    Bumping it from this backup-only path made other computers skip those files.
    """
    export_shared_contacts_to_google_drive()
    access_token, _account_email = _get_valid_access_token()
    storage = _ensure_shared_drive_storage(access_token)
    _write_contacts_full_snapshot_to_google_drive(access_token, storage)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")


def export_json_backup_kind_to_google_drive(backup_kind: str) -> dict:
    """Save a Contacts or MeetingData JSON backup into Drive Backups (Import from backup list)."""
    from app.services.backup_service import (
        build_contacts_backup_bytes,
        build_meetingdata_backup_bytes,
    )

    normalized = str(backup_kind or "").strip().lower()
    if normalized == "meetingdata":
        filename, content = build_meetingdata_backup_bytes()
    elif normalized == "contacts":
        filename, content = build_contacts_backup_bytes()
    else:
        raise ValueError(f"Unsupported backup kind: {backup_kind}")
    return export_backup_file_to_google_drive(filename, content, "application/json")


def export_editor_session_drive_backups() -> None:
    """Write pending contacts/meetingdata Shared snapshots and Import-from-backup JSON on editor sign-out."""
    account = _load_google_account()
    if str(account.get("account_status") or "") != "connected":
        return
    if not _account_has_drive_scope(account):
        return

    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            return
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        contacts_snapshot_needed = bool(state.get("contacts_manifest_needs_drive_export") or 0) or _count_pending_shared_contact_exports(conn) > 0
        meetingdata_snapshot_needed = bool(state.get("meetingdata_needs_drive_export") or 0)
        changes_list_needed = bool(state.get("changes_list_needs_drive_export") or 0)
        # Dedicated sign-out flags survive mid-session Shared Drive sync, which clears
        # contacts_manifest_needs_drive_export / shared_drive_needs_export before logout.
        contacts_json_needed = bool(state.get("contacts_signout_backup_needed") or 0) or contacts_snapshot_needed
        meetingdata_json_needed = bool(state.get("meetingdata_signout_backup_needed") or 0) or meetingdata_snapshot_needed

    if (
        not contacts_snapshot_needed
        and not meetingdata_snapshot_needed
        and not contacts_json_needed
        and not meetingdata_json_needed
        and not changes_list_needed
    ):
        return

    errors: list[str] = []
    if changes_list_needed:
        try:
            export_changes_list_to_google_drive()
        except Exception as exc:
            errors.append(f"changes_list: {exc}")
    if meetingdata_snapshot_needed:
        try:
            export_shared_meetingdata_to_google_drive()
            with get_connection() as conn:
                _clear_pending_meetingdata_drive_export(conn)
                conn.commit()
        except Exception as exc:
            errors.append(f"meetingdata: {exc}")
    if contacts_snapshot_needed:
        try:
            _auto_backup_contacts_snapshot_to_google_drive()
        except Exception as exc:
            errors.append(f"contacts: {exc}")

    # Refresh Drive Backups JSON files shown on Import from backup (changed kinds only).
    json_kinds: list[str] = []
    if contacts_json_needed:
        json_kinds.append("contacts")
    if meetingdata_json_needed:
        json_kinds.append("meetingdata")
    for kind in json_kinds:
        try:
            export_json_backup_kind_to_google_drive(kind)
            with get_connection() as conn:
                if kind == "contacts":
                    _clear_contacts_signout_backup_needed(conn)
                else:
                    _clear_meetingdata_signout_backup_needed(conn)
                conn.commit()
        except Exception as exc:
            errors.append(f"{kind}_json_backup: {exc}")

    if errors:
        _log_editor_signout_drive_backup_failure(errors)
        raise RuntimeError("; ".join(errors))


def _mark_contacts_signout_backup_needed(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET contacts_signout_backup_needed = 1, updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _mark_meetingdata_signout_backup_needed(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET meetingdata_signout_backup_needed = 1, updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_contacts_signout_backup_needed(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET contacts_signout_backup_needed = 0, updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_meetingdata_signout_backup_needed(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET meetingdata_signout_backup_needed = 0, updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_stale_signin_sync_if_needed(conn) -> bool:
    current_state = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
    if not current_state:
        return False
    state = dict(current_state)
    if not bool(state.get("signin_sync_in_progress") or 0):
        return False
    if _signin_sync_thread_running():
        return False
    # Cloud Run cold starts kill the sync thread but leave in_progress=1 in SQLite.
    # Clear immediately so mobile editing is not blocked for up to 90 seconds.
    _set_signin_sync_state(conn, in_progress=False, phase="", error="")
    return True


def _state_has_resumable_upload_work(conn, state: dict) -> bool:
    if str(state.get("bootstrap_status") or "") in {"running", "failed"}:
        return _count_unbootstrapped_contacts(conn) > 0
    pending_count = _read_pending_upload_count(conn)
    if (
        pending_count > 0
        or _count_pending_shared_contact_exports(conn) > 0
        or bool(state.get("needs_drive_export") or 0)
        or bool(state.get("contacts_manifest_needs_drive_export") or 0)
        or bool(state.get("book_layouts_needs_drive_export") or 0)
        or bool(state.get("meetingdata_needs_drive_export") or 0)
        or bool(state.get("field_list_needs_drive_export") or 0)
        or bool(state.get("changes_list_needs_drive_export") or 0)
    ):
        return True
    return False


def _resume_upload_job_if_needed() -> bool:
    global _UPLOAD_JOB_THREAD
    with _UPLOAD_JOB_LOCK:
        if _upload_thread_running():
            return False
        with get_connection() as conn:
            state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
            state = dict(state_row) if state_row else {}
            if not bool(state.get("upload_in_progress") or 0):
                conn.commit()
                return False
            if not _state_has_resumable_upload_work(conn, state):
                _set_upload_progress(
                    conn,
                    in_progress=False,
                    phase="",
                    total_count=0,
                    processed_count=0,
                    current_contact="",
                    started_at="",
                )
                _set_bootstrap_state(
                    conn,
                    status="completed" if _count_unbootstrapped_contacts(conn) <= 0 else "idle",
                    total_contacts=0 if _count_unbootstrapped_contacts(conn) <= 0 else None,
                    processed_contacts=0 if _count_unbootstrapped_contacts(conn) <= 0 else None,
                    last_contact_id=0 if _count_unbootstrapped_contacts(conn) <= 0 else None,
                    started_at="" if _count_unbootstrapped_contacts(conn) <= 0 else None,
                    error="",
                )
                conn.commit()
                return False
            if not str(state.get("upload_started_at") or "").strip():
                _set_upload_progress(conn, in_progress=True, started_at=_now_text())
            _clear_last_sync_error(conn)
            conn.commit()
        _UPLOAD_JOB_THREAD = threading.Thread(target=_run_google_upload_job, daemon=True)
        _UPLOAD_JOB_THREAD.start()
        return True


def _oauth_is_configured() -> bool:
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI)


def _google_picker_app_id() -> str:
    configured_app_id = _clean(GOOGLE_PICKER_APP_ID)
    if configured_app_id:
        return configured_app_id
    client_id_prefix = _clean(GOOGLE_OAUTH_CLIENT_ID).split("-", 1)[0]
    return client_id_prefix if client_id_prefix.isdigit() else ""


def _safe_load_json(value: str | None) -> dict:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _scope_values(scope_text: str | None) -> set[str]:
    return {chunk.strip() for chunk in str(scope_text or "").split() if chunk.strip()}


def _account_has_drive_scope(account: dict | None = None) -> bool:
    details = account or _load_google_account()
    scopes = _scope_values(details.get("scopes"))
    return any(scope in scopes for scope in GOOGLE_DRIVE_VISIBLE_SCOPES)


def _account_has_picker_scope(account: dict | None = None) -> bool:
    details = account or _load_google_account()
    scopes = _scope_values(details.get("scopes"))
    return any(scope in scopes for scope in GOOGLE_PICKER_VISIBLE_SCOPES)


def _format_queue_contact_name(contact_row: dict, payload: dict) -> str:
    family_name = _clean((contact_row or {}).get("family_name") or payload.get("family_name"))
    given_name = _clean((contact_row or {}).get("given_name") or payload.get("given_name"))
    if family_name and given_name:
        return f"{family_name}, {given_name}"
    if family_name:
        return family_name
    if given_name:
        return given_name
    google_contact_id = _clean((contact_row or {}).get("google_contact_id") or payload.get("google_contact_id"))
    return google_contact_id or "Local-only contact"


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    label = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {label}"


def _contact_job_phrase(action: str, count: int) -> str:
    if action == "Job Title cleanup":
        return f"owner Job Title cleanup for {_count_phrase(count, 'contact')}"
    if action == "Shared group label":
        return f"shared group label updates for {_count_phrase(count, 'contact')}"
    if action == "Create":
        return f"{_count_phrase(count, 'new contact')} to create in Google"
    if action == "Update":
        return f"{_count_phrase(count, 'contact update')}"
    if action == "Move":
        return f"{_count_phrase(count, 'contact move')}"
    if action == "Restore":
        return f"{_count_phrase(count, 'contact restore')}"
    if action == "Delete":
        return f"{_count_phrase(count, 'contact deletion')}"
    if action == "Permanently delete":
        return f"{_count_phrase(count, 'permanent contact deletion')}"
    lowered = action[:1].lower() + action[1:] if action else "contact job"
    return f"{_count_phrase(count, lowered + ' job')}"


def _pending_drive_job_names(state: dict) -> list[str]:
    names: list[str] = []
    if state.get("settings_needs_drive_export"):
        names.append("Settings")
    if state.get("contact_records_needs_drive_export") or state.get("contacts_manifest_needs_drive_export"):
        names.append("shared contacts")
    if state.get("book_layouts_needs_drive_export"):
        names.append("Address Book template")
    if state.get("meetingdata_needs_drive_export"):
        names.append("MeetingData")
    if state.get("field_list_needs_drive_export"):
        names.append("Field List")
    if state.get("changes_list_needs_drive_export"):
        names.append("Changes List")
    return names


def describe_pending_jobs(pending_queue: list[dict] | None, state: dict | None = None) -> str:
    """Plain-language summary of the jobs waiting on Upload."""
    actions = Counter()
    for item in pending_queue or []:
        label = _clean(item.get("action_label")) or "Update"
        actions[label] += 1
    preferred_order = (
        "Job Title cleanup",
        "Shared group label",
        "Create",
        "Update",
        "Move",
        "Restore",
        "Delete",
        "Permanently delete",
    )
    job_phrases = [
        _contact_job_phrase(action, actions[action])
        for action in preferred_order
        if actions.get(action)
    ]
    leftover = sorted(name for name in actions if name not in preferred_order)
    job_phrases.extend(_contact_job_phrase(action, actions[action]) for action in leftover)
    drive_names = _pending_drive_job_names(state or {})
    if drive_names:
        if len(drive_names) == 1:
            drive_label = drive_names[0]
        else:
            drive_label = ", ".join(drive_names[:-1]) + f", and {drive_names[-1]}"
        job_phrases.append(f"save {drive_label} to Drive")
    if not job_phrases:
        return ""
    if len(job_phrases) == 1:
        return f"Waiting to run: {job_phrases[0]}. Click Upload to send this job."
    return (
        "Waiting to run: "
        + ", ".join(job_phrases[:-1])
        + f", and {job_phrases[-1]}. Click Upload to send these jobs."
    )


def _list_pending_queue_entries() -> list[dict]:
    with get_connection() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                  q.id,
                  q.contact_id,
                  q.google_contact_id,
                  q.operation,
                  q.payload_json,
                  q.status,
                  q.last_error,
                  q.created_at,
                  q.updated_at,
                  c.family_name,
                  c.given_name
                FROM google_sync_queue q
                LEFT JOIN contacts c
                  ON c.id = q.contact_id
                WHERE q.status = 'pending'
                ORDER BY q.id
                """
            ).fetchall()
        ]

    entries = []
    for row in rows:
        item = row
        payload = _safe_load_json(item.get("payload_json"))
        operation = _clean(item.get("operation")) or "update"
        action_label = _clean(payload.get("sync_action")) or {
            "create": "Create",
            "update": "Update",
            "delete": "Delete",
        }.get(operation, operation.title())
        entries.append(
            {
                "id": int(item["id"]),
                "contact_name": _format_queue_contact_name(item, payload),
                "operation": operation,
                "action_label": action_label,
                "operation_label": f"{action_label} in Google",
                "google_contact_id": _clean(item.get("google_contact_id") or payload.get("google_contact_id")),
                "queued_at": _clean(item.get("created_at")),
                "updated_at": _clean(item.get("updated_at")),
                "last_error": _clean(item.get("last_error")),
            }
        )
    return entries


def get_google_sync_summary() -> dict:
    ensure_google_sync_records()
    with get_connection() as conn:
        _clear_stale_signin_sync_if_needed(conn)
        _clear_stale_upload_progress_if_needed(conn)
        _clear_orphaned_upload_progress_if_needed(conn)
        account_row = conn.execute("SELECT * FROM google_sync_accounts WHERE id = 1").fetchone()
        state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
        pending_count = _read_pending_upload_count(conn)
        pending_contact_drive_export_count = _count_pending_shared_contact_exports(conn)
        bootstrap_remaining_count = _count_unbootstrapped_contacts(conn)
        current_contact_count = _count_contacts(conn)
        if state_row:
            stored_bootstrap_status = str(state_row["bootstrap_status"] or "idle")
            if bootstrap_remaining_count <= 0 and stored_bootstrap_status in {"running", "failed"}:
                bootstrap_total = int(state_row["bootstrap_total_contacts"] or current_contact_count)
                _set_bootstrap_state(
                    conn,
                    status="completed",
                    total_contacts=bootstrap_total,
                    processed_contacts=bootstrap_total,
                    updated_at=_now_text(),
                    error="",
                )
                conn.commit()
                state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
            if (
                bootstrap_remaining_count <= 0
                and bool(state_row["upload_in_progress"] or 0)
                and str(state_row["upload_phase"] or "") == "drive_bootstrap"
            ):
                _set_upload_progress(
                    conn,
                    in_progress=False,
                    phase="",
                    total_count=int(state_row["upload_total_count"] or 0),
                    processed_count=int(state_row["upload_total_count"] or state_row["upload_processed_count"] or 0),
                    current_contact="",
                    started_at="",
                )
                conn.commit()
                state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()

        account = dict(account_row) if account_row else {}
        state = dict(state_row) if state_row else {}

    state["pending_upload_count"] = pending_count
    stored_bootstrap_status = str(state.get("bootstrap_status") or "idle")
    if bootstrap_remaining_count > 0 and stored_bootstrap_status == "completed":
        state["bootstrap_status"] = "idle"
    state["bootstrap_remaining_contacts"] = bootstrap_remaining_count
    bootstrap_total = int(state.get("bootstrap_total_contacts") or 0)
    if bootstrap_remaining_count > 0:
        stored_bootstrap_status = str(state.get("bootstrap_status") or "idle")
        incremental_bootstrap = (
            stored_bootstrap_status == "completed"
            or bootstrap_remaining_count < current_contact_count
        )
        if incremental_bootstrap:
            if bootstrap_total != bootstrap_remaining_count:
                bootstrap_total = bootstrap_remaining_count
                state["bootstrap_total_contacts"] = bootstrap_total
                state["bootstrap_processed_contacts"] = max(bootstrap_total - bootstrap_remaining_count, 0)
        elif bootstrap_total != current_contact_count:
            bootstrap_total = current_contact_count
            state["bootstrap_total_contacts"] = bootstrap_total
            state["bootstrap_processed_contacts"] = max(current_contact_count - bootstrap_remaining_count, 0)
    settings_needs_drive_export = bool(state.get("needs_drive_export") or 0)
    contacts_manifest_needs_drive_export = bool(state.get("contacts_manifest_needs_drive_export") or 0)
    book_layouts_needs_drive_export = bool(state.get("book_layouts_needs_drive_export") or 0)
    meetingdata_needs_drive_export = bool(state.get("meetingdata_needs_drive_export") or 0)
    field_list_needs_drive_export = bool(state.get("field_list_needs_drive_export") or 0)
    changes_list_needs_drive_export = bool(state.get("changes_list_needs_drive_export") or 0)
    shared_drive_needs_export = (
        settings_needs_drive_export
        or pending_contact_drive_export_count > 0
        or contacts_manifest_needs_drive_export
        or book_layouts_needs_drive_export
        or meetingdata_needs_drive_export
        or field_list_needs_drive_export
        or changes_list_needs_drive_export
    )
    last_sync_error = str(state.get("last_sync_error") or "")
    if (
        last_sync_error
        and not bool(state.get("upload_in_progress") or 0)
        and pending_count <= 0
        and not shared_drive_needs_export
        and str(state.get("bootstrap_status") or "idle") not in {"running", "failed"}
    ):
        with get_connection() as clear_conn:
            _clear_last_sync_error(clear_conn)
            clear_conn.commit()
        state["last_sync_error"] = ""
    if (
        pending_count > 0
        or shared_drive_needs_export
    ):
        state["needs_upload_reminder"] = 1
    pending_queue = _list_pending_queue_entries()
    return {
        "account": {
            "email": str(account.get("account_email") or ""),
            "remember_preferred_account": bool(account.get("remember_preferred_account") or 0),
            "status": str(account.get("account_status") or "not_connected"),
            "google_user_id": str(account.get("google_user_id") or ""),
            "display_name": str(account.get("account_email") or account.get("google_display_name") or ""),
            "oauth_configured": _oauth_is_configured(),
            "drive_scope_ready": _account_has_drive_scope(account),
        },
        "state": {
            "source_of_truth": str(state.get("source_of_truth") or "app"),
            "sync_enabled": bool(state.get("sync_enabled") or 0),
            "pending_upload_count": int(state.get("pending_upload_count") or 0),
            "upload_in_progress": bool(state.get("upload_in_progress") or 0),
            "upload_phase": str(state.get("upload_phase") or ""),
            "upload_total_count": int(state.get("upload_total_count") or 0),
            "upload_processed_count": int(state.get("upload_processed_count") or 0),
            "upload_remaining_count": max(
                int(state.get("upload_total_count") or 0) - int(state.get("upload_processed_count") or 0),
                0,
            ),
            "upload_current_contact": str(state.get("upload_current_contact") or ""),
            "upload_started_at": str(state.get("upload_started_at") or ""),
            "bootstrap_status": str(state.get("bootstrap_status") or "idle"),
            "bootstrap_total_contacts": bootstrap_total,
            "bootstrap_processed_contacts": int(state.get("bootstrap_processed_contacts") or 0),
            "bootstrap_remaining_contacts": bootstrap_remaining_count,
            "bootstrap_last_contact_id": int(state.get("bootstrap_last_contact_id") or 0),
            "bootstrap_started_at": str(state.get("bootstrap_started_at") or ""),
            "bootstrap_updated_at": str(state.get("bootstrap_updated_at") or ""),
            "bootstrap_error": str(state.get("bootstrap_error") or ""),
            "last_import_at": str(state.get("last_import_at") or ""),
            "last_import_account_email": str(state.get("last_import_account_email") or ""),
            "last_upload_at": str(state.get("last_upload_at") or ""),
            "last_sync_error": str(state.get("last_sync_error") or ""),
            "last_drive_export_at": str(state.get("last_drive_export_at") or ""),
            "contact_records_needs_drive_export": pending_contact_drive_export_count > 0,
            "pending_contact_drive_export_count": pending_contact_drive_export_count,
            "contacts_manifest_needs_drive_export": contacts_manifest_needs_drive_export,
            "settings_needs_drive_export": settings_needs_drive_export,
            "shared_drive_needs_export": shared_drive_needs_export,
            "needs_drive_export": settings_needs_drive_export,
            "book_layouts_needs_drive_export": book_layouts_needs_drive_export,
            "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
            "field_list_needs_drive_export": field_list_needs_drive_export,
            "changes_list_needs_drive_export": changes_list_needs_drive_export,
            "settings_sync_revision": int(state.get("settings_sync_revision") or 0),
            "book_layouts_sync_revision": int(state.get("book_layouts_sync_revision") or 0),
            "contacts_sync_revision": int(state.get("contacts_sync_revision") or 0),
            "meetingdata_sync_revision": int(state.get("meetingdata_sync_revision") or 0),
            "field_list_sync_revision": int(state.get("field_list_sync_revision") or 0),
            "changes_list_sync_revision": int(state.get("changes_list_sync_revision") or 0),
            "signin_sync_in_progress": bool(state.get("signin_sync_in_progress") or 0),
            "signin_sync_phase": str(state.get("signin_sync_phase") or ""),
            "signin_sync_error": str(state.get("signin_sync_error") or ""),
            "signin_sync_total_count": int(state.get("signin_sync_total_count") or 0),
            "signin_sync_processed_count": int(state.get("signin_sync_processed_count") or 0),
            "signin_sync_current_item": str(state.get("signin_sync_current_item") or ""),
            "signin_sync_updated_at": str(state.get("signin_sync_updated_at") or ""),
            "drive_root_folder_id": str(state.get("drive_root_folder_id") or ""),
            "drive_app_revisions_folder_id": str(state.get("drive_app_revisions_folder_id") or ""),
            "drive_app_revisions_file_id": str(state.get("drive_app_revisions_file_id") or ""),
            "address_book_pdf_share_enabled": bool(state.get("address_book_pdf_share_enabled") or 0),
            "address_book_pdf_share_folder_id": str(state.get("address_book_pdf_share_folder_id") or ""),
            "address_book_pdf_share_folder_name": str(state.get("address_book_pdf_share_folder_name") or ""),
            "public_web_url": str(state.get("public_web_url") or ""),
            "share_web_api_key_configured": bool(_clean(state.get("share_web_api_key") or "")),
            "share_sync_last_message": _share_sync_last_message_for_account(
                str(state.get("share_sync_last_message") or ""),
                str(account.get("email") or ""),
            ),
            "import_warning_acknowledged_at": str(state.get("import_warning_acknowledged_at") or ""),
            "needs_upload_reminder": bool(state.get("needs_upload_reminder") or 0),
            "multi_editor_enabled": bool(state.get("multi_editor_enabled") or 0),
            "multi_editor_account_locked": bool(state.get("multi_editor_account_locked") or 0),
            "editor_lock_timeout_minutes": int(state.get("editor_lock_timeout_minutes") or 60),
            "editor_name": str(state.get("editor_name") or ""),
            "editor_session_id": str(state.get("editor_session_id") or ""),
            "editor_mode": str(state.get("editor_mode") or "normal"),
            "editor_lock_owner_name": str(state.get("editor_lock_owner_name") or ""),
            "editor_lock_expires_at": str(state.get("editor_lock_expires_at") or ""),
            "pending_editor_name": str(state.get("pending_editor_name") or ""),
            "access_role": str(state.get("access_role") or "editor"),
            "access_name": str(state.get("access_name") or ""),
            "access_signed_in_at": str(state.get("access_signed_in_at") or ""),
        },
        "pending_queue": pending_queue,
        "job_title_cleanup_queued": any(
            str(item.get("action_label") or "") == "Job Title cleanup" for item in pending_queue
        ),
        "pending_job_message": describe_pending_jobs(
            pending_queue,
            {
                "settings_needs_drive_export": settings_needs_drive_export,
                "contact_records_needs_drive_export": pending_contact_drive_export_count > 0,
                "contacts_manifest_needs_drive_export": contacts_manifest_needs_drive_export,
                "book_layouts_needs_drive_export": book_layouts_needs_drive_export,
                "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
                "field_list_needs_drive_export": field_list_needs_drive_export,
                "changes_list_needs_drive_export": changes_list_needs_drive_export,
            },
        ),
    }


def get_google_picker_config(origin: str = "") -> dict:
    ensure_google_sync_records()
    account = _load_google_account()
    if str(account.get("account_status") or "") != "connected":
        return {"ok": False, "reason": "not_connected"}
    if not _account_has_drive_scope(account):
        return {"ok": False, "reason": "drive_scope_missing"}
    if not _account_has_picker_scope(account):
        return {
            "ok": False,
            "reason": "picker_scope_missing",
            "message": (
                "Reconnect Google on the Google Connect page so ContactsFreeShare can browse Drive folders."
            ),
        }

    api_key = _clean(GOOGLE_PICKER_API_KEY)
    app_id = _google_picker_app_id()
    if not api_key or not app_id:
        return {
            "ok": False,
            "reason": "picker_not_configured",
            "api_key_configured": bool(api_key),
            "app_id_configured": bool(app_id),
        }

    access_token, account_email = _get_valid_access_token()
    normalized_origin = _clean(origin).rstrip("/")
    if not normalized_origin:
        normalized_origin = f"http://{HOST}:{PORT}"
    return {
        "ok": True,
        "api_key": api_key,
        "app_id": app_id,
        "access_token": access_token,
        "account_email": account_email,
        "origin": normalized_origin,
        "referrer_pattern": f"{normalized_origin}/*",
    }


def verify_google_picker_folder(folder_id: str) -> dict:
    ensure_google_sync_records()
    account = _load_google_account()
    if str(account.get("account_status") or "") != "connected":
        return {"ok": False, "reason": "not_connected"}
    if not _account_has_drive_scope(account):
        return {"ok": False, "reason": "drive_scope_missing"}

    normalized_folder_id = _clean(folder_id)
    if not normalized_folder_id:
        return {"ok": False, "reason": "missing_folder_id", "message": "Folder ID is required."}

    access_token, _account_email = _get_valid_access_token()
    try:
        folder = _drive_find_folder_by_id_or_name(
            access_token,
            folder_id=normalized_folder_id,
            folder_name="",
        )
    except HTTPError as exc:
        if exc.code == 403:
            return {
                "ok": False,
                "reason": "access_denied",
                "message": (
                    "ContactsFreeShare cannot access this folder with the current Google connection. "
                    "Reconnect Google on the Google Connect page, then choose the folder again."
                ),
            }
        raise

    if not folder:
        return {
            "ok": False,
            "reason": "folder_not_found",
            "message": "Folder was not found or is not accessible with the current Google connection.",
        }

    return {
        "ok": True,
        "folder_id": _clean(folder.get("id")),
        "folder_name": _clean(folder.get("name")),
    }


def is_multi_editor_enabled() -> bool:
    return bool((get_google_sync_summary().get("state") or {}).get("multi_editor_enabled"))


def _editor_lock_is_own(
    lock: dict,
    *,
    local_session_id: str = "",
    editor_name: str | list[str] | tuple[str, ...] = "",
    account_email: str = "",
) -> bool:
    """True when the Drive lock belongs to this Editor, including after a reconnect."""
    existing_session_id = _clean((lock or {}).get("session_id"))
    normalized_local_session = _clean(local_session_id)
    if existing_session_id and normalized_local_session and existing_session_id == normalized_local_session:
        return True
    existing_owner = _clean((lock or {}).get("editor_name"))
    wanted_names = [
        _clean(item)
        for item in (editor_name if isinstance(editor_name, (list, tuple)) else [editor_name])
        if _clean(item)
    ]
    if not existing_owner or not wanted_names:
        return False
    if not any(existing_owner.casefold() == name.casefold() for name in wanted_names):
        return False
    existing_email = _clean((lock or {}).get("account_email")).casefold()
    wanted_email = _clean(account_email).casefold()
    if existing_email and wanted_email:
        return existing_email == wanted_email
    # Older lock files omit account_email. Same editor name is enough to reclaim
    # after a dropped connection; a different email still blocks.
    return not existing_email


def _has_active_local_editor_session(account: dict, state: dict, *, now: datetime | None = None) -> bool:
    if not bool(state.get("multi_editor_enabled")):
        return False
    if str(account.get("status") or "") != "connected":
        return False
    if str(state.get("editor_mode") or "") != "edit":
        return False
    if not _clean(state.get("editor_session_id")):
        return False
    expires_at = _parse_iso_datetime(_clean(state.get("editor_lock_expires_at")))
    if not expires_at:
        return False
    return expires_at > (now or _utc_now())


def _clear_expired_local_editor_session(state: dict, *, now: datetime | None = None) -> bool:
    if str(state.get("editor_mode") or "") != "edit":
        return False
    if not _clean(state.get("editor_session_id")):
        return False
    expires_at = _parse_iso_datetime(_clean(state.get("editor_lock_expires_at")))
    if not expires_at or expires_at > (now or _utc_now()):
        return False
    previous_editor_name = _clean(state.get("editor_name")) or _clean(state.get("pending_editor_name"))
    _set_editor_session(mode="no_edit", pending_editor_name=previous_editor_name)
    if normalize_access_role(state.get("access_role")) == "editor":
        clear_access_session()
    return True


def is_editing_allowed() -> bool:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    now = _utc_now()
    if _clear_expired_local_editor_session(state, now=now):
        summary = get_google_sync_summary()
        account = summary.get("account") or {}
        state = summary.get("state") or {}
    access_role = normalize_access_role(state.get("access_role"))
    if _is_bootstrap_blocking_state(state):
        return False
    if bool(state.get("signin_sync_in_progress") or 0) or _clean(state.get("signin_sync_error")):
        return False
    if not bool(state.get("multi_editor_enabled") or state.get("multi_editor_account_locked")):
        return True
    if is_non_editor_access_role(access_role):
        return False
    return _has_active_local_editor_session(account, state, now=now)


def can_retry_signin_sync() -> bool:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    connected_drive_account = str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready"))
    editor_role = normalize_access_role(state.get("access_role")) == "editor"
    if not connected_drive_account or not editor_role:
        return False
    if bool(state.get("upload_in_progress") or 0):
        return False
    if _is_bootstrap_blocking_state(state):
        return False
    return True


def retry_google_signin_sync() -> bool:
    if not can_retry_signin_sync():
        return False
    with get_connection() as conn:
        _clear_stale_signin_sync_if_needed(conn)
        _set_signin_sync_state(conn, in_progress=False, phase="", error="")
        conn.commit()
    _start_signin_sync_job()
    return True


def start_google_signin_sync() -> bool:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if str(account.get("status") or "") != "connected" or not bool(account.get("drive_scope_ready")):
        return False
    if bool(state.get("upload_in_progress") or 0):
        return False
    if _is_bootstrap_blocking_state(state):
        return False
    with get_connection() as conn:
        _clear_stale_signin_sync_if_needed(conn)
        _set_signin_sync_state(conn, in_progress=False, phase="", error="")
        conn.commit()
    _start_signin_sync_job()
    return True


def get_edit_block_notice_key() -> str:
    state = (get_google_sync_summary().get("state") or {})
    if _is_bootstrap_blocking_state(state):
        return _bootstrap_notice_key(state)
    if bool(state.get("signin_sync_in_progress") or 0):
        return "signin_sync_blocked"
    if _clean(state.get("signin_sync_error")):
        return "signin_sync_failed_blocked"
    return "editor_lock_blocked"


def _normalize_editor_timeout_minutes(value: int | str | None) -> int:
    try:
        minutes = int(value or 60)
    except (TypeError, ValueError):
        minutes = 60
    if minutes <= 0:
        return 15
    return min(minutes, 105)


def save_multi_editor_setting(
    enabled: bool,
    timeout_minutes: int | str | None = 60,
    *,
    allow_unlock: bool = False,
) -> None:
    normalized_timeout_minutes = _normalize_editor_timeout_minutes(timeout_minutes)
    ensure_google_sync_records()
    with get_connection() as conn:
        state_row = conn.execute(
            "SELECT multi_editor_account_locked FROM google_sync_state WHERE id = 1"
        ).fetchone()
        already_locked = bool(state_row and (state_row["multi_editor_account_locked"] or 0))
        effective_enabled = bool(enabled or (already_locked and not allow_unlock))
        effective_locked = bool(enabled or (already_locked and not allow_unlock))
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              multi_editor_enabled = ?,
              multi_editor_account_locked = ?,
              editor_lock_timeout_minutes = ?,
              editor_mode = CASE WHEN ? = 1 THEN CASE WHEN editor_mode = 'edit' THEN editor_mode ELSE 'no_edit' END ELSE 'normal' END,
              editor_name = CASE WHEN ? = 1 THEN editor_name ELSE '' END,
              editor_session_id = CASE WHEN ? = 1 THEN editor_session_id ELSE '' END,
              editor_lock_owner_name = CASE WHEN ? = 1 THEN editor_lock_owner_name ELSE '' END,
              editor_lock_expires_at = CASE WHEN ? = 1 THEN editor_lock_expires_at ELSE '' END,
              pending_editor_name = CASE WHEN ? = 1 THEN pending_editor_name ELSE '' END,
              access_role = CASE WHEN ? = 1 THEN access_role ELSE 'editor' END,
              access_name = CASE WHEN ? = 1 THEN access_name ELSE '' END,
              access_signed_in_at = CASE WHEN ? = 1 THEN access_signed_in_at ELSE '' END,
              updated_at = ?
            WHERE id = 1
            """,
            (
                1 if effective_enabled else 0,
                1 if effective_locked else 0,
                normalized_timeout_minutes,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                1 if effective_enabled else 0,
                _now_text(),
            ),
        )
        conn.commit()


def get_public_web_url() -> str:
    from app.config import PUBLIC_WEB_URL, is_placeholder_share_web_url

    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute("SELECT public_web_url FROM google_sync_state WHERE id = 1").fetchone()
        saved = _clean(row["public_web_url"] if row else "")
    if saved and not is_placeholder_share_web_url(saved):
        return saved.rstrip("/")
    if PUBLIC_WEB_URL and not is_placeholder_share_web_url(PUBLIC_WEB_URL):
        return PUBLIC_WEB_URL.rstrip("/")
    return PUBLIC_WEB_URL.rstrip("/")


def save_public_web_url(url: str) -> None:
    ensure_google_sync_records()
    normalized = _clean(url).rstrip("/")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET public_web_url = ?, updated_at = ?
            WHERE id = 1
            """,
            (normalized, _now_text()),
        )
        conn.commit()


def get_share_web_api_key() -> str:
    from app.config import SHARE_WEB_API_KEY

    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute("SELECT share_web_api_key FROM google_sync_state WHERE id = 1").fetchone()
        saved = _clean(row["share_web_api_key"] if row else "")
    if saved:
        return saved
    return _clean(SHARE_WEB_API_KEY)


def save_share_web_api_key(api_key: str) -> bool:
    normalized = _clean(api_key)
    if not normalized:
        return False
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET share_web_api_key = ?, updated_at = ?
            WHERE id = 1
            """,
            (normalized, _now_text()),
        )
        conn.commit()
    return True


def _apply_remote_share_connection_settings(settings: dict) -> None:
    remote_public_web_url = _clean(settings.get("public_web_url"))
    remote_share_web_api_key = _clean(settings.get("share_web_api_key"))
    if remote_public_web_url and not get_public_web_url():
        save_public_web_url(remote_public_web_url)
    if remote_share_web_api_key and not get_share_web_api_key():
        save_share_web_api_key(remote_share_web_api_key)


def _apply_remote_pdf_share_settings(settings: dict) -> None:
    if "address_book_pdf_share_enabled" not in settings:
        return
    local_state = get_google_sync_summary().get("state") or {}
    local_folder_id = _clean(local_state.get("address_book_pdf_share_folder_id"))
    local_folder_name = _clean(local_state.get("address_book_pdf_share_folder_name"))
    remote_folder_id = _clean(settings.get("address_book_pdf_share_folder_id"))
    remote_folder_name = _clean(settings.get("address_book_pdf_share_folder_name"))
    folder_id = remote_folder_id or local_folder_id
    folder_name = remote_folder_name or local_folder_name
    enabled = bool(settings.get("address_book_pdf_share_enabled"))
    if local_folder_id and not remote_folder_id:
        enabled = bool(local_state.get("address_book_pdf_share_enabled") or enabled)
    save_address_book_pdf_share_setting(
        enabled=enabled,
        folder_id=folder_id,
        folder_name=folder_name,
    )


def _share_sync_last_message_for_account(message: str, account_email: str) -> str:
    message_text = str(message or "").strip()
    current_email = _clean(account_email).lower()
    if not message_text or not current_email:
        return ""
    for prefix in ("Share sync for ", "Share sync skipped for "):
        if not message_text.startswith(prefix):
            continue
        owner = message_text[len(prefix) :].split(":", 1)[0].strip().lower()
        if owner != current_email:
            return ""
        break
    return message_text


def record_share_sync_result(owner_email: str, person_ids: list[str], result: dict | None) -> None:
    from app.config import ERROR_LOG_PATH
    from app.logging_utils import append_log_line

    owner = _clean(owner_email)
    count = len(person_ids)
    if result is None:
        message = (
            f"Share sync skipped for {owner} ({count} contact(s)): "
            "set Share web API key in App Settings (same as share app APP_SECRET_KEY)."
        )
    elif result.get("ok") is False:
        message = f"Share sync skipped for {owner}: {result.get('reason', 'unknown')}"
    else:
        summary = result.get("result") if isinstance(result.get("result"), dict) else {}
        message = (
            f"Share sync for {owner}: requested={summary.get('requested', count)} "
            f"updated={summary.get('updated', 0)} skipped={summary.get('skipped', 0)} "
            f"errors={summary.get('errors', 0)}"
        )
        details = summary.get("error_details") or []
        if details:
            message = f"{message} — {'; '.join(str(item) for item in details[:3])}"
    append_log_line(ERROR_LOG_PATH, message)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET share_sync_last_message = ?, updated_at = ?
            WHERE id = 1
            """,
            (message[:500], _now_text()),
        )
        conn.commit()


def save_address_book_pdf_share_setting(
    *,
    enabled: bool,
    folder_id: str | None = None,
    folder_name: str | None = None,
) -> None:
    ensure_google_sync_records()
    normalized_folder_id = _clean(folder_id)
    normalized_folder_name = _clean(folder_name)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              address_book_pdf_share_enabled = ?,
              address_book_pdf_share_folder_id = ?,
              address_book_pdf_share_folder_name = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (
                1 if enabled else 0,
                normalized_folder_id,
                normalized_folder_name,
                _now_text(),
            ),
        )
        if normalized_folder_id:
            conn.execute(
                """
                UPDATE google_sync_state
                SET needs_drive_export = 1, updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )
        conn.commit()


def set_pending_editor_name(editor_name: str) -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET pending_editor_name = ?, updated_at = ?
            WHERE id = 1
            """,
            (_clean(editor_name), _now_text()),
        )
        conn.commit()


def get_pending_editor_name() -> str:
    return _clean((get_google_sync_summary().get("state") or {}).get("pending_editor_name"))


def get_current_access_role() -> str:
    return normalize_access_role((get_google_sync_summary().get("state") or {}).get("access_role"))


ACCESS_ROLE_VALUES = {
    "editor",
    "non_editor",
}

LEGACY_NON_EDITOR_ACCESS_ROLES = {
    "field_list_creation",
    "address_book_creation",
    "changes_contributor",
}


def normalize_access_role(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in LEGACY_NON_EDITOR_ACCESS_ROLES:
        return "non_editor"
    return normalized if normalized in ACCESS_ROLE_VALUES else "editor"


def is_non_editor_access_role(value: str | None = None) -> bool:
    role = normalize_access_role(value if value is not None else get_current_access_role())
    return role == "non_editor"


def is_share_contacts_nav_visible() -> bool:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if str(account.get("status") or "") != "connected":
        return False
    if is_non_editor_access_role():
        return False
    if bool(state.get("multi_editor_enabled") or state.get("multi_editor_account_locked")):
        return (
            str(state.get("editor_mode") or "") == "edit"
            and bool(_clean(state.get("editor_session_id")))
        )
    return True


def initials_from_name(name: str | None) -> str:
    cleaned_words = [
        "".join(char for char in word if char.isalpha())
        for word in str(name or "").strip().split()
    ]
    words = [word for word in cleaned_words if word]
    if len(words) >= 2:
        return f"{words[0][0]}{words[-1][0]}".upper()
    if len(words) == 1:
        word = words[0]
        if len(word) == 1:
            return word[0].upper()
        return f"{word[0].upper()}{word[1].lower()}"
    return ""


def get_current_editor_initials() -> str:
    state = get_google_sync_summary().get("state") or {}
    access_role = normalize_access_role(state.get("access_role"))
    name = state.get("access_name") or state.get("editor_name") or state.get("pending_editor_name")
    initials = initials_from_name(name)
    if initials:
        return initials
    if not bool(state.get("multi_editor_enabled")):
        return "SE"
    if is_non_editor_access_role(access_role):
        return "NE"
    if str(state.get("editor_mode") or "") == "edit":
        return "Ed"
    return ""


def get_current_access_initials() -> str:
    state = get_google_sync_summary().get("state") or {}
    return initials_from_name(state.get("access_name") or state.get("editor_name") or state.get("pending_editor_name"))


def can_edit_changes_list() -> bool:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if is_non_editor_access_role(state.get("access_role")) and _clean(state.get("access_name")):
        return True
    return (
        bool(state.get("multi_editor_enabled"))
        and str(account.get("status") or "") == "connected"
        and str(state.get("editor_mode") or "") == "edit"
        and bool(_clean(state.get("editor_session_id")))
    )


def can_complete_changes_list() -> bool:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    return (
        bool(state.get("multi_editor_enabled"))
        and str(account.get("status") or "") == "connected"
        and str(state.get("editor_mode") or "") == "edit"
        and bool(_clean(state.get("editor_session_id")))
    )


def get_current_changes_list_initials() -> str:
    if not can_edit_changes_list():
        return ""
    state = get_google_sync_summary().get("state") or {}
    return initials_from_name(state.get("access_name") or state.get("editor_name"))


def get_current_changes_completion_initials() -> str:
    if not can_complete_changes_list():
        return ""
    state = get_google_sync_summary().get("state") or {}
    return initials_from_name(state.get("editor_name"))


def save_access_session(access_role: str, access_name: str) -> None:
    normalized_role = normalize_access_role(access_role)
    normalized_name = _clean(access_name)
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET access_role = ?, access_name = ?, access_signed_in_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (normalized_role, normalized_name, _now_text(), _now_text()),
        )
        conn.commit()


def clear_access_session() -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET access_role = 'editor', access_name = '', access_signed_in_at = '', updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def _set_editor_session(
    *,
    mode: str,
    editor_name: str = "",
    session_id: str = "",
    owner_name: str = "",
    expires_at: str = "",
    pending_editor_name: str | None = None,
) -> None:
    updates = [
        "editor_mode = ?",
        "editor_name = ?",
        "editor_session_id = ?",
        "editor_lock_owner_name = ?",
        "editor_lock_expires_at = ?",
        "updated_at = ?",
    ]
    params: list[object] = [mode, editor_name, session_id, owner_name, expires_at, _now_text()]
    if pending_editor_name is not None:
        updates.insert(-1, "pending_editor_name = ?")
        params.insert(-1, pending_editor_name)
    params.append(1)
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            f"UPDATE google_sync_state SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()


def save_google_sync_settings(account_email: str, remember_preferred_account: bool = True) -> None:
    ensure_google_sync_records()
    normalized_email = str(account_email or "").strip() if remember_preferred_account else ""
    with get_connection() as conn:
        existing_row = conn.execute(
            """
            SELECT account_email, account_status
            FROM google_sync_accounts
            WHERE id = 1
            """
        ).fetchone()
        existing_email = str(existing_row["account_email"] or "") if existing_row else ""
        existing_status = str(existing_row["account_status"] or "") if existing_row else ""
        if (
            normalized_email
            and existing_status == "connected"
            and existing_email.strip().lower() == normalized_email.lower()
        ):
            status = "connected"
        elif normalized_email and not _oauth_is_configured():
            status = "oauth_not_configured"
        elif normalized_email:
            status = "configured"
        else:
            status = "not_connected"
        conn.execute(
            """
            UPDATE google_sync_accounts
            SET
              account_email = ?,
              remember_preferred_account = ?,
              account_status = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (normalized_email, 1 if remember_preferred_account and normalized_email else 0, status, _now_text()),
        )
        conn.commit()


def save_single_editor_sync_setting(enabled: bool) -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        state_row = conn.execute(
            "SELECT multi_editor_account_locked FROM google_sync_state WHERE id = 1"
        ).fetchone()
        if state_row and bool(state_row["multi_editor_account_locked"] or 0):
            conn.execute(
                """
                UPDATE google_sync_state
                SET multi_editor_enabled = 1, editor_mode = 'no_edit', updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )
            conn.commit()
            return
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              sync_enabled = ?,
              pending_upload_count = CASE WHEN ? = 1 THEN pending_upload_count ELSE 0 END,
              needs_upload_reminder = CASE WHEN ? = 1 THEN needs_upload_reminder ELSE 0 END,
              needs_drive_export = CASE WHEN ? = 1 THEN needs_drive_export ELSE 0 END,
              book_layouts_needs_drive_export = CASE WHEN ? = 1 THEN book_layouts_needs_drive_export ELSE 0 END,
              contacts_manifest_needs_drive_export = CASE WHEN ? = 1 THEN contacts_manifest_needs_drive_export ELSE 0 END,
              meetingdata_needs_drive_export = CASE WHEN ? = 1 THEN meetingdata_needs_drive_export ELSE 0 END,
              field_list_needs_drive_export = CASE WHEN ? = 1 THEN field_list_needs_drive_export ELSE 0 END,
              changes_list_needs_drive_export = CASE WHEN ? = 1 THEN changes_list_needs_drive_export ELSE 0 END,
              updated_at = ?
            WHERE id = 1
            """,
            (
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                1 if enabled else 0,
                _now_text(),
            ),
        )
        if not enabled:
            conn.execute("DELETE FROM google_sync_queue WHERE status = 'pending'")
        else:
            _queue_unuploaded_local_contacts(conn)
            _refresh_pending_upload_count(conn)
        conn.commit()


def queue_contact_sync(contact_id: int | None, operation: str, google_contact_id: str = "", payload: dict | None = None) -> None:
    ensure_google_sync_records()
    payload_json = json.dumps(payload or {}, sort_keys=True)
    normalized_google_contact_id = str(google_contact_id or "").strip()
    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            return
        if contact_id is not None:
            conn.execute(
                """
                DELETE FROM google_sync_queue
                WHERE status = 'pending' AND contact_id = ?
                """,
                (contact_id,),
            )
        elif normalized_google_contact_id:
            conn.execute(
                """
                DELETE FROM google_sync_queue
                WHERE status = 'pending' AND google_contact_id = ?
                """,
                (normalized_google_contact_id,),
            )
        conn.execute(
            """
            INSERT INTO google_sync_queue (
              contact_id,
              google_contact_id,
              operation,
              payload_json,
              status,
              last_error,
              updated_at
            ) VALUES (?, ?, ?, ?, 'pending', '', ?)
            """,
            (contact_id, normalized_google_contact_id, operation, payload_json, _now_text()),
        )
        _refresh_pending_upload_count(conn)
        conn.commit()


def mark_shared_data_for_drive_export() -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            return
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              needs_drive_export = 1,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def mark_contacts_manifest_for_drive_export() -> None:
    try:
        from app.services.field_list_service import invalidate_field_list_print_payload_cache

        invalidate_field_list_print_payload_cache()
    except Exception:
        pass
    ensure_google_sync_records()
    with get_connection() as conn:
        # Always remember that Contacts need an Import-from-backup JSON on editor sign-out,
        # even if Shared Drive writes are temporarily disabled.
        _mark_contacts_signout_backup_needed(conn)
        if not _google_sync_is_enabled_for_writes(conn):
            conn.commit()
            return
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              contacts_manifest_needs_drive_export = 1,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def mark_meetingdata_for_drive_export() -> None:
    try:
        from app.services.field_list_service import invalidate_field_list_print_payload_cache

        invalidate_field_list_print_payload_cache()
    except Exception:
        pass
    ensure_google_sync_records()
    with get_connection() as conn:
        _mark_meetingdata_signout_backup_needed(conn)
        if not _google_sync_is_enabled_for_writes(conn):
            conn.commit()
            return
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              meetingdata_needs_drive_export = 1,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def mark_field_list_for_drive_export() -> None:
    try:
        from app.services.field_list_service import invalidate_field_list_print_payload_cache

        invalidate_field_list_print_payload_cache()
    except Exception:
        pass
    ensure_google_sync_records()
    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            return
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              field_list_needs_drive_export = 1,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def mark_book_layouts_for_drive_export() -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        if not _google_sync_is_enabled_for_writes(conn):
            return
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              book_layouts_needs_drive_export = 1,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def _clear_pending_drive_export(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          needs_drive_export = 0,
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_pending_contacts_manifest_drive_export(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          contacts_manifest_needs_drive_export = 0,
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_pending_meetingdata_drive_export(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          meetingdata_needs_drive_export = 0,
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_pending_field_list_drive_export(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          field_list_needs_drive_export = 0,
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_pending_book_layouts_drive_export(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          book_layouts_needs_drive_export = 0,
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _clear_pending_changes_list_drive_export(conn) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          changes_list_needs_drive_export = 0,
          updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def clear_pending_contact_sync(contact_id: int | None = None, google_contact_id: str = "") -> None:
    normalized_google_contact_id = str(google_contact_id or "").strip()
    if contact_id is None and not normalized_google_contact_id:
        return
    ensure_google_sync_records()
    with get_connection() as conn:
        if contact_id is not None:
            conn.execute(
                """
                DELETE FROM google_sync_queue
                WHERE status = 'pending' AND contact_id = ?
                """,
                (contact_id,),
            )
        if normalized_google_contact_id:
            conn.execute(
                """
                DELETE FROM google_sync_queue
                WHERE status = 'pending' AND google_contact_id = ?
                """,
                (normalized_google_contact_id,),
            )
        _refresh_pending_upload_count(conn)
        conn.commit()


def record_google_import_warning() -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              import_warning_acknowledged_at = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(), _now_text()),
        )
        conn.commit()


def record_google_upload_attempt() -> bool:
    ensure_google_sync_records()
    with get_connection() as conn:
        pending_count = _refresh_pending_upload_count(conn)
        if pending_count <= 0:
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  last_sync_error = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (SYNC_NOTICE_MESSAGES["upload_queue_empty"], _now_text()),
            )
            conn.commit()
            return False

        conn.execute(
            """
            UPDATE google_sync_state
            SET
              last_sync_error = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (SYNC_NOTICE_MESSAGES["upload_not_ready"], _now_text()),
        )
        conn.commit()
        return True


def _set_last_sync_error(conn, message: str) -> None:
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          last_sync_error = ?,
          updated_at = ?
        WHERE id = 1
        """,
        (str(message or "").strip(), _now_text()),
    )


def _clear_last_sync_error(conn) -> None:
    _set_last_sync_error(conn, "")


def record_google_sync_error(message: str) -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        _set_last_sync_error(conn, message)
        conn.commit()


def store_pending_oauth_state(state: str) -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              pending_oauth_state = ?,
              pending_oauth_state_created_at = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (state, _now_text(), _now_text()),
        )
        conn.commit()


def clear_pending_oauth_state() -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              pending_oauth_state = '',
              pending_oauth_state_created_at = '',
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def create_google_auth_url() -> str:
    if not _oauth_is_configured():
        raise RuntimeError("Google OAuth is not configured.")
    state = secrets.token_urlsafe(24)
    store_pending_oauth_state(state)
    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "select_account consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_BASE_URL}?{urlencode(params)}"


def _load_pending_oauth_state() -> str:
    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute("SELECT pending_oauth_state FROM google_sync_state WHERE id = 1").fetchone()
    return str(row["pending_oauth_state"] or "") if row else ""


def _exchange_code_for_tokens(code: str) -> dict:
    return _json_request(
        GOOGLE_TOKEN_URL,
        method="POST",
        data={
            "code": code,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )


def _refresh_access_token(refresh_token: str) -> dict:
    return _json_request(
        GOOGLE_TOKEN_URL,
        method="POST",
        data={
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )


def _fetch_userinfo(access_token: str) -> dict:
    return _json_request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def complete_google_oauth(code: str, state: str) -> None:
    if not _oauth_is_configured():
        raise RuntimeError(SYNC_NOTICE_MESSAGES["oauth_not_configured"])

    pending_state = _load_pending_oauth_state()
    if not pending_state or state != pending_state:
        raise RuntimeError("Google OAuth state did not match the pending request.")

    token_data = _exchange_code_for_tokens(code)
    access_token = str(token_data.get("access_token") or "")
    refresh_token = str(token_data.get("refresh_token") or "")
    expires_in = int(token_data.get("expires_in") or 3600)
    scope_text = str(token_data.get("scope") or "")
    token_expiry = (_utc_now() + timedelta(seconds=max(expires_in - 60, 60))).isoformat()
    userinfo = _fetch_userinfo(access_token)
    account_email = str(userinfo.get("email") or "").strip()
    google_user_id = str(userinfo.get("sub") or "").strip()
    google_display_name = ""

    with get_connection() as conn:
        existing_row = conn.execute(
            "SELECT refresh_token, account_email, google_user_id FROM google_sync_accounts WHERE id = 1"
        ).fetchone()
        previous_email = str(existing_row["account_email"] or "") if existing_row else ""
        previous_user_id = str(existing_row["google_user_id"] or "") if existing_row else ""
        email_changed = bool(previous_email and account_email and previous_email != account_email)
        user_id_changed = bool(previous_user_id and google_user_id and previous_user_id != google_user_id)
        account_changed = email_changed or user_id_changed
        saved_refresh_token = refresh_token or ("" if account_changed else (str(existing_row["refresh_token"] or "") if existing_row else ""))
        conn.execute(
            """
            UPDATE google_sync_accounts
            SET
              account_email = ?,
              remember_preferred_account = 1,
              account_status = 'connected',
              google_user_id = ?,
              google_display_name = ?,
              access_token = ?,
              refresh_token = ?,
              token_expiry = ?,
              scopes = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (
                account_email,
                google_user_id,
                google_display_name,
                access_token,
                saved_refresh_token,
                token_expiry,
                scope_text,
                _now_text(),
            ),
        )
        clear_editor_session = 1 if account_changed else 0
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              last_sync_error = '',
              drive_root_folder_id = '',
              drive_app_revisions_folder_id = '',
              drive_app_revisions_file_id = '',
              pending_oauth_state = '',
              pending_oauth_state_created_at = '',
              signin_sync_in_progress = 0,
              signin_sync_phase = '',
              signin_sync_error = '',
              signin_sync_total_count = 0,
              signin_sync_processed_count = 0,
              signin_sync_current_item = '',
              editor_name = CASE WHEN ? = 1 THEN '' ELSE editor_name END,
              editor_session_id = CASE WHEN ? = 1 THEN '' ELSE editor_session_id END,
              sync_enabled = CASE WHEN multi_editor_enabled = 1 THEN sync_enabled ELSE 1 END,
              editor_mode = CASE
                WHEN ? = 1 THEN CASE WHEN multi_editor_enabled = 1 THEN 'no_edit' ELSE 'normal' END
                ELSE editor_mode
              END,
              editor_lock_owner_name = CASE WHEN ? = 1 THEN '' ELSE editor_lock_owner_name END,
              editor_lock_expires_at = CASE WHEN ? = 1 THEN '' ELSE editor_lock_expires_at END,
              updated_at = ?
            WHERE id = 1
            """,
            (
                clear_editor_session,
                clear_editor_session,
                clear_editor_session,
                clear_editor_session,
                clear_editor_session,
                _now_text(),
            ),
        )
        if account_changed:
            _clear_transfer_progress_for_account_switch(conn)
            conn.execute(
                "UPDATE google_sync_state SET share_sync_last_message = '' WHERE id = 1"
            )
        state_row = conn.execute("SELECT multi_editor_enabled FROM google_sync_state WHERE id = 1").fetchone()
        if state_row and not bool(state_row["multi_editor_enabled"] or 0):
            _queue_unuploaded_local_contacts(conn)
            _refresh_pending_upload_count(conn)
        conn.commit()

    try:
        import_shared_settings_from_google_drive(export_if_missing=False)
    except (RuntimeError, HTTPError, URLError):
        summary = get_google_sync_summary()
        state = summary.get("state") or {}
        if bool(state.get("multi_editor_enabled")):
            raise

def disconnect_google_account(*, preserve_editor_lock_notice: bool = False) -> None:
    ensure_google_sync_records()
    with get_connection() as conn:
        account_row = conn.execute("SELECT account_email, remember_preferred_account FROM google_sync_accounts WHERE id = 1").fetchone()
        remembered_email = ""
        if account_row and bool(account_row["remember_preferred_account"] or 0):
            remembered_email = str(account_row["account_email"] or "")
        lock_notice_owner = ""
        lock_notice_expires_at = ""
        if preserve_editor_lock_notice:
            state_row = conn.execute(
                "SELECT editor_lock_owner_name, editor_lock_expires_at FROM google_sync_state WHERE id = 1"
            ).fetchone()
            if state_row:
                lock_notice_owner = str(state_row["editor_lock_owner_name"] or "")
                lock_notice_expires_at = str(state_row["editor_lock_expires_at"] or "")
        conn.execute(
            """
            UPDATE google_sync_accounts
            SET
              account_email = ?,
              account_status = 'not_connected',
              google_user_id = '',
              google_display_name = '',
              access_token = '',
              refresh_token = '',
              token_expiry = '',
              scopes = '',
              updated_at = ?
            WHERE id = 1
            """,
            (remembered_email, _now_text()),
        )
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              sync_enabled = 0,
              last_sync_error = '',
              last_drive_export_at = '',
              signin_sync_in_progress = 0,
              signin_sync_phase = '',
              signin_sync_error = '',
              share_sync_last_message = '',
              drive_root_folder_id = '',
              drive_app_revisions_folder_id = '',
              drive_app_revisions_file_id = '',
              upload_in_progress = 0,
              upload_phase = '',
              upload_total_count = 0,
              upload_processed_count = 0,
              upload_current_contact = '',
              upload_started_at = '',
              bootstrap_status = 'idle',
              bootstrap_total_contacts = 0,
              bootstrap_processed_contacts = 0,
              bootstrap_last_contact_id = 0,
              bootstrap_started_at = '',
              bootstrap_updated_at = ?,
              bootstrap_error = '',
              pending_oauth_state = '',
              pending_oauth_state_created_at = '',
              editor_name = '',
              editor_session_id = '',
              editor_mode = CASE WHEN multi_editor_enabled = 1 THEN 'no_edit' ELSE 'normal' END,
              editor_lock_owner_name = ?,
              editor_lock_expires_at = ?,
              pending_editor_name = '',
              access_role = 'editor',
              access_name = '',
              access_signed_in_at = '',
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(), lock_notice_owner, lock_notice_expires_at, _now_text()),
        )
        conn.commit()


def _load_google_account() -> dict:
    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM google_sync_accounts WHERE id = 1").fetchone()
        return dict(row) if row else {}


def _mark_google_reconnect_required(message: str) -> None:
    error_message = _clean(message) or "Google sign-in expired or was revoked. Reconnect the Google account."
    with get_connection() as conn:
        account_row = conn.execute(
            "SELECT account_email, remember_preferred_account FROM google_sync_accounts WHERE id = 1"
        ).fetchone()
        remembered_email = ""
        if account_row and bool(account_row["remember_preferred_account"] or 0):
            remembered_email = str(account_row["account_email"] or "")
        conn.execute(
            """
            UPDATE google_sync_accounts
            SET
              account_email = ?,
              account_status = 'not_connected',
              google_user_id = '',
              google_display_name = '',
              access_token = '',
              refresh_token = '',
              token_expiry = '',
              scopes = '',
              updated_at = ?
            WHERE id = 1
            """,
            (remembered_email, _now_text()),
        )
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              sync_enabled = 0,
              upload_in_progress = 0,
              upload_phase = '',
              upload_current_contact = '',
              last_sync_error = ?,
              signin_sync_in_progress = 0,
              signin_sync_phase = '',
              signin_sync_error = '',
              updated_at = ?
            WHERE id = 1
            """,
            (error_message, _now_text()),
        )
        conn.commit()


def _get_valid_access_token(*, force_refresh: bool = False) -> tuple[str, str]:
    account = _load_google_account()
    access_token = str(account.get("access_token") or "")
    refresh_token = str(account.get("refresh_token") or "")
    token_expiry_raw = str(account.get("token_expiry") or "")
    if not access_token and not refresh_token:
        raise RuntimeError("Google account is not connected.")

    expiry = None
    if token_expiry_raw:
        try:
            expiry = datetime.fromisoformat(token_expiry_raw)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except ValueError:
            expiry = None

    if access_token and expiry and expiry > _utc_now() and not force_refresh:
        return access_token, str(account.get("account_email") or "")

    if not refresh_token:
        raise RuntimeError("Google refresh token is missing. Reconnect the Google account.")

    try:
        refreshed = _refresh_access_token(refresh_token)
    except HTTPError as exc:
        message = _http_error_message(exc)
        normalized_message = message.lower()
        if _looks_like_reconnect_error(normalized_message):
            reconnect_message = "Google sign-in expired or was revoked. Reconnect the Google account."
            _mark_google_reconnect_required(reconnect_message)
            raise RuntimeError(reconnect_message) from exc
        raise
    new_access_token = str(refreshed.get("access_token") or "")
    stored_email = str(account.get("account_email") or "")
    stored_user_id = str(account.get("google_user_id") or "")
    if new_access_token and (stored_email or stored_user_id):
        refreshed_userinfo = _fetch_userinfo(new_access_token)
        refreshed_email = str(refreshed_userinfo.get("email") or "").strip()
        refreshed_user_id = str(refreshed_userinfo.get("sub") or "").strip()
        if (stored_email and refreshed_email != stored_email) or (stored_user_id and refreshed_user_id != stored_user_id):
            reconnect_message = "Google sign-in does not match the connected account. Reconnect the Google account."
            _mark_google_reconnect_required(reconnect_message)
            raise RuntimeError(reconnect_message)
    expires_in = int(refreshed.get("expires_in") or 3600)
    token_expiry = (_utc_now() + timedelta(seconds=max(expires_in - 60, 60))).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_accounts
            SET
              access_token = ?,
              token_expiry = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (new_access_token, token_expiry, _now_text()),
        )
        conn.commit()
    return new_access_token, str(account.get("account_email") or "")


def _api_get_json(url: str, access_token: str, *, timeout: int | None = None) -> dict:
    kwargs = {"headers": {"Authorization": f"Bearer {access_token}"}}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return _json_request(url, **kwargs)


def _mobile_people_get_json(url: str, access_token: str) -> dict:
    """Use httpx on Cloud Run; urllib to People API has been hanging without returning."""
    import httpx

    try:
        response = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=12.0,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise RuntimeError("Google Contacts did not respond in time. Try Sync again.") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Google Contacts request failed ({exc.response.status_code}).") from exc
    return response.json() if response.content else {}


def _api_json_request(
    url: str,
    access_token: str,
    *,
    method: str = "GET",
    json_data: dict | None = None,
) -> dict:
    return _json_request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {access_token}"},
        json_data=json_data,
    )


def load_contact_photo_bytes(photo_url: str | None) -> bytes:
    """Return image bytes for a local CFS photo path or a still-reachable http(s) photo URL."""
    normalized = _clean(photo_url)
    if not normalized:
        return b""
    local_bytes = _local_contact_photo_bytes(normalized)
    if local_bytes:
        return local_bytes
    if not (normalized.startswith("http://") or normalized.startswith("https://")):
        return b""
    try:
        request = Request(normalized, headers={"User-Agent": "ContactsFreeShare/1.0"})
        with _urlopen_prefer_ipv4(request, timeout=30) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            payload = response.read()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return b""
    if not payload:
        return b""
    if content_type and not content_type.startswith("image/"):
        return b""
    return payload


def update_google_contact_photo(resource_name: str, photo_bytes: bytes) -> str:
    normalized_resource_name = _clean(resource_name)
    if not normalized_resource_name:
        raise RuntimeError(SYNC_NOTICE_MESSAGES["contact_photo_google_not_connected"])
    if not photo_bytes:
        raise RuntimeError(SYNC_NOTICE_MESSAGES["contact_photo_google_missing"])
    access_token, _account_email = _get_valid_access_token()
    return _update_google_contact_photo(access_token, normalized_resource_name, photo_bytes)


def delete_google_contact_photo(resource_name: str) -> None:
    normalized_resource_name = _clean(resource_name)
    if not normalized_resource_name:
        raise RuntimeError(SYNC_NOTICE_MESSAGES["contact_photo_google_not_connected"])
    access_token, _account_email = _get_valid_access_token()
    _delete_google_contact_photo(access_token, normalized_resource_name)


def fetch_google_contact_stock_photo_url(resource_name: str) -> str:
    """Return the Google Contacts photo URL after a custom photo is removed (default letter avatar)."""
    normalized_resource_name = _clean(resource_name)
    if not normalized_resource_name:
        return ""
    access_token, _account_email = _get_valid_access_token()
    # GOOGLE_UPLOAD_PERSON_FIELDS omits photos; request them explicitly for the stock avatar.
    person = _api_get_json(
        f"{GOOGLE_PEOPLE_BASE_URL}/{normalized_resource_name}?personFields=photos,names,metadata",
        access_token,
    )
    return _extract_photo_url(person, include_default=True)


def _delete_google_contact_photo(access_token: str, resource_name: str) -> None:
    normalized_resource_name = _clean(resource_name)
    if not normalized_resource_name:
        raise RuntimeError(SYNC_NOTICE_MESSAGES["contact_photo_google_not_connected"])
    _api_json_request(
        f"{GOOGLE_PEOPLE_BASE_URL}/{normalized_resource_name}:deleteContactPhoto?personFields=photos",
        access_token,
        method="DELETE",
    )


def _update_google_contact_photo(access_token: str, resource_name: str, photo_bytes: bytes) -> str:
    normalized_resource_name = _clean(resource_name)
    if not normalized_resource_name:
        raise RuntimeError(SYNC_NOTICE_MESSAGES["contact_photo_google_not_connected"])
    if not photo_bytes:
        raise RuntimeError(SYNC_NOTICE_MESSAGES["contact_photo_google_missing"])
    payload = {
        "photoBytes": base64.b64encode(photo_bytes).decode("ascii"),
        "personFields": "photos",
        "sources": ["READ_SOURCE_TYPE_CONTACT"],
    }
    response = _api_json_request(
        f"{GOOGLE_PEOPLE_BASE_URL}/{normalized_resource_name}:updateContactPhoto",
        access_token,
        method="PATCH",
        json_data=payload,
    )
    person = response.get("person") or {}
    return _extract_photo_url(person)


def _drive_json_request(
    url: str,
    access_token: str,
    *,
    method: str = "GET",
    json_data: dict | None = None,
    raw_data: bytes | None = None,
    headers: dict | None = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict:
    request_headers = {"Authorization": f"Bearer {access_token}"}
    if headers:
        request_headers.update(headers)
    return _json_request(
        url,
        method=method,
        headers=request_headers,
        json_data=json_data,
        raw_data=raw_data,
        timeout=timeout,
    )


def _drive_query_literal(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _store_drive_state(
    *,
    root_folder_id: str | None = None,
    app_revisions_folder_id: str | None = None,
    app_revisions_file_id: str | None = None,
    last_drive_export_at: str | None = None,
    last_sync_error: str | None = None,
) -> None:
    ensure_google_sync_records()
    updates: list[str] = []
    params: list[str] = []
    if root_folder_id is not None:
        updates.append("drive_root_folder_id = ?")
        params.append(str(root_folder_id))
    if app_revisions_folder_id is not None:
        updates.append("drive_app_revisions_folder_id = ?")
        params.append(str(app_revisions_folder_id))
    if app_revisions_file_id is not None:
        updates.append("drive_app_revisions_file_id = ?")
        params.append(str(app_revisions_file_id))
    if last_drive_export_at is not None:
        updates.append("last_drive_export_at = ?")
        params.append(str(last_drive_export_at))
    if last_sync_error is not None:
        updates.append("last_sync_error = ?")
        params.append(str(last_sync_error))
    if not updates:
        return
    updates.append("updated_at = ?")
    params.append(_now_text())
    params.append("1")
    with get_connection() as conn:
        conn.execute(
            f"""
            UPDATE google_sync_state
            SET {", ".join(updates)}
            WHERE id = ?
            """,
            params,
        )
        conn.commit()


def _load_google_sync_state() -> dict:
    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
        return dict(row) if row else {}


def _store_local_sync_revisions(
    *,
    conn=None,
    settings_sync_revision: int | None = None,
    book_layouts_sync_revision: int | None = None,
    contacts_sync_revision: int | None = None,
    meetingdata_sync_revision: int | None = None,
    field_list_sync_revision: int | None = None,
    changes_list_sync_revision: int | None = None,
) -> None:
    updates: list[str] = []
    params: list[int | str] = []
    if settings_sync_revision is not None:
        updates.append("settings_sync_revision = ?")
        params.append(int(settings_sync_revision))
    if book_layouts_sync_revision is not None:
        updates.append("book_layouts_sync_revision = ?")
        params.append(int(book_layouts_sync_revision))
    if contacts_sync_revision is not None:
        updates.append("contacts_sync_revision = ?")
        params.append(int(contacts_sync_revision))
    if meetingdata_sync_revision is not None:
        updates.append("meetingdata_sync_revision = ?")
        params.append(int(meetingdata_sync_revision))
    if field_list_sync_revision is not None:
        updates.append("field_list_sync_revision = ?")
        params.append(int(field_list_sync_revision))
    if changes_list_sync_revision is not None:
        updates.append("changes_list_sync_revision = ?")
        params.append(int(changes_list_sync_revision))
    if not updates:
        return
    updates.append("updated_at = ?")
    params.append(_now_text())
    params.append("1")
    if conn is not None:
        conn.execute(
            f"""
            UPDATE google_sync_state
            SET {", ".join(updates)}
            WHERE id = ?
            """,
            params,
        )
        return
    ensure_google_sync_records()
    with get_connection() as managed_conn:
        managed_conn.execute(
            f"""
            UPDATE google_sync_state
            SET {", ".join(updates)}
            WHERE id = ?
            """,
            params,
        )
        managed_conn.commit()


def _normalize_sync_revision(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _shared_sync_state_payload(
    *,
    settings_sync_revision: int,
    contacts_sync_revision: int,
    meetingdata_sync_revision: int,
    field_list_sync_revision: int,
    changes_list_sync_revision: int = 0,
    book_layouts_sync_revision: int = 0,
) -> dict:
    return {
        "format": "contactsfreeshare.shared_sync_state.v1",
        "exported_at": _iso_now(),
        "settings_sync_revision": _normalize_sync_revision(settings_sync_revision),
        "book_layouts_sync_revision": _normalize_sync_revision(book_layouts_sync_revision),
        "contacts_sync_revision": _normalize_sync_revision(contacts_sync_revision),
        "meetingdata_sync_revision": _normalize_sync_revision(meetingdata_sync_revision),
        "field_list_sync_revision": _normalize_sync_revision(field_list_sync_revision),
        "changes_list_sync_revision": _normalize_sync_revision(changes_list_sync_revision),
    }


def _normalize_shared_sync_state(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {
            "settings_sync_revision": 0,
            "book_layouts_sync_revision": 0,
            "contacts_sync_revision": 0,
            "meetingdata_sync_revision": 0,
            "field_list_sync_revision": 0,
            "changes_list_sync_revision": 0,
        }
    return {
        "settings_sync_revision": _normalize_sync_revision(payload.get("settings_sync_revision")),
        "book_layouts_sync_revision": _normalize_sync_revision(payload.get("book_layouts_sync_revision")),
        "contacts_sync_revision": _normalize_sync_revision(payload.get("contacts_sync_revision")),
        "meetingdata_sync_revision": _normalize_sync_revision(payload.get("meetingdata_sync_revision")),
        "field_list_sync_revision": _normalize_sync_revision(payload.get("field_list_sync_revision")),
        "changes_list_sync_revision": _normalize_sync_revision(payload.get("changes_list_sync_revision")),
    }


def _drive_get_file(access_token: str, file_id: str, *, fields: str = "id,name,parents,mimeType") -> dict | None:
    normalized_file_id = _clean(file_id)
    if not normalized_file_id:
        return None
    try:
        return _drive_json_request(
            f"{GOOGLE_DRIVE_BASE_URL}/files/{normalized_file_id}?{urlencode({'fields': fields, 'supportsAllDrives': 'true'})}",
            access_token,
        )
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _drive_find_child(
    access_token: str,
    *,
    parent_id: str,
    name: str,
    mime_type: str,
) -> dict | None:
    query = " and ".join(
        [
            f"'{_drive_query_literal(parent_id)}' in parents",
            f"name = '{_drive_query_literal(name)}'",
            f"mimeType = '{_drive_query_literal(mime_type)}'",
            "trashed = false",
        ]
    )
    payload = _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': query, 'fields': 'files(id,name,parents,mimeType)', 'pageSize': 1, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
        access_token,
    )
    files = payload.get("files") or []
    return dict(files[0]) if files else None


def _drive_find_children(
    access_token: str,
    *,
    parent_id: str,
    name: str,
    mime_type: str,
    fields: str = "files(id,name,parents,mimeType,createdTime,modifiedTime)",
) -> list[dict]:
    query = " and ".join(
        [
            f"'{_drive_query_literal(parent_id)}' in parents",
            f"name = '{_drive_query_literal(name)}'",
            f"mimeType = '{_drive_query_literal(mime_type)}'",
            "trashed = false",
        ]
    )
    payload = _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': query, 'fields': fields, 'pageSize': 1000, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
        access_token,
    )
    return [dict(item) for item in payload.get("files") or []]


def _drive_list_children(access_token: str, *, parent_id: str) -> list[dict]:
    query = " and ".join(
        [
            f"'{_drive_query_literal(parent_id)}' in parents",
            "trashed = false",
        ]
    )
    files: list[dict] = []
    page_token = ""
    while True:
        params = {
            "q": query,
            "fields": "nextPageToken,files(id,name,parents,mimeType)",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _drive_json_request(
            f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode(params)}",
            access_token,
        )
        files.extend(dict(item) for item in payload.get("files") or [])
        page_token = str(payload.get("nextPageToken") or "").strip()
        if not page_token:
            break
    return files


def _drive_children_by_name(access_token: str, *, parent_id: str) -> dict[str, dict]:
    return {
        _clean(item.get("name")): item
        for item in _drive_list_children(access_token, parent_id=parent_id)
        if _clean(item.get("name"))
    }


def _shared_sync_state_score(payload: dict) -> int:
    return (
        _normalize_sync_revision(payload.get("contacts_sync_revision")) * 1000
        + _normalize_sync_revision(payload.get("settings_sync_revision"))
        + _normalize_sync_revision(payload.get("book_layouts_sync_revision"))
        + _normalize_sync_revision(payload.get("meetingdata_sync_revision"))
        + _normalize_sync_revision(payload.get("field_list_sync_revision"))
        + _normalize_sync_revision(payload.get("changes_list_sync_revision"))
    )


def _drive_root_folder_score(access_token: str, folder_item: dict) -> tuple[int, str]:
    folder_id = _clean(folder_item.get("id"))
    if not folder_id:
        return (0, "")
    modified_time = _clean(folder_item.get("modifiedTime") or folder_item.get("createdTime"))
    try:
        shared_folder = _drive_find_child(
            access_token,
            parent_id=folder_id,
            name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
            mime_type="application/vnd.google-apps.folder",
        )
        if shared_folder is None:
            return (0, modified_time)
        sync_file = _drive_find_child(
            access_token,
            parent_id=_clean(shared_folder.get("id")),
            name=GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME,
            mime_type="application/json",
        )
        if sync_file is None:
            return (0, modified_time)
        return (_shared_sync_state_score(_drive_load_json_file_content(access_token, _clean(sync_file.get("id")))), modified_time)
    except (RuntimeError, HTTPError, URLError, ValueError):
        return (0, modified_time)


def _drive_find_best_root_folder(access_token: str) -> dict | None:
    candidates = _drive_find_children(
        access_token,
        parent_id="root",
        name=GOOGLE_DRIVE_ROOT_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if not candidates:
        return None
    return max(candidates, key=lambda item: _drive_root_folder_score(access_token, item))


def _drive_create_folder(access_token: str, *, name: str, parent_id: str) -> dict:
    payload = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    return _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'fields': 'id,name,parents,mimeType', 'supportsAllDrives': 'true'})}",
        access_token,
        method="POST",
        json_data=payload,
    )


def _drive_create_json_file(access_token: str, *, name: str, parent_id: str) -> dict:
    payload = {
        "name": name,
        "mimeType": "application/json",
        "parents": [parent_id],
    }
    return _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'fields': 'id,name,parents,mimeType', 'supportsAllDrives': 'true'})}",
        access_token,
        method="POST",
        json_data=payload,
    )


def _drive_create_json_file_with_content(
    access_token: str,
    *,
    name: str,
    parent_id: str,
    payload: dict,
) -> dict:
    boundary = f"contactsfreeshare_{secrets.token_hex(12)}"
    metadata = {
        "name": name,
        "mimeType": "application/json",
        "parents": [parent_id],
    }
    body = "\r\n".join(
        [
            f"--{boundary}",
            "Content-Type: application/json; charset=UTF-8",
            "",
            json.dumps(metadata, separators=(",", ":")),
            f"--{boundary}",
            "Content-Type: application/json; charset=UTF-8",
            "",
            json.dumps(payload, indent=2),
            f"--{boundary}--",
            "",
        ]
    ).encode("utf-8")
    return _drive_json_request(
        f"{GOOGLE_DRIVE_UPLOAD_BASE_URL}/files?{urlencode({'uploadType': 'multipart', 'fields': 'id,name,parents,mimeType', 'supportsAllDrives': 'true'})}",
        access_token,
        method="POST",
        raw_data=body,
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        timeout=DRIVE_UPLOAD_TIMEOUT_SECONDS,
    )


def _drive_create_file(
    access_token: str,
    *,
    name: str,
    parent_id: str,
    mime_type: str,
    app_properties: dict[str, str] | None = None,
) -> dict:
    payload = {
        "name": name,
        "mimeType": mime_type,
        "parents": [parent_id],
    }
    cleaned_app_properties = {
        _clean(key): _clean(value)
        for key, value in (app_properties or {}).items()
        if _clean(key) and _clean(value)
    }
    if cleaned_app_properties:
        payload["appProperties"] = cleaned_app_properties
    return _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'fields': 'id,name,parents,mimeType,appProperties', 'supportsAllDrives': 'true'})}",
        access_token,
        method="POST",
        json_data=payload,
    )


def _drive_media_update_urls(file_id: str) -> list[str]:
    # supportsAllDrives makes some My Drive files return "Invalid argument".
    # Retry the same bytes without that flag before giving up.
    return [
        f"{GOOGLE_DRIVE_UPLOAD_BASE_URL}/files/{file_id}?{urlencode({'uploadType': 'media', 'supportsAllDrives': 'true'})}",
        f"{GOOGLE_DRIVE_UPLOAD_BASE_URL}/files/{file_id}?{urlencode({'uploadType': 'media'})}",
    ]


def _drive_update_media_content(
    access_token: str,
    file_id: str,
    *,
    content: bytes,
    mime_type: str,
    timeout: int,
) -> dict:
    last_error: HTTPError | None = None
    for index, url in enumerate(_drive_media_update_urls(file_id)):
        try:
            return _drive_json_request(
                url,
                access_token,
                method="PATCH",
                raw_data=content,
                headers={"Content-Type": mime_type},
                timeout=timeout,
            )
        except HTTPError as exc:
            last_error = exc
            if exc.code != 400 or index == len(_drive_media_update_urls(file_id)) - 1:
                raise
    raise last_error or RuntimeError("Google Drive file update failed.")


def _drive_update_json_file_content(access_token: str, file_id: str, payload: dict) -> dict:
    return _drive_update_media_content(
        access_token,
        file_id,
        content=json.dumps(payload, indent=2).encode("utf-8"),
        mime_type="application/json",
        timeout=DRIVE_JSON_UPLOAD_TIMEOUT_SECONDS,
    )


def _drive_update_file_content(access_token: str, file_id: str, *, content: bytes, mime_type: str) -> dict:
    return _drive_update_media_content(
        access_token,
        file_id,
        content=content,
        mime_type=mime_type,
        timeout=DRIVE_UPLOAD_TIMEOUT_SECONDS,
    )


def _drive_ensure_file_parent(access_token: str, file_id: str, parent_id: str) -> dict | None:
    normalized_file_id = _clean(file_id)
    normalized_parent_id = _clean(parent_id)
    if not normalized_file_id or not normalized_parent_id:
        return None
    file_item = _drive_get_file(
        access_token,
        normalized_file_id,
        fields="id,name,parents,mimeType",
    )
    if not file_item:
        return None
    parents = [_clean(parent) for parent in (file_item.get("parents") or []) if _clean(parent)]
    if parents == [normalized_parent_id]:
        return file_item
    query_params = {
        "fields": "id,name,parents,mimeType",
        "supportsAllDrives": "true",
        "addParents": normalized_parent_id,
    }
    parents_to_remove = [parent for parent in parents if parent != normalized_parent_id]
    if parents_to_remove:
        query_params["removeParents"] = ",".join(parents_to_remove)
    return _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files/{normalized_file_id}?{urlencode(query_params)}",
        access_token,
        method="PATCH",
        json_data={},
    )


def _drive_load_json_file_content(access_token: str, file_id: str) -> dict:
    return _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files/{file_id}?{urlencode({'alt': 'media', 'supportsAllDrives': 'true'})}",
        access_token,
    )


def _drive_load_contacts_manifest_cached(access_token: str, storage: dict) -> dict:
    file_id = str(storage.get("contacts_current_file_id") or "").strip()
    if not file_id:
        return {}
    now = time.monotonic()
    cached = _CONTACTS_MANIFEST_CACHE.get(file_id)
    if cached and now - cached[0] < CONTACTS_MANIFEST_CACHE_TTL_SECONDS:
        return dict(cached[1])
    payload = _drive_load_json_file_content(access_token, file_id)
    _CONTACTS_MANIFEST_CACHE[file_id] = (now, dict(payload))
    return payload


def _drive_load_file_content(
    access_token: str,
    file_id: str,
    *,
    timeout: int = DRIVE_UPLOAD_TIMEOUT_SECONDS,
) -> bytes:
    request = Request(
        f"{GOOGLE_DRIVE_BASE_URL}/files/{file_id}?{urlencode({'alt': 'media', 'supportsAllDrives': 'true'})}",
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    for attempt in range(1, DRIVE_READ_RETRY_ATTEMPTS + 1):
        try:
            with _urlopen_prefer_ipv4(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            if attempt >= DRIVE_READ_RETRY_ATTEMPTS or exc.code not in {429, 500, 502, 503, 504}:
                raise
            time.sleep(DRIVE_READ_RETRY_BASE_SECONDS * attempt)
        except (
            TimeoutError,
            socket.timeout,
            URLError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            http.client.BadStatusLine,
        ):
            if attempt >= DRIVE_READ_RETRY_ATTEMPTS:
                raise
            time.sleep(DRIVE_READ_RETRY_BASE_SECONDS * attempt)
    return b""


def _drive_delete_file_if_exists(access_token: str, file_id: str) -> None:
    normalized_file_id = _clean(file_id)
    if not normalized_file_id:
        return
    try:
        _drive_json_request(
            f"{GOOGLE_DRIVE_BASE_URL}/files/{normalized_file_id}?{urlencode({'supportsAllDrives': 'true'})}",
            access_token,
            method="DELETE",
        )
    except HTTPError as exc:
        # 404: already gone. 403: not created by this app under the drive.file
        # scope, so it cannot be deleted. Either way, skip it rather than aborting
        # the surrounding cleanup/mirror so the new copy can still be saved.
        if exc.code not in (403, 404):
            raise


def ensure_google_drive_app_revisions_storage() -> dict:
    ensure_google_sync_records()
    account = _load_google_account()
    if str(account.get("account_status") or "") != "connected":
        raise RuntimeError(SYNC_NOTICE_MESSAGES["drive_not_connected"])
    if not _account_has_drive_scope(account):
        raise RuntimeError(SYNC_NOTICE_MESSAGES["drive_scope_missing"])

    access_token, account_email = _get_valid_access_token()
    state = _load_google_sync_state()

    root_folder = _drive_get_file(access_token, state.get("drive_root_folder_id") or "")
    if root_folder is None:
        root_folder = _drive_find_best_root_folder(access_token)
    if root_folder is None:
        root_folder = _drive_create_folder(
            access_token,
            name=GOOGLE_DRIVE_ROOT_FOLDER_NAME,
            parent_id="root",
        )

    root_folder_id = _clean(root_folder.get("id"))
    app_revisions_folder = _drive_get_file(access_token, state.get("drive_app_revisions_folder_id") or "")
    if app_revisions_folder is None:
        app_revisions_folder = _drive_find_child(
            access_token,
            parent_id=root_folder_id,
            name=GOOGLE_DRIVE_APP_REVISIONS_FOLDER_NAME,
            mime_type="application/vnd.google-apps.folder",
        )
    if app_revisions_folder is None:
        app_revisions_folder = _drive_create_folder(
            access_token,
            name=GOOGLE_DRIVE_APP_REVISIONS_FOLDER_NAME,
            parent_id=root_folder_id,
        )

    app_revisions_folder_id = _clean(app_revisions_folder.get("id"))
    app_revisions_file = _drive_get_file(access_token, state.get("drive_app_revisions_file_id") or "")
    if app_revisions_file is None:
        app_revisions_file = _drive_find_child(
            access_token,
            parent_id=app_revisions_folder_id,
            name=GOOGLE_DRIVE_APP_REVISIONS_FILE_NAME,
            mime_type="application/json",
        )
    if app_revisions_file is None:
        app_revisions_file = _drive_create_json_file(
            access_token,
            name=GOOGLE_DRIVE_APP_REVISIONS_FILE_NAME,
            parent_id=app_revisions_folder_id,
        )

    app_revisions_file_id = _clean(app_revisions_file.get("id"))
    _store_drive_state(
        root_folder_id=root_folder_id,
        app_revisions_folder_id=app_revisions_folder_id,
        app_revisions_file_id=app_revisions_file_id,
        last_sync_error="",
    )
    return {
        "account_email": account_email,
        "root_folder_id": root_folder_id,
        "app_revisions_folder_id": app_revisions_folder_id,
        "app_revisions_file_id": app_revisions_file_id,
    }


def ensure_google_drive_root_storage(*, access_token: str | None = None) -> dict:
    token = str(access_token or "").strip()
    if token:
        # Borrowed token (owner Drive for Pending Edits). Find the folder on that
        # Drive and do not write its IDs into the signed-in user's sqlite.
        root_folder = _drive_find_best_root_folder(token)
        if root_folder is None:
            root_folder = _drive_create_folder(
                token,
                name=GOOGLE_DRIVE_ROOT_FOLDER_NAME,
                parent_id="root",
            )
        return {
            "account_email": "",
            "root_folder_id": _clean(root_folder.get("id")),
        }

    ensure_google_sync_records()
    account = _load_google_account()
    if str(account.get("account_status") or "") != "connected":
        raise RuntimeError(SYNC_NOTICE_MESSAGES["drive_not_connected"])
    if not _account_has_drive_scope(account):
        raise RuntimeError(SYNC_NOTICE_MESSAGES["drive_scope_missing"])

    current_token, account_email = _get_valid_access_token()
    state = _load_google_sync_state()

    root_folder = _drive_get_file(current_token, state.get("drive_root_folder_id") or "")
    if root_folder is None:
        root_folder = _drive_find_best_root_folder(current_token)
    if root_folder is None:
        root_folder = _drive_create_folder(
            current_token,
            name=GOOGLE_DRIVE_ROOT_FOLDER_NAME,
            parent_id="root",
        )
    root_folder_id = _clean(root_folder.get("id"))
    _store_drive_state(root_folder_id=root_folder_id, last_sync_error="")
    return {
        "account_email": account_email,
        "root_folder_id": root_folder_id,
    }


def _ensure_shared_drive_storage(access_token: str | None = None, *, contact_only: bool = False) -> dict:
    """Resolve shared Drive folder/file IDs with one children listing (not N find calls)."""
    token = access_token or _get_valid_access_token()[0]
    storage = ensure_google_drive_root_storage(access_token=access_token)
    cache_key = _clean(storage.get("root_folder_id"))
    if cache_key:
        if contact_only:
            cached = _CONTACT_ONLY_SHARED_DRIVE_STORAGE_CACHE.get(cache_key)
            if cached:
                return {**cached, **storage}
        else:
            cached = _SHARED_DRIVE_STORAGE_CACHE.get(cache_key)
            if cached:
                return {**cached, **storage}

    shared_folder = _drive_find_child(
        token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if shared_folder is None:
        shared_folder = _drive_create_folder(
            token,
            name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
            parent_id=storage["root_folder_id"],
        )
    shared_folder_id = _clean(shared_folder.get("id"))
    # One list replaces ~10 sequential name queries that previously dominated sign-in time.
    shared_children = _drive_children_by_name(token, parent_id=shared_folder_id)

    def ensure_folder(name: str) -> str:
        folder_item = shared_children.get(name)
        if folder_item is None or _clean(folder_item.get("mimeType")) != "application/vnd.google-apps.folder":
            folder_item = _drive_create_folder(token, name=name, parent_id=shared_folder_id)
            shared_children[name] = folder_item
        return _clean(folder_item.get("id"))

    def ensure_file(name: str) -> str:
        file_item = shared_children.get(name)
        if file_item is None or _clean(file_item.get("mimeType")) != "application/json":
            file_item = _drive_create_json_file(token, name=name, parent_id=shared_folder_id)
            _drive_update_json_file_content(token, _clean(file_item.get("id")), {})
            shared_children[name] = file_item
        return _clean(file_item.get("id"))

    contact_records_folder_id = ensure_folder(GOOGLE_DRIVE_SHARED_CONTACT_RECORDS_FOLDER_NAME)
    contact_photos_folder_id = ensure_folder(GOOGLE_DRIVE_SHARED_CONTACT_PHOTOS_FOLDER_NAME)
    contact_result = {
        **storage,
        "shared_folder_id": shared_folder_id,
        "contact_records_folder_id": contact_records_folder_id,
        "contact_photos_folder_id": contact_photos_folder_id,
        "contacts_current_file_id": ensure_file(GOOGLE_DRIVE_CONTACTS_CURRENT_FILE_NAME),
        "shared_sync_state_file_id": ensure_file(GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME),
    }
    if cache_key:
        _CONTACT_ONLY_SHARED_DRIVE_STORAGE_CACHE[cache_key] = dict(contact_result)
    if contact_only:
        return contact_result

    result = {
        **contact_result,
        "editor_lock_file_id": ensure_file(GOOGLE_DRIVE_EDITOR_LOCK_FILE_NAME),
        "contacts_full_file_id": ensure_file(GOOGLE_DRIVE_CONTACTS_FULL_FILE_NAME),
        "meetingdata_current_file_id": ensure_file(GOOGLE_DRIVE_MEETINGDATA_CURRENT_FILE_NAME),
        "field_list_current_file_id": ensure_file(GOOGLE_DRIVE_FIELD_LIST_CURRENT_FILE_NAME),
        "settings_current_file_id": ensure_file(GOOGLE_DRIVE_SETTINGS_CURRENT_FILE_NAME),
        "book_layouts_current_file_id": ensure_file(GOOGLE_DRIVE_BOOK_LAYOUTS_CURRENT_FILE_NAME),
        "changes_list_current_file_id": ensure_file(GOOGLE_DRIVE_CHANGES_LIST_CURRENT_FILE_NAME),
    }
    if cache_key:
        _SHARED_DRIVE_STORAGE_CACHE[cache_key] = dict(result)
    return result


def _ensure_shared_settings_drive_storage(access_token: str | None = None) -> dict:
    token = access_token or _get_valid_access_token()[0]
    storage = ensure_google_drive_root_storage(access_token=access_token)
    shared_folder = _drive_find_child(
        token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if shared_folder is None:
        shared_folder = _drive_create_folder(
            token,
            name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
            parent_id=storage["root_folder_id"],
        )
    shared_folder_id = _clean(shared_folder.get("id"))
    shared_children = _drive_children_by_name(token, parent_id=shared_folder_id)

    def ensure_file(name: str) -> str:
        file_item = shared_children.get(name)
        if file_item is None or _clean(file_item.get("mimeType")) != "application/json":
            file_item = _drive_create_json_file(token, name=name, parent_id=shared_folder_id)
            _drive_update_json_file_content(token, _clean(file_item.get("id")), {})
            shared_children[name] = file_item
        return _clean(file_item.get("id"))

    return {
        **storage,
        "shared_folder_id": shared_folder_id,
        "settings_current_file_id": ensure_file(GOOGLE_DRIVE_SETTINGS_CURRENT_FILE_NAME),
        "shared_sync_state_file_id": ensure_file(GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME),
    }


def _ensure_editor_lock_drive_storage(access_token: str | None = None) -> dict:
    token = access_token or _get_valid_access_token()[0]
    storage = ensure_google_drive_root_storage(access_token=access_token)
    shared_folder = _drive_find_child(
        token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if shared_folder is None:
        shared_folder = _drive_create_folder(
            token,
            name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
            parent_id=storage["root_folder_id"],
        )
    shared_folder_id = _clean(shared_folder.get("id"))
    shared_children = _drive_children_by_name(token, parent_id=shared_folder_id)

    def ensure_file(name: str) -> str:
        file_item = shared_children.get(name)
        if file_item is None or _clean(file_item.get("mimeType")) != "application/json":
            file_item = _drive_create_json_file(token, name=name, parent_id=shared_folder_id)
            _drive_update_json_file_content(token, _clean(file_item.get("id")), {})
            shared_children[name] = file_item
        return _clean(file_item.get("id"))

    return {
        **storage,
        "shared_folder_id": shared_folder_id,
        "editor_lock_file_id": ensure_file(GOOGLE_DRIVE_EDITOR_LOCK_FILE_NAME),
    }


def _ensure_shared_book_layouts_drive_storage(access_token: str | None = None) -> dict:
    token = access_token or _get_valid_access_token()[0]
    storage = ensure_google_drive_root_storage(access_token=access_token)
    shared_folder = _drive_find_child(
        token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if shared_folder is None:
        shared_folder = _drive_create_folder(
            token,
            name=GOOGLE_DRIVE_SHARED_FOLDER_NAME,
            parent_id=storage["root_folder_id"],
        )
    shared_folder_id = _clean(shared_folder.get("id"))
    shared_children = _drive_children_by_name(token, parent_id=shared_folder_id)

    def ensure_file(name: str) -> str:
        file_item = shared_children.get(name)
        if file_item is None or _clean(file_item.get("mimeType")) != "application/json":
            file_item = _drive_create_json_file(token, name=name, parent_id=shared_folder_id)
            _drive_update_json_file_content(token, _clean(file_item.get("id")), {})
            shared_children[name] = file_item
        return _clean(file_item.get("id"))

    return {
        **storage,
        "shared_folder_id": shared_folder_id,
        "book_layouts_current_file_id": ensure_file(GOOGLE_DRIVE_BOOK_LAYOUTS_CURRENT_FILE_NAME),
        "shared_sync_state_file_id": ensure_file(GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME),
    }


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_editor_lock(access_token: str, file_id: str) -> dict:
    try:
        payload = _drive_load_json_file_content(access_token, file_id)
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    return payload if isinstance(payload, dict) else {}


def _load_shared_sync_state(access_token: str, storage: dict) -> dict:
    try:
        payload = _drive_load_json_file_content(access_token, storage["shared_sync_state_file_id"])
    except HTTPError as exc:
        if exc.code == 404:
            return _normalize_shared_sync_state({})
        raise
    return _normalize_shared_sync_state(payload)


def _write_shared_sync_state(access_token: str, storage: dict, sync_state: dict) -> dict:
    payload = _shared_sync_state_payload(
        settings_sync_revision=sync_state.get("settings_sync_revision"),
        contacts_sync_revision=sync_state.get("contacts_sync_revision"),
        meetingdata_sync_revision=sync_state.get("meetingdata_sync_revision"),
        field_list_sync_revision=sync_state.get("field_list_sync_revision"),
        changes_list_sync_revision=sync_state.get("changes_list_sync_revision"),
        book_layouts_sync_revision=sync_state.get("book_layouts_sync_revision"),
    )
    _drive_update_json_file_content(access_token, storage["shared_sync_state_file_id"], payload)
    return payload


def _next_dataset_sync_revision(
    access_token: str,
    storage: dict,
    *,
    revision_key: str,
    local_revision: int,
    remote_sync_state: dict | None = None,
) -> tuple[int, dict]:
    sync_state = remote_sync_state or _load_shared_sync_state(access_token, storage)
    remote_revision = _normalize_sync_revision(sync_state.get(revision_key))
    return max(_normalize_sync_revision(local_revision), remote_revision) + 1, sync_state


def _clean_contact_photo_path(photo_url: str | None) -> str:
    photo_path = _clean(str(photo_url or ""))
    return photo_path if photo_path.startswith(CONTACT_PHOTO_STATIC_PREFIX) else ""


def _contact_photo_local_path(photo_url: str | None) -> Path | None:
    normalized_photo = _clean_contact_photo_path(photo_url)
    if not normalized_photo:
        return None
    filename = Path(normalized_photo).name
    if not filename:
        return None
    return get_contact_photo_storage_dir() / filename


def _local_contact_photo_bytes(photo_url: str | None) -> bytes:
    local_path = _contact_photo_local_path(photo_url)
    if local_path is None or not local_path.is_file():
        return b""
    return local_path.read_bytes()


def _contact_photo_drive_filename(contact_id: int, photo_path: str) -> str:
    suffix = Path(photo_path).suffix.lower() or ".jpg"
    return f"contact_{contact_id}_photo{suffix}"


def _contact_record_drive_filename(contact_id: int) -> str:
    return f"contact_{contact_id}.json"


def mark_contact_for_drive_export(contact_id: int) -> None:
    try:
        from app.services.field_list_service import invalidate_field_list_print_payload_cache

        invalidate_field_list_print_payload_cache()
    except Exception:
        pass
    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE contacts
            SET
              shared_drive_needs_export = 1
            WHERE id = ?
            """,
            (int(contact_id),),
        )
        _mark_contacts_signout_backup_needed(conn)
        conn.commit()


def _normalize_exported_contact_row(contact_row: dict) -> dict:
    payload = {
        key: value
        for key, value in dict(contact_row).items()
        if key not in {"relationships", "phones", "emails", "addresses", "custom_fields"}
    }
    payload["photo_needs_export"] = 0
    payload["shared_drive_needs_export"] = 0
    payload["shared_drive_bootstrapped"] = 1
    return payload


def _load_contact_edit_log_rows(conn, contact_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM contact_edit_log_entries
        WHERE contact_id = ?
        ORDER BY batch_created_at DESC, batch_row_index ASC, id ASC
        """,
        (contact_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _build_shared_contact_record_payload(conn, contact_id: int) -> dict | None:
    contact = _load_contact_for_sync(conn, contact_id)
    if not contact:
        return None
    edit_logs = _load_contact_edit_log_rows(conn, contact_id)
    payload = {
        "format": "contactsfreeshare.shared_contact.v1",
        "exported_at": _iso_now(),
        "contact": _normalize_exported_contact_row(contact),
        "relationships": [dict(item) for item in contact.get("relationships") or []],
        "phones": [dict(item) for item in contact.get("phones") or []],
        "emails": [dict(item) for item in contact.get("emails") or []],
        "addresses": [dict(item) for item in contact.get("addresses") or []],
        "custom_fields": [dict(item) for item in contact.get("custom_fields") or []],
        "contact_edit_log_entries": edit_logs,
    }
    payload["content_digest"] = _shared_contact_payload_digest(payload, include_edit_logs=False)
    return payload


_SHARED_CONTACT_COMPARE_IGNORE_CONTACT_FIELDS = frozenset(
    {
        "shared_drive_revision",
        "shared_drive_needs_export",
        "photo_needs_export",
    }
)
_SHARED_CONTACT_COMPARE_TOP_LEVEL_KEYS = (
    "format",
    "contact",
    "relationships",
    "phones",
    "emails",
    "addresses",
    "custom_fields",
)
_SHARED_CONTACT_COMPARE_IGNORE_TOP_LEVEL_FIELDS = frozenset({"content_digest", "exported_at"})


def _shared_contact_comparable_payload(
    payload: dict,
    *,
    include_edit_logs: bool = True,
) -> dict:
    if not isinstance(payload, dict):
        return {}
    comparable_keys = _SHARED_CONTACT_COMPARE_TOP_LEVEL_KEYS
    if include_edit_logs:
        comparable_keys = (*comparable_keys, "contact_edit_log_entries")

    def _normalize_compare_value(key: str, value: object) -> object:
        if key == "contact" and isinstance(value, dict):
            return {
                field: field_value
                for field, field_value in value.items()
                if field not in _SHARED_CONTACT_COMPARE_IGNORE_CONTACT_FIELDS
            }
        return value

    return {
        key: _normalize_compare_value(key, payload.get(key))
        for key in comparable_keys
        if key not in _SHARED_CONTACT_COMPARE_IGNORE_TOP_LEVEL_FIELDS
    }


def _shared_contact_payload_digest(payload: dict, *, include_edit_logs: bool = False) -> str:
    comparable = _shared_contact_comparable_payload(payload, include_edit_logs=include_edit_logs)
    encoded = json.dumps(comparable, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _shared_contact_payload_matches(
    local_payload: dict,
    remote_payload: dict,
    *,
    include_edit_logs: bool = True,
) -> bool:
    if not isinstance(local_payload, dict) or not isinstance(remote_payload, dict):
        return False
    local_comparable = _shared_contact_comparable_payload(local_payload, include_edit_logs=include_edit_logs)
    remote_comparable = _shared_contact_comparable_payload(remote_payload, include_edit_logs=include_edit_logs)
    return json.dumps(local_comparable, sort_keys=True, default=str) == json.dumps(
        remote_comparable,
        sort_keys=True,
        default=str,
    )


def _load_shared_contact_record_payload_from_drive(
    access_token: str,
    storage: dict,
    contact_id: int,
    *,
    manifest_by_id: dict[int, dict] | None = None,
    contact_record_files: dict[str, dict] | None = None,
    file_cache: dict[str, dict] | None = None,
) -> dict | None:
    contact_id = int(contact_id)
    if contact_id <= 0:
        return None
    manifest_by_id = manifest_by_id or {}
    contact_record_files = contact_record_files or {}
    file_cache = file_cache if file_cache is not None else {}
    filename = _contact_record_drive_filename(contact_id)
    file_item = contact_record_files.get(filename)
    manifest_item = manifest_by_id.get(contact_id)
    if file_item is None and manifest_item is not None:
        file_item = _drive_find_child(
            access_token,
            parent_id=storage["contact_records_folder_id"],
            name=_clean(manifest_item.get("file_name")) or filename,
            mime_type="application/json",
        )
        if file_item is not None:
            contact_record_files[filename] = file_item
    if file_item is None:
        file_item = _drive_find_child(
            access_token,
            parent_id=storage["contact_records_folder_id"],
            name=filename,
            mime_type="application/json",
        )
        if file_item is not None:
            contact_record_files[filename] = file_item
    if file_item is None:
        return None
    drive_file_id = _clean(file_item.get("id"))
    if drive_file_id in file_cache:
        payload = file_cache[drive_file_id]
    else:
        payload = _drive_load_json_file_content(access_token, drive_file_id)
        file_cache[drive_file_id] = payload
    if isinstance(payload, dict) and isinstance(payload.get("contact"), dict):
        return payload
    return None


def _shared_contact_drive_export_needed(
    *,
    bootstrapped: bool,
    local_payload: dict | None,
    remote_payload: dict | None,
) -> bool:
    if not bootstrapped:
        return True
    if local_payload is None:
        return False
    if remote_payload is None:
        return True
    return not _shared_contact_payload_matches(
        local_payload,
        remote_payload,
        include_edit_logs=True,
    )


_SHARED_CONTACT_APP_ONLY_CONTACT_FIELDS = frozenset(
    {
        "mtg_home_elder_flag",
        "meets_in_state",
        "birthday",
        "notes",
        "do_not_print",
        "photo",
        "photo_drive_file_id",
        "photo_sync_revision",
        "photo_needs_export",
    }
)


def _shared_contact_app_only_drive_export_needed(
    *,
    bootstrapped: bool,
    local_payload: dict | None,
    remote_payload: dict | None,
) -> bool:
    if not bootstrapped:
        return True
    if local_payload is None:
        return False
    if remote_payload is None:
        return True
    local_contact = local_payload.get("contact") if isinstance(local_payload, dict) else {}
    remote_contact = remote_payload.get("contact") if isinstance(remote_payload, dict) else {}
    if not isinstance(local_contact, dict):
        local_contact = {}
    if not isinstance(remote_contact, dict):
        remote_contact = {}
    for field_name in _SHARED_CONTACT_APP_ONLY_CONTACT_FIELDS:
        if _clean(local_contact.get(field_name)) != _clean(remote_contact.get(field_name)):
            return True
    if _contact_needs_photo_sync(local_contact, remote_contact):
        return True
    local_logs = local_payload.get("contact_edit_log_entries") or []
    remote_logs = remote_payload.get("contact_edit_log_entries") or []
    return json.dumps(local_logs, sort_keys=True, default=str) != json.dumps(
        remote_logs,
        sort_keys=True,
        default=str,
    )


def _reconcile_shared_contact_drive_export_flags(
    contact_ids: list[int],
    *,
    access_token: str,
    storage: dict,
    manifest_by_id: dict[int, dict] | None = None,
    contact_record_files: dict[str, dict] | None = None,
    file_cache: dict[str, dict] | None = None,
    compare_app_fields_only: bool = False,
) -> int:
    normalized_ids = sorted({int(contact_id) for contact_id in contact_ids if int(contact_id) > 0})
    if not normalized_ids:
        with get_connection() as conn:
            _sync_manifest_drive_export_flag(conn)
            conn.commit()
        return 0

    bootstrapped: dict[int, bool] = {}
    local_payloads: dict[int, dict | None] = {}
    with get_connection() as conn:
        for contact_id in normalized_ids:
            row = conn.execute(
                "SELECT shared_drive_bootstrapped FROM contacts WHERE id = ?",
                (contact_id,),
            ).fetchone()
            if not row:
                continue
            is_bootstrapped = bool(int(row["shared_drive_bootstrapped"] or 0))
            bootstrapped[contact_id] = is_bootstrapped
            local_payloads[contact_id] = (
                _build_shared_contact_record_payload(conn, contact_id)
                if is_bootstrapped
                else None
            )

    if manifest_by_id is None:
        manifest_payload = _drive_load_json_file_content(access_token, storage["contacts_current_file_id"])
        manifest_by_id = {
            int(item.get("id") or 0): item
            for item in _shared_contact_manifest_entries(manifest_payload)
            if int(item.get("id") or 0) > 0
        }
    if contact_record_files is None:
        contact_record_files = _drive_children_by_name(
            access_token,
            parent_id=storage["contact_records_folder_id"],
        )
    file_cache = file_cache if file_cache is not None else {}

    decisions: dict[int, bool] = {}
    still_pending = 0
    for contact_id in normalized_ids:
        if contact_id not in bootstrapped:
            continue
        remote_payload = None
        if bootstrapped[contact_id]:
            remote_payload = _load_shared_contact_record_payload_from_drive(
                access_token,
                storage,
                contact_id,
                manifest_by_id=manifest_by_id,
                contact_record_files=contact_record_files,
                file_cache=file_cache,
            )
        export_check = (
            _shared_contact_app_only_drive_export_needed
            if compare_app_fields_only
            else _shared_contact_drive_export_needed
        )
        needed = export_check(
            bootstrapped=bootstrapped[contact_id],
            local_payload=local_payloads.get(contact_id),
            remote_payload=remote_payload,
        )
        decisions[contact_id] = needed
        if needed:
            still_pending += 1

    with get_connection() as conn:
        for contact_id, needed in decisions.items():
            conn.execute(
                "UPDATE contacts SET shared_drive_needs_export = ? WHERE id = ?",
                (1 if needed else 0, contact_id),
            )
        _sync_manifest_drive_export_flag(conn)
        conn.commit()
    return still_pending


def _sync_manifest_drive_export_flag(conn) -> None:
    if _count_pending_shared_contact_exports(conn) > 0:
        conn.execute(
            """
            UPDATE google_sync_state
            SET contacts_manifest_needs_drive_export = 1, updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        return
    conn.execute(
        """
        UPDATE google_sync_state
        SET contacts_manifest_needs_drive_export = 0, updated_at = ?
        WHERE id = 1
        """,
        (_now_text(),),
    )


def _manifest_contact_row_matches(contact_row: dict, manifest_row: dict | None) -> bool:
    if not isinstance(manifest_row, dict):
        return False
    contact_id = int(contact_row.get("id") or 0)
    if contact_id <= 0:
        return False
    return (
        int(manifest_row.get("id") or 0) == contact_id
        and int(manifest_row.get("shared_drive_revision") or 0) == int(contact_row.get("shared_drive_revision") or 0)
        and _clean(manifest_row.get("status")) == _clean(contact_row.get("status"))
        and _clean(manifest_row.get("family_name")) == _clean(contact_row.get("family_name"))
        and _clean(manifest_row.get("given_name")) == _clean(contact_row.get("given_name"))
        and _clean(manifest_row.get("file_name")) == _contact_record_drive_filename(contact_id)
    )


def _shared_assignment_options_payload(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM contact_assignment_options
        ORDER BY kind, value, id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _manifest_contact_entry_from_row(row, *, conn=None, include_content_digest: bool = False) -> dict:
    contact_id = int(row["id"])
    entry = {
        "id": contact_id,
        "shared_drive_revision": int(row["shared_drive_revision"] or 0),
        "status": _clean(row["status"]),
        "family_name": _clean(row["family_name"]),
        "given_name": _clean(row["given_name"]),
        "file_name": _contact_record_drive_filename(contact_id),
    }
    if include_content_digest and conn is not None:
        payload = _build_shared_contact_record_payload(conn, contact_id)
        if payload:
            entry["content_digest"] = _clean(payload.get("content_digest")) or _shared_contact_payload_digest(
                payload,
                include_edit_logs=False,
            )
    return entry


def _patch_shared_contacts_manifest(existing_manifest: dict, conn, contact_ids: list[int]) -> dict:
    patched = dict(existing_manifest)
    patched["exported_at"] = _iso_now()
    contacts_by_id = {
        int(item.get("id") or 0): dict(item)
        for item in (patched.get("contacts") or [])
        if isinstance(item, dict) and int(item.get("id") or 0) > 0
    }
    if contact_ids:
        placeholders = ",".join("?" for _ in contact_ids)
        rows = conn.execute(
            f"""
            SELECT id, shared_drive_revision, status, family_name, given_name
            FROM contacts
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            tuple(contact_ids),
        ).fetchall()
        for row in rows:
            contacts_by_id[int(row["id"])] = _manifest_contact_entry_from_row(
                row,
                conn=conn,
                include_content_digest=True,
            )
    patched["contacts"] = [contacts_by_id[contact_id] for contact_id in sorted(contacts_by_id)]
    return patched


def _shared_contacts_manifest_payload(conn, *, include_content_digest: bool = False) -> dict:
    rows = conn.execute(
        """
        SELECT id, shared_drive_revision, status, family_name, given_name
        FROM contacts
        ORDER BY id
        """
    ).fetchall()
    return {
        "format": "contactsfreeshare.shared_contacts.v2",
        "exported_at": _iso_now(),
        "assignment_options": _shared_assignment_options_payload(conn),
        "contacts": [
            _manifest_contact_entry_from_row(
                row,
                conn=conn if include_content_digest else None,
                include_content_digest=include_content_digest,
            )
            for row in rows
        ],
    }


def _drive_export_payload_already_on_drive(
    conn,
    contact_id: int,
    payload: dict,
    manifest_item: dict | None,
) -> bool:
    """Return True when Drive already has this contact payload; clears stale export flags."""
    if not _manifest_contact_row_matches(payload.get("contact") or {}, manifest_item):
        return False
    manifest_digest = _clean((manifest_item or {}).get("content_digest"))
    if manifest_digest:
        local_digest = _clean(payload.get("content_digest")) or _shared_contact_payload_digest(
            payload,
            include_edit_logs=False,
        )
        return local_digest == manifest_digest
    return False


def _build_patched_contacts_manifest_for_drive_export(
    access_token: str,
    storage: dict,
    conn,
    existing_manifest: dict,
    *,
    changed_contact_ids: list[int],
    uploaded_contact_ids: list[int],
    skip_manifest_read: bool,
) -> dict:
    manifest_source = existing_manifest if isinstance(existing_manifest, dict) else {}
    if skip_manifest_read and not _shared_contact_manifest_entries(manifest_source):
        loaded = _drive_load_json_file_content(access_token, storage["contacts_current_file_id"])
        if isinstance(loaded, dict):
            manifest_source = loaded
    if (
        isinstance(manifest_source, dict)
        and str(manifest_source.get("format") or "").strip() == "contactsfreeshare.shared_contacts.v2"
        and _shared_contact_manifest_entries(manifest_source)
    ):
        patch_ids = sorted(
            {
                int(contact_id)
                for contact_id in (*uploaded_contact_ids, *changed_contact_ids)
                if int(contact_id) > 0
            }
        )
        return _patch_shared_contacts_manifest(manifest_source, conn, patch_ids)
    return _shared_contacts_manifest_payload(conn)


def _write_contacts_full_snapshot_to_google_drive(access_token: str, storage: dict) -> None:
    from app.services.backup_service import build_contacts_shared_payload

    _drive_update_json_file_content(
        access_token,
        storage["contacts_full_file_id"],
        build_contacts_shared_payload(),
    )


def _insert_row_dict(conn, table_name: str, row: dict) -> None:
    if not isinstance(row, dict) or not row:
        return
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(row[column] for column in columns),
    )


def _insert_imported_row_dict(conn, table_name: str, row: dict) -> None:
    if not isinstance(row, dict) or not row:
        return
    imported_row = dict(row)
    imported_row.pop("id", None)
    _insert_row_dict(conn, table_name, imported_row)


def _replace_contact_assignment_options(conn, rows: list[dict]) -> None:
    conn.execute("DELETE FROM contact_assignment_options")
    for row in rows:
        _insert_row_dict(conn, "contact_assignment_options", dict(row))


def _delete_local_contact_record(conn, contact_id: int) -> None:
    contact_id = int(contact_id)
    conn.execute("DELETE FROM contact_edit_log_entries WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM relationships WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM phones WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM addresses WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM emails WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM custom_fields WHERE contact_id = ?", (contact_id,))
    try:
        conn.execute("DELETE FROM google_sync_queue WHERE contact_id = ?", (contact_id,))
    except Exception:
        # Unit-test DBs may omit the upload queue table.
        pass
    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))


def _remap_shared_contact_payload_contact_id(payload: dict, target_id: int) -> None:
    """Point a Drive contact payload and its child rows at a local keeper contact id."""
    target_id = int(target_id)
    contact_row = payload.get("contact")
    if isinstance(contact_row, dict):
        contact_row["id"] = target_id
    for table_name in (
        "relationships",
        "phones",
        "emails",
        "addresses",
        "custom_fields",
        "contact_edit_log_entries",
    ):
        for row in payload.get(table_name) or []:
            if isinstance(row, dict):
                row["contact_id"] = target_id


def _collapse_contacts_sharing_google_id(conn, google_contact_id: str, keep_contact_id: int) -> None:
    """Delete extra local rows that share one Google person id (keeps keep_contact_id)."""
    gid = _clean(google_contact_id)
    keep_contact_id = int(keep_contact_id)
    if not gid or keep_contact_id <= 0:
        return
    extras = conn.execute(
        """
        SELECT id
        FROM contacts
        WHERE google_contact_id = ? AND id != ?
        ORDER BY id
        """,
        (gid, keep_contact_id),
    ).fetchall()
    for row in extras:
        _delete_local_contact_record(conn, int(row["id"]))


def _normalize_phone_identity_key(value: object) -> str:
    digits = "".join(character for character in _clean(value) if character.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return digits if len(digits) >= 7 else ""


def _normalize_email_identity_key(value: object) -> str:
    return _clean(value).casefold()


def _contact_name_identity_key(given_name: object, family_name: object) -> tuple[str, str]:
    return (_clean(given_name).casefold(), _clean(family_name).casefold())


def _phone_identity_keys(values) -> set[str]:
    keys: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("phone_value") or value.get("phone_number") or value.get("value")
        key = _normalize_phone_identity_key(value)
        if key:
            keys.add(key)
    return keys


def _email_identity_keys(values) -> set[str]:
    keys: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("email_value") or value.get("email_address") or value.get("value")
        key = _normalize_email_identity_key(value)
        if key:
            keys.add(key)
    return keys


def _load_contact_identity_values(conn, contact_id: int) -> tuple[set[str], set[str]]:
    phone_rows = conn.execute("SELECT * FROM phones WHERE contact_id = ?", (int(contact_id),)).fetchall()
    email_rows = conn.execute("SELECT * FROM emails WHERE contact_id = ?", (int(contact_id),)).fetchall()
    return (
        _phone_identity_keys(dict(row) for row in phone_rows),
        _email_identity_keys(dict(row) for row in email_rows),
    )


def _identity_keys_overlap(
    left_phones: set[str],
    left_emails: set[str],
    right_phones: set[str],
    right_emails: set[str],
) -> bool:
    return bool(left_phones & right_phones) or bool(left_emails & right_emails)


def _find_local_contact_id_by_identity(
    conn,
    given_name,
    family_name,
    phones=None,
    emails=None,
    *,
    exclude_ids: set[int] | None = None,
) -> int | None:
    """Return a local contact that matches name plus a shared phone or email."""
    given_key, family_key = _contact_name_identity_key(given_name, family_name)
    if not given_key or not family_key:
        return None
    incoming_phones = _phone_identity_keys(phones)
    incoming_emails = _email_identity_keys(emails)
    if not incoming_phones and not incoming_emails:
        return None
    excluded = {int(item) for item in (exclude_ids or set()) if int(item or 0) > 0}
    candidates = [dict(row) for row in conn.execute("SELECT * FROM contacts").fetchall()]
    candidates.sort(
        key=lambda row: (
            str(row.get("last_updated") or ""),
            int(row.get("shared_drive_revision") or 0),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )
    for row in candidates:
        contact_id = int(row.get("id") or 0)
        if contact_id <= 0 or contact_id in excluded:
            continue
        if _contact_name_identity_key(row.get("given_name"), row.get("family_name")) != (given_key, family_key):
            continue
        local_phones, local_emails = _load_contact_identity_values(conn, contact_id)
        if _identity_keys_overlap(incoming_phones, incoming_emails, local_phones, local_emails):
            return contact_id
    return None


def collapse_stale_identity_duplicate_contacts(conn) -> list[int]:
    """Delete extra local rows that share a name plus phone or email with a newer keeper."""
    rows = [dict(row) for row in conn.execute("SELECT * FROM contacts").fetchall()]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        name_key = _contact_name_identity_key(row.get("given_name"), row.get("family_name"))
        if not name_key[0] or not name_key[1]:
            continue
        groups.setdefault(name_key, []).append(row)

    deleted_ids: list[int] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        identity_by_id = {
            int(row["id"]): _load_contact_identity_values(conn, int(row["id"]))
            for row in group
        }
        parent = {int(row["id"]): int(row["id"]) for row in group}

        def _root(contact_id: int) -> int:
            while parent[contact_id] != contact_id:
                parent[contact_id] = parent[parent[contact_id]]
                contact_id = parent[contact_id]
            return contact_id

        for left_index, left in enumerate(group):
            left_id = int(left["id"])
            left_phones, left_emails = identity_by_id[left_id]
            if not left_phones and not left_emails:
                continue
            for right in group[left_index + 1 :]:
                right_id = int(right["id"])
                right_phones, right_emails = identity_by_id[right_id]
                if _identity_keys_overlap(left_phones, left_emails, right_phones, right_emails):
                    left_root = _root(left_id)
                    right_root = _root(right_id)
                    if left_root != right_root:
                        parent[right_root] = left_root

        clusters: dict[int, list[dict]] = {}
        for row in group:
            clusters.setdefault(_root(int(row["id"])), []).append(row)
        for cluster in clusters.values():
            if len(cluster) < 2:
                continue
            keeper = max(
                cluster,
                key=lambda row: (
                    str(row.get("last_updated") or ""),
                    int(row.get("shared_drive_revision") or 0),
                    int(row.get("id") or 0),
                ),
            )
            keeper_id = int(keeper["id"])
            for row in cluster:
                extra_id = int(row["id"])
                if extra_id == keeper_id:
                    continue
                _delete_local_contact_record(conn, extra_id)
                deleted_ids.append(extra_id)
    return deleted_ids


def _prune_local_contacts_missing_google_ids(conn, live_google_ids: set[str]) -> list[int]:
    """Delete local contacts whose Google person id is no longer in a full Google list."""
    live_ids = {_clean(item) for item in live_google_ids if _clean(item)}
    if not live_ids:
        return []
    deleted_ids: list[int] = []
    rows = conn.execute(
        """
        SELECT id, google_contact_id
        FROM contacts
        WHERE COALESCE(google_contact_id, '') != ''
        """
    ).fetchall()
    for row in rows:
        google_contact_id = _clean(row["google_contact_id"])
        if google_contact_id in live_ids:
            continue
        contact_id = int(row["id"])
        _delete_local_contact_record(conn, contact_id)
        deleted_ids.append(contact_id)
    return deleted_ids


def _sync_shared_contact_revision_only(conn, contact_id: int, remote_revision: int) -> None:
    conn.execute(
        """
        UPDATE contacts
        SET
          shared_drive_revision = ?,
          shared_drive_needs_export = 0
        WHERE id = ?
        """,
        (int(remote_revision), int(contact_id)),
    )


def _shared_contact_remote_is_ahead(local_revision: int | None, remote_revision: int | None) -> bool:
    """True only when Drive has a newer shared-contact revision than the local app."""
    return int(remote_revision or 0) > int(local_revision or 0)


def _peek_shared_contact_import_action(
    conn,
    contact_id: int,
    remote_revision: int,
    manifest_item: dict | None = None,
) -> str | None:
    """Return import or skip without a Drive payload when safe; else None to download.

    Manifest field digests omit edit logs, so a digest match is not enough to skip
    the download. Other Editors' Edit List rows live in those logs.
    """
    del manifest_item
    contact_id = int(contact_id)
    remote_revision = int(remote_revision or 0)
    row = conn.execute(
        "SELECT id, shared_drive_revision FROM contacts WHERE id = ?",
        (contact_id,),
    ).fetchone()
    if not row:
        return "import"
    local_revision = int(row["shared_drive_revision"] or 0)
    if not _shared_contact_remote_is_ahead(local_revision, remote_revision):
        return "skip"
    return None


def _resolve_shared_contact_import_action(
    conn,
    contact_id: int,
    remote_revision: int,
    contact_payload: dict,
) -> str:
    """Return import, revision_only, or skip for a Drive contact record."""
    row = conn.execute(
        "SELECT id, shared_drive_revision FROM contacts WHERE id = ?",
        (int(contact_id),),
    ).fetchone()
    local_revision = int(row["shared_drive_revision"] or 0) if row else 0
    remote_revision = int(remote_revision or 0)
    if not _shared_contact_remote_is_ahead(local_revision, remote_revision):
        return "skip"
    if not row:
        return "import"
    local_payload = _build_shared_contact_record_payload(conn, int(contact_id))
    if local_payload and _shared_contact_payload_matches(
        local_payload,
        contact_payload,
        include_edit_logs=True,
    ):
        return "revision_only"
    return "import"


def _merge_shared_contact_record_from_drive(conn, payload: dict) -> int:
    contact_row = payload.get("contact")
    if not isinstance(contact_row, dict) or not contact_row:
        raise ValueError("Shared contact payload is missing the contact row.")
    contact_id = int(contact_row.get("id") or 0)
    if contact_id <= 0:
        raise ValueError("Shared contact payload is missing a valid contact id.")

    normalized_contact_row = dict(contact_row)
    normalized_contact_row["photo_needs_export"] = 0
    normalized_contact_row["shared_drive_needs_export"] = 0
    normalized_contact_row["shared_drive_bootstrapped"] = 1
    google_contact_id = _clean(normalized_contact_row.get("google_contact_id"))

    existing_row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    target_id = contact_id
    if not existing_row and google_contact_id:
        # Historical Drive contact_{id}.json can disagree with local autoincrement ids
        # after a Google re-import. Never insert a second CFS row for the same Google person.
        gid_match = conn.execute(
            """
            SELECT *
            FROM contacts
            WHERE google_contact_id = ?
            ORDER BY shared_drive_revision DESC, id ASC
            LIMIT 1
            """,
            (google_contact_id,),
        ).fetchone()
        if gid_match:
            target_id = int(gid_match["id"])
            existing_row = gid_match
            _remap_shared_contact_payload_contact_id(payload, target_id)
            normalized_contact_row["id"] = target_id

    if not existing_row:
        identity_id = _find_local_contact_id_by_identity(
            conn,
            normalized_contact_row.get("given_name"),
            normalized_contact_row.get("family_name"),
            payload.get("phones") or [],
            payload.get("emails") or [],
        )
        if identity_id:
            target_id = identity_id
            existing_row = conn.execute("SELECT * FROM contacts WHERE id = ?", (target_id,)).fetchone()
            _remap_shared_contact_payload_contact_id(payload, target_id)
            normalized_contact_row["id"] = target_id

    if existing_row:
        existing = dict(existing_row)
        if _clean(existing.get("google_contact_id")) and not _clean(normalized_contact_row.get("google_contact_id")):
            normalized_contact_row["google_contact_id"] = existing["google_contact_id"]
            google_contact_id = _clean(normalized_contact_row.get("google_contact_id"))
            normalized_contact_row["etag"] = existing.get("etag") or normalized_contact_row.get("etag")
            if _clean(existing.get("status")):
                normalized_contact_row["status"] = existing["status"]
        existing_mtg = _clean(existing.get("mtg_home_elder_flag"))
        incoming_mtg = _clean(normalized_contact_row.get("mtg_home_elder_flag"))
        if existing_mtg and not incoming_mtg:
            normalized_contact_row["mtg_home_elder_flag"] = existing_mtg

        columns = [column for column in normalized_contact_row.keys() if column != "id"]
        assignments = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(
            f"UPDATE contacts SET {assignments} WHERE id = ?",
            tuple(normalized_contact_row[column] for column in columns) + (target_id,),
        )

        for table_name in ("relationships", "phones", "emails", "addresses", "custom_fields"):
            conn.execute(f"DELETE FROM {table_name} WHERE contact_id = ?", (target_id,))
            for row in payload.get(table_name) or []:
                child = dict(row)
                child["contact_id"] = target_id
                _insert_imported_row_dict(conn, table_name, child)

        existing_batch_ids = {
            str(batch_row["batch_id"])
            for batch_row in conn.execute(
                """
                SELECT DISTINCT batch_id
                FROM contact_edit_log_entries
                WHERE contact_id = ?
                """,
                (target_id,),
            ).fetchall()
        }
        for row in payload.get("contact_edit_log_entries") or []:
            row_dict = dict(row)
            row_dict["contact_id"] = target_id
            batch_id = str(row_dict.get("batch_id") or "")
            if batch_id and batch_id not in existing_batch_ids:
                _insert_imported_row_dict(conn, "contact_edit_log_entries", row_dict)
        _collapse_contacts_sharing_google_id(conn, google_contact_id, target_id)
        return target_id

    _insert_row_dict(conn, "contacts", normalized_contact_row)
    for table_name in ("relationships", "phones", "emails", "addresses", "custom_fields", "contact_edit_log_entries"):
        for row in payload.get(table_name) or []:
            child = dict(row)
            child["contact_id"] = contact_id
            _insert_imported_row_dict(conn, table_name, child)
    _collapse_contacts_sharing_google_id(conn, google_contact_id, contact_id)
    return contact_id


def _upsert_shared_contact_record(conn, payload: dict) -> int:
    return _merge_shared_contact_record_from_drive(conn, payload)


def _sync_pending_contact_photos_to_google_drive(access_token: str, contact_photos_folder_id: str) -> None:
    get_contact_photo_storage_dir().mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, photo, photo_drive_file_id, photo_needs_export
            FROM contacts
            WHERE photo_needs_export = 1
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            contact = dict(row)
            contact_id = int(contact.get("id") or 0)
            photo_path = _clean_contact_photo_path(contact.get("photo"))
            drive_file_id = _clean(contact.get("photo_drive_file_id"))
            local_path = _contact_photo_local_path(photo_path)

            if not photo_path or local_path is None or not local_path.is_file():
                if drive_file_id:
                    _drive_delete_file_if_exists(access_token, drive_file_id)
                conn.execute(
                    """
                    UPDATE contacts
                    SET
                      photo_drive_file_id = '',
                      photo_needs_export = 0
                    WHERE id = ?
                    """,
                    (contact_id,),
                )
                continue

            filename = _contact_photo_drive_filename(contact_id, photo_path)
            if not drive_file_id:
                existing_file = _drive_find_child(
                    access_token,
                    parent_id=contact_photos_folder_id,
                    name=filename,
                    mime_type=mimetypes.guess_type(filename)[0] or "image/jpeg",
                )
                if existing_file is None:
                    existing_file = _drive_create_file(
                        access_token,
                        name=filename,
                        parent_id=contact_photos_folder_id,
                        mime_type=mimetypes.guess_type(filename)[0] or "image/jpeg",
                    )
                drive_file_id = _clean(existing_file.get("id"))

            _drive_update_file_content(
                access_token,
                drive_file_id,
                content=local_path.read_bytes(),
                mime_type=mimetypes.guess_type(local_path.name)[0] or "application/octet-stream",
            )
            conn.execute(
                """
                UPDATE contacts
                SET
                  photo_drive_file_id = ?,
                  photo_needs_export = 0
                WHERE id = ?
                """,
                (drive_file_id, contact_id),
            )
        conn.commit()


def _cleanup_remote_contact_photos(access_token: str, contact_photos_folder_id: str) -> None:
    with get_connection() as conn:
        referenced_ids = {
            _clean(row["photo_drive_file_id"])
            for row in conn.execute(
                """
                SELECT photo_drive_file_id
                FROM contacts
                WHERE TRIM(COALESCE(photo_drive_file_id, '')) != ''
                """
            ).fetchall()
            if _clean(row["photo_drive_file_id"])
        }
    for file_item in _drive_list_children(access_token, parent_id=contact_photos_folder_id):
        file_id = _clean(file_item.get("id"))
        if file_id and file_id not in referenced_ids:
            _drive_delete_file_if_exists(access_token, file_id)


def _cleanup_local_contact_photos(referenced_photo_paths: set[str]) -> None:
    storage_dir = get_contact_photo_storage_dir()
    if not storage_dir.exists():
        return
    referenced_filenames = {Path(item).name for item in referenced_photo_paths if item}
    for existing_file in storage_dir.iterdir():
        if existing_file.is_file() and existing_file.name not in referenced_filenames:
            existing_file.unlink()


def _import_changed_contact_photos_from_google_drive(
    access_token: str,
    previous_photo_state: dict[int, tuple[str, int, str]],
) -> None:
    get_contact_photo_storage_dir().mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, photo, photo_drive_file_id, photo_sync_revision
                FROM contacts
                ORDER BY id
                """
            ).fetchall()
        ]

    referenced_photo_paths = {
        _clean_contact_photo_path(row.get("photo"))
        for row in rows
        if _clean_contact_photo_path(row.get("photo"))
    }
    for row in rows:
        contact_id = int(row.get("id") or 0)
        photo_path = _clean_contact_photo_path(row.get("photo"))
        drive_file_id = _clean(row.get("photo_drive_file_id"))
        local_path = _contact_photo_local_path(photo_path)
        if not photo_path or not drive_file_id or local_path is None:
            continue
        previous_file_id, previous_revision, previous_photo_path = previous_photo_state.get(contact_id, ("", 0, ""))
        current_revision = int(row.get("photo_sync_revision") or 0)
        if (
            local_path.is_file()
            and drive_file_id == previous_file_id
            and current_revision == previous_revision
            and photo_path == previous_photo_path
        ):
            continue
        local_path.write_bytes(_drive_load_file_content(access_token, drive_file_id))

    _cleanup_local_contact_photos(referenced_photo_paths)


def _can_skip_drive_manifest_read_for_incremental_export(
    *,
    contacts_manifest_needs_drive_export: bool,
    pending_contact_export_count: int,
    pending_photo_export_count: int,
    bootstrap_remaining_count: int,
) -> bool:
    """Skip downloading the Drive contacts manifest when only dirty records need uploading."""
    return (
        pending_contact_export_count > 0
        and pending_photo_export_count <= 0
        and bootstrap_remaining_count <= 0
        and not contacts_manifest_needs_drive_export
    )


def _should_queue_local_ahead_shared_app_fields_on_drive_export(
    *,
    skip_manifest_read: bool,
    bootstrap_needed: bool,
    contacts_manifest_needs_drive_export: bool,
) -> bool:
    """Avoid scanning the whole library during a single-contact incremental upload."""
    if skip_manifest_read:
        return False
    return bootstrap_needed or contacts_manifest_needs_drive_export


def _schedule_share_app_contact_sync(account_email: str, person_ids: list[str]) -> None:
    normalized_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in person_ids
            if str(item or "").strip()
        )
    )
    if not normalized_ids:
        return

    def _run() -> None:
        from app.services.share_sync_service import sync_share_app_contact_changes

        try:
            share_result = sync_share_app_contact_changes(account_email, normalized_ids)
        except Exception as exc:
            share_result = {"ok": False, "reason": _clean(str(exc)) or "share_sync_failed"}
        record_share_sync_result(account_email, normalized_ids, share_result)

    threading.Thread(target=_run, daemon=True).start()


def _manifest_contact_ids(manifest: dict) -> set[int]:
    ids: set[int] = set()
    for item in manifest.get("contacts") or []:
        if not isinstance(item, dict):
            continue
        contact_id = int(item.get("id") or 0)
        if contact_id > 0:
            ids.add(contact_id)
    return ids


def _removed_contact_record_file_names(existing_manifest: dict, manifest_payload: dict) -> list[str]:
    """Return Drive record filenames only for contacts removed from the local manifest."""
    removed_ids = _manifest_contact_ids(existing_manifest) - _manifest_contact_ids(manifest_payload)
    return sorted(_contact_record_drive_filename(contact_id) for contact_id in removed_ids)


def _export_contacts_manifest_only_to_google_drive(
    conn,
    access_token: str,
    storage: dict,
    remote_sync_state: dict,
    existing_manifest: dict,
    local_contacts_revision: int,
) -> bool:
    if str(existing_manifest.get("format") or "").strip() != "contactsfreeshare.shared_contacts.v2":
        return False

    manifest_payload = _shared_contacts_manifest_payload(conn)
    existing_referenced_files = {
        _clean(item.get("file_name")) or _contact_record_drive_filename(int(item.get("id") or 0))
        for item in existing_manifest.get("contacts") or []
        if isinstance(item, dict) and int(item.get("id") or 0) > 0
    }
    referenced_files = {
        _clean(item.get("file_name"))
        for item in manifest_payload.get("contacts") or []
        if isinstance(item, dict) and _clean(item.get("file_name"))
    }
    removed_files = _removed_contact_record_file_names(existing_manifest, manifest_payload)

    _set_upload_progress(
        conn,
        in_progress=True,
        phase="drive_shared_data",
        total_count=1,
        processed_count=0,
        current_contact="Drive: uploading contact manifest",
    )
    conn.commit()

    _drive_update_json_file_content(access_token, storage["contacts_current_file_id"], manifest_payload)
    for file_name in removed_files:
        _set_upload_progress(
            conn,
            in_progress=True,
            phase="drive_shared_data",
            total_count=1,
            processed_count=0,
            current_contact=f"Drive: finding old contact file {file_name}",
        )
        conn.commit()
        file_item = _drive_find_child(
            access_token,
            parent_id=storage["contact_records_folder_id"],
            name=file_name,
            mime_type="application/json",
        )
        if file_item is not None:
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=1,
                processed_count=0,
                current_contact=f"Drive: deleting old contact file {file_name}",
            )
            conn.commit()
            _drive_delete_file_if_exists(access_token, _clean(file_item.get("id")))

    if existing_referenced_files != referenced_files:
        _set_upload_progress(
            conn,
            in_progress=True,
            phase="drive_shared_data",
            total_count=1,
            processed_count=0,
            current_contact="Drive: updating contact sync revision",
        )
        conn.commit()
        next_contacts_revision, remote_sync_state = _next_dataset_sync_revision(
            access_token,
            storage,
            revision_key="contacts_sync_revision",
            local_revision=local_contacts_revision,
            remote_sync_state=remote_sync_state,
        )
        remote_sync_state["contacts_sync_revision"] = next_contacts_revision
        _write_shared_sync_state(access_token, storage, remote_sync_state)
        _store_local_sync_revisions(conn=conn, contacts_sync_revision=next_contacts_revision)

    _clear_pending_contacts_manifest_drive_export(conn)
    # Keep upload_in_progress set so the contacts UI does not treat this mid-job
    # manifest step as a finished Upload and re-show the pending banner.
    _set_upload_progress(
        conn,
        in_progress=True,
        phase="drive_shared_data",
        total_count=1,
        processed_count=1,
        current_contact="Shared app data",
        started_at="",
    )
    conn.commit()
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    return True


def export_shared_contacts_to_google_drive() -> bool:
    _set_drive_upload_step("Drive: getting Google access")
    access_token, _account_email = _get_valid_access_token()
    _set_drive_upload_step("Drive: checking contact storage")
    storage = _ensure_shared_drive_storage(access_token, contact_only=True)
    _set_drive_upload_step("Drive: loading sync state")
    remote_sync_state = _load_shared_sync_state(access_token, storage)

    existing_manifest: dict = {}
    manifest_is_v2 = False
    local_contacts_revision = 0
    pending_contact_export_count = 0
    pending_photo_export_count = 0
    bootstrap_needed = False

    with get_connection() as conn:
        total_contacts = _count_contacts(conn)
        state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
        state = dict(state_row) if state_row else {}
        local_contacts_revision = _normalize_sync_revision(state.get("contacts_sync_revision"))
        pending_contact_export_count = _count_pending_shared_contact_exports(conn)
        pending_photo_export_count = int(
            (conn.execute("SELECT COUNT(*) AS count FROM contacts WHERE photo_needs_export = 1").fetchone() or {"count": 0})["count"]
            or 0
        )
        bootstrap_remaining_count = _count_unbootstrapped_contacts(conn)
        contacts_manifest_needs_drive_export = bool(state.get("contacts_manifest_needs_drive_export") or 0)
        skip_manifest_read = _can_skip_drive_manifest_read_for_incremental_export(
            contacts_manifest_needs_drive_export=contacts_manifest_needs_drive_export,
            pending_contact_export_count=pending_contact_export_count,
            pending_photo_export_count=pending_photo_export_count,
            bootstrap_remaining_count=bootstrap_remaining_count,
        )
        if skip_manifest_read:
            manifest_is_v2 = True
            bootstrap_needed = False
        else:
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                current_contact="Drive: loading contact manifest",
            )
            conn.commit()
            existing_manifest = _drive_load_json_file_content(access_token, storage["contacts_current_file_id"])
            manifest_is_v2 = (
                isinstance(existing_manifest, dict)
                and str(existing_manifest.get("format") or "").strip() == "contactsfreeshare.shared_contacts.v2"
            )
            if (
                manifest_is_v2
                and contacts_manifest_needs_drive_export
                and pending_contact_export_count <= 0
                and pending_photo_export_count <= 0
                and bootstrap_remaining_count <= 0
            ):
                return _export_contacts_manifest_only_to_google_drive(
                    conn,
                    access_token,
                    storage,
                    remote_sync_state,
                    existing_manifest,
                    local_contacts_revision,
                )
            bootstrap_needed = (not manifest_is_v2) or bootstrap_remaining_count > 0

    if pending_photo_export_count > 0:
        _set_drive_upload_step("Drive: uploading contact photos")
        _sync_pending_contact_photos_to_google_drive(access_token, storage["contact_photos_folder_id"])
    if bootstrap_needed:
        _set_drive_upload_step("Drive: listing contact files")
    contact_record_files = (
        _drive_children_by_name(access_token, parent_id=storage["contact_records_folder_id"])
        if bootstrap_needed
        else {}
    )

    with get_connection() as conn:
        total_contacts = _count_contacts(conn)
        state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
        state = dict(state_row) if state_row else {}
        if not skip_manifest_read:
            bootstrap_needed = (not manifest_is_v2) or _count_unbootstrapped_contacts(conn) > 0

        if bootstrap_needed:
            bootstrap_total, bootstrap_processed = _prepare_drive_bootstrap_run(
                conn,
                manifest_is_v2=manifest_is_v2,
                total_contacts=total_contacts,
                state=state,
            )
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_bootstrap",
                total_count=bootstrap_total,
                processed_count=_visible_bootstrap_processed(bootstrap_processed, bootstrap_total),
                current_contact="",
                started_at=str(state.get("bootstrap_started_at") or _now_text()),
            )
            conn.commit()

            bootstrap_batch_query = """
                SELECT id
                FROM contacts
                WHERE shared_drive_bootstrapped = 0
                ORDER BY id
                LIMIT ?
                """

            while True:
                state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
                bootstrap_total = int(state.get("bootstrap_total_contacts") or total_contacts)
                bootstrap_processed = int(state.get("bootstrap_processed_contacts") or 0)
                remaining_contacts = _count_drive_bootstrap_remaining(conn, manifest_is_v2=manifest_is_v2)
                if remaining_contacts <= 0:
                    break
                batch_rows = conn.execute(
                    bootstrap_batch_query,
                    (DRIVE_BOOTSTRAP_BATCH_SIZE,),
                ).fetchall()
                if not batch_rows:
                    break

                current_contact_name = ""
                for row in batch_rows:
                    contact_id = int(row["id"])
                    payload = _build_shared_contact_record_payload(conn, contact_id)
                    if payload is not None:
                        current_contact_name = _format_queue_contact_name(payload.get("contact") or {}, {})
                        filename = _contact_record_drive_filename(contact_id)
                        file_item = contact_record_files.get(filename)
                        if file_item is None:
                            file_item = _drive_create_json_file_with_content(
                                access_token,
                                name=filename,
                                parent_id=storage["contact_records_folder_id"],
                                payload=payload,
                            )
                            contact_record_files[filename] = file_item
                        else:
                            _drive_update_json_file_content(access_token, _clean(file_item.get("id")), payload)
                    bootstrap_processed += 1
                    conn.execute(
                        """
                        UPDATE contacts
                        SET
                          shared_drive_bootstrapped = 1,
                          shared_drive_needs_export = 0,
                          shared_drive_revision = CASE WHEN shared_drive_revision > 0 THEN shared_drive_revision ELSE 1 END
                        WHERE id = ?
                        """,
                        (contact_id,),
                    )
                    _set_bootstrap_state(
                        conn,
                        status="running",
                        total_contacts=bootstrap_total,
                        processed_contacts=bootstrap_processed,
                        last_contact_id=contact_id,
                        updated_at=_now_text(),
                        error="",
                    )
                    if bootstrap_processed % DRIVE_BOOTSTRAP_VISIBLE_PROGRESS_STEP == 0 or bootstrap_processed >= bootstrap_total:
                        _set_upload_progress(
                            conn,
                            in_progress=True,
                            phase="drive_bootstrap",
                            total_count=bootstrap_total,
                            processed_count=_visible_bootstrap_processed(bootstrap_processed, bootstrap_total),
                            current_contact=current_contact_name,
                            started_at=str(state.get("bootstrap_started_at") or _now_text()),
                        )
                    conn.commit()

                _set_upload_progress(
                    conn,
                    in_progress=True,
                    phase="drive_bootstrap",
                    total_count=bootstrap_total,
                    processed_count=_visible_bootstrap_processed(bootstrap_processed, bootstrap_total),
                    current_contact=current_contact_name,
                    started_at=str(state.get("bootstrap_started_at") or _now_text()),
                )
                conn.commit()

            remaining_dirty_count = _count_drive_bootstrap_remaining(conn, manifest_is_v2=manifest_is_v2)
            if remaining_dirty_count <= 0:
                manifest_payload = _shared_contacts_manifest_payload(conn)
                _drive_update_json_file_content(access_token, storage["contacts_current_file_id"], manifest_payload)

                referenced_files = {
                    item["file_name"]
                    for item in manifest_payload.get("contacts") or []
                    if isinstance(item, dict) and _clean(item.get("file_name"))
                }
                for file_item in contact_record_files.values():
                    file_name = _clean(file_item.get("name"))
                    file_id = _clean(file_item.get("id"))
                    if file_name and file_name not in referenced_files:
                        _drive_delete_file_if_exists(access_token, file_id)
                next_contacts_revision, remote_sync_state = _next_dataset_sync_revision(
                    access_token,
                    storage,
                    revision_key="contacts_sync_revision",
                    local_revision=local_contacts_revision,
                    remote_sync_state=remote_sync_state,
                )
                remote_sync_state["contacts_sync_revision"] = next_contacts_revision
                _write_shared_sync_state(access_token, storage, remote_sync_state)
                _store_local_sync_revisions(conn=conn, contacts_sync_revision=next_contacts_revision)
                _mark_bootstrap_complete(conn)
                _clear_pending_drive_export(conn)
                _set_upload_progress(
                    conn,
                    in_progress=False,
                    phase="",
                    total_count=bootstrap_total,
                    processed_count=bootstrap_total,
                    current_contact="",
                    started_at="",
                )
            conn.commit()

        if _count_drive_bootstrap_remaining(conn, manifest_is_v2=manifest_is_v2) > 0:
            conn.commit()
            _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
            return False

        if _count_pending_shared_contact_exports(conn) <= 0:
            conn.commit()
            _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
            return True

        if _should_queue_local_ahead_shared_app_fields_on_drive_export(
            skip_manifest_read=skip_manifest_read,
            bootstrap_needed=bootstrap_needed,
            contacts_manifest_needs_drive_export=contacts_manifest_needs_drive_export,
        ):
            _queue_contacts_with_local_ahead_shared_app_fields(
                conn,
                access_token,
                storage,
                contact_record_files=contact_record_files,
            )
            conn.commit()

        changed_rows = conn.execute(
            """
            SELECT id
            FROM contacts
            WHERE shared_drive_needs_export = 1
            ORDER BY id
            """
        ).fetchall()
        changed_contact_ids = [int(row["id"]) for row in changed_rows]
        exported_contact_ids: list[int] = []
        uploaded_contact_ids: list[int] = []
        manifest_by_contact_id = {
            int(item.get("id") or 0): item
            for item in (existing_manifest.get("contacts") if isinstance(existing_manifest, dict) else []) or []
            if isinstance(item, dict) and int(item.get("id") or 0) > 0
        }
        for contact_id in changed_contact_ids:
            payload = _build_shared_contact_record_payload(conn, contact_id)
            if payload is None:
                continue
            current_contact_name = _format_queue_contact_name(payload.get("contact") or {}, {})
            filename = _contact_record_drive_filename(contact_id)
            file_item = contact_record_files.get(filename)
            if file_item is None:
                _set_upload_progress(
                    conn,
                    in_progress=True,
                    phase="drive_shared_data",
                    total_count=1,
                    processed_count=0,
                    current_contact=f"Drive: finding contact file {filename}",
                    started_at=str(state.get("upload_started_at") or _now_text()),
                )
                conn.commit()
                file_item = _drive_find_child(
                    access_token,
                    parent_id=storage["contact_records_folder_id"],
                    name=filename,
                    mime_type="application/json",
                )
                if file_item is not None:
                    contact_record_files[filename] = file_item
            if file_item is not None and _manifest_contact_row_matches(
                payload.get("contact") or {},
                manifest_by_contact_id.get(contact_id),
            ):
                manifest_item = manifest_by_contact_id.get(contact_id)
                if _drive_export_payload_already_on_drive(conn, contact_id, payload, manifest_item):
                    exported_contact_ids.append(contact_id)
                    continue
                try:
                    remote_payload = _drive_load_json_file_content(access_token, _clean(file_item.get("id")))
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
                    remote_payload = {}
                if _shared_contact_payload_matches(payload, remote_payload):
                    exported_contact_ids.append(contact_id)
                    continue
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=1,
                processed_count=0,
                current_contact=current_contact_name or "Shared app data",
                started_at=str(state.get("upload_started_at") or _now_text()),
            )
            conn.commit()
            if file_item is None:
                _set_upload_progress(
                    conn,
                    in_progress=True,
                    phase="drive_shared_data",
                    total_count=1,
                    processed_count=0,
                    current_contact=f"Drive: creating contact file {filename}",
                    started_at=str(state.get("upload_started_at") or _now_text()),
                )
                conn.commit()
                file_item = _drive_create_json_file(
                    access_token,
                    name=filename,
                    parent_id=storage["contact_records_folder_id"],
                )
                contact_record_files[filename] = file_item
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=1,
                processed_count=0,
                current_contact=f"Drive: uploading {current_contact_name or filename}",
                started_at=str(state.get("upload_started_at") or _now_text()),
            )
            conn.commit()
            _drive_update_json_file_content(access_token, _clean(file_item.get("id")), payload)
            exported_contact_ids.append(contact_id)
            uploaded_contact_ids.append(contact_id)
        if exported_contact_ids:
            placeholders = ",".join("?" for _ in exported_contact_ids)
            conn.execute(
                f"""
                UPDATE contacts
                SET shared_drive_needs_export = 0
                WHERE id IN ({placeholders})
                """,
                tuple(exported_contact_ids),
            )
        remaining_dirty_count = _count_pending_shared_contact_exports(conn)

        if remaining_dirty_count <= 0:
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=1,
                processed_count=0,
                current_contact="Drive: building contact manifest",
            )
            conn.commit()
            manifest_payload = _build_patched_contacts_manifest_for_drive_export(
                access_token,
                storage,
                conn,
                existing_manifest,
                changed_contact_ids=changed_contact_ids,
                uploaded_contact_ids=uploaded_contact_ids,
                skip_manifest_read=skip_manifest_read,
            )
            existing_referenced_files = {
                _clean(item.get("file_name")) or _contact_record_drive_filename(int(item.get("id") or 0))
                for item in (existing_manifest.get("contacts") if isinstance(existing_manifest, dict) else []) or []
                if isinstance(item, dict) and int(item.get("id") or 0) > 0
            }
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=1,
                processed_count=0,
                current_contact="Drive: uploading contact manifest",
            )
            conn.commit()
            _drive_update_json_file_content(access_token, storage["contacts_current_file_id"], manifest_payload)

            referenced_files = {
                item["file_name"]
                for item in manifest_payload.get("contacts") or []
                if isinstance(item, dict) and _clean(item.get("file_name"))
            }
            removed_drive_record_count = 0
            for file_name in _removed_contact_record_file_names(existing_manifest, manifest_payload):
                _set_upload_progress(
                    conn,
                    in_progress=True,
                    phase="drive_shared_data",
                    total_count=1,
                    processed_count=0,
                    current_contact=f"Drive: finding old contact file {file_name}",
                )
                conn.commit()
                file_item = _drive_find_child(
                    access_token,
                    parent_id=storage["contact_records_folder_id"],
                    name=file_name,
                    mime_type="application/json",
                )
                file_id = _clean((file_item or {}).get("id"))
                if file_id:
                    _set_upload_progress(
                        conn,
                        in_progress=True,
                        phase="drive_shared_data",
                        total_count=1,
                        processed_count=0,
                        current_contact=f"Drive: deleting old contact file {file_name}",
                    )
                    conn.commit()
                    _drive_delete_file_if_exists(access_token, file_id)
                    removed_drive_record_count += 1
            manifest_contacts_changed = existing_referenced_files != referenced_files
            if uploaded_contact_ids or removed_drive_record_count > 0 or manifest_contacts_changed:
                _set_upload_progress(
                    conn,
                    in_progress=True,
                    phase="drive_shared_data",
                    total_count=1,
                    processed_count=0,
                    current_contact="Drive: updating contact sync revision",
                )
                conn.commit()
                next_contacts_revision, remote_sync_state = _next_dataset_sync_revision(
                    access_token,
                    storage,
                    revision_key="contacts_sync_revision",
                    local_revision=local_contacts_revision,
                    remote_sync_state=remote_sync_state,
                )
                remote_sync_state["contacts_sync_revision"] = next_contacts_revision
                _write_shared_sync_state(access_token, storage, remote_sync_state)
                _store_local_sync_revisions(conn=conn, contacts_sync_revision=next_contacts_revision)

        conn.commit()
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    return remaining_dirty_count <= 0


def export_shared_meetingdata_to_google_drive() -> None:
    from app.services.backup_service import build_meetingdata_backup_bytes

    _filename, content = build_meetingdata_backup_bytes()
    summary = get_google_sync_summary()
    state = summary.get("state") or {}
    access_token, _account_email = _get_valid_access_token()
    storage = _ensure_shared_drive_storage(access_token)
    remote_sync_state = _load_shared_sync_state(access_token, storage)
    next_meetingdata_revision, remote_sync_state = _next_dataset_sync_revision(
        access_token,
        storage,
        revision_key="meetingdata_sync_revision",
        local_revision=_normalize_sync_revision(state.get("meetingdata_sync_revision")),
        remote_sync_state=remote_sync_state,
    )
    payload = json.loads(content.decode("utf-8"))
    _drive_update_json_file_content(access_token, storage["meetingdata_current_file_id"], payload)
    remote_sync_state["meetingdata_sync_revision"] = next_meetingdata_revision
    _write_shared_sync_state(access_token, storage, remote_sync_state)
    _store_local_sync_revisions(meetingdata_sync_revision=next_meetingdata_revision)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")


_SHARED_CONTACT_DOWNLOAD_WORKERS = 8
_LOCAL_CONTACT_PRUNE_GUARD_MIN_COUNT = 20


def _drive_manifest_should_preserve_local_contacts(local_count: int, remote_count: int) -> bool:
    """Keep a full local set when Drive looks empty, partial, or unexpectedly small."""
    safe_local = max(int(local_count or 0), 0)
    safe_remote = max(int(remote_count or 0), 0)
    if safe_local <= 0:
        return False
    if safe_remote <= 0:
        return True
    return safe_local >= _LOCAL_CONTACT_PRUNE_GUARD_MIN_COUNT and safe_remote * 2 < safe_local


def import_shared_contacts_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
    progress_callback=None,
    backfill_manifest_digests: bool = True,
) -> bool:
    from app.services.backup_service import restore_contacts_backup_payload

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token)
    sync_state = remote_sync_state or _load_shared_sync_state(token, resolved_storage)
    summary = get_google_sync_summary()
    local_contacts_revision = _normalize_sync_revision((summary.get("state") or {}).get("contacts_sync_revision"))
    remote_contacts_revision = _normalize_sync_revision(sync_state.get("contacts_sync_revision"))
    if remote_contacts_revision > 0 and remote_contacts_revision == local_contacts_revision:
        return True
    with get_connection() as conn:
        local_contact_count = _count_contacts(conn)
        previous_photo_state = {
            int(row["id"]): (
                _clean(row["photo_drive_file_id"]),
                int(row["photo_sync_revision"] or 0),
                _clean_contact_photo_path(row["photo"]),
            )
            for row in conn.execute(
                """
                SELECT id, photo, photo_drive_file_id, photo_sync_revision
                FROM contacts
                """
            ).fetchall()
        }
    payload = _drive_load_json_file_content(token, resolved_storage["contacts_current_file_id"])
    if isinstance(payload, dict) and payload.get("tables"):
        backup_rows = (payload.get("tables") or {}).get("contacts") or []
        backup_count = len(backup_rows) if isinstance(backup_rows, list) else 0
        if _drive_manifest_should_preserve_local_contacts(local_contact_count, backup_count):
            _LOGGER.warning(
                "Skipped Drive contacts backup restore because the remote set is much smaller "
                "than local (%s vs %s).",
                backup_count,
                local_contact_count,
            )
            return True
        restore_contacts_backup_payload(payload, restore_files=False)
        if remote_contacts_revision > 0:
            _store_local_sync_revisions(contacts_sync_revision=remote_contacts_revision)
        _import_changed_contact_photos_from_google_drive(token, previous_photo_state)
        return True

    if not isinstance(payload, dict) or str(payload.get("format") or "").strip() != "contactsfreeshare.shared_contacts.v2":
        if local_contact_count <= 0 and _clean(resolved_storage.get("contacts_full_file_id")):
            full_payload = _drive_load_json_file_content(token, resolved_storage["contacts_full_file_id"])
            if isinstance(full_payload, dict) and full_payload.get("tables"):
                restore_contacts_backup_payload(full_payload, restore_files=False)
                if remote_contacts_revision > 0:
                    _store_local_sync_revisions(contacts_sync_revision=remote_contacts_revision)
                _import_changed_contact_photos_from_google_drive(token, previous_photo_state)
                return True
        export_shared_contacts_to_google_drive()
        return False

    with get_connection() as conn:
        local_revision_by_id = {
            int(row["id"]): int(row["shared_drive_revision"] or 0)
            for row in conn.execute(
                """
                SELECT id, shared_drive_revision
                FROM contacts
                """
            ).fetchall()
        }

    remote_contacts = [
        item
        for item in (payload.get("contacts") or [])
        if isinstance(item, dict) and int(item.get("id") or 0) > 0
    ]
    remote_ids = {int(item["id"]) for item in remote_contacts}
    # Only Drive-ahead contacts need import. Local-ahead mismatches used to be counted
    # every sign-in, then skipped without updating revisions, so the same set (e.g. 162)
    # kept reappearing forever.
    changed_contacts = [
        item
        for item in remote_contacts
        if _shared_contact_remote_is_ahead(
            local_revision_by_id.get(int(item["id"])),
            item.get("shared_drive_revision"),
        )
    ]
    removed_contact_ids = set(local_revision_by_id) - remote_ids
    if _drive_manifest_should_preserve_local_contacts(local_contact_count, len(remote_contacts)):
        _LOGGER.warning(
            "Keeping %s local contacts instead of adopting a much smaller Drive list (%s).",
            local_contact_count,
            len(remote_contacts),
        )
        removed_contact_ids = set()

    # Fast path: dataset revision differed, but no per-contact Drive-ahead work and no
    # removals. Adopt the remote dataset revision without listing/downloading records.
    if not changed_contacts and not removed_contact_ids:
        with get_connection() as conn:
            if not remote_contacts and local_contact_count > 0:
                # Keep local contacts when Drive manifest is unexpectedly empty.
                pass
            elif not remote_contacts:
                _clear_local_contact_data(conn)
                conn.execute(
                    """
                    UPDATE google_sync_state
                    SET
                      pending_upload_count = 0,
                      needs_upload_reminder = 0,
                      needs_drive_export = 0,
                      last_sync_error = '',
                      updated_at = ?
                    WHERE id = 1
                    """,
                    (_now_text(),),
                )
            else:
                _replace_contact_assignment_options(conn, payload.get("assignment_options") or [])
                conn.execute(
                    """
                    UPDATE google_sync_state
                    SET
                      last_sync_error = '',
                      updated_at = ?
                    WHERE id = 1
                    """,
                    (_now_text(),),
                )
            conn.commit()
        if remote_contacts_revision > 0:
            _store_local_sync_revisions(contacts_sync_revision=remote_contacts_revision)
        return True

    changed_contact_count = len(changed_contacts)
    if changed_contacts and progress_callback is not None:
        progress_callback(
            "contacts",
            0,
            changed_contact_count,
            "Preparing shared Drive contacts",
            local_contact_count=local_contact_count,
            drive_manifest_count=len(remote_contacts),
        )

    # First pass: resolve cheap skip/revision_only actions without listing Drive files.
    needs_download: list[tuple[int, dict, int, str]] = []
    missing_shared_contact_ids: list[int] = []
    for index, contact_item in enumerate(changed_contacts, start=1):
        contact_name = ", ".join(
            item
            for item in (
                _clean(contact_item.get("family_name")),
                _clean(contact_item.get("given_name")),
            )
            if item
        ) or f"Contact {int(contact_item['id'])}"
        if progress_callback is not None and (index == 1 or index % 10 == 0 or index == changed_contact_count):
            progress_callback("contacts", index, changed_contact_count, contact_name)
        contact_id = int(contact_item["id"])
        remote_revision = int(contact_item.get("shared_drive_revision") or 0)
        with get_connection() as conn:
            peek_action = _peek_shared_contact_import_action(
                conn,
                contact_id,
                remote_revision,
                contact_item,
            )
            if peek_action == "skip":
                continue
            if peek_action == "revision_only":
                _sync_shared_contact_revision_only(conn, contact_id, remote_revision)
                conn.commit()
                continue
        filename = _clean(contact_item.get("file_name")) or _contact_record_drive_filename(contact_id)
        needs_download.append((contact_id, contact_item, remote_revision, filename))

    # Only list/download contact record files when peeks could not finish the work.
    if needs_download:
        contact_record_files = _drive_children_by_name(
            token,
            parent_id=resolved_storage["contact_records_folder_id"],
        )
        download_jobs: list[tuple[int, int, str]] = []
        for contact_id, _contact_item, remote_revision, filename in needs_download:
            file_item = contact_record_files.get(filename)
            if file_item is None:
                missing_shared_contact_ids.append(contact_id)
                continue
            drive_file_id = _clean(file_item.get("id"))
            if not drive_file_id:
                missing_shared_contact_ids.append(contact_id)
                continue
            download_jobs.append((contact_id, remote_revision, drive_file_id))

        payloads_by_contact_id: dict[int, dict] = {}
        if download_jobs:
            worker_count = max(1, min(_SHARED_CONTACT_DOWNLOAD_WORKERS, len(download_jobs)))

            def _load_one(job: tuple[int, int, str]) -> tuple[int, dict]:
                contact_id, _remote_revision, drive_file_id = job
                return contact_id, _drive_load_json_file_content(token, drive_file_id)

            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [pool.submit(_load_one, job) for job in download_jobs]
                for future in as_completed(futures):
                    contact_id, contact_payload = future.result()
                    payloads_by_contact_id[contact_id] = contact_payload

        for contact_id, remote_revision, _drive_file_id in download_jobs:
            contact_payload = payloads_by_contact_id.get(contact_id)
            if not isinstance(contact_payload, dict):
                continue
            with get_connection() as conn:
                import_action = _resolve_shared_contact_import_action(
                    conn,
                    contact_id,
                    remote_revision,
                    contact_payload,
                )
                if import_action == "import":
                    _merge_shared_contact_record_from_drive(conn, contact_payload)
                elif import_action == "revision_only":
                    _sync_shared_contact_revision_only(conn, contact_id, remote_revision)
                conn.commit()

    if missing_shared_contact_ids:
        _LOGGER.warning(
            "Skipped %d shared contact record(s) whose Drive file was missing "
            "during sign-in sync (contact ids: %s). The next full Editor export "
            "will rebuild the contacts manifest and drop these stale entries.",
            len(missing_shared_contact_ids),
            ", ".join(str(cid) for cid in missing_shared_contact_ids[:20]),
        )

    with get_connection() as conn:
        if not remote_contacts:
            if local_contact_count > 0:
                conn.commit()
                return True
            _clear_local_contact_data(conn)
            if remote_contacts_revision > 0:
                _store_local_sync_revisions(conn=conn, contacts_sync_revision=remote_contacts_revision)
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  pending_upload_count = 0,
                  needs_upload_reminder = 0,
                  needs_drive_export = 0,
                  last_sync_error = '',
                  updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )
            conn.commit()
            return True

        for contact_id in sorted(removed_contact_ids):
            _delete_local_contact_record(conn, contact_id)
        if collapse_stale_identity_duplicate_contacts(conn):
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  contacts_manifest_needs_drive_export = 1,
                  updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )

        _replace_contact_assignment_options(conn, payload.get("assignment_options") or [])
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              last_sync_error = '',
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()

    if backfill_manifest_digests:
        _backfill_shared_contacts_manifest_digests(token, resolved_storage, payload)

    if remote_contacts_revision > 0:
        _store_local_sync_revisions(contacts_sync_revision=remote_contacts_revision)
    _import_changed_contact_photos_from_google_drive(token, previous_photo_state)
    return True


def _backfill_shared_contacts_manifest_digests(
    access_token: str,
    storage: dict,
    manifest_payload: dict,
) -> bool:
    if not isinstance(manifest_payload, dict):
        return False
    if str(manifest_payload.get("format") or "").strip() != "contactsfreeshare.shared_contacts.v2":
        return False
    contacts = manifest_payload.get("contacts") or []
    if not any(
        isinstance(item, dict) and int(item.get("id") or 0) > 0 and not _clean(item.get("content_digest"))
        for item in contacts
    ):
        return False
    patched = dict(manifest_payload)
    patched_contacts: list[dict] = []
    updated = False
    with get_connection() as conn:
        for item in contacts:
            if not isinstance(item, dict):
                continue
            patched_item = dict(item)
            contact_id = int(patched_item.get("id") or 0)
            if contact_id > 0 and not _clean(patched_item.get("content_digest")):
                local_payload = _build_shared_contact_record_payload(conn, contact_id)
                if local_payload:
                    digest = _clean(local_payload.get("content_digest")) or _shared_contact_payload_digest(
                        local_payload,
                        include_edit_logs=False,
                    )
                    if digest:
                        patched_item["content_digest"] = digest
                        updated = True
            patched_contacts.append(patched_item)
    if not updated:
        return False
    patched["contacts"] = patched_contacts
    patched["exported_at"] = _iso_now()
    _drive_update_json_file_content(access_token, storage["contacts_current_file_id"], patched)
    return True


def _shared_contact_manifest_entries(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("format") or "").strip() != "contactsfreeshare.shared_contacts.v2":
        return []
    return [
        item
        for item in (payload.get("contacts") or [])
        if isinstance(item, dict) and int(item.get("id") or 0) > 0
    ]


def _load_shared_contact_row_from_drive(
    token: str,
    storage: dict,
    manifest_item: dict,
    *,
    file_cache: dict[str, dict] | None = None,
) -> dict:
    filename = _clean(manifest_item.get("file_name")) or _contact_record_drive_filename(int(manifest_item["id"]))
    if file_cache is not None and filename in file_cache:
        return file_cache[filename]
    file_item = _drive_find_child(
        token,
        parent_id=storage["contact_records_folder_id"],
        name=filename,
        mime_type="application/json",
    )
    if not file_item:
        return {}
    remote_payload = _drive_load_json_file_content(token, _clean(file_item.get("id")))
    contact_row = remote_payload.get("contact") if isinstance(remote_payload, dict) and isinstance(remote_payload.get("contact"), dict) else remote_payload
    normalized = dict(contact_row) if isinstance(contact_row, dict) else {}
    if file_cache is not None:
        file_cache[filename] = normalized
    return normalized


def _contact_has_usable_local_photo(local_row: dict) -> bool:
    photo_path = _clean_contact_photo_path(local_row.get("photo"))
    drive_file_id = _clean(local_row.get("photo_drive_file_id"))
    if not photo_path or not drive_file_id:
        return False
    local_path = _contact_photo_local_path(photo_path)
    return local_path is not None and local_path.is_file()


def _remote_has_custom_drive_photo(remote_row: dict) -> bool:
    photo_path = _clean_contact_photo_path(remote_row.get("photo"))
    drive_file_id = _clean(remote_row.get("photo_drive_file_id"))
    return bool(photo_path and drive_file_id)


def _contact_needs_photo_sync(local_row: dict, remote_row: dict) -> bool:
    if not _remote_has_custom_drive_photo(remote_row):
        return False
    remote_photo = _clean_contact_photo_path(remote_row.get("photo"))
    remote_drive_id = _clean(remote_row.get("photo_drive_file_id"))
    remote_revision = int(remote_row.get("photo_sync_revision") or 0)
    local_photo = _clean_contact_photo_path(local_row.get("photo"))
    local_drive_id = _clean(local_row.get("photo_drive_file_id"))
    local_revision = int(local_row.get("photo_sync_revision") or 0)
    if (
        remote_photo != local_photo
        or remote_drive_id != local_drive_id
        or remote_revision != local_revision
    ):
        return True
    local_path = _contact_photo_local_path(remote_photo)
    return local_path is None or not local_path.is_file()


def _download_shared_contact_photo_from_drive(
    access_token: str,
    photo_path: str,
    drive_file_id: str,
) -> bool:
    local_path = _contact_photo_local_path(photo_path)
    drive_file_id = _clean(drive_file_id)
    if local_path is None or not drive_file_id:
        return False
    get_contact_photo_storage_dir().mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_drive_load_file_content(access_token, drive_file_id))
    return local_path.is_file()


def _find_contact_photo_file_on_drive(
    access_token: str,
    contact_photos_folder_id: str,
    contact_id: int,
    *,
    photo_path: str = "",
) -> tuple[str, str]:
    candidate_names: list[str] = []
    cleaned_photo_path = _clean_contact_photo_path(photo_path)
    if cleaned_photo_path:
        candidate_names.append(_contact_photo_drive_filename(contact_id, cleaned_photo_path))
    for extension in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        candidate_name = f"contact_{int(contact_id)}_photo{extension}"
        if candidate_name not in candidate_names:
            candidate_names.append(candidate_name)
    for candidate_name in candidate_names:
        file_item = _drive_find_child(
            access_token,
            parent_id=contact_photos_folder_id,
            name=candidate_name,
            mime_type=mimetypes.guess_type(candidate_name)[0] or "image/jpeg",
        )
        if file_item is not None:
            return _clean(file_item.get("id")), candidate_name
    return "", ""


def _coalesce_mtg_home_elder_flag(local_value: object, remote_value: object) -> str:
    """Keep a filled local 1/2 when Drive has a blank meeting-home/elder code."""
    remote = _clean(remote_value)
    return remote or _clean(local_value)


def _apply_shared_contact_app_fields(
    local_row: dict,
    remote_row: dict,
    remote_revision: int,
    *,
    access_token: str | None = None,
) -> bool:
    google_contact_id = _clean(remote_row.get("google_contact_id"))
    local_google_contact_id = _clean(local_row.get("google_contact_id"))
    if google_contact_id and local_google_contact_id and google_contact_id != local_google_contact_id:
        return False
    mtg_home_elder_flag = _coalesce_mtg_home_elder_flag(
        local_row.get("mtg_home_elder_flag"),
        remote_row.get("mtg_home_elder_flag"),
    )
    meets_in_state = (
        _clean(remote_row.get("meets_in_state"))
        if "meets_in_state" in remote_row
        else _clean(local_row.get("meets_in_state"))
    )
    birthday = _clean(remote_row.get("birthday"))
    remote_photo = _clean_contact_photo_path(remote_row.get("photo"))
    remote_drive_id = _clean(remote_row.get("photo_drive_file_id"))
    remote_photo_revision = int(remote_row.get("photo_sync_revision") or 0)
    apply_photo = _remote_has_custom_drive_photo(remote_row)
    with get_connection() as conn:
        if apply_photo:
            conn.execute(
                """
                UPDATE contacts
                SET
                  mtg_home_elder_flag = ?,
                  meets_in_state = ?,
                  birthday = ?,
                  shared_drive_revision = ?,
                  photo = ?,
                  photo_drive_file_id = ?,
                  photo_sync_revision = ?
                WHERE id = ?
                """,
                (
                    mtg_home_elder_flag,
                    meets_in_state,
                    birthday,
                    remote_revision,
                    remote_photo,
                    remote_drive_id,
                    remote_photo_revision,
                    int(local_row["id"]),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE contacts
                SET
                  mtg_home_elder_flag = ?,
                  meets_in_state = ?,
                  birthday = ?,
                  shared_drive_revision = ?
                WHERE id = ?
                """,
                (mtg_home_elder_flag, meets_in_state, birthday, remote_revision, int(local_row["id"])),
            )
        conn.commit()
    if apply_photo and access_token:
        _download_shared_contact_photo_from_drive(access_token, remote_photo, remote_drive_id)
    return True


def _shared_contact_app_fields_stale(local_row: dict, remote_row: dict, remote_revision: int) -> bool:
    local_revision = int(local_row.get("shared_drive_revision") or 0)
    if remote_revision > local_revision:
        return True
    if _contact_needs_photo_sync(local_row, remote_row):
        return True
    local_mtg = _clean(local_row.get("mtg_home_elder_flag"))
    remote_mtg = _clean(remote_row.get("mtg_home_elder_flag"))
    if remote_mtg and remote_mtg != local_mtg:
        return True
    if "meets_in_state" in remote_row:
        local_state = _clean(local_row.get("meets_in_state"))
        remote_state = _clean(remote_row.get("meets_in_state"))
        if remote_state != local_state:
            return True
    local_birthday = _clean(local_row.get("birthday"))
    remote_birthday = _clean(remote_row.get("birthday"))
    if remote_birthday != local_birthday:
        return True
    return False


def _shared_contact_local_app_fields_should_push(local_contact: dict, remote_contact: dict) -> bool:
    for key in ("mtg_home_elder_flag", "meets_in_state", "birthday"):
        local_value = _clean(local_contact.get(key))
        remote_value = _clean(remote_contact.get(key))
        if local_value == remote_value:
            continue
        if local_value or not remote_value:
            return True
    return False


def _queue_contacts_with_local_ahead_shared_app_fields(
    conn,
    access_token: str,
    storage: dict,
    *,
    contact_record_files: dict[str, dict] | None = None,
    progress_callback=None,
) -> int:
    """Mark contacts for Drive export when local app-only fields never reached shared storage."""
    contact_record_files = dict(contact_record_files or {})
    if not contact_record_files:
        contact_record_files.update(
            _drive_children_by_name(access_token, parent_id=storage["contact_records_folder_id"])
        )
    candidate_rows = conn.execute(
        """
        SELECT id
        FROM contacts
        WHERE shared_drive_bootstrapped = 1
          AND shared_drive_needs_export = 0
          AND (
            COALESCE(mtg_home_elder_flag, '') != ''
            OR COALESCE(meets_in_state, '') != ''
            OR COALESCE(birthday, '') != ''
          )
        ORDER BY id
        """
    ).fetchall()
    manifest_by_id: dict[int, dict] = {}
    try:
        manifest_payload = _drive_load_json_file_content(access_token, storage["contacts_current_file_id"])
        manifest_by_id = {
            int(item.get("id") or 0): item
            for item in _shared_contact_manifest_entries(manifest_payload)
            if int(item.get("id") or 0) > 0
        }
    except (HTTPError, URLError, RuntimeError):
        manifest_by_id = {}

    queued = 0
    file_cache: dict[str, dict] = {}
    total_candidates = len(candidate_rows)
    for index, row in enumerate(candidate_rows, start=1):
        if progress_callback is not None and (index == 1 or index % 10 == 0 or index == total_candidates):
            progress_callback(index, total_candidates)
        contact_id = int(row["id"])
        manifest_item = manifest_by_id.get(contact_id)
        local_payload = _build_shared_contact_record_payload(conn, contact_id)
        if local_payload is None:
            continue
        local_contact = local_payload.get("contact") or {}
        manifest_digest = _clean((manifest_item or {}).get("content_digest"))
        if manifest_digest:
            local_digest = _clean(local_payload.get("content_digest")) or _shared_contact_payload_digest(
                local_payload,
                include_edit_logs=False,
            )
            if local_digest == manifest_digest:
                continue
        filename = _contact_record_drive_filename(contact_id)
        file_item = contact_record_files.get(filename)
        if file_item is None and manifest_item is not None:
            file_item = _drive_find_child(
                access_token,
                parent_id=storage["contact_records_folder_id"],
                name=_clean(manifest_item.get("file_name")) or filename,
                mime_type="application/json",
            )
            if file_item is not None:
                contact_record_files[filename] = file_item
        elif file_item is None:
            file_item = _drive_find_child(
                access_token,
                parent_id=storage["contact_records_folder_id"],
                name=filename,
                mime_type="application/json",
            )
            if file_item is not None:
                contact_record_files[filename] = file_item
        if file_item is None:
            conn.execute(
                """
                UPDATE contacts
                SET
                  shared_drive_needs_export = 1,
                  shared_drive_revision = CASE
                    WHEN shared_drive_revision > 0 THEN shared_drive_revision + 1
                    ELSE 1
                  END
                WHERE id = ?
                """,
                (contact_id,),
            )
            queued += 1
            continue
        try:
            if manifest_item is not None:
                remote_row = _load_shared_contact_row_from_drive(
                    access_token,
                    storage,
                    manifest_item,
                    file_cache=file_cache,
                )
            else:
                remote_payload = _drive_load_json_file_content(access_token, _clean(file_item.get("id")))
                remote_contact = remote_payload.get("contact") if isinstance(remote_payload, dict) else {}
                remote_row = remote_contact if isinstance(remote_contact, dict) else {}
        except HTTPError as exc:
            if exc.code != 404:
                raise
            remote_row = {}
        if not _shared_contact_local_app_fields_should_push(local_contact, remote_row):
            continue
        conn.execute(
            """
            UPDATE contacts
            SET
              shared_drive_needs_export = 1,
              shared_drive_revision = CASE
                WHEN shared_drive_revision > 0 THEN shared_drive_revision + 1
                ELSE 1
              END
            WHERE id = ?
            """,
            (contact_id,),
        )
        queued += 1
    return queued


def _find_shared_contact_manifest_item(
    token: str,
    storage: dict,
    local_row: dict,
    manifest_entries: list[dict],
    *,
    file_cache: dict[str, dict] | None = None,
) -> dict | None:
    contact_id = int(local_row.get("id") or 0)
    if contact_id > 0:
        matched = next(
            (item for item in manifest_entries if int(item.get("id") or 0) == contact_id),
            None,
        )
        if matched is not None:
            return matched

    family = _clean(local_row.get("family_name")).lower()
    given = _clean(local_row.get("given_name")).lower()
    if family or given:
        matched = next(
            (
                item
                for item in manifest_entries
                if (
                    _clean(item.get("family_name")).lower() == family
                    and _clean(item.get("given_name")).lower() == given
                )
            ),
            None,
        )
        if matched is not None:
            return matched

    local_google_contact_id = _clean(local_row.get("google_contact_id"))
    if not local_google_contact_id:
        return None
    for item in manifest_entries:
        remote_row = _load_shared_contact_row_from_drive(
            token,
            storage,
            item,
            file_cache=file_cache,
        )
        if _clean(remote_row.get("google_contact_id")) == local_google_contact_id:
            return item
    return None


def sync_mobile_contact_app_fields_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
) -> int:
    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token)
    payload = _drive_load_json_file_content(token, resolved_storage["contacts_current_file_id"])
    manifest_entries = _shared_contact_manifest_entries(payload)
    if not manifest_entries:
        return 0

    with get_connection() as conn:
        local_rows = {
            (
                _clean(row["family_name"]).lower(),
                _clean(row["given_name"]).lower(),
            ): dict(row)
            for row in conn.execute(
                """
                SELECT
                  id,
                  google_contact_id,
                  family_name,
                  given_name,
                  mtg_home_elder_flag,
                  meets_in_state,
                  birthday,
                  photo,
                  photo_drive_file_id,
                  photo_sync_revision,
                  shared_drive_revision
                FROM contacts
                """
            ).fetchall()
        }

    updated = 0
    file_cache: dict[str, dict] = {}
    for item in manifest_entries:
        key = (_clean(item.get("family_name")).lower(), _clean(item.get("given_name")).lower())
        local_row = local_rows.get(key)
        if not local_row:
            continue
        remote_revision = int(item.get("shared_drive_revision") or 0)
        local_revision = int(local_row.get("shared_drive_revision") or 0)
        if local_revision > remote_revision:
            continue
        remote_row = _load_shared_contact_row_from_drive(token, resolved_storage, item, file_cache=file_cache)
        if not remote_row:
            continue
        if not _shared_contact_app_fields_stale(local_row, remote_row, remote_revision):
            continue
        if _apply_shared_contact_app_fields(
            local_row,
            remote_row,
            remote_revision,
            access_token=token,
        ):
            updated += 1
    return updated


def sync_mobile_contact_app_fields_for_contact(
    contact_id: int,
    *,
    access_token: str | None = None,
    storage: dict | None = None,
) -> bool:
    with get_connection() as conn:
        local_row = conn.execute(
            """
            SELECT
              id,
              google_contact_id,
              family_name,
              given_name,
              mtg_home_elder_flag,
              meets_in_state,
              birthday,
              photo,
              photo_drive_file_id,
              photo_sync_revision,
              shared_drive_revision
            FROM contacts
            WHERE id = ?
            """,
            (int(contact_id),),
        ).fetchone()
    if not local_row:
        return False
    local_row = dict(local_row)

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token, contact_only=True)
    payload = _drive_load_contacts_manifest_cached(token, resolved_storage)
    manifest_entries = _shared_contact_manifest_entries(payload)
    if not manifest_entries:
        return False

    file_cache: dict[str, dict] = {}
    manifest_item = _find_shared_contact_manifest_item(
        token,
        resolved_storage,
        local_row,
        manifest_entries,
        file_cache=file_cache,
    )
    if not manifest_item:
        return False
    remote_revision = int(manifest_item.get("shared_drive_revision") or 0)
    if int(local_row.get("shared_drive_revision") or 0) > remote_revision:
        return False
    remote_row = _load_shared_contact_row_from_drive(
        token,
        resolved_storage,
        manifest_item,
        file_cache=file_cache,
    )
    if not remote_row:
        return False
    if not _shared_contact_app_fields_stale(local_row, remote_row, remote_revision):
        return False
    return _apply_shared_contact_app_fields(
        local_row,
        remote_row,
        remote_revision,
        access_token=token,
    )


def sync_pending_contact_photo_from_drive_for_editor(
    contact_id: int,
    *,
    expected_photo_path: str = "",
) -> bool:
    """Download a pending contact photo from Drive so an Editor can apply it locally."""
    expected_photo_path = _clean_contact_photo_path(expected_photo_path)
    try:
        token, _account_email = _get_valid_access_token()
        storage = _ensure_shared_drive_storage(token)
    except RuntimeError:
        return False

    try:
        sync_mobile_contact_app_fields_for_contact(contact_id, access_token=token, storage=storage)
    except (HTTPError, URLError, RuntimeError):
        pass

    if expected_photo_path:
        local_path = _contact_photo_local_path(expected_photo_path)
        if local_path is not None and local_path.is_file():
            return True

    remote_photo = ""
    remote_drive_id = ""
    remote_revision = 0
    shared_drive_revision = 0

    filename = _contact_record_drive_filename(int(contact_id))
    try:
        file_item = _drive_find_child(
            token,
            parent_id=storage["contact_records_folder_id"],
            name=filename,
            mime_type="application/json",
        )
        if file_item is not None:
            remote_payload = _drive_load_json_file_content(token, _clean(file_item.get("id")))
            remote_row = remote_payload.get("contact") if isinstance(remote_payload, dict) else None
            if isinstance(remote_row, dict) and _remote_has_custom_drive_photo(remote_row):
                remote_photo = _clean_contact_photo_path(remote_row.get("photo"))
                remote_drive_id = _clean(remote_row.get("photo_drive_file_id"))
                remote_revision = int(remote_row.get("photo_sync_revision") or 0)
                shared_drive_revision = int(remote_row.get("shared_drive_revision") or 0)
    except (HTTPError, URLError, RuntimeError):
        pass

    if not remote_drive_id:
        remote_drive_id, _drive_name = _find_contact_photo_file_on_drive(
            token,
            storage["contact_photos_folder_id"],
            contact_id,
            photo_path=expected_photo_path,
        )
        if remote_drive_id and not remote_photo:
            remote_photo = expected_photo_path

    if not remote_drive_id:
        return False

    target_photo_path = expected_photo_path or remote_photo
    if not target_photo_path:
        return False
    if not _download_shared_contact_photo_from_drive(token, target_photo_path, remote_drive_id):
        return False

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE contacts
            SET
              photo = ?,
              photo_drive_file_id = ?,
              photo_sync_revision = ?,
              photo_needs_export = 0,
              shared_drive_revision = CASE
                WHEN ? > COALESCE(shared_drive_revision, 0) THEN ?
                ELSE shared_drive_revision
              END
            WHERE id = ?
            """,
            (
                target_photo_path,
                remote_drive_id,
                remote_revision or 1,
                shared_drive_revision,
                shared_drive_revision,
                int(contact_id),
            ),
        )
        conn.commit()

    local_path = _contact_photo_local_path(target_photo_path)
    return local_path is not None and local_path.is_file()


def import_shared_meetingdata_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
    export_if_missing: bool = True,
) -> bool:
    from app.services.backup_service import restore_meetingdata_backup_payload

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token)
    sync_state = remote_sync_state or _load_shared_sync_state(token, resolved_storage)
    summary = get_google_sync_summary()
    local_meetingdata_revision = _normalize_sync_revision((summary.get("state") or {}).get("meetingdata_sync_revision"))
    remote_meetingdata_revision = _normalize_sync_revision(sync_state.get("meetingdata_sync_revision"))
    if remote_meetingdata_revision > 0 and remote_meetingdata_revision == local_meetingdata_revision:
        return True
    payload = _drive_load_json_file_content(token, resolved_storage["meetingdata_current_file_id"])
    if not isinstance(payload, dict) or not payload.get("tables"):
        if local_meetingdata_revision <= 0:
            with get_connection() as conn:
                _clear_local_meetingdata_data(conn)
                conn.execute(
                    """
                    UPDATE google_sync_state
                    SET meetingdata_needs_drive_export = 0, updated_at = ?
                    WHERE id = 1
                    """,
                    (_now_text(),),
                )
                conn.commit()
        if export_if_missing:
            export_shared_meetingdata_to_google_drive()
        return False
    if remote_meetingdata_revision <= 0 and local_meetingdata_revision <= 0:
        with get_connection() as conn:
            _clear_local_meetingdata_data(conn)
            conn.execute(
                """
                UPDATE google_sync_state
                SET meetingdata_needs_drive_export = 0, updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )
            conn.commit()
        if export_if_missing:
            export_shared_meetingdata_to_google_drive()
        return False
    restore_meetingdata_backup_payload(payload)
    if remote_meetingdata_revision > 0:
        _store_local_sync_revisions(meetingdata_sync_revision=remote_meetingdata_revision)
    return True


def export_shared_settings_to_google_drive() -> None:
    summary = get_google_sync_summary()
    state = summary.get("state") or {}
    account_locked = bool(state.get("multi_editor_account_locked") or state.get("multi_editor_enabled"))
    with get_connection() as conn:
        _ensure_editor_secret_configured(conn)
        secret_row = conn.execute(
            """
            SELECT editor_secret_hash, editor_secret_salt
            FROM google_sync_state
            WHERE id = 1
            """
        ).fetchone()
        conn.commit()
        secret_settings = dict(secret_row) if secret_row else {}
    access_token, _account_email = _get_valid_access_token()
    storage = _ensure_shared_settings_drive_storage(access_token)
    remote_sync_state = _load_shared_sync_state(access_token, storage)
    next_settings_revision, remote_sync_state = _next_dataset_sync_revision(
        access_token,
        storage,
        revision_key="settings_sync_revision",
        local_revision=_normalize_sync_revision(state.get("settings_sync_revision")),
        remote_sync_state=remote_sync_state,
    )
    payload = {
        "format": "contactsfreeshare.settings.v1",
        "exported_at": _iso_now(),
        "settings": {
            "multi_editor_enabled": bool(state.get("multi_editor_enabled")),
            "multi_editor_account_locked": account_locked,
            "editor_lock_timeout_minutes": _normalize_editor_timeout_minutes(
                state.get("editor_lock_timeout_minutes")
            ),
            "editor_secret_hash": _clean(secret_settings.get("editor_secret_hash")),
            "editor_secret_salt": _clean(secret_settings.get("editor_secret_salt")),
            "public_web_url": get_public_web_url(),
            "share_web_api_key": get_share_web_api_key(),
            "sync_enabled": bool(state.get("sync_enabled")),
            "address_book_pdf_share_enabled": bool(state.get("address_book_pdf_share_enabled")),
            "address_book_pdf_share_folder_id": str(state.get("address_book_pdf_share_folder_id") or ""),
            "address_book_pdf_share_folder_name": str(state.get("address_book_pdf_share_folder_name") or ""),
        },
    }
    _drive_update_json_file_content(access_token, storage["settings_current_file_id"], payload)
    remote_sync_state["settings_sync_revision"] = next_settings_revision
    _write_shared_sync_state(access_token, storage, remote_sync_state)
    _store_local_sync_revisions(settings_sync_revision=next_settings_revision)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    with get_connection() as conn:
        _clear_pending_drive_export(conn)
        conn.commit()


def export_book_layouts_to_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
) -> None:
    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_book_layouts_drive_storage(token)
    state = get_google_sync_summary().get("state") or {}
    next_revision, sync_state = _next_dataset_sync_revision(
        token,
        resolved_storage,
        revision_key="book_layouts_sync_revision",
        local_revision=_normalize_sync_revision(state.get("book_layouts_sync_revision")),
        remote_sync_state=remote_sync_state,
    )
    payload = {
        "format": "contactsfreeshare.book_layouts.v1",
        "exported_at": _iso_now(),
        "book_layout_presets": _export_book_layout_presets_payload(),
    }
    _drive_update_json_file_content(token, resolved_storage["book_layouts_current_file_id"], payload)
    sync_state["book_layouts_sync_revision"] = next_revision
    _write_shared_sync_state(token, resolved_storage, sync_state)
    _store_local_sync_revisions(book_layouts_sync_revision=next_revision)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    with get_connection() as conn:
        _clear_pending_book_layouts_drive_export(conn)
        conn.commit()


def _export_book_layout_presets_payload() -> list[dict]:
    ensure_google_sync_records()
    from app.services.meeting_v2_service import ensure_default_book_layout_preset

    ensure_default_book_layout_preset()
    columns_sql = ", ".join(BOOK_LAYOUT_SETTING_COLUMNS)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {columns_sql}
            FROM book_layout_presets
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _coerce_book_layout_preset_payload(raw_preset: object) -> dict | None:
    if not isinstance(raw_preset, dict):
        return None
    cleaned = {column: raw_preset.get(column) for column in BOOK_LAYOUT_SETTING_COLUMNS}
    try:
        cleaned["id"] = int(cleaned.get("id") or 0)
    except (TypeError, ValueError):
        cleaned["id"] = 0
    cleaned["name"] = _clean(cleaned.get("name")) or "Pocket Address Book"
    cleaned["book_title"] = _clean(cleaned.get("book_title")) or "Address Book"
    cleaned["font_family"] = _clean(cleaned.get("font_family")) or "Arial"
    for column in (
        "trim_width_in",
        "trim_height_in",
        "margin_left_in",
        "margin_right_in",
        "margin_top_in",
        "margin_bottom_in",
        "base_font_size_pt",
        "title_font_size_pt",
        "toc_font_size_pt",
        "index_font_size_pt",
        "line_height",
        "screen_preview_scale",
    ):
        try:
            cleaned[column] = float(cleaned.get(column))
        except (TypeError, ValueError):
            cleaned[column] = 0.0
    for column in ("meeting_table_column_count", "meeting_table_column_gap_px", "is_default"):
        try:
            cleaned[column] = int(cleaned.get(column) or 0)
        except (TypeError, ValueError):
            cleaned[column] = 0
    for column, fallback in (
        ("meetingdata_title_bar_color", "#e3eff3"),
        ("meetingdata_primary_row_color", "#f8f3e3"),
        ("meetingdata_secondary_row_color", "#edf5f7"),
        ("meetingdata_highlight_row_color", "#ffe3a1"),
        ("contacts_title_bar_color", "#e3eff3"),
        ("contacts_primary_row_color", "#f8f3e3"),
        ("contacts_secondary_row_color", "#edf5f7"),
        ("contacts_highlight_row_color", "#ffe3a1"),
        ("shared_contacts_group_name", "TriState Separates (Shared)"),
    ):
        cleaned[column] = _clean(cleaned.get(column)) or fallback
    return cleaned if cleaned["id"] > 0 else None


def _import_book_layout_presets_payload(raw_presets: object) -> bool:
    if not isinstance(raw_presets, list):
        return False
    presets = [
        preset
        for preset in (_coerce_book_layout_preset_payload(item) for item in raw_presets)
        if preset is not None
    ]
    if not presets:
        return False

    update_columns = [column for column in BOOK_LAYOUT_SETTING_COLUMNS if column != "id"]
    insert_placeholders = ", ".join("?" for _ in BOOK_LAYOUT_SETTING_COLUMNS)
    update_assignments = ", ".join(f"{column} = ?" for column in update_columns)
    default_preset = next((preset for preset in presets if int(preset.get("is_default") or 0)), None)

    with get_connection() as conn:
        previous_shared_group_row = conn.execute(
            """
            SELECT shared_contacts_group_name
            FROM book_layout_presets
            WHERE is_default = 1
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        previous_shared_group_name = _clean(
            previous_shared_group_row["shared_contacts_group_name"] if previous_shared_group_row else ""
        )
        for preset in presets:
            existing_by_id = conn.execute(
                f"""
                SELECT id, {", ".join(BOOK_LAYOUT_COLOR_COLUMNS)}
                FROM book_layout_presets
                WHERE id = ?
                """,
                (preset["id"],),
            ).fetchone()
            if existing_by_id:
                merged_preset = dict(preset)
                for column in BOOK_LAYOUT_COLOR_COLUMNS:
                    merged_preset[column] = existing_by_id[column]
                conn.execute(
                    f"""
                    UPDATE book_layout_presets
                    SET {update_assignments}
                    WHERE id = ?
                    """,
                    tuple(merged_preset[column] for column in update_columns) + (preset["id"],),
                )
                continue

            existing_by_name = conn.execute(
                f"""
                SELECT id, {", ".join(BOOK_LAYOUT_COLOR_COLUMNS)}
                FROM book_layout_presets
                WHERE name = ?
                """,
                (preset["name"],),
            ).fetchone()
            if existing_by_name:
                merged_preset = dict(preset)
                for column in BOOK_LAYOUT_COLOR_COLUMNS:
                    merged_preset[column] = existing_by_name[column]
                conn.execute(
                    f"""
                    UPDATE book_layout_presets
                    SET {update_assignments}
                    WHERE id = ?
                    """,
                    tuple(merged_preset[column] for column in update_columns) + (int(existing_by_name["id"]),),
                )
                continue

            conn.execute(
                f"""
                INSERT INTO book_layout_presets ({", ".join(BOOK_LAYOUT_SETTING_COLUMNS)})
                VALUES ({insert_placeholders})
                """,
                tuple(preset[column] for column in BOOK_LAYOUT_SETTING_COLUMNS),
            )
        if default_preset:
            conn.execute("UPDATE book_layout_presets SET is_default = 0")
            updated_default = conn.execute(
                """
                UPDATE book_layout_presets
                SET is_default = 1
                WHERE id = ?
                """,
                (default_preset["id"],),
            ).rowcount
            if not updated_default:
                conn.execute(
                    """
                    UPDATE book_layout_presets
                    SET is_default = 1
                    WHERE name = ?
                    """,
                    (default_preset["name"],),
                )
        else:
            conn.execute(
                """
                UPDATE book_layout_presets
                SET is_default = CASE
                  WHEN id = (SELECT id FROM book_layout_presets ORDER BY id LIMIT 1) THEN 1
                  ELSE 0
                END
                """
            )
        _keep_renamed_shared_group_name(conn, previous_shared_group_name)
        conn.commit()
    return True


def _keep_renamed_shared_group_name(conn, previous_name: str) -> None:
    """Do not let a Drive layout that still says TriState Separates erase a rename.

    The catch-all group name is one setting. Copy the kept name onto every
    layout so a different default preset cannot show the old label.
    """
    legacy_name = "TriState Separates (Shared)"
    row = conn.execute(
        """
        SELECT shared_contacts_group_name
        FROM book_layout_presets
        WHERE is_default = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    imported_name = _clean(row["shared_contacts_group_name"] if row else "")
    previous = _clean(previous_name)
    if imported_name and imported_name != legacy_name:
        chosen = imported_name
    elif previous and previous != legacy_name:
        chosen = previous
    else:
        chosen = imported_name or previous or legacy_name
    conn.execute(
        "UPDATE book_layout_presets SET shared_contacts_group_name = ?",
        (chosen,),
    )


def import_shared_settings_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
    export_if_missing: bool = True,
) -> bool:
    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_settings_drive_storage(token)
    sync_state = remote_sync_state or _load_shared_sync_state(token, resolved_storage)
    summary = get_google_sync_summary()
    local_state = summary.get("state") or {}
    local_settings_revision = _normalize_sync_revision(local_state.get("settings_sync_revision"))
    remote_settings_revision = _normalize_sync_revision(sync_state.get("settings_sync_revision"))
    if (
        remote_settings_revision > 0
        and remote_settings_revision == local_settings_revision
        and bool(local_state.get("multi_editor_enabled") or local_state.get("multi_editor_account_locked"))
    ):
        # Dataset revision already matches — skip re-downloading settings on every sign-in.
        return True
    payload = _drive_load_json_file_content(token, resolved_storage["settings_current_file_id"])
    settings = payload.get("settings") if isinstance(payload, dict) else None
    if not isinstance(settings, dict):
        if export_if_missing:
            export_shared_settings_to_google_drive()
        return False
    locked = bool(settings.get("multi_editor_account_locked") or settings.get("multi_editor_enabled"))
    enabled = bool(settings.get("multi_editor_enabled") or locked)
    timeout_minutes = _normalize_editor_timeout_minutes(settings.get("editor_lock_timeout_minutes"))
    remote_secret_hash = _clean(settings.get("editor_secret_hash"))
    remote_secret_salt = _clean(settings.get("editor_secret_salt"))
    ensure_google_sync_records()
    with get_connection() as conn:
        if remote_secret_hash and remote_secret_salt:
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  multi_editor_enabled = ?,
                  multi_editor_account_locked = ?,
                  editor_lock_timeout_minutes = ?,
                  editor_mode = CASE WHEN ? = 1 AND editor_mode != 'edit' THEN 'no_edit' ELSE editor_mode END,
                  editor_secret_hash = ?,
                  editor_secret_salt = ?,
                  settings_sync_revision = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (
                    1 if enabled else 0,
                    1 if locked else 0,
                    timeout_minutes,
                    1 if enabled else 0,
                    remote_secret_hash,
                    remote_secret_salt,
                    remote_settings_revision,
                    _now_text(),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  multi_editor_enabled = ?,
                  multi_editor_account_locked = ?,
                  editor_lock_timeout_minutes = ?,
                  editor_mode = CASE WHEN ? = 1 AND editor_mode != 'edit' THEN 'no_edit' ELSE editor_mode END,
                  settings_sync_revision = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (
                    1 if enabled else 0,
                    1 if locked else 0,
                    timeout_minutes,
                    1 if enabled else 0,
                    remote_settings_revision,
                    _now_text(),
                ),
            )
            _ensure_editor_secret_configured(conn)
        conn.commit()
    _apply_remote_share_connection_settings(settings)
    _apply_remote_pdf_share_settings(settings)
    _import_book_layout_presets_payload(payload.get("book_layout_presets"))
    return True


def import_book_layouts_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
    export_if_missing: bool = True,
) -> bool:
    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_book_layouts_drive_storage(token)
    sync_state = remote_sync_state or _load_shared_sync_state(token, resolved_storage)
    summary = get_google_sync_summary()
    local_revision = _normalize_sync_revision((summary.get("state") or {}).get("book_layouts_sync_revision"))
    remote_revision = _normalize_sync_revision(sync_state.get("book_layouts_sync_revision"))
    if remote_revision > 0 and remote_revision == local_revision:
        return True
    if remote_revision <= 0:
        if export_if_missing:
            export_book_layouts_to_google_drive(
                access_token=token,
                storage=resolved_storage,
                remote_sync_state=sync_state,
            )
        return False
    payload = _drive_load_json_file_content(token, resolved_storage["book_layouts_current_file_id"])
    presets = payload.get("book_layout_presets") if isinstance(payload, dict) else None
    if not _import_book_layout_presets_payload(presets):
        return False
    _store_local_sync_revisions(book_layouts_sync_revision=remote_revision)
    with get_connection() as conn:
        _clear_pending_book_layouts_drive_export(conn)
        conn.commit()
    return True


def export_field_list_to_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
) -> None:
    from app.services.field_list_service import export_field_list_payload

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token)
    state = get_google_sync_summary().get("state") or {}
    next_revision, sync_state = _next_dataset_sync_revision(
        token,
        resolved_storage,
        revision_key="field_list_sync_revision",
        local_revision=_normalize_sync_revision(state.get("field_list_sync_revision")),
        remote_sync_state=remote_sync_state,
    )
    payload = {
        **export_field_list_payload(),
        "exported_at": _iso_now(),
    }
    _drive_update_json_file_content(token, resolved_storage["field_list_current_file_id"], payload)
    sync_state["field_list_sync_revision"] = next_revision
    _write_shared_sync_state(token, resolved_storage, sync_state)
    _store_local_sync_revisions(field_list_sync_revision=next_revision)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")


def _local_print_order_json() -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT print_order_json FROM address_book_settings WHERE id = 1").fetchone()
    return str(row["print_order_json"] or "").strip() if row else ""


def import_field_list_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
    export_if_missing: bool = True,
) -> bool:
    from app.services.field_list_service import import_field_list_payload

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token)
    sync_state = remote_sync_state or _load_shared_sync_state(token, resolved_storage)
    summary = get_google_sync_summary()
    local_revision = _normalize_sync_revision((summary.get("state") or {}).get("field_list_sync_revision"))
    remote_revision = _normalize_sync_revision(sync_state.get("field_list_sync_revision"))
    local_print_order_json = _local_print_order_json()
    if remote_revision > 0 and remote_revision == local_revision and local_print_order_json:
        return True
    if remote_revision <= 0:
        if export_if_missing:
            export_field_list_to_google_drive(
                access_token=token,
                storage=resolved_storage,
                remote_sync_state=sync_state,
            )
        return False
    payload = _drive_load_json_file_content(token, resolved_storage["field_list_current_file_id"])
    if not import_field_list_payload(payload):
        return False
    _store_local_sync_revisions(field_list_sync_revision=remote_revision)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET field_list_needs_drive_export = 0, updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()
    return True


def import_print_role_shared_data_from_google_drive() -> None:
    """Pull print datasets for contributor / Field List / Address Book roles.

    These roles do not run the Editor sign-in sync job, so Meeting Data and
    book layouts would otherwise stay empty on a contributor computer.
    Never write empty local copies back to Drive.
    """
    import_book_layouts_from_google_drive(export_if_missing=False)
    import_shared_meetingdata_from_google_drive(export_if_missing=False)
    import_field_list_from_google_drive(export_if_missing=False)
    import_changes_list_from_google_drive()


def export_pending_edits_contact_assets_to_google_drive(*, access_token: str | None = None) -> None:
    """Upload only pending photos and their contact records for Pending Edits workflows."""
    token = access_token or _get_valid_access_token()[0]
    storage = _ensure_shared_drive_storage(token, contact_only=True)
    remote_sync_state = _load_shared_sync_state(token, storage)

    with get_connection() as conn:
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        local_contacts_revision = _normalize_sync_revision(state.get("contacts_sync_revision"))
        pending_photo_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM contacts WHERE photo_needs_export = 1 ORDER BY id"
            ).fetchall()
        ]

    contact_ids_to_export = pending_photo_ids
    if not contact_ids_to_export:
        return

    if pending_photo_ids:
        _sync_pending_contact_photos_to_google_drive(token, storage["contact_photos_folder_id"])

    existing_manifest = _drive_load_json_file_content(token, storage["contacts_current_file_id"])
    if str(existing_manifest.get("format") or "").strip() != "contactsfreeshare.shared_contacts.v2":
        raise RuntimeError(
            "Shared contacts are not set up on Google Drive yet. "
            "Ask your Editor to run Upload once from the desktop app, then upload Pending Edits again."
        )

    contact_record_files = _drive_children_by_name(
        token,
        parent_id=storage["contact_records_folder_id"],
    )
    uploaded_contact_ids: list[int] = []
    with get_connection() as conn:
        for contact_id in contact_ids_to_export:
            payload = _build_shared_contact_record_payload(conn, contact_id)
            if payload is None:
                continue
            filename = _contact_record_drive_filename(contact_id)
            file_item = contact_record_files.get(filename)
            if file_item is None:
                file_item = _drive_create_json_file_with_content(
                    token,
                    name=filename,
                    parent_id=storage["contact_records_folder_id"],
                    payload=payload,
                )
                contact_record_files[filename] = file_item
            else:
                _drive_update_json_file_content(token, _clean(file_item.get("id")), payload)
            uploaded_contact_ids.append(contact_id)
            conn.execute(
                """
                UPDATE contacts
                SET
                  shared_drive_needs_export = 0,
                  shared_drive_bootstrapped = 1
                WHERE id = ?
                """,
                (contact_id,),
            )

        if uploaded_contact_ids:
            manifest_payload = _patch_shared_contacts_manifest(
                existing_manifest,
                conn,
                uploaded_contact_ids,
            )
            _drive_update_json_file_content(token, storage["contacts_current_file_id"], manifest_payload)
            next_contacts_revision, remote_sync_state = _next_dataset_sync_revision(
                token,
                storage,
                revision_key="contacts_sync_revision",
                local_revision=local_contacts_revision,
                remote_sync_state=remote_sync_state,
            )
            remote_sync_state["contacts_sync_revision"] = next_contacts_revision
            _write_shared_sync_state(token, storage, remote_sync_state)
            _store_local_sync_revisions(conn=conn, contacts_sync_revision=next_contacts_revision)
        conn.commit()

    if not uploaded_contact_ids:
        raise RuntimeError("Pending contact photos could not be uploaded to Google Drive.")


def export_mobile_pending_shared_contacts_to_google_drive(
    *,
    max_contacts: int = 25,
    access_token: str | None = None,
    storage: dict | None = None,
) -> int:
    """Upload only locally dirty shared contact records for mobile Editor sync."""
    limit = max(1, int(max_contacts))
    with get_connection() as conn:
        pending_rows = conn.execute(
            """
            SELECT id
            FROM contacts
            WHERE shared_drive_needs_export = 1 OR photo_needs_export = 1
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        pending_contact_ids = [int(row["id"]) for row in pending_rows]
        if not pending_contact_ids:
            return 0
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        local_contacts_revision = _normalize_sync_revision(state.get("contacts_sync_revision"))

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token, contact_only=True)
    remote_sync_state = _load_shared_sync_state(token, resolved_storage)

    placeholders = ",".join("?" for _ in pending_contact_ids)
    with get_connection() as conn:
        pending_photo_ids = [
            int(row["id"])
            for row in conn.execute(
                f"""
                SELECT id
                FROM contacts
                WHERE photo_needs_export = 1
                  AND id IN ({placeholders})
                ORDER BY id
                """,
                tuple(pending_contact_ids),
            ).fetchall()
        ]
    if pending_photo_ids:
        _sync_pending_contact_photos_to_google_drive(token, resolved_storage["contact_photos_folder_id"])

    existing_manifest = _drive_load_json_file_content(token, resolved_storage["contacts_current_file_id"])
    if str(existing_manifest.get("format") or "").strip() != "contactsfreeshare.shared_contacts.v2":
        return 0

    contact_record_files: dict[str, dict] = {}
    uploaded_contact_ids: list[int] = []
    with get_connection() as conn:
        for contact_id in pending_contact_ids:
            payload = _build_shared_contact_record_payload(conn, contact_id)
            if payload is None:
                continue
            filename = _contact_record_drive_filename(contact_id)
            file_item = contact_record_files.get(filename)
            if file_item is None:
                file_item = _drive_find_child(
                    token,
                    parent_id=resolved_storage["contact_records_folder_id"],
                    name=filename,
                    mime_type="application/json",
                )
                if file_item is not None:
                    contact_record_files[filename] = file_item
            if file_item is None:
                file_item = _drive_create_json_file_with_content(
                    token,
                    name=filename,
                    parent_id=resolved_storage["contact_records_folder_id"],
                    payload=payload,
                )
                contact_record_files[filename] = file_item
            else:
                _drive_update_json_file_content(token, _clean(file_item.get("id")), payload)
            uploaded_contact_ids.append(contact_id)
            conn.execute(
                """
                UPDATE contacts
                SET
                  shared_drive_needs_export = 0,
                  shared_drive_bootstrapped = 1
                WHERE id = ?
                """,
                (contact_id,),
            )

        if uploaded_contact_ids:
            manifest_payload = _patch_shared_contacts_manifest(
                existing_manifest,
                conn,
                uploaded_contact_ids,
            )
            _drive_update_json_file_content(token, resolved_storage["contacts_current_file_id"], manifest_payload)
            next_contacts_revision, remote_sync_state = _next_dataset_sync_revision(
                token,
                resolved_storage,
                revision_key="contacts_sync_revision",
                local_revision=local_contacts_revision,
                remote_sync_state=remote_sync_state,
            )
            remote_sync_state["contacts_sync_revision"] = next_contacts_revision
            _write_shared_sync_state(token, resolved_storage, remote_sync_state)
            _store_local_sync_revisions(conn=conn, contacts_sync_revision=next_contacts_revision)
        conn.commit()
    return len(uploaded_contact_ids)


def sync_mobile_editor_contact_changes() -> dict:
    """Two-way mobile Editor sync: upload local edits, then pull Google changes.

    Upload first so phone edits win over stale Google copies; then download so
    contacts created on another device (laptop) appear on the phone. Contacts
    with pending local edits are skipped on download (conflict-safe).
    """
    ensure_google_sync_records()
    with get_connection() as conn:
        pending_upload = _read_pending_upload_count(conn)
        pending_drive_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM contacts
            WHERE shared_drive_needs_export = 1 OR photo_needs_export = 1
            """
        ).fetchone()
        pending_drive = int(pending_drive_row["count"] or 0) if pending_drive_row else 0
        token_row = conn.execute(
            "SELECT google_contacts_sync_token FROM google_sync_state WHERE id = 1"
        ).fetchone()
        has_sync_token = bool(
            _clean(token_row["google_contacts_sync_token"] if token_row else "")
        )

    # Defer Share Contacts push until after Drive export (same pattern as desktop Upload).
    # Previously skip_share_sync=True left recipient accounts stale after mobile Editor sync.
    deferred_share_sync: dict = {}
    contact_upload_count = 0
    if pending_upload > 0:
        contact_upload_count = upload_google_contacts(
            finish_progress=True,
            skip_share_sync=False,
            defer_share_sync_into=deferred_share_sync,
        )

    drive_exported = 0
    if pending_drive > 0 or contact_upload_count > 0:
        access_token, _account_email = _get_valid_access_token()
        storage = _ensure_shared_drive_storage(access_token, contact_only=True)
        drive_exported = export_mobile_pending_shared_contacts_to_google_drive(
            access_token=access_token,
            storage=storage,
        )

    person_ids = list(deferred_share_sync.get("person_ids") or [])
    account_email = str(deferred_share_sync.get("account_email") or "").strip()
    if person_ids and account_email:
        from app.services.share_sync_service import sync_share_app_contact_changes

        try:
            share_result = sync_share_app_contact_changes(account_email, person_ids)
        except Exception as exc:
            share_result = {"ok": False, "reason": _clean(str(exc)) or "share_sync_failed"}
        record_share_sync_result(account_email, person_ids, share_result)

    download_result = sync_google_contact_changes(
        force_full_fetch=not has_sync_token,
        skip_drive_reconcile=True,
    )
    return {
        **download_result,
        "contact_upload_count": contact_upload_count,
        "upload_processed_count": contact_upload_count,
        "drive_exported": drive_exported,
        "drive_field_updates": 0,
    }


def export_pending_edits_support_to_google_drive(*, access_token: str | None = None) -> None:
    """Upload pending contact photos and dirty contact records for Pending Edits workflows."""
    export_pending_edits_contact_assets_to_google_drive(access_token=access_token)


_CHANGES_LIST_DRIVE_EXPORT_LOCK = threading.Lock()


def export_changes_list_to_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
    merge_remote: bool = True,
) -> None:
    from app.services.changes_list_service import (
        export_changes_list_payload,
        merge_changes_list_entry_lists,
    )

    token = access_token or _get_valid_access_token()[0]
    resolved_storage = storage or _ensure_shared_drive_storage(token)
    state = get_google_sync_summary().get("state") or {}
    next_revision, sync_state = _next_dataset_sync_revision(
        token,
        resolved_storage,
        revision_key="changes_list_sync_revision",
        local_revision=_normalize_sync_revision(state.get("changes_list_sync_revision")),
        remote_sync_state=remote_sync_state,
    )
    local_entries = export_changes_list_payload()
    # Merge remote rows into the Drive payload only. Do not rewrite the local SQLite
    # list — replace_changes_list_from_payload assigns new row ids and breaks the
    # open Pending Edits page (Completed Edit(s) stops updating after the first tap).
    if merge_remote:
        remote_payload = _drive_load_json_file_content(token, resolved_storage["changes_list_current_file_id"])
        if isinstance(remote_payload, dict) and isinstance(remote_payload.get("entries"), list):
            local_entries = merge_changes_list_entry_lists(remote_payload["entries"], local_entries)
    payload = {
        "format": "contactsfreeshare.changes_list.v1",
        "exported_at": _iso_now(),
        "entries": local_entries,
    }
    _drive_update_json_file_content(token, resolved_storage["changes_list_current_file_id"], payload)
    sync_state["changes_list_sync_revision"] = next_revision
    _write_shared_sync_state(token, resolved_storage, sync_state)
    _store_local_sync_revisions(changes_list_sync_revision=next_revision)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET changes_list_needs_drive_export = 0, updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()


def schedule_changes_list_drive_export(*, merge_remote: bool = True) -> None:
    """Push Pending Edits to Drive in the background so UI actions stay instant."""

    def _run() -> None:
        from app.config import ERROR_LOG_PATH
        from app.logging_utils import append_log_line

        with _CHANGES_LIST_DRIVE_EXPORT_LOCK:
            try:
                export_changes_list_to_google_drive(merge_remote=merge_remote)
            except Exception as exc:
                append_log_line(
                    ERROR_LOG_PATH,
                    f"Background Pending Edits Drive export failed: {exc}",
                )

    threading.Thread(target=_run, daemon=True, name="changes-list-drive-export").start()


def import_changes_list_from_google_drive(
    *,
    access_token: str | None = None,
    storage: dict | None = None,
    remote_sync_state: dict | None = None,
) -> bool:
    from app.services.changes_list_service import (
        changes_list_entries_equal,
        export_changes_list_payload,
        has_meaningful_changes_list_entries,
        merge_changes_list_entry_lists,
        reconcile_pending_edits_upload_flags,
        replace_changes_list_from_payload,
    )

    # Hold the export lock so a background Completed Edit(s) upload cannot race
    # with Download from Drive and leave a stale incomplete copy on Drive.
    with _CHANGES_LIST_DRIVE_EXPORT_LOCK:
        token = access_token or _get_valid_access_token()[0]
        resolved_storage = storage or _ensure_shared_drive_storage(token)
        sync_state = remote_sync_state or _load_shared_sync_state(token, resolved_storage)
        summary = get_google_sync_summary()
        state = summary.get("state") or {}

        # Completed Edit(s) writes locally first and exports to Drive in the background.
        # Download must flush that export first; otherwise Drive still has the white/incomplete
        # row and a remote-only replace clears the green completed state.
        if bool(state.get("changes_list_needs_drive_export")):
            try:
                export_changes_list_to_google_drive(
                    access_token=token,
                    storage=resolved_storage,
                    remote_sync_state=sync_state,
                    merge_remote=True,
                )
                sync_state = _load_shared_sync_state(token, resolved_storage)
            except Exception:
                pass

        summary = get_google_sync_summary()
        local_revision = _normalize_sync_revision((summary.get("state") or {}).get("changes_list_sync_revision"))
        remote_revision = _normalize_sync_revision(sync_state.get("changes_list_sync_revision"))
        remote_payload = _drive_load_json_file_content(token, resolved_storage["changes_list_current_file_id"])
        remote_entries = remote_payload.get("entries") if isinstance(remote_payload, dict) else None
        local_entries = export_changes_list_payload()
        # Prefer completed local rows over stale incomplete Drive copies, while still
        # picking up new remote Pending Edits from other editors/devices.
        merged_entries = merge_changes_list_entry_lists(remote_entries or [], local_entries)

        if remote_revision > 0 and remote_revision == local_revision:
            if changes_list_entries_equal(merged_entries, local_entries):
                # Content already matches (including completions). Re-apply for newest-first order
                # so CR/LF duplicates left by older merges are collapsed.
                replace_changes_list_from_payload(merged_entries)
                with get_connection() as conn:
                    reconcile_pending_edits_upload_flags(conn)
                    conn.commit()
                return True
        if remote_revision <= 0 and not has_meaningful_changes_list_entries(remote_entries) and not has_meaningful_changes_list_entries(local_entries):
            export_changes_list_to_google_drive(
                access_token=token,
                storage=resolved_storage,
                remote_sync_state=sync_state,
            )
            return False
        if not replace_changes_list_from_payload(merged_entries):
            return False
        if remote_revision > local_revision:
            _store_local_sync_revisions(changes_list_sync_revision=remote_revision)

        # If local Completed Edit(s) (or other local wins) are ahead of Drive, push them now.
        if not changes_list_entries_equal(merged_entries, remote_entries or []):
            try:
                export_changes_list_to_google_drive(
                    access_token=token,
                    storage=resolved_storage,
                    remote_sync_state=_load_shared_sync_state(token, resolved_storage),
                    merge_remote=False,
                )
            except Exception:
                from app.services.changes_list_service import mark_changes_list_for_drive_export

                with get_connection() as conn:
                    mark_changes_list_for_drive_export(conn)
                    conn.commit()
                return True

        with get_connection() as conn:
            conn.execute(
                """
                UPDATE google_sync_state
                SET changes_list_needs_drive_export = 0, updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )
            reconcile_pending_edits_upload_flags(conn)
            conn.commit()
        return True


def sync_shared_data_from_google_drive() -> None:
    access_token, _account_email = _get_valid_access_token()
    storage = _ensure_shared_drive_storage(access_token)
    remote_sync_state = _load_shared_sync_state(access_token, storage)
    import_shared_settings_from_google_drive(access_token=access_token, storage=storage, remote_sync_state=remote_sync_state)
    import_book_layouts_from_google_drive(access_token=access_token, storage=storage, remote_sync_state=remote_sync_state)
    import_shared_contacts_from_google_drive(access_token=access_token, storage=storage, remote_sync_state=remote_sync_state)
    import_shared_meetingdata_from_google_drive(access_token=access_token, storage=storage, remote_sync_state=remote_sync_state)
    import_field_list_from_google_drive(access_token=access_token, storage=storage, remote_sync_state=remote_sync_state)
    import_changes_list_from_google_drive(access_token=access_token, storage=storage, remote_sync_state=remote_sync_state)


def _run_signin_sync_job(*, skip_settings: bool = False, storage: dict | None = None) -> None:
    global _SIGNIN_SYNC_THREAD, _SIGNIN_SYNC_CONTACTS_CONTEXT
    _SIGNIN_SYNC_CONTACTS_CONTEXT = {}
    unlocked = False
    account_email = ""
    started_at = time.perf_counter()
    try:
        # Critical path: keep editing blocked until settings + contacts are ready.
        _touch_signin_sync("connecting", current_item="Connecting to Google Drive...")
        access_token, account_email = _get_valid_access_token()
        phase_started = time.perf_counter()
        resolved_storage = storage if isinstance(storage, dict) and storage.get("contacts_current_file_id") else None
        if resolved_storage is None:
            resolved_storage = _ensure_shared_drive_storage(access_token)
        _LOGGER.info(
            "Sign-in sync storage ready in %.2fs (reused=%s)",
            time.perf_counter() - phase_started,
            bool(storage and storage.get("contacts_current_file_id")),
        )
        remote_sync_state = _load_shared_sync_state(access_token, resolved_storage)

        if not skip_settings:
            with get_connection() as conn:
                _set_signin_sync_state(conn, in_progress=True, phase="shared_settings", error="")
                conn.commit()
            phase_started = time.perf_counter()
            import_shared_settings_from_google_drive(
                access_token=access_token,
                storage=resolved_storage,
                remote_sync_state=remote_sync_state,
            )
            _LOGGER.info("Sign-in sync settings phase in %.2fs", time.perf_counter() - phase_started)
        with get_connection() as conn:
            _set_signin_sync_state(conn, in_progress=True, phase="contacts", error="")
            conn.commit()
        phase_started = time.perf_counter()
        import_shared_contacts_from_google_drive(
            access_token=access_token,
            storage=resolved_storage,
            remote_sync_state=remote_sync_state,
            progress_callback=_touch_signin_sync,
            backfill_manifest_digests=False,
        )
        _LOGGER.info("Sign-in sync contacts phase in %.2fs", time.perf_counter() - phase_started)

        # Unlock the UI now. Remaining Drive imports continue in the background.
        with get_connection() as conn:
            _set_signin_sync_state(conn, in_progress=False, phase="", error="")
            conn.commit()
        unlocked = True
        _LOGGER.info(
            "Sign-in sync unlocked editing after %.2fs",
            time.perf_counter() - started_at,
        )

        deferred_phases = (
            (
                "book_layouts",
                lambda: import_book_layouts_from_google_drive(
                    access_token=access_token,
                    storage=resolved_storage,
                    remote_sync_state=remote_sync_state,
                ),
            ),
            (
                "meetingdata",
                lambda: import_shared_meetingdata_from_google_drive(
                    access_token=access_token,
                    storage=resolved_storage,
                    remote_sync_state=remote_sync_state,
                ),
            ),
            (
                "field_list",
                lambda: import_field_list_from_google_drive(
                    access_token=access_token,
                    storage=resolved_storage,
                    remote_sync_state=remote_sync_state,
                ),
            ),
            (
                "changes_list",
                lambda: import_changes_list_from_google_drive(
                    access_token=access_token,
                    storage=resolved_storage,
                    remote_sync_state=remote_sync_state,
                ),
            ),
        )
        for phase_name, importer in deferred_phases:
            try:
                phase_started = time.perf_counter()
                importer()
                _LOGGER.info(
                    "Background sign-in sync phase %s in %.2fs",
                    phase_name,
                    time.perf_counter() - phase_started,
                )
            except Exception as exc:
                _LOGGER.warning(
                    "Background sign-in sync phase %s failed: %s",
                    phase_name,
                    _clean(str(exc)) or exc.__class__.__name__,
                )

        with get_connection() as conn:
            conn.execute(
                """
                UPDATE google_sync_state
                SET last_import_at = ?, last_import_account_email = ?, updated_at = ?
                WHERE id = 1
                """,
                (_now_text(), account_email, _now_text()),
            )
            conn.commit()
        _LOGGER.info(
            "Sign-in sync finished all phases in %.2fs",
            time.perf_counter() - started_at,
        )
    except Exception as exc:
        message = _clean(str(exc)) or SYNC_NOTICE_MESSAGES["import_failed"]
        if unlocked:
            _LOGGER.warning("Background sign-in sync failed after unlock: %s", message)
        else:
            with get_connection() as conn:
                _set_signin_sync_state(conn, in_progress=False, phase="", error=message)
                conn.commit()
    finally:
        with _SIGNIN_SYNC_LOCK:
            _SIGNIN_SYNC_THREAD = None
        _SIGNIN_SYNC_CONTACTS_CONTEXT = {}


def _touch_signin_sync(
    phase: str,
    processed_count: int | None = None,
    total_count: int | None = None,
    current_item: str | None = None,
    **kwargs,
) -> None:
    global _SIGNIN_SYNC_CONTACTS_CONTEXT
    if phase == "contacts":
        if kwargs.get("local_contact_count") is not None:
            _SIGNIN_SYNC_CONTACTS_CONTEXT["local_contact_count"] = int(kwargs["local_contact_count"])
        if kwargs.get("drive_manifest_count") is not None:
            _SIGNIN_SYNC_CONTACTS_CONTEXT["drive_manifest_count"] = int(kwargs["drive_manifest_count"])
    with get_connection() as conn:
        _set_signin_sync_state(
            conn,
            in_progress=True,
            phase=phase,
            error="",
            total_count=total_count,
            processed_count=processed_count,
            current_item=current_item,
        )
        conn.commit()


def _start_signin_sync_job(*, skip_settings: bool = False, storage: dict | None = None) -> None:
    global _SIGNIN_SYNC_THREAD
    with _SIGNIN_SYNC_LOCK:
        if _signin_sync_thread_running():
            return
        with get_connection() as conn:
            _set_signin_sync_state(conn, in_progress=True, phase="starting", error="")
            conn.commit()
        _SIGNIN_SYNC_THREAD = threading.Thread(
            target=_run_signin_sync_job,
            kwargs={"skip_settings": skip_settings, "storage": storage},
            daemon=True,
        )
        _SIGNIN_SYNC_THREAD.start()


def get_google_signin_sync_progress() -> dict:
    with get_connection() as conn:
        _clear_stale_signin_sync_if_needed(conn)
        conn.commit()
    summary = get_google_sync_summary()
    state = summary.get("state") or {}
    return {
        "in_progress": bool(state.get("signin_sync_in_progress") or 0),
        "phase": str(state.get("signin_sync_phase") or ""),
        "error": str(state.get("signin_sync_error") or ""),
        "total_count": int(state.get("signin_sync_total_count") or 0),
        "processed_count": int(state.get("signin_sync_processed_count") or 0),
        "current_item": str(state.get("signin_sync_current_item") or ""),
        "local_contact_count": int(_SIGNIN_SYNC_CONTACTS_CONTEXT.get("local_contact_count") or 0),
        "drive_manifest_count": int(_SIGNIN_SYNC_CONTACTS_CONTEXT.get("drive_manifest_count") or 0),
    }


def export_shared_data_to_google_drive() -> bool:
    with get_connection() as conn:
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        settings_needed = bool(state.get("needs_drive_export") or 0)
        book_layouts_needed = bool(state.get("book_layouts_needs_drive_export") or 0)
        contacts_manifest_needed = bool(state.get("contacts_manifest_needs_drive_export") or 0)
        meetingdata_needed = bool(state.get("meetingdata_needs_drive_export") or 0)
        field_list_needed = bool(state.get("field_list_needs_drive_export") or 0)
        changes_list_needed = bool(state.get("changes_list_needs_drive_export") or 0)
        contact_export_needed = _count_pending_shared_contact_exports(conn) > 0
        conn.commit()

    contacts_complete = True
    if settings_needed:
        export_shared_settings_to_google_drive()
    if book_layouts_needed:
        export_book_layouts_to_google_drive()
    if contacts_manifest_needed or contact_export_needed:
        contacts_complete = export_shared_contacts_to_google_drive()
    if meetingdata_needed:
        export_shared_meetingdata_to_google_drive()
    if field_list_needed:
        export_field_list_to_google_drive()
    if changes_list_needed:
        export_changes_list_to_google_drive()

    with get_connection() as conn:
        if settings_needed and contacts_complete:
            _clear_pending_drive_export(conn)
        if book_layouts_needed:
            _clear_pending_book_layouts_drive_export(conn)
        if contacts_manifest_needed and contacts_complete:
            _clear_pending_contacts_manifest_drive_export(conn)
        if meetingdata_needed:
            _clear_pending_meetingdata_drive_export(conn)
        if field_list_needed:
            _clear_pending_field_list_drive_export(conn)
        if changes_list_needed:
            _clear_pending_changes_list_drive_export(conn)
        drive_work_remaining = _state_has_pending_drive_export(conn)
        conn.commit()
    return contacts_complete and not drive_work_remaining


def _current_sync_work() -> dict:
    summary = get_google_sync_summary()
    state = summary.get("state") or {}
    bootstrap_status = str(state.get("bootstrap_status") or "idle")
    bootstrap_remaining = int(state.get("bootstrap_remaining_contacts") or 0)
    pending_contact_count = int(state.get("pending_upload_count") or 0)
    drive_export_needed = bool(state.get("needs_drive_export") or 0)
    book_layouts_drive_export_needed = bool(state.get("book_layouts_needs_drive_export") or 0)
    contacts_manifest_drive_export_needed = bool(state.get("contacts_manifest_needs_drive_export") or 0)
    contact_records_drive_export_needed = bool(state.get("contact_records_needs_drive_export") or 0)
    meetingdata_drive_export_needed = bool(state.get("meetingdata_needs_drive_export") or 0)
    field_list_drive_export_needed = bool(state.get("field_list_needs_drive_export") or 0)
    changes_list_drive_export_needed = bool(state.get("changes_list_needs_drive_export") or 0)
    shared_drive_needed = (
        drive_export_needed
        or book_layouts_drive_export_needed
        or contacts_manifest_drive_export_needed
        or contact_records_drive_export_needed
        or meetingdata_drive_export_needed
        or field_list_drive_export_needed
        or changes_list_drive_export_needed
    )
    return {
        "bootstrap_incomplete": bootstrap_status in {"running", "failed"} or bootstrap_remaining > 0,
        "pending_contact_count": pending_contact_count,
        "drive_export_needed": shared_drive_needed,
        "book_layouts_drive_export_needed": book_layouts_drive_export_needed,
        "meetingdata_drive_export_needed": meetingdata_drive_export_needed,
        "field_list_drive_export_needed": field_list_drive_export_needed,
        "changes_list_drive_export_needed": changes_list_drive_export_needed,
        "total_count": pending_contact_count + (1 if shared_drive_needed else 0),
    }


def _editor_lock_is_active(lock: dict, *, now: datetime | None = None) -> bool:
    current = now or _utc_now()
    owner = _clean((lock or {}).get("editor_name"))
    expiry = _parse_iso_datetime(_clean((lock or {}).get("expires_at")))
    return bool(owner and expiry and expiry > current)


def _clear_expired_remote_editor_lock(access_token: str, storage: dict, lock: dict, *, now: datetime | None = None) -> bool:
    """Drop leftover Drive lock files so the next Editor is not blocked by an expired session."""
    if _editor_lock_is_active(lock, now=now):
        return False
    if not _clean((lock or {}).get("editor_name")) and not _clean((lock or {}).get("session_id")):
        return False
    file_id = _clean((storage or {}).get("editor_lock_file_id"))
    if not file_id:
        return False
    _drive_update_json_file_content(access_token, file_id, {})
    return True


def probe_remote_editor_lock(
    access_token: str | None = None,
    *,
    pending_editor_name: str = "",
    apply_blocked_state: bool = False,
) -> dict:
    token = access_token or _get_valid_access_token()[0]
    storage = _ensure_editor_lock_drive_storage(token)
    lock = _load_editor_lock(token, storage["editor_lock_file_id"])
    now = _utc_now()
    existing_owner = _clean(lock.get("editor_name"))
    existing_expiry = _parse_iso_datetime(_clean(lock.get("expires_at")))
    existing_active = _editor_lock_is_active(lock, now=now)
    if not existing_active:
        try:
            _clear_expired_remote_editor_lock(token, storage, lock, now=now)
        except (RuntimeError, HTTPError, URLError, socket.timeout, TimeoutError):
            pass
        return {"blocked": False}
    summary = get_google_sync_summary()
    local_state = summary.get("state") or {}
    account = summary.get("account") or {}
    if _editor_lock_is_own(
        lock,
        local_session_id=_clean(local_state.get("editor_session_id")),
        editor_name=[
            pending_editor_name,
            local_state.get("editor_name"),
            local_state.get("pending_editor_name"),
            local_state.get("access_name"),
        ],
        account_email=_clean(account.get("email")),
    ):
        return {"blocked": False}
    expires_at = existing_expiry.isoformat() if existing_expiry else ""
    if apply_blocked_state:
        _set_editor_session(
            mode="no_edit",
            owner_name=existing_owner,
            expires_at=expires_at,
            pending_editor_name=_clean(pending_editor_name),
        )
    return {
        "blocked": True,
        "owner_name": existing_owner,
        "expires_at": expires_at,
    }


def acquire_editor_lock(
    editor_name: str,
    *,
    force: bool = False,
    settings_loaded: bool = False,
    start_signin_sync: bool = True,
    minimal_drive_storage: bool = False,
) -> dict:
    normalized_name = _clean(editor_name)
    if not normalized_name:
        return {"status": "name_required"}
    lock_started = time.perf_counter()
    access_token, account_email = _get_valid_access_token()
    if minimal_drive_storage:
        storage = _ensure_editor_lock_drive_storage(access_token)
    else:
        storage = _ensure_shared_drive_storage(access_token)
    _LOGGER.info(
        "Editor lock storage ready in %.2fs (minimal=%s)",
        time.perf_counter() - lock_started,
        minimal_drive_storage,
    )
    lock = _load_editor_lock(access_token, storage["editor_lock_file_id"])
    now = _utc_now()
    local_state = get_google_sync_summary().get("state") or {}
    existing_owner = _clean(lock.get("editor_name"))
    existing_session_id = _clean(lock.get("session_id"))
    existing_expiry = _parse_iso_datetime(_clean(lock.get("expires_at")))
    existing_active = _editor_lock_is_active(lock, now=now)
    if not existing_active:
        try:
            _clear_expired_remote_editor_lock(access_token, storage, lock, now=now)
        except (RuntimeError, HTTPError, URLError, socket.timeout, TimeoutError):
            pass
    local_session_id = _clean(local_state.get("editor_session_id"))
    lock_is_own = existing_active and _editor_lock_is_own(
        lock,
        local_session_id=local_session_id,
        editor_name=[
            normalized_name,
            local_state.get("editor_name"),
            local_state.get("pending_editor_name"),
            local_state.get("access_name"),
        ],
        account_email=account_email,
    )

    if lock_is_own:
        session_id = existing_session_id or secrets.token_urlsafe(18)
        # Keep the established Editor name when this computer already holds the
        # lock. A contributor tab on the same app must not rename it.
        if existing_owner:
            normalized_name = existing_owner
    elif existing_active and not force:
        _set_editor_session(
            mode="no_edit",
            owner_name=existing_owner,
            expires_at=existing_expiry.isoformat(),
            pending_editor_name=normalized_name,
        )
        return {"status": "blocked", "owner_name": existing_owner, "expires_at": existing_expiry.isoformat()}
    else:
        session_id = secrets.token_urlsafe(18)

    if not settings_loaded:
        settings_started = time.perf_counter()
        import_shared_settings_from_google_drive(access_token=access_token, storage=storage)
        settings_loaded = True
        local_state = get_google_sync_summary().get("state") or {}
        _LOGGER.info(
            "Editor lock settings import in %.2fs",
            time.perf_counter() - settings_started,
        )

    timeout_minutes = _normalize_editor_timeout_minutes(local_state.get("editor_lock_timeout_minutes"))
    expires_at = now + timedelta(minutes=timeout_minutes)

    payload = {
        "format": "contactsfreeshare.editor_lock.v1",
        "editor_name": normalized_name,
        "account_email": account_email,
        "session_id": session_id,
        "locked_at": _iso_now(),
        "heartbeat_at": _iso_now(),
        "timeout_minutes": timeout_minutes,
        "expires_at": expires_at.isoformat(),
    }
    _drive_update_json_file_content(access_token, storage["editor_lock_file_id"], payload)
    _set_editor_session(
        mode="edit",
        editor_name=normalized_name,
        session_id=session_id,
        owner_name=normalized_name,
        expires_at=expires_at.isoformat(),
        pending_editor_name="",
    )
    with get_connection() as conn:
        _set_signin_sync_state(conn, in_progress=False, phase="", error="")
        conn.commit()
    if start_signin_sync:
        # Reuse the storage map so the background job skips another Drive bootstrap.
        signin_storage = None if minimal_drive_storage else storage
        _start_signin_sync_job(skip_settings=settings_loaded, storage=signin_storage)
    _LOGGER.info(
        "Editor lock acquired in %.2fs (signin_sync=%s)",
        time.perf_counter() - lock_started,
        start_signin_sync,
    )
    return {"status": "acquired", "editor_name": normalized_name, "expires_at": expires_at.isoformat()}


def release_editor_lock() -> None:
    state = get_google_sync_summary().get("state") or {}
    if not bool(state.get("multi_editor_enabled")):
        return
    previous_editor_name = _clean(state.get("editor_name")) or _clean(state.get("pending_editor_name"))
    session_id = _clean(state.get("editor_session_id"))
    if session_id:
        try:
            with get_connection() as conn:
                drive_export_still_needed = _state_has_pending_drive_export(conn)
                conn.commit()
            if drive_export_still_needed:
                try:
                    export_shared_data_to_google_drive()
                except (RuntimeError, HTTPError, URLError):
                    pass
            access_token, _account_email = _get_valid_access_token()
            storage = _ensure_shared_drive_storage(access_token)
            lock = _load_editor_lock(access_token, storage["editor_lock_file_id"])
            if _clean(lock.get("session_id")) == session_id:
                _drive_update_json_file_content(access_token, storage["editor_lock_file_id"], {})
        finally:
            _set_editor_session(mode="no_edit", pending_editor_name=previous_editor_name)
    else:
        _set_editor_session(mode="no_edit", pending_editor_name=previous_editor_name)


def format_editor_lock_duration(seconds: int) -> str:
    safe_seconds = max(0, int(seconds or 0))
    if safe_seconds <= 0:
        return "unknown"
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    remainder = safe_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def format_editor_lock_blocked_message(
    *,
    owner_name: str = "",
    remaining_seconds: int | None = None,
    expires_at: str = "",
) -> str:
    owner = _clean(owner_name) or "Another Editor"
    if remaining_seconds is None:
        parsed = _parse_iso_datetime(_clean(expires_at))
        if parsed:
            remaining_seconds = max(0, int((parsed - _utc_now()).total_seconds()))
        else:
            remaining_seconds = 0
    time_label = format_editor_lock_duration(int(remaining_seconds or 0))
    return (
        f"{owner} is already signed in. This app stays connected in NO-EDIT mode. "
        f"Remaining time: {time_label}. Try Sign in again after they finish, or when that time ends."
    )


def get_editor_lock_status() -> dict:
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    expires_at = _parse_iso_datetime(_clean(state.get("editor_lock_expires_at")))
    now = _utc_now()
    if _clear_expired_local_editor_session(state, now=now):
        summary = get_google_sync_summary()
        account = summary.get("account") or {}
        state = summary.get("state") or {}
        expires_at = _parse_iso_datetime(_clean(state.get("editor_lock_expires_at")))
    remaining_seconds = 0
    if expires_at:
        remaining_seconds = max(0, int((expires_at - now).total_seconds()))
    active = (
        remaining_seconds > 0
        and _has_active_local_editor_session(account, state, now=now)
    )
    return {
        "active": active,
        "multi_editor_enabled": bool(state.get("multi_editor_enabled")),
        "editor_name": _clean(state.get("editor_name")),
        "owner_name": _clean(state.get("editor_lock_owner_name")) or _clean(state.get("editor_name")),
        "expires_at": expires_at.isoformat() if expires_at else "",
        "remaining_seconds": remaining_seconds,
    }


def extend_editor_lock(minutes: int = EDITOR_LOCK_EXTENSION_MINUTES) -> dict:
    state = get_google_sync_summary().get("state") or {}
    if not bool(state.get("multi_editor_enabled")) or str(state.get("editor_mode") or "") != "edit":
        return {"status": "not_active", **get_editor_lock_status()}
    session_id = _clean(state.get("editor_session_id"))
    if not session_id:
        return {"status": "not_active", **get_editor_lock_status()}
    access_token, account_email = _get_valid_access_token()
    storage = _ensure_editor_lock_drive_storage(access_token)
    lock = _load_editor_lock(access_token, storage["editor_lock_file_id"])
    remote_session_id = _clean(lock.get("session_id"))
    if not remote_session_id:
        # An empty Drive lock file is a read miss, not another Editor. Keep this
        # computer's session so a contributor tab cannot lock us out of our own lock.
        return {"status": "extended_local", **get_editor_lock_status()}
    if not _editor_lock_is_own(
        lock,
        local_session_id=session_id,
        editor_name=[
            state.get("editor_name"),
            state.get("pending_editor_name"),
            state.get("access_name"),
        ],
        account_email=account_email,
    ):
        _set_editor_session(mode="no_edit", pending_editor_name=_clean(state.get("editor_name")))
        return {"status": "lost_lock", **get_editor_lock_status()}
    current_expiry = _parse_iso_datetime(_clean(lock.get("expires_at"))) or _utc_now()
    base_expiry = max(current_expiry, _utc_now())
    expires_at = base_expiry + timedelta(minutes=max(1, int(minutes or EDITOR_LOCK_EXTENSION_MINUTES)))
    lock["heartbeat_at"] = _iso_now()
    lock["expires_at"] = expires_at.isoformat()
    _set_editor_session(
        mode="edit",
        editor_name=_clean(state.get("editor_name")),
        session_id=session_id,
        owner_name=_clean(state.get("editor_lock_owner_name")) or _clean(state.get("editor_name")),
        expires_at=expires_at.isoformat(),
        pending_editor_name="",
    )
    try:
        _drive_update_json_file_content(access_token, storage["editor_lock_file_id"], lock)
    except (RuntimeError, HTTPError, URLError, socket.timeout, TimeoutError):
        return {"status": "extended_local", "drive_sync_failed": True, **get_editor_lock_status()}
    return {"status": "extended", **get_editor_lock_status()}


def export_shared_data_after_edit() -> None:
    mark_shared_data_for_drive_export()


def export_app_revisions_to_google_drive(revisions: list[dict] | None = None) -> dict:
    storage = ensure_google_drive_app_revisions_storage()
    access_token, account_email = _get_valid_access_token()
    if revisions is None:
        from app.services.app_revision_service import APP_REVISION_DISPLAY_LIMIT, list_app_revisions

        revisions = list_app_revisions(limit=APP_REVISION_DISPLAY_LIMIT)
    payload = {
        "format": "contactsfreeshare.app_revisions.v1",
        "account_email": account_email,
        "exported_at": _now_text(),
        "revisions": [
            {
                "date_display": str(item.get("date_display") or ""),
                "version": str(item.get("version") or ""),
                "description": str(item.get("description") or ""),
            }
            for item in revisions
        ],
    }
    _drive_update_json_file_content(access_token, storage["app_revisions_file_id"], payload)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    return {**storage, "account_email": account_email}


def import_app_update_manifest_from_google_drive() -> dict:
    storage = ensure_google_drive_root_storage()
    access_token, account_email = _get_valid_access_token()
    updates_folder = _drive_find_child(
        access_token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_APP_UPDATES_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if updates_folder is None:
        raise RuntimeError("App Updates folder was not found in Google Drive.")
    manifest_file = _drive_find_child(
        access_token,
        parent_id=_clean(updates_folder.get("id")),
        name=GOOGLE_DRIVE_APP_UPDATE_MANIFEST_FILE_NAME,
        mime_type="application/json",
    )
    if manifest_file is None:
        raise RuntimeError("App update manifest was not found in Google Drive.")
    payload = _drive_load_json_file_content(access_token, _clean(manifest_file.get("id")))
    return {
        "account_email": account_email,
        "updates_folder_id": _clean(updates_folder.get("id")),
        "manifest_file_id": _clean(manifest_file.get("id")),
        "manifest": payload if isinstance(payload, dict) else {},
    }


def download_app_update_package_from_google_drive(*, file_id: str) -> dict:
    access_token, account_email = _get_valid_access_token()
    file_item = _drive_get_file(
        access_token,
        file_id,
        fields="id,name,mimeType,size",
    )
    if file_item is None:
        raise RuntimeError("The requested app update package was not found in Google Drive.")
    content = _drive_load_file_content(access_token, _clean(file_item.get("id")), timeout=DRIVE_UPLOAD_TIMEOUT_SECONDS)
    return {
        "account_email": account_email,
        "file_id": _clean(file_item.get("id")),
        "filename": _clean(file_item.get("name")) or "ContactsFreeShare-update.zip",
        "mime_type": _clean(file_item.get("mimeType")) or "application/octet-stream",
        "content": content,
    }


def export_contacts_file_to_google_drive(filename: str, content: bytes, mime_type: str) -> dict:
    storage = ensure_google_drive_root_storage()
    access_token, account_email = _get_valid_access_token()
    existing_file = _drive_find_child(
        access_token,
        parent_id=storage["root_folder_id"],
        name=filename,
        mime_type=mime_type,
    )
    if existing_file is None:
        existing_file = _drive_create_file(
            access_token,
            name=filename,
            parent_id=storage["root_folder_id"],
            mime_type=mime_type,
        )
    file_id = _clean(existing_file.get("id"))
    _drive_update_file_content(access_token, file_id, content=content, mime_type=mime_type)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    return {
        **storage,
        "account_email": account_email,
        "file_id": file_id,
        "filename": filename,
    }


def export_backup_file_to_google_drive(filename: str, content: bytes, mime_type: str) -> dict:
    storage = ensure_google_drive_root_storage()
    access_token, account_email = _get_valid_access_token()
    backups_folder = _drive_find_child(
        access_token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_BACKUPS_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if backups_folder is None:
        backups_folder = _drive_create_folder(
            access_token,
            name=GOOGLE_DRIVE_BACKUPS_FOLDER_NAME,
            parent_id=storage["root_folder_id"],
        )
    backups_folder_id = _clean(backups_folder.get("id"))
    existing_file = _drive_find_child(
        access_token,
        parent_id=backups_folder_id,
        name=filename,
        mime_type=mime_type,
    )
    if existing_file is None:
        existing_file = _drive_create_file(
            access_token,
            name=filename,
            parent_id=backups_folder_id,
            mime_type=mime_type,
        )
    file_id = _clean(existing_file.get("id"))
    _drive_update_file_content(access_token, file_id, content=content, mime_type=mime_type)
    backup_kind = _google_drive_backup_kind_from_filename(filename)
    if backup_kind:
        _prune_google_drive_backup_files(access_token, backups_folder_id, backup_kind)
    _store_drive_state(last_drive_export_at=_now_text(), last_sync_error="")
    return {
        **storage,
        "account_email": account_email,
        "backups_folder_id": backups_folder_id,
        "file_id": file_id,
        "filename": filename,
    }


def _ensure_google_drive_backups_folder(access_token: str, root_folder_id: str) -> dict:
    backups_folder = _drive_find_child(
        access_token,
        parent_id=root_folder_id,
        name=GOOGLE_DRIVE_BACKUPS_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    if backups_folder is None:
        backups_folder = _drive_create_folder(
            access_token,
            name=GOOGLE_DRIVE_BACKUPS_FOLDER_NAME,
            parent_id=root_folder_id,
        )
    return backups_folder


def upload_address_book_pdf_to_google_drive(filename: str, content: bytes) -> dict:
    return _upload_address_book_pdf_to_folder(
        filename,
        content,
        folder_name=GOOGLE_DRIVE_ADDRESS_BOOK_PDFS_FOLDER_NAME,
    )


def upload_address_book_printed_pdf_to_google_drive(filename: str, content: bytes) -> dict:
    return _upload_address_book_pdf_to_folder(
        filename,
        content,
        folder_name=GOOGLE_DRIVE_ADDRESS_BOOK_PRINTED_FOLDER_NAME,
    )


def _prune_address_book_pdf_folder(access_token: str, folder_id: str, *, keep_count: int = GOOGLE_DRIVE_ADDRESS_BOOK_PDF_RETENTION_LIMIT) -> None:
    query = " and ".join(
        [
            f"'{_drive_query_literal(folder_id)}' in parents",
            "mimeType = 'application/pdf'",
            "trashed = false",
        ]
    )
    payload = _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': query, 'fields': 'files(id,name,createdTime,modifiedTime)', 'pageSize': 1000, 'orderBy': 'createdTime desc', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
        access_token,
    )
    files = [dict(item) for item in payload.get("files") or []]
    files.sort(
        key=lambda item: (
            _clean(item.get("createdTime")) or _clean(item.get("modifiedTime")),
            _clean(item.get("id")),
        ),
        reverse=True,
    )
    for file_item in files[max(0, keep_count):]:
        _drive_delete_file_if_exists(access_token, _clean(file_item.get("id")))


def _address_book_pdf_base_name(filename: str) -> str:
    base = re.sub(r"\.[^.]+$", "", _clean(filename), flags=re.IGNORECASE)
    base = re.sub(r"[\s_-]*\d{4}-\d{2}-\d{2}$", "", base)
    base = re.sub(r"[\s_-]*\d{2}-\d{2}-\d{4}$", "", base)
    base = re.sub(r"[\s_-]*\d{1,2}/\d{1,2}/\d{2,4}$", "", base)
    # Treat spaces, underscores, and hyphens as equivalent so a legacy filename
    # like "Address Book 2026-06-30.pdf" matches "Address_Book_2026-07-02.pdf".
    base = re.sub(r"[\s_-]+", " ", base)
    return base.strip().casefold()


def _strip_address_book_pdf_date_suffix(filename: str) -> str:
    return _address_book_pdf_base_name(filename)


def _drive_find_folder_by_id_or_name(access_token: str, *, folder_id: str, folder_name: str) -> dict | None:
    normalized_folder_id = _clean(folder_id)
    if normalized_folder_id:
        try:
            folder = _drive_get_file(access_token, normalized_folder_id, fields="id,name,parents,mimeType")
            if folder and _clean(folder.get("mimeType")) == "application/vnd.google-apps.folder":
                return folder
        except HTTPError:
            pass

    # Name-only lookup across My Drive is intentionally avoided.
    # Shared folders must be chosen in Google Picker or entered by folder ID.
    return None


def _address_book_pdf_share_settings() -> dict:
    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              multi_editor_enabled,
              sync_enabled,
              address_book_pdf_share_enabled,
              address_book_pdf_share_folder_id,
              address_book_pdf_share_folder_name
            FROM google_sync_state
            WHERE id = 1
            """
        ).fetchone()
    state = dict(row) if row else {}
    return {
        "enabled": bool((state.get("multi_editor_enabled") or 0) or (state.get("sync_enabled") or 0)) and bool(state.get("address_book_pdf_share_enabled") or 0),
        "folder_id": _clean(state.get("address_book_pdf_share_folder_id")),
        "folder_name": _clean(state.get("address_book_pdf_share_folder_name")),
    }


def _is_address_book_pdf_replacement(
    file_item: dict, target_base: str, *, include_tagged: bool = False
) -> bool:
    # In single-copy folders (the shared folder) any PDF this app previously
    # created as an address book is an older copy to replace, even if its title
    # changed between runs. Match on the app tag in that case.
    if include_tagged:
        app_properties = (
            file_item.get("appProperties")
            if isinstance(file_item.get("appProperties"), dict)
            else {}
        )
        if _clean(app_properties.get("contactsfreeshare_kind")) == ADDRESS_BOOK_PDF_APP_PROPERTY_KIND:
            return True
    if not target_base:
        return False
    existing_name = _clean(file_item.get("name"))
    if not existing_name:
        return False
    return _address_book_pdf_base_name(existing_name) == target_base


def _delete_existing_address_book_pdfs(
    access_token: str, folder_id: str, filename: str, *, include_tagged: bool = False
) -> int:
    target_base = _address_book_pdf_base_name(filename)
    query = " and ".join(
        [
            f"'{_drive_query_literal(folder_id)}' in parents",
            "mimeType = 'application/pdf'",
            "trashed = false",
        ]
    )
    payload = _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': query, 'fields': 'files(id,name,mimeType,appProperties)', 'pageSize': 1000, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
        access_token,
    )
    deleted_count = 0
    for file_item in payload.get("files") or []:
        if not _is_address_book_pdf_replacement(
            dict(file_item), target_base, include_tagged=include_tagged
        ):
            continue
        file_id = _clean(file_item.get("id"))
        if not file_id:
            continue
        _drive_delete_file_if_exists(access_token, file_id)
        deleted_count += 1
    return deleted_count


def _mirror_address_book_pdf_to_shared_folder(access_token: str, filename: str, content: bytes) -> dict | None:
    settings = _address_book_pdf_share_settings()
    if not settings["enabled"]:
        return None
    folder = _drive_find_folder_by_id_or_name(
        access_token,
        folder_id=settings["folder_id"],
        folder_name=settings["folder_name"],
    )
    if folder is None:
        return None
    folder_id = _clean(folder.get("id"))
    if not folder_id:
        return None
    # The shared folder holds a single current copy, so remove every prior
    # app-created address book here (not just exact name matches).
    deleted_count = _delete_existing_address_book_pdfs(
        access_token, folder_id, filename, include_tagged=True
    )
    file_item = _drive_create_file(
        access_token,
        name=_clean(filename) or "Address_Book.pdf",
        parent_id=folder_id,
        mime_type="application/pdf",
        app_properties={
            "contactsfreeshare_kind": ADDRESS_BOOK_PDF_APP_PROPERTY_KIND,
            "contactsfreeshare_folder": "shared_address_book_pdf",
        },
    )
    file_id = _clean(file_item.get("id"))
    _drive_ensure_file_parent(access_token, file_id, folder_id)
    _drive_update_file_content(
        access_token,
        file_id,
        content=content,
        mime_type="application/pdf",
    )
    _drive_ensure_file_parent(access_token, file_id, folder_id)
    return {
        "folder_id": folder_id,
        "file_id": file_id,
        "folder_name": _clean(folder.get("name")),
        "deleted_count": deleted_count,
    }


def cleanup_orphaned_address_book_pdf_root_files() -> int:
    ensure_google_sync_records()
    account = _load_google_account()
    if str(account.get("account_status") or "") != "connected" or not _account_has_drive_scope(account):
        return 0
    access_token, _account_email = _get_valid_access_token()
    storage = ensure_google_drive_root_storage()
    pdf_folder = _drive_find_child(
        access_token,
        parent_id=storage["root_folder_id"],
        name=GOOGLE_DRIVE_ADDRESS_BOOK_PDFS_FOLDER_NAME,
        mime_type="application/vnd.google-apps.folder",
    )
    in_folder_names: set[str] = set()
    if pdf_folder is not None:
        folder_id = _clean(pdf_folder.get("id"))
        if folder_id:
            folder_query = " and ".join(
                [
                    f"'{_drive_query_literal(folder_id)}' in parents",
                    "mimeType = 'application/pdf'",
                    "trashed = false",
                ]
            )
            folder_payload = _drive_json_request(
                f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': folder_query, 'fields': 'files(name)', 'pageSize': 1000, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
                access_token,
            )
            in_folder_names = {
                _clean(item.get("name"))
                for item in folder_payload.get("files") or []
                if _clean(item.get("name"))
            }
    query = " and ".join(
        [
            "'root' in parents",
            "mimeType = 'application/pdf'",
            "trashed = false",
            "'me' in owners",
        ]
    )
    payload = _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': query, 'fields': 'files(id,name,parents,mimeType,appProperties)', 'pageSize': 1000, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
        access_token,
    )
    deleted_count = 0
    for file_item in payload.get("files") or []:
        app_properties = file_item.get("appProperties") if isinstance(file_item.get("appProperties"), dict) else {}
        is_tagged_address_book_pdf = _clean(app_properties.get("contactsfreeshare_kind")) == ADDRESS_BOOK_PDF_APP_PROPERTY_KIND
        is_duplicate_address_book_pdf = _clean(file_item.get("name")) in in_folder_names
        if not is_tagged_address_book_pdf and not is_duplicate_address_book_pdf:
            continue
        file_id = _clean(file_item.get("id"))
        if not file_id:
            continue
        _drive_delete_file_if_exists(access_token, file_id)
        deleted_count += 1
    return deleted_count


def _run_orphaned_address_book_pdf_cleanup_loop() -> None:
    while not _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_STOP.wait(ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_INTERVAL_SECONDS):
        try:
            cleanup_orphaned_address_book_pdf_root_files()
        except (RuntimeError, HTTPError, URLError) as exc:
            record_google_sync_error(f"Address Book PDF root cleanup failed: {exc}")


def start_orphaned_address_book_pdf_cleanup_job() -> None:
    if not _account_has_drive_scope():
        return
    global _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_THREAD
    with _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_LOCK:
        if _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_THREAD and _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_THREAD.is_alive():
            return
        _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_STOP.clear()
        _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_THREAD = threading.Thread(
            target=_run_orphaned_address_book_pdf_cleanup_loop,
            daemon=True,
        )
        _ORPHANED_ADDRESS_BOOK_PDF_CLEANUP_THREAD.start()


def _upload_address_book_pdf_to_folder(filename: str, content: bytes, *, folder_name: str) -> dict:
    access_token, _account_email = _get_valid_access_token()
    storage = ensure_google_drive_root_storage()
    folder = _drive_find_child(
        access_token,
        parent_id=storage["root_folder_id"],
        name=folder_name,
        mime_type="application/vnd.google-apps.folder",
    )
    if folder is None:
        folder = _drive_create_folder(
            access_token,
            name=folder_name,
            parent_id=storage["root_folder_id"],
        )
    folder_id = _clean(folder.get("id"))
    if not folder_id:
        raise RuntimeError(f"Google Drive folder for {folder_name} did not return an ID.")
    deleted_count = _delete_existing_address_book_pdfs(access_token, folder_id, filename)
    file_item = _drive_create_file(
        access_token,
        name=_clean(filename) or "Address_Book.pdf",
        parent_id=folder_id,
        mime_type="application/pdf",
        app_properties={
            "contactsfreeshare_kind": ADDRESS_BOOK_PDF_APP_PROPERTY_KIND,
            "contactsfreeshare_folder": folder_name,
        },
    )
    file_id = _clean(file_item.get("id"))
    _drive_ensure_file_parent(access_token, file_id, folder_id)
    _drive_update_file_content(
        access_token,
        file_id,
        content=content,
        mime_type="application/pdf",
    )
    _drive_ensure_file_parent(access_token, file_id, folder_id)
    _prune_address_book_pdf_folder(access_token, folder_id)
    shared_file = None
    if folder_name == GOOGLE_DRIVE_ADDRESS_BOOK_PDFS_FOLDER_NAME:
        try:
            shared_file = _mirror_address_book_pdf_to_shared_folder(access_token, filename, content)
        except (RuntimeError, HTTPError, URLError) as exc:
            shared_file = {"error": str(exc)}
    if _account_has_drive_scope():
        cleanup_orphaned_address_book_pdf_root_files()
    return {
        "folder_id": folder_id,
        "file_id": file_id,
        "filename": _clean(filename),
        "folder_name": folder_name,
        "deleted_count": deleted_count,
        "shared_file": shared_file,
    }


def _google_drive_backup_name_prefix(backup_kind: str) -> str:
    label = "MeetingData" if str(backup_kind or "").strip().lower() == "meetingdata" else "Contacts"
    return f"ContactsFreeShare_{label}"


def _google_drive_backup_canonical_filename(backup_kind: str) -> str:
    from app.services.backup_service import backup_filename

    return backup_filename(backup_kind)


def _google_drive_backup_kind_from_filename(filename: str) -> str | None:
    cleaned = _clean(filename)
    # MeetingData first so ContactsFreeShare_MeetingData* is never treated as Contacts.
    if cleaned.startswith(_google_drive_backup_name_prefix("meetingdata")):
        return "meetingdata"
    if cleaned.startswith(_google_drive_backup_name_prefix("contacts")):
        return "contacts"
    return None


def _list_google_drive_backup_files_for_kind(
    access_token: str,
    backups_folder_id: str,
    backup_kind: str,
    *,
    page_size: int = 1000,
) -> list[dict]:
    name_prefix = _google_drive_backup_name_prefix(backup_kind)
    # Broad folder query; filter in Python so Drive token matching cannot drop either kind.
    query = " and ".join(
        [
            f"'{_drive_query_literal(backups_folder_id)}' in parents",
            "mimeType = 'application/json'",
            "trashed = false",
        ]
    )
    payload = _drive_json_request(
        f"{GOOGLE_DRIVE_BASE_URL}/files?{urlencode({'q': query, 'fields': 'files(id,name,createdTime,modifiedTime,size,mimeType)', 'pageSize': page_size, 'orderBy': 'modifiedTime desc', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})}",
        access_token,
    )
    canonical = _google_drive_backup_canonical_filename(backup_kind)
    files = [
        dict(item)
        for item in payload.get("files") or []
        if _google_drive_backup_kind_from_filename(_clean(item.get("name"))) == backup_kind
        and _clean(item.get("id"))
        and _clean(item.get("name")).startswith(name_prefix)
    ]
    files.sort(
        key=lambda item: (
            1 if _clean(item.get("name")) == canonical else 0,
            _clean(item.get("modifiedTime")) or _clean(item.get("createdTime")),
            _clean(item.get("id")),
        ),
        reverse=True,
    )
    return files


def _prune_google_drive_backup_files(
    access_token: str,
    backups_folder_id: str,
    backup_kind: str,
    *,
    keep_count: int = GOOGLE_DRIVE_BACKUP_RETENTION_LIMIT,
) -> None:
    files = _list_google_drive_backup_files_for_kind(access_token, backups_folder_id, backup_kind)
    for file_item in files[max(0, keep_count):]:
        _drive_delete_file_if_exists(access_token, _clean(file_item.get("id")))


def list_google_drive_backup_files() -> dict[str, list[dict]]:
    storage = ensure_google_drive_root_storage()
    access_token, _account_email = _get_valid_access_token()
    backups_folder = _ensure_google_drive_backups_folder(access_token, storage["root_folder_id"])
    backups_folder_id = _clean(backups_folder.get("id"))
    for backup_kind in ("contacts", "meetingdata"):
        _prune_google_drive_backup_files(access_token, backups_folder_id, backup_kind)

    def list_kind(kind: str) -> list[dict]:
        return [
            {
                "id": _clean(item.get("id")),
                "name": _clean(item.get("name")),
                "created_time": _clean(item.get("createdTime")),
                "modified_time": _clean(item.get("modifiedTime")),
                "size": _clean(item.get("size")),
            }
            for item in _list_google_drive_backup_files_for_kind(
                access_token,
                backups_folder_id,
                kind,
            )[:GOOGLE_DRIVE_BACKUP_RETENTION_LIMIT]
            if _clean(item.get("id"))
        ]

    return {
        "contacts": list_kind("contacts"),
        "meetingdata": list_kind("meetingdata"),
    }


def load_google_drive_backup_json(file_id: str) -> dict:
    ensure_google_drive_root_storage()
    access_token, _account_email = _get_valid_access_token()
    payload = _drive_load_json_file_content(access_token, file_id)
    return payload if isinstance(payload, dict) else {}


def import_app_revisions_from_google_drive() -> list[dict]:
    storage = ensure_google_drive_app_revisions_storage()
    access_token, _account_email = _get_valid_access_token()
    payload = _drive_load_json_file_content(access_token, storage["app_revisions_file_id"])
    revisions = payload.get("revisions") or []
    normalized_revisions: list[dict] = []
    for revision in revisions:
        if not isinstance(revision, dict):
            continue
        normalized_revisions.append(
            {
                "date_display": str(revision.get("date_display") or "").strip(),
                "version": str(revision.get("version") or "").strip(),
                "description": str(revision.get("description") or "").strip(),
            }
        )
    return normalized_revisions


def _contact_groups_map(access_token: str) -> dict[str, str]:
    groups: dict[str, str] = {}
    page_token = ""
    for _ in range(20):
        params = {"pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        url = f"{GOOGLE_PEOPLE_BASE_URL}/contactGroups?{urlencode(params)}"
        payload = _api_get_json(url, access_token)
        for item in payload.get("contactGroups", []):
          resource_name = str(item.get("resourceName") or "")
          name = str(item.get("formattedName") or item.get("name") or resource_name)
          if resource_name:
              groups[resource_name] = name
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break
    return groups


def _contact_groups_lookup(access_token: str) -> tuple[dict[str, str], dict[str, str]]:
    resource_to_name = _contact_groups_map(access_token)
    name_to_resource = {name: resource for resource, name in resource_to_name.items() if name}
    resource_to_name.setdefault("contactGroups/myContacts", "myContacts")
    name_to_resource.setdefault("myContacts", "contactGroups/myContacts")
    return resource_to_name, name_to_resource


def _clean(value) -> str:
    return str(value or "").strip()


def _google_single_line(value: object) -> str:
    """People API rejects address, name, and organization fields that contain line breaks."""
    text = _clean(value).replace("\r", "\n")
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


_GOOGLE_NOTE_BREAK = re.compile(r"<br\s*/?>|</(?:div|p)\s*>", re.IGNORECASE)
_GOOGLE_NOTE_BLOCK_OPEN = re.compile(r"<(?:div|p)[^>]*>", re.IGNORECASE)
_GOOGLE_NOTE_TAG = re.compile(r"<[^>]+>")
_GOOGLE_NOTE_PRINT_CODE = re.compile(r"^\s*(?:pb|pa)/\s*", re.IGNORECASE)


def _google_plain_notes(value: object) -> str:
    """Google Notes is plain text. Convert CFS HTML breaks and drop print codes."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _GOOGLE_NOTE_BLOCK_OPEN.sub("", text)
    text = _GOOGLE_NOTE_BREAK.sub("\n", text)
    text = _GOOGLE_NOTE_TAG.sub("", text)
    text = unescape(text)
    lines: list[str] = []
    for line in text.split("\n"):
        line = _GOOGLE_NOTE_PRINT_CODE.sub("", line, count=1)
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _prefer_local_print_code_notes(local_notes: object, google_notes: object) -> str:
    """Keep CFS pb/ and pa/ lines when Google only has the same notes without those codes."""
    local = str(local_notes or "")
    incoming = str(google_notes or "")
    if local.strip() and _google_plain_notes(local) == _google_plain_notes(incoming):
        return local
    return incoming


def _strip_trailing_parenthetical(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip()


def _split_group_membership_text(value: str | None) -> list[str]:
    text = str(value or "").replace(":::", "\n").replace(";", "\n")
    values: list[str] = []
    seen: set[str] = set()
    for chunk in text.splitlines():
        cleaned = chunk.strip()
        if not cleaned or cleaned.lower() == "no entry" or cleaned in seen:
            continue
        seen.add(cleaned)
        values.append(cleaned)
    return values


def _group_membership_text(group_names: list[str]) -> str:
    return ":::".join(name for name in group_names if name)


def _shared_group_membership_names(field_name: str, meeting_name: str) -> list[str]:
    cleaned_field_name = _clean(field_name)
    cleaned_meeting_name = _clean(meeting_name)
    if not cleaned_field_name or not cleaned_meeting_name:
        return []
    from app.services.preset_service import get_shared_contacts_group_name

    return [
        f"{cleaned_field_name} - {cleaned_meeting_name} (Shared)",
        get_shared_contacts_group_name(),
        "myContacts",
    ]


def _assignment_from_group_name(name: str) -> tuple[str, str]:
    text = str(name or "").strip()
    if " - " not in text:
        return "", ""
    base = text[:-8].strip() if text.endswith("(Shared)") else text
    field_name, meeting_name = base.split(" - ", 1)
    return field_name.strip(), meeting_name.strip()


def _parse_field_meeting_groups(group_names: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in group_names:
        field_name, meeting_name = _assignment_from_group_name(name)
        if not field_name or not meeting_name:
            continue
        key = (field_name.casefold(), meeting_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        found.append((field_name, meeting_name))
    return found


def _parse_field_meeting(group_names: list[str]) -> tuple[str, str]:
    found = _parse_field_meeting_groups(group_names)
    return found[0] if found else ("", "")


def _is_bucket_field_meeting(field_name: str, meeting_name: str) -> bool:
    """Catch-all labels such as Other Contacts / Western VA Contacts are not the assignment."""
    field = _clean(field_name).casefold()
    meeting = _clean(meeting_name).casefold()
    if field == "other contacts":
        return True
    return meeting.endswith(" contacts")


def _parse_field_meeting_from_organizations(organizations: list[dict]) -> tuple[str, str]:
    """Field is Google Job Title. Meeting is Google Company. Empty Job Title falls back to Department."""
    items = [item for item in organizations or [] if isinstance(item, dict)]
    items.sort(key=lambda item: 0 if (item.get("metadata") or {}).get("primary") else 1)
    for item in items:
        field_name = _clean(item.get("title")) or _clean(item.get("department"))
        meeting_name = _clean(item.get("name"))
        if field_name or meeting_name:
            return field_name, meeting_name
    return "", ""


def _resolve_field_meeting(group_names: list[str], organizations: list[dict] | None) -> tuple[str, str]:
    org_field, org_meeting = _parse_field_meeting_from_organizations(organizations or [])
    group_assignments = _parse_field_meeting_groups(group_names)
    real_groups = [
        item for item in group_assignments if not _is_bucket_field_meeting(item[0], item[1])
    ]
    group_field, group_meeting = real_groups[0] if real_groups else (group_assignments[0] if group_assignments else ("", ""))
    # Job Title and Company are the contact card. A Google label must not replace them.
    return org_field or group_field, org_meeting or group_meeting


def _google_contact_source(person: dict) -> dict:
    for source in (person.get("metadata") or {}).get("sources") or []:
        if str(source.get("type") or "").upper() == "CONTACT":
            return dict(source)
    return {}


def _google_contact_source_for_update(person: dict) -> dict:
    """updateContact rejects output-only source fields such as updateTime."""
    source = _google_contact_source(person)
    payload: dict[str, str] = {}
    source_type = _clean(source.get("type")) or "CONTACT"
    source_id = _clean(source.get("id"))
    etag = _clean(source.get("etag"))
    if source_type:
        payload["type"] = source_type
    if source_id:
        payload["id"] = source_id
    if etag:
        payload["etag"] = etag
    elif _clean(person.get("etag")):
        payload["etag"] = _clean(person.get("etag"))
    return payload


def _primary_text(items: list[dict], value_key: str) -> str:
    for item in items:
        metadata = item.get("metadata") or {}
        if metadata.get("primary"):
            return _clean(item.get(value_key))
    for item in items:
        value = _clean(item.get(value_key))
        if value:
            return value
    return ""


_COORDINATES_RE = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)")


def _normalize_coordinates(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = unquote_plus(text)
    match = _COORDINATES_RE.search(text)
    if not match:
        return ""
    latitude = match.group(1)
    longitude = match.group(2)
    try:
        lat_value = float(latitude)
        lng_value = float(longitude)
    except ValueError:
        return ""
    if not (-90 <= lat_value <= 90 and -180 <= lng_value <= 180):
        return ""
    return f"{latitude}, {longitude}"


def _coordinate_map_url(base_url: str, coordinates: str) -> str:
    return f"{base_url}?q={quote(_clean(coordinates), safe=',')}"


def _coordinates_from_mapping(value: object) -> str:
    if not isinstance(value, dict):
        return _normalize_coordinates(value)
    direct = _normalize_coordinates(
        value.get("coordinates")
        or value.get("coordinate")
        or value.get("gps")
        or value.get("gpsCoordinates")
        or value.get("latLng")
        or value.get("latlng")
    )
    if direct:
        return direct
    latitude = value.get("latitude") or value.get("lat")
    longitude = value.get("longitude") or value.get("lng") or value.get("lon")
    if latitude is not None and longitude is not None:
        return _normalize_coordinates(f"{latitude}, {longitude}")
    for item in value.values():
        nested = _coordinates_from_mapping(item)
        if nested:
            return nested
    return ""


def _normalize_coordinate_label(value: object) -> str:
    label = _clean(value).lower()
    label = re.sub(r"\bcoordinates?\b", "", label)
    label = re.sub(r"\bgps\b", "", label)
    label = re.sub(r"\blat(?:itude)?\b", "", label)
    label = re.sub(r"\b(?:lng|longitude)\b", "", label)
    label = re.sub(r"[^a-z0-9]+", " ", label)
    return label.strip()


def _google_user_defined_coordinates(user_defined: list[dict]) -> dict[object, str]:
    coordinates_by_key: dict[object, str] = {}
    unassigned = ""
    for item in user_defined or []:
        key = _clean(item.get("key")).lower()
        value = _clean(item.get("value"))
        if not key or not value:
            continue
        if not any(token in key for token in ("coordinate", "coordinates", "coord", "gps", "lat", "lng", "longitude", "latitude")):
            continue
        coordinates = _normalize_coordinates(value)
        if not coordinates:
            continue
        index_match = re.search(r"\b(?:address|addr)\s*(\d+)\b", key)
        if index_match:
            coordinates_by_key[int(index_match.group(1))] = coordinates
        elif not unassigned:
            label_key = _normalize_coordinate_label(key)
            if label_key:
                coordinates_by_key[label_key] = coordinates
            else:
                unassigned = coordinates
    if unassigned:
        coordinates_by_key.setdefault(0, unassigned)
    return coordinates_by_key


def _is_coordinate_custom_field_key(value: object) -> bool:
    key = _clean(value).lower()
    if not key:
        return False
    return any(token in key for token in ("coordinate", "coordinates", "coord", "gps", "lat", "lng", "longitude", "latitude"))


def _google_url_coordinate_candidates(urls: list[dict]) -> list[dict[str, str]]:
    candidates = []
    for item in urls or []:
        value = _clean(item.get("value"))
        if not value:
            continue
        normalized_value = value.lower()
        if not (
            "maps.google." in normalized_value
            or "google.com/maps" in normalized_value
            or "maps.apple." in normalized_value
        ):
            continue
        coordinates = _normalize_coordinates(value)
        if not coordinates:
            continue
        candidates.append(
            {
                "label": _clean(item.get("formattedType") or item.get("type")),
                "value": value,
                "coordinates": coordinates,
            }
        )
    return candidates


def _google_url_coordinates(
    url_coordinate_candidates: list[dict[str, str]],
    address_type: object,
    position: int,
    address_count: int,
) -> str:
    if not url_coordinate_candidates:
        return ""
    normalized_address_type = _clean(address_type).lower()
    if normalized_address_type:
        for item in url_coordinate_candidates:
            if _clean(item.get("label")).lower() == normalized_address_type:
                return _clean(item.get("coordinates"))
    if address_count == 1 and len(url_coordinate_candidates) == 1:
        return _clean(url_coordinate_candidates[0].get("coordinates"))
    if position <= len(url_coordinate_candidates):
        item = url_coordinate_candidates[position - 1]
        if not _clean(item.get("label")):
            return _clean(item.get("coordinates"))
    return ""


def _google_address_coordinates(
    address: dict,
    user_defined_coordinates: dict[object, str],
    url_coordinate_candidates: list[dict[str, str]],
    position: int,
    address_count: int,
) -> str:
    for key in ("coordinates", "location", "metadata"):
        coordinates = _coordinates_from_mapping(address.get(key))
        if coordinates:
            return coordinates
    address_label_key = _normalize_coordinate_label(address.get("formattedType") or address.get("type"))
    return (
        user_defined_coordinates.get(address_label_key)
        or user_defined_coordinates.get(position)
        or user_defined_coordinates.get(0)
        or _google_url_coordinates(url_coordinate_candidates, address.get("formattedType") or address.get("type"), position, address_count)
        or ""
    )


def _extract_photo_url(person: dict, *, include_default: bool = False) -> str:
    """Return a contact photo URL.

    By default skips Google's letter/default avatars so imports keep custom photos only.
    Pass include_default=True after deleteContactPhoto to store the stock letter avatar URL.
    """
    photos = person.get("photos") or []

    def _is_default(item: dict) -> bool:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return bool(metadata.get("default"))

    def _candidate(item: dict, *, allow_default: bool) -> str:
        if not isinstance(item, dict):
            return ""
        url = _clean(item.get("url"))
        if not url:
            return ""
        if not allow_default and _is_default(item):
            return ""
        return url

    for item in photos:
        url = _candidate(item, allow_default=False)
        if url and (item.get("metadata") or {}).get("primary"):
            return url
    for item in photos:
        url = _candidate(item, allow_default=False)
        if url:
            return url
    if include_default:
        for item in photos:
            url = _candidate(item, allow_default=True)
            if url and (item.get("metadata") or {}).get("primary"):
                return url
        for item in photos:
            url = _candidate(item, allow_default=True)
            if url:
                return url
    return ""


def google_photo_display_url(photo_url: str, *, size: int = 2048) -> str:
    url = _clean(photo_url)
    if not url:
        return ""
    if url.startswith(CONTACT_PHOTO_STATIC_PREFIX):
        return url
    if "googleusercontent.com" not in url and "ggpht.com" not in url:
        return url
    target = max(int(size or 0), 0)
    if re.search(r"=s\d+", url, re.I):
        return re.sub(r"=s\d+(-[a-z0-9]+)?", f"=s{target}", url, flags=re.I)
    if "?" in url:
        if re.search(r"([?&])sz=\d+", url, re.I):
            return re.sub(r"([?&])sz=\d+", rf"\1sz={target}", url, flags=re.I)
        return f"{url}&sz={target}"
    return f"{url}=s{target}"


def _load_contact_children(conn, table_name: str, contact_id: int) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT *
        FROM {table_name}
        WHERE contact_id = ?
        ORDER BY position, id
        """,
        (contact_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_contact_for_sync(conn, contact_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not row:
        return None
    contact = dict(row)
    contact["relationships"] = _load_contact_children(conn, "relationships", contact_id)
    contact["phones"] = _load_contact_children(conn, "phones", contact_id)
    contact["emails"] = _load_contact_children(conn, "emails", contact_id)
    contact["addresses"] = _load_contact_children(conn, "addresses", contact_id)
    contact["custom_fields"] = _load_contact_children(conn, "custom_fields", contact_id)
    return contact


def _parse_birthday(value: str | None) -> dict | None:
    text = _clean(value)
    if not text or text.lower() == "no entry":
        return None
    normalized = text.replace("/", "-").replace(".", "-")
    parts = [part.strip() for part in normalized.split("-") if part.strip()]
    try:
        if len(parts) == 3:
            first, second, third = (int(part) for part in parts)
            if len(parts[0]) == 4:
                year, month, day = first, second, third
            else:
                month, day, year = first, second, third
            if month and day:
                return {"year": year, "month": month, "day": day}
        if len(parts) == 2:
            month, day = (int(part) for part in parts)
            if month and day:
                return {"month": month, "day": day}
    except ValueError:
        return None
    return None


def _normalize_google_type(value: str | None) -> str:
    cleaned = _clean(value)
    normalized = cleaned.lower().replace(" ", "_")
    predefined = {
        "home",
        "work",
        "mobile",
        "cell",
        "main",
        "home_fax",
        "work_fax",
        "pager",
        "other",
        "school",
    }
    if normalized in predefined:
        return normalized
    return cleaned


GOOGLE_PREDEFINED_RELATION_TYPES = {
    "assistant",
    "brother",
    "child",
    "domesticpartner",
    "father",
    "friend",
    "manager",
    "mother",
    "parent",
    "partner",
    "referredby",
    "relative",
    "sister",
    "spouse",
}

GOOGLE_RELATION_TYPE_ALIASES = {
    "domestic_partner": "domesticPartner",
    "domesticpartner": "domesticPartner",
    "referred_by": "referredBy",
    "referredby": "referredBy",
}


CFS_RELATION_TYPES_FIELD = "CFS Relation Types"
CFS_MTG_HOME_ELDER_FIELD = "1 - Meeting home, 2 - Elder"
CFS_MEETS_IN_STATE_FIELD = "Meets in different state"
CFS_DO_NOT_PRINT_FIELD = "Do not Print"
_CFS_MTG_HOME_ELDER_FIELD_KEYS = frozenset(
    {
        CFS_MTG_HOME_ELDER_FIELD.casefold(),
        "meeting/home/elder",
        "mtg home-1/elder-2",
    }
)
_CFS_MEETS_IN_STATE_FIELD_KEYS = frozenset(
    {
        CFS_MEETS_IN_STATE_FIELD.casefold(),
        "meets_in_state",
    }
)
_CFS_DO_NOT_PRINT_FIELD_KEYS = frozenset(
    {
        CFS_DO_NOT_PRINT_FIELD.casefold(),
        "do not print",
        "do_not_print",
    }
)
_LOSSY_GOOGLE_RELATION_TYPES = {"", "other", "spouse"}
_CFS_SPOUSE_LABELS = {"husband", "wife"}


def _google_relation_type(value: str | None) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ""
    normalized = cleaned.lower().replace(" ", "_")
    if normalized in GOOGLE_RELATION_TYPE_ALIASES:
        return GOOGLE_RELATION_TYPE_ALIASES[normalized]
    lookup_key = normalized.replace("_", "")
    if lookup_key in GOOGLE_PREDEFINED_RELATION_TYPES:
        return normalized
    return cleaned


def _is_cfs_relation_types_field(value: object) -> bool:
    return _clean(value).casefold() == CFS_RELATION_TYPES_FIELD.casefold()


def _is_mtg_home_elder_field(value: object) -> bool:
    return _clean(value).casefold() in _CFS_MTG_HOME_ELDER_FIELD_KEYS


def _is_meets_in_state_field(value: object) -> bool:
    return _clean(value).casefold() in _CFS_MEETS_IN_STATE_FIELD_KEYS


def _is_do_not_print_field(value: object) -> bool:
    return _clean(value).casefold() in _CFS_DO_NOT_PRINT_FIELD_KEYS


def _is_cfs_hidden_user_defined_field(value: object) -> bool:
    return (
        _is_cfs_relation_types_field(value)
        or _is_mtg_home_elder_field(value)
        or _is_meets_in_state_field(value)
        or _is_do_not_print_field(value)
    )


def _user_defined_field_value(user_defined, matcher) -> str:
    for item in user_defined or []:
        if not isinstance(item, dict):
            continue
        if matcher(item.get("key") or item.get("field_type")):
            return _clean(item.get("value") or item.get("field_value"))
    return ""


def _mtg_home_elder_flag_from_user_defined(user_defined) -> str:
    """Read the 1/2 code from the Google custom-field VALUE, never from the label."""
    return _user_defined_field_value(user_defined, _is_mtg_home_elder_field)


def _meets_in_state_from_user_defined(user_defined) -> str:
    return _user_defined_field_value(user_defined, _is_meets_in_state_field)


def _do_not_print_flag_from_value(value: object) -> int:
    return 1 if _clean(value).lower() in {"1", "true", "yes", "on"} else 0


def _do_not_print_from_user_defined(user_defined) -> int:
    return _do_not_print_flag_from_value(_user_defined_field_value(user_defined, _is_do_not_print_field))


def _encode_cfs_relation_types(relationships: list) -> str:
    parts: list[str] = []
    for item in relationships or []:
        if not isinstance(item, dict):
            continue
        relation_type = _clean(item.get("relation_type"))
        relation_value = _clean(item.get("relation_value"))
        if not relation_type or not relation_value:
            continue
        lookup_key = relation_type.lower().replace(" ", "").replace("_", "")
        if lookup_key in GOOGLE_PREDEFINED_RELATION_TYPES:
            continue
        parts.append(f"{relation_type}={relation_value}")
    return "|".join(parts)


def _parse_cfs_relation_types(value: object) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for part in _clean(value).split("|"):
        piece = part.strip()
        if "=" not in piece:
            continue
        relation_type, relation_value = piece.split("=", 1)
        relation_type = _clean(relation_type)
        relation_value = _clean(relation_value)
        if relation_type and relation_value:
            mapping[relation_value.casefold()] = relation_type
    return mapping


def _cfs_relation_labels_from_user_defined(user_defined: list) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in user_defined or []:
        if not isinstance(item, dict):
            continue
        if _is_cfs_relation_types_field(item.get("key")):
            mapping.update(_parse_cfs_relation_types(item.get("value")))
    return mapping


def _cfs_relation_labels_from_local(relationships: list) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in relationships or []:
        if not isinstance(item, dict):
            continue
        relation_type = _clean(item.get("relation_type"))
        relation_value = _clean(item.get("relation_value"))
        if relation_type and relation_value:
            mapping[relation_value.casefold()] = relation_type
    return mapping


def _complementary_spouse_label(relation_type: str) -> str:
    key = _clean(relation_type).lower()
    if key == "husband":
        return "Wife"
    if key == "wife":
        return "Husband"
    return ""


def _lookup_complementary_spouse_label(conn, contact_id: int, person_name: str) -> str:
    if conn is None or not contact_id or not _clean(person_name):
        return ""
    me = conn.execute(
        "SELECT given_name, family_name FROM contacts WHERE id = ?",
        (int(contact_id),),
    ).fetchone()
    my_given = _clean(me["given_name"] if me else "")
    my_family = _clean(me["family_name"] if me else "")
    if not my_given:
        return ""
    rows = conn.execute(
        """
        SELECT c.family_name, r.relation_type, r.relation_value
        FROM contacts c
        JOIN relationships r ON r.contact_id = c.id
        WHERE c.id != ?
          AND lower(c.given_name) = lower(?)
          AND lower(r.relation_type) IN ('husband', 'wife')
        """,
        (int(contact_id), _clean(person_name)),
    ).fetchall()
    my_given_key = my_given.casefold()
    family_match = ""
    any_match = ""
    for row in rows:
        if _clean(row["relation_value"]).casefold() != my_given_key:
            continue
        label = _complementary_spouse_label(row["relation_type"])
        if not label:
            continue
        any_match = label
        if my_family and _clean(row["family_name"]).casefold() == my_family.casefold():
            family_match = label
    return family_match or any_match


def _restore_imported_relation_type(
    google_type: str,
    person_name: str,
    *,
    stored_labels: dict[str, str] | None = None,
    local_labels: dict[str, str] | None = None,
    complementary_label: str = "",
) -> str:
    google_type = _clean(google_type)
    person_name = _clean(person_name)
    google_key = google_type.lower().replace(" ", "").replace("_", "")
    stored = _clean((stored_labels or {}).get(person_name.casefold()))
    local = _clean((local_labels or {}).get(person_name.casefold()))
    if stored:
        return stored
    if google_key in _LOSSY_GOOGLE_RELATION_TYPES:
        if local.lower() in _CFS_SPOUSE_LABELS:
            return local
        if complementary_label:
            return complementary_label
        if local and google_key == "other":
            return local
    return google_type


def _relation_compare_key(relation_type: str, relation_value: str) -> tuple[str, str]:
    type_key = _clean(relation_type).lower().replace(" ", "").replace("_", "")
    if type_key in {"husband", "wife", "spouse", "other"}:
        type_key = "spouse"
    return (type_key, _clean(relation_value).casefold())


def _google_address_street_lines(street: object, extended: object) -> tuple[str, str]:
    """Keep street and street 2 as separate lines. A line break inside one field is split out."""
    street_lines = [line.strip() for line in _clean(street).replace("\r", "\n").split("\n") if line.strip()]
    extended_lines = [line.strip() for line in _clean(extended).replace("\r", "\n").split("\n") if line.strip()]
    if len(street_lines) > 1:
        extended_lines = street_lines[1:] + extended_lines
        street_lines = street_lines[:1]
    return (
        street_lines[0] if street_lines else "",
        " ".join(extended_lines),
    )


def _contact_names_payload(contact: dict) -> list[dict]:
    family_name = _google_single_line(contact.get("family_name"))
    given_name = _google_single_line(contact.get("given_name"))
    if not family_name and not given_name:
        return []
    payload = {"displayName": ", ".join(part for part in [family_name, given_name] if part)}
    if family_name:
        payload["familyName"] = family_name
    if given_name:
        payload["givenName"] = given_name
    return [payload]


def _contact_person_payload(contact: dict) -> dict:
    payload: dict[str, list[dict]] = {}

    names = _contact_names_payload(contact)
    if names:
        payload["names"] = names

    field_name = _google_single_line(contact.get("fields_text"))
    meeting_name = _google_single_line(contact.get("meetings_text"))
    if field_name or meeting_name:
        organization_payload = {"current": True}
        if meeting_name:
            organization_payload["name"] = meeting_name
        if field_name:
            organization_payload["title"] = field_name
        payload["organizations"] = [organization_payload]

    phones = []
    for item in contact.get("phones", []):
        phone_value = _clean(item.get("phone_value"))
        if not phone_value:
            continue
        phone_payload = {"value": phone_value}
        phone_type = _normalize_google_type(item.get("phone_type"))
        if phone_type:
            phone_payload["type"] = phone_type
        phones.append(phone_payload)
    if phones:
        payload["phoneNumbers"] = phones

    emails = []
    for item in contact.get("emails", []):
        email_value = _clean(item.get("email_value"))
        if not email_value:
            continue
        email_payload = {"value": email_value}
        email_type = _normalize_google_type(item.get("email_type"))
        if email_type:
            email_payload["type"] = email_type
        emails.append(email_payload)
    if emails:
        payload["emailAddresses"] = emails

    addresses = []
    for item in contact.get("addresses", []):
        enriched = enrich_address_parts(
            street_address=item.get("street_address"),
            extended_address=item.get("extended_address"),
            city=item.get("city"),
            region=item.get("region"),
            postal_code=item.get("postal_code"),
            formatted_address=item.get("formatted_address"),
        )
        street_address, extended_address = _google_address_street_lines(
            enriched.get("street_address"),
            enriched.get("extended_address"),
        )
        city = _google_single_line(enriched.get("city"))
        region = _google_single_line(enriched.get("region"))
        postal_code = _google_single_line(enriched.get("postal_code"))
        formatted_address = build_formatted_address(
            street_address,
            extended_address,
            city,
            region,
            postal_code,
        )
        if not any((formatted_address, street_address, extended_address, city, region, postal_code)):
            continue
        address_payload = {}
        if street_address:
            address_payload["streetAddress"] = street_address
        if extended_address:
            address_payload["extendedAddress"] = extended_address
        if city:
            address_payload["city"] = city
        if region:
            address_payload["region"] = region
        if postal_code:
            address_payload["postalCode"] = postal_code
        # Google Contacts shows formattedValue on the card. Keep the same
        # street / street 2 / city line breaks as the CFS contact card.
        if formatted_address:
            address_payload["formattedValue"] = formatted_address
        address_type = _normalize_google_type(item.get("address_type"))
        if address_type:
            address_payload["type"] = address_type
        addresses.append(address_payload)
    if addresses:
        payload["addresses"] = addresses

    urls = []
    for index, item in enumerate(contact.get("addresses", []), start=1):
        coordinates = _clean(item.get("coordinates"))
        if not coordinates:
            continue
        address_label = _clean(item.get("address_type")) or f"Address {index}"
        url_type = _normalize_google_type(address_label)
        for map_url in (
            _coordinate_map_url("http://maps.apple.com/", coordinates),
            _coordinate_map_url("http://maps.google.com/", coordinates),
        ):
            url_payload = {"value": map_url}
            if url_type:
                url_payload["type"] = url_type
            urls.append(url_payload)
    if urls:
        payload["urls"] = urls

    relations = []
    for item in contact.get("relationships", []):
        relation_value = _clean(item.get("relation_value"))
        relation_type = _google_relation_type(item.get("relation_type"))
        if not relation_value and not relation_type:
            continue
        relation_payload = {}
        if relation_value:
            relation_payload["person"] = relation_value
        if relation_type:
            relation_payload["type"] = relation_type
        relations.append(relation_payload)
    if relations:
        payload["relations"] = relations

    custom_fields = []
    seen_custom_keys: set[str] = set()

    def _append_custom_field(key: str, value: str) -> None:
        field_key = _google_single_line(key)
        field_value = _clean(value)
        if not field_key or not field_value:
            return
        unique_key = field_key
        suffix = 2
        while unique_key.casefold() in seen_custom_keys:
            unique_key = f"{field_key} {suffix}"
            suffix += 1
        seen_custom_keys.add(unique_key.casefold())
        custom_fields.append({"key": unique_key, "value": field_value})

    for index, item in enumerate(contact.get("addresses", []), start=1):
        coordinates = _clean(item.get("coordinates"))
        if coordinates:
            address_label = _clean(item.get("address_type")) or f"Address {index}"
            _append_custom_field(f"{address_label} - Coordinates", coordinates)
    for item in contact.get("custom_fields", []):
        field_key = _clean(item.get("field_type"))
        field_value = _clean(item.get("field_value"))
        if _is_cfs_hidden_user_defined_field(field_key):
            continue
        if _is_coordinate_custom_field_key(field_key) and _normalize_coordinates(field_value):
            continue
        _append_custom_field(field_key, field_value)
    encoded_relation_types = _encode_cfs_relation_types(contact.get("relationships"))
    if encoded_relation_types:
        _append_custom_field(CFS_RELATION_TYPES_FIELD, encoded_relation_types)
    mtg_home_elder_flag = _clean(contact.get("mtg_home_elder_flag")) or _mtg_home_elder_flag_from_user_defined(
        contact.get("custom_fields")
    )
    if mtg_home_elder_flag:
        _append_custom_field(CFS_MTG_HOME_ELDER_FIELD, mtg_home_elder_flag)
    meets_in_state = _clean(contact.get("meets_in_state")) or _meets_in_state_from_user_defined(
        contact.get("custom_fields")
    )
    if meets_in_state:
        _append_custom_field(CFS_MEETS_IN_STATE_FIELD, meets_in_state)
    if _do_not_print_flag_from_value(contact.get("do_not_print")):
        _append_custom_field(CFS_DO_NOT_PRINT_FIELD, "1")
    if custom_fields:
        payload["userDefined"] = custom_fields

    note_text = _google_plain_notes(contact.get("notes"))
    if note_text and note_text.lower() != "no entry":
        payload["biographies"] = [{"value": note_text, "contentType": "TEXT_PLAIN"}]

    birthday = _parse_birthday(contact.get("birthday"))
    if birthday:
        month = int(birthday.get("month") or 0)
        day = int(birthday.get("day") or 0)
        year = birthday.get("year")
        if 1 <= month <= 12 and 1 <= day <= 31:
            if year is not None and not (1 <= int(year) <= 9999):
                birthday = {"month": month, "day": day}
            payload["birthdays"] = [{"date": birthday}]

    return payload


def _desired_group_names(contact: dict) -> list[str]:
    values = _split_group_membership_text(contact.get("group_membership"))
    field_name = _clean(contact.get("fields_text"))
    meeting_name = _clean(contact.get("meetings_text"))
    if field_name and meeting_name:
        shared_group_names = _shared_group_membership_names(field_name, meeting_name)
        canonical_names = {value.lower() for value in shared_group_names}
        values = [value for value in values if " - " not in value and _clean(value).lower() not in canonical_names]
        values.extend(shared_group_names)
    elif "myContacts" not in values:
        values.append("myContacts")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = _clean(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _extract_contact_group_memberships(person: dict) -> set[str]:
    groups: set[str] = set()
    for membership in person.get("memberships") or []:
        group_membership = membership.get("contactGroupMembership") or {}
        resource_name = _clean(group_membership.get("contactGroupResourceName"))
        if resource_name:
            groups.add(resource_name)
    return groups


def _fetch_google_person(access_token: str, resource_name: str) -> dict:
    url = (
        f"{GOOGLE_PEOPLE_BASE_URL}/{resource_name}"
        f"?personFields={GOOGLE_UPLOAD_PERSON_FIELDS}"
    )
    return _api_get_json(url, access_token)


def _ensure_contact_group_resource(
    access_token: str,
    group_name: str,
    name_to_resource: dict[str, str],
    resource_to_name: dict[str, str],
) -> str:
    normalized_name = _clean(group_name)
    if not normalized_name:
        return ""
    existing = name_to_resource.get(normalized_name)
    if existing:
        return existing
    if normalized_name in {"myContacts", "starred"}:
        resource_name = f"contactGroups/{normalized_name}"
        name_to_resource[normalized_name] = resource_name
        resource_to_name[resource_name] = normalized_name
        return resource_name

    payload = _api_json_request(
        f"{GOOGLE_PEOPLE_BASE_URL}/contactGroups",
        access_token,
        method="POST",
        json_data={"contactGroup": {"name": normalized_name}},
    )
    resource_name = _clean(payload.get("resourceName"))
    formatted_name = _clean(payload.get("formattedName") or payload.get("name")) or normalized_name
    if resource_name:
        name_to_resource[formatted_name] = resource_name
        name_to_resource[normalized_name] = resource_name
        resource_to_name[resource_name] = formatted_name
    return resource_name


def _sync_google_contact_groups(
    access_token: str,
    resource_name: str,
    current_person: dict,
    desired_group_names: list[str],
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
) -> None:
    desired_resources = {
        _ensure_contact_group_resource(access_token, group_name, name_to_resource, resource_to_name)
        for group_name in desired_group_names
    }
    desired_resources.discard("")
    desired_resources = {
        group_resource
        for group_resource in desired_resources
        if not _is_system_contact_group_resource(group_resource, resource_to_name)
    }
    current_resources = {
        group_resource
        for group_resource in _extract_contact_group_memberships(current_person)
        if not _is_system_contact_group_resource(group_resource, resource_to_name)
    }
    removed_resources = sorted(current_resources - desired_resources)

    for group_resource in removed_resources:
        _api_json_request(
            f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}/members:modify",
            access_token,
            method="POST",
            json_data={"resourceNamesToRemove": [resource_name]},
        )

    for group_resource in sorted(desired_resources - current_resources):
        _api_json_request(
            f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}/members:modify",
            access_token,
            method="POST",
            json_data={"resourceNamesToAdd": [resource_name]},
        )

    _cleanup_empty_contact_group_resources(
        access_token,
        removed_resources,
        resource_to_name,
        name_to_resource,
        removed_contact_resources=[resource_name],
    )


def _chunked(values: list[str], size: int = 200) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


GOOGLE_GROUP_SYNC_FLUSH_EVERY = 40


def _flush_pending_group_sync(
    group_sync_batch: dict[str, dict[str, set[str]]],
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
) -> str:
    """Write batched Google label adds/removes now so a later failure cannot drop them."""
    if not group_sync_batch:
        return ""
    try:
        access_token, _account_email = _get_valid_access_token()
        _flush_google_contact_group_batch(access_token, group_sync_batch, resource_to_name, name_to_resource)
        group_sync_batch.clear()
        return ""
    except HTTPError as exc:
        message = _http_error_message(exc) or _clean(str(exc))
        if _looks_like_expired_access_token_error(exc, message):
            try:
                access_token, _account_email = _get_valid_access_token(force_refresh=True)
                _flush_google_contact_group_batch(
                    access_token, group_sync_batch, resource_to_name, name_to_resource
                )
                group_sync_batch.clear()
                return ""
            except Exception as retry_exc:
                return _clean(str(retry_exc)) or "Google contact group update failed."
        return _clean(message) or "Google contact group update failed."
    except Exception as exc:
        return _clean(str(exc)) or "Google contact group update failed."


def _queue_google_contact_group_sync(
    batch: dict[str, dict[str, set[str]]],
    access_token: str,
    resource_name: str,
    current_person: dict,
    desired_group_names: list[str],
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
) -> None:
    desired_resources = {
        _ensure_contact_group_resource(access_token, group_name, name_to_resource, resource_to_name)
        for group_name in desired_group_names
    }
    desired_resources.discard("")
    desired_resources = {
        group_resource
        for group_resource in desired_resources
        if not _is_system_contact_group_resource(group_resource, resource_to_name)
    }
    current_resources = {
        group_resource
        for group_resource in _extract_contact_group_memberships(current_person)
        if not _is_system_contact_group_resource(group_resource, resource_to_name)
    }
    for group_resource in current_resources - desired_resources:
        batch.setdefault(group_resource, {"add": set(), "remove": set()})["remove"].add(resource_name)
    for group_resource in desired_resources - current_resources:
        batch.setdefault(group_resource, {"add": set(), "remove": set()})["add"].add(resource_name)


def _flush_google_contact_group_batch(
    access_token: str,
    batch: dict[str, dict[str, set[str]]],
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
) -> None:
    removed_by_group: dict[str, set[str]] = {}
    for group_resource in sorted(batch):
        changes = batch[group_resource]
        remove_names = sorted(changes.get("remove") or set())
        add_names = sorted(changes.get("add") or set())
        if remove_names:
            for chunk in _chunked(remove_names):
                _api_json_request(
                    f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}/members:modify",
                    access_token,
                    method="POST",
                    json_data={"resourceNamesToRemove": chunk},
                )
            removed_by_group.setdefault(group_resource, set()).update(remove_names)
        if add_names:
            for chunk in _chunked(add_names):
                _api_json_request(
                    f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}/members:modify",
                    access_token,
                    method="POST",
                    json_data={"resourceNamesToAdd": chunk},
                )

    cleanup_group_resources = [
        group_resource
        for group_resource, changes in batch.items()
        if changes.get("remove") and not _is_system_contact_group_resource(group_resource, resource_to_name)
    ]
    removed_contact_resources = sorted(
        {
            resource_name
            for group_resource in cleanup_group_resources
            for resource_name in removed_by_group.get(group_resource, set())
        }
    )
    _cleanup_empty_contact_group_resources(
        access_token,
        cleanup_group_resources,
        resource_to_name,
        name_to_resource,
        removed_contact_resources=removed_contact_resources,
    )


def _is_system_contact_group_resource(group_resource: str, resource_to_name: dict[str, str]) -> bool:
    system_group_names = {"myContacts", "starred"}
    system_group_resources = {"contactGroups/myContacts", "contactGroups/starred"}
    group_name = _clean(resource_to_name.get(group_resource))
    return group_resource in system_group_resources or group_name in system_group_names


def _remove_contact_from_group_resources(
    access_token: str,
    resource_name: str,
    group_resources: list[str],
    resource_to_name: dict[str, str],
) -> list[str]:
    removed_resources: list[str] = []
    seen_resources: set[str] = set()
    for group_resource_value in group_resources:
        group_resource = _clean(group_resource_value)
        if (
            not group_resource
            or group_resource in seen_resources
            or _is_system_contact_group_resource(group_resource, resource_to_name)
        ):
            continue
        seen_resources.add(group_resource)
        try:
            _api_json_request(
                f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}/members:modify",
                access_token,
                method="POST",
                json_data={"resourceNamesToRemove": [resource_name]},
            )
        except HTTPError as exc:
            if exc.code not in {400, 404}:
                raise
            continue
        removed_resources.append(group_resource)
    return removed_resources


def _contact_group_member_state(access_token: str, group_resource: str) -> tuple[int | None, list[str]]:
    detail = _api_get_json(
        f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}?{urlencode({'maxMembers': 1000})}",
        access_token,
    )
    visible_members = [
        _clean(item)
        for item in (detail.get("memberResourceNames") or [])
        if _clean(item)
    ]
    try:
        member_count = int(detail.get("memberCount"))
    except (TypeError, ValueError):
        member_count = len(visible_members) if "memberResourceNames" in detail else None
    return member_count, visible_members


def _cleanup_empty_contact_group_resources(
    access_token: str,
    group_resources: list[str],
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
    removed_contact_resources: list[str] | None = None,
) -> None:
    removed_contacts = {_clean(item) for item in (removed_contact_resources or []) if _clean(item)}
    seen_resources: set[str] = set()
    for group_resource_value in group_resources:
        group_resource = _clean(group_resource_value)
        if (
            not group_resource
            or group_resource in seen_resources
            or _is_system_contact_group_resource(group_resource, resource_to_name)
        ):
            continue
        seen_resources.add(group_resource)
        member_count: int | None = None
        visible_members: list[str] = []
        group_missing = False
        for attempt in range(3):
            try:
                member_count, visible_members = _contact_group_member_state(access_token, group_resource)
            except HTTPError as exc:
                if exc.code == 404:
                    member_count = None
                    visible_members = []
                    group_missing = True
                    break
                raise

            if visible_members and removed_contacts and set(visible_members).issubset(removed_contacts):
                for visible_member in visible_members:
                    _remove_contact_from_group_resources(access_token, visible_member, [group_resource], resource_to_name)
                time.sleep(0.5 * (attempt + 1))
                continue
            if member_count == 0 or not visible_members or attempt >= 2:
                break
            time.sleep(0.5 * (attempt + 1))
        if group_missing:
            continue
        if member_count not in {0, None} and visible_members:
            continue
        try:
            _api_json_request(
                f"{GOOGLE_PEOPLE_BASE_URL}/{group_resource}?{urlencode({'deleteContacts': 'false'})}",
                access_token,
                method="DELETE",
            )
        except HTTPError as exc:
            if exc.code not in {400, 404}:
                raise
            continue
        deleted_name = resource_to_name.pop(group_resource, "")
        if deleted_name:
            name_to_resource.pop(deleted_name, None)


def _cleanup_empty_contact_groups(
    access_token: str,
    group_names: list[str],
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
    removed_contact_resources: list[str] | None = None,
) -> None:
    group_resources = [
        _clean(name_to_resource.get(_clean(group_name)))
        for group_name in group_names
        if _clean(group_name)
    ]
    _cleanup_empty_contact_group_resources(
        access_token,
        group_resources,
        resource_to_name,
        name_to_resource,
        removed_contact_resources=removed_contact_resources,
    )


def _cleanup_empty_app_contact_groups(
    access_token: str,
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
) -> None:
    group_resources = [
        resource_name
        for resource_name, group_name in resource_to_name.items()
        if _clean(group_name).endswith("(Shared)")
    ]
    _cleanup_empty_contact_group_resources(access_token, group_resources, resource_to_name, name_to_resource)


def _previous_group_names_from_payload(payload: dict) -> list[str]:
    values = _split_group_membership_text(payload.get("previous_group_membership"))
    field_name = _clean(payload.get("previous_fields_text"))
    meeting_name = _clean(payload.get("previous_meetings_text"))
    if field_name and meeting_name:
        modern_group_name = f"{field_name} - {meeting_name}"
        legacy_group_name = f"{field_name} - {_strip_trailing_parenthetical(meeting_name)} (Shared)"
        shared_group_names = _shared_group_membership_names(field_name, meeting_name)
        existing_names = {value.lower() for value in values}
        if modern_group_name.lower() not in existing_names and legacy_group_name.lower() not in existing_names:
            values.append(modern_group_name)
        values.extend(shared_group_names)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = _clean(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _update_local_contact_google_state(
    conn,
    contact_id: int,
    google_contact_id: str,
    etag: str,
    status: str,
) -> None:
    conn.execute(
        """
        UPDATE contacts
        SET
          google_contact_id = ?,
          etag = ?,
          last_updated = ?,
          status = ?
        WHERE id = ?
        """,
        (google_contact_id, etag, _now_text(), status, contact_id),
    )


def _delete_queue_entry(conn, queue_id: int) -> None:
    conn.execute("DELETE FROM google_sync_queue WHERE id = ?", (queue_id,))


def _update_queue_error(conn, queue_id: int, message: str) -> None:
    conn.execute(
        """
        UPDATE google_sync_queue
        SET
          last_error = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (str(message or "").strip(), _now_text(), queue_id),
    )


def _queue_entry_google_contact_id(queue_entry: dict, payload: dict | None = None) -> str:
    payload = payload if isinstance(payload, dict) else _safe_load_json(queue_entry.get("payload_json"))
    return _clean(queue_entry.get("google_contact_id") or payload.get("google_contact_id"))


def _resolve_queue_entry_contact_id(conn, queue_entry: dict, payload: dict | None = None) -> int | None:
    payload = payload if isinstance(payload, dict) else _safe_load_json(queue_entry.get("payload_json"))
    raw_contact_id = queue_entry.get("contact_id")
    if raw_contact_id is not None:
        try:
            contact_id = int(raw_contact_id)
        except (TypeError, ValueError):
            contact_id = 0
        if contact_id > 0:
            return contact_id

    google_contact_id = _queue_entry_google_contact_id(queue_entry, payload)
    if google_contact_id:
        row = conn.execute(
            "SELECT id FROM contacts WHERE google_contact_id = ? LIMIT 1",
            (google_contact_id,),
        ).fetchone()
        if row:
            return int(row["id"])
    return None


def _discard_stale_queue_entry(conn, queue_entry: dict) -> None:
    _delete_queue_entry(conn, int(queue_entry["id"]))
    _refresh_pending_upload_count(conn)


def _process_delete_queue_entry(
    access_token: str,
    queue_entry: dict,
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
) -> None:
    payload = _safe_load_json(queue_entry.get("payload_json"))
    google_contact_id = _queue_entry_google_contact_id(queue_entry, payload)
    if not google_contact_id:
        return
    current_group_resources: list[str] = []
    try:
        current_person = _fetch_google_person(access_token, google_contact_id)
        current_group_resources = sorted(_extract_contact_group_memberships(current_person))
    except HTTPError as exc:
        if exc.code != 404:
            raise

    removed_group_resources = _remove_contact_from_group_resources(
        access_token,
        google_contact_id,
        current_group_resources,
        resource_to_name,
    )
    try:
        _api_json_request(
            f"{GOOGLE_PEOPLE_BASE_URL}/{google_contact_id}:deleteContact",
            access_token,
            method="DELETE",
        )
    except HTTPError as exc:
        if exc.code != 404:
            raise
    _cleanup_empty_contact_group_resources(
        access_token,
        removed_group_resources,
        resource_to_name,
        name_to_resource,
        removed_contact_resources=[google_contact_id],
    )
    group_names = payload.get("group_names")
    if isinstance(group_names, list):
        _cleanup_empty_contact_groups(
            access_token,
            [str(item or "") for item in group_names],
            resource_to_name,
            name_to_resource,
            removed_contact_resources=[google_contact_id],
        )


def _process_upsert_queue_entry(
    conn,
    access_token: str,
    queue_entry: dict,
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
    group_sync_batch: dict[str, dict[str, set[str]]] | None = None,
) -> None:
    raw_contact_id = queue_entry.get("contact_id")
    if raw_contact_id is None:
        raise RuntimeError("Queued contact upload is missing a local contact reference.")
    contact_id = int(raw_contact_id)
    contact = _load_contact_for_sync(conn, contact_id)
    if not contact:
        return

    queue_payload = _safe_load_json(queue_entry.get("payload_json"))
    person_payload = _contact_person_payload(contact)
    desired_group_names = _desired_group_names(contact)
    google_contact_id = _clean(contact.get("google_contact_id"))
    if _clean(queue_payload.get("sync_action")) == "Shared group label" and google_contact_id:
        # Apply only needs Google labels. Skip GET/PATCH/photo so a 730-contact
        # backfill cannot die halfway through rewriting unchanged contact bodies.
        if group_sync_batch is not None:
            _queue_google_contact_group_sync(
                group_sync_batch,
                access_token,
                google_contact_id,
                {},
                desired_group_names,
                resource_to_name,
                name_to_resource,
            )
        else:
            _sync_google_contact_groups(
                access_token,
                google_contact_id,
                {},
                desired_group_names,
                resource_to_name,
                name_to_resource,
            )
        return

    if google_contact_id:
        try:
            current_person = _fetch_google_person(access_token, google_contact_id)
        except HTTPError as exc:
            if exc.code == 404:
                google_contact_id = ""
                current_person = {}
            else:
                raise
    else:
        current_person = {}

    if not google_contact_id:
        created = _api_json_request(
            f"{GOOGLE_PEOPLE_BASE_URL}/people:createContact?personFields={urlencode({'fields': GOOGLE_UPLOAD_PERSON_FIELDS})[7:]}",
            access_token,
            method="POST",
            json_data=person_payload,
        )
        resource_name = _clean(created.get("resourceName"))
        etag = _clean(created.get("etag"))
        if not etag:
            for source in (created.get("metadata") or {}).get("sources") or []:
                etag = _clean(source.get("etag"))
                if etag:
                    break
        _update_local_contact_google_state(conn, contact_id, resource_name, etag, "synced")
        current_person = created
        _sync_google_contact_groups(access_token, resource_name, current_person, desired_group_names, resource_to_name, name_to_resource)
        photo_bytes = _local_contact_photo_bytes(contact.get("photo"))
        if photo_bytes:
            _update_google_contact_photo(access_token, resource_name, photo_bytes)
        return

    source_payload = _google_contact_source_for_update(current_person)
    if not source_payload.get("etag"):
        raise RuntimeError("Google contact metadata is missing the CONTACT source required for update.")

    update_payload = dict(person_payload)
    update_payload["resourceName"] = google_contact_id
    update_payload["etag"] = _clean(current_person.get("etag"))
    update_payload["metadata"] = {"sources": [source_payload]}
    updated = _api_json_request(
        (
            f"{GOOGLE_PEOPLE_BASE_URL}/{google_contact_id}:updateContact"
            f"?updatePersonFields={GOOGLE_UPLOAD_UPDATE_FIELDS}"
            f"&personFields={GOOGLE_UPLOAD_PERSON_FIELDS}"
        ),
        access_token,
        method="PATCH",
        json_data=update_payload,
    )
    if group_sync_batch is not None:
        _queue_google_contact_group_sync(
            group_sync_batch,
            access_token,
            google_contact_id,
            current_person,
            desired_group_names,
            resource_to_name,
            name_to_resource,
        )
    else:
        _sync_google_contact_groups(access_token, google_contact_id, current_person, desired_group_names, resource_to_name, name_to_resource)
    photo_bytes = _local_contact_photo_bytes(contact.get("photo"))
    if photo_bytes:
        _update_google_contact_photo(access_token, google_contact_id, photo_bytes)
    elif not _clean(contact.get("photo")):
        # Intentionally cleared in CFS — remove custom Google photo so stock avatar shows.
        try:
            _delete_google_contact_photo(access_token, google_contact_id)
        except (RuntimeError, HTTPError, URLError):
            pass
    previous_group_names = _previous_group_names_from_payload(queue_payload)
    if previous_group_names and group_sync_batch is None:
        _cleanup_empty_contact_groups(
            access_token,
            previous_group_names,
            resource_to_name,
            name_to_resource,
            removed_contact_resources=[google_contact_id],
        )
    etag = _clean(updated.get("etag"))
    if not etag:
        for source in (updated.get("metadata") or {}).get("sources") or []:
            etag = _clean(source.get("etag"))
            if etag:
                break
    _update_local_contact_google_state(conn, contact_id, google_contact_id, etag, "synced")


def _clear_contact_child_rows(conn, contact_id: int) -> None:
    conn.execute("DELETE FROM relationships WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM phones WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM emails WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM addresses WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM custom_fields WHERE contact_id = ?", (contact_id,))


def contact_has_pending_google_sync(
    contact_id: int | None = None,
    google_contact_id: str = "",
) -> bool:
    """True when this contact still has a queued Google Contacts upload."""
    with get_connection() as conn:
        return _contact_has_pending_google_sync(
            conn,
            contact_id=contact_id,
            google_contact_id=google_contact_id,
        )


def _contact_has_pending_google_sync(
    conn,
    *,
    contact_id: int | None = None,
    google_contact_id: str = "",
) -> bool:
    if contact_id:
        row = conn.execute(
            """
            SELECT 1
            FROM google_sync_queue
            WHERE status = 'pending' AND contact_id = ?
            LIMIT 1
            """,
            (int(contact_id),),
        ).fetchone()
        if row:
            return True
    resource_name = _clean(google_contact_id)
    if resource_name:
        row = conn.execute(
            """
            SELECT 1
            FROM google_sync_queue
            WHERE status = 'pending' AND google_contact_id = ?
            LIMIT 1
            """,
            (resource_name,),
        ).fetchone()
        if row:
            return True
    return False


def _google_person_import_fields(person: dict, groups_map: dict[str, str]) -> dict | None:
    names = person.get("names") or []
    display_name = _primary_text(names, "displayName")
    family_name = _primary_text(names, "familyName")
    given_name = _primary_text(names, "givenName")
    group_names = []
    for membership in person.get("memberships") or []:
        group_membership = membership.get("contactGroupMembership") or {}
        resource_name = _clean(group_membership.get("contactGroupResourceName"))
        if resource_name and resource_name in groups_map:
            group_names.append(groups_map[resource_name])
    fields_text, meetings_text = _resolve_field_meeting(group_names, person.get("organizations") or [])
    birthdays = person.get("birthdays") or []
    birthday_text = ""
    for birthday in birthdays:
        date = birthday.get("date") or {}
        year = date.get("year")
        month = date.get("month")
        day = date.get("day")
        if month and day:
            if year:
                birthday_text = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                birthday_text = f"{month:02d}-{day:02d}"
            break
    biographies = person.get("biographies") or []
    notes = _primary_text(biographies, "value")
    user_defined = person.get("userDefined") or []
    user_defined_coordinates = _google_user_defined_coordinates(user_defined)
    addresses = person.get("addresses") or []
    url_coordinate_candidates = _google_url_coordinate_candidates(person.get("urls") or [])
    metadata = person.get("metadata") or {}
    sources = metadata.get("sources") or []
    etag = _clean(person.get("etag"))
    if not etag:
        for source in sources:
            etag = _clean(source.get("etag"))
            if etag:
                break
    resource_name = _clean(person.get("resourceName"))
    if not resource_name:
        return None
    return {
        "resource_name": resource_name,
        "etag": etag,
        "fields_text": fields_text,
        "meetings_text": meetings_text,
        "group_names": group_names,
        "family_name": family_name or display_name,
        "given_name": given_name,
        "photo": _extract_photo_url(person),
        "birthday": birthday_text,
        "notes": notes,
        "relations": person.get("relations") or [],
        "phones": person.get("phoneNumbers") or [],
        "emails": person.get("emailAddresses") or [],
        "addresses": addresses,
        "user_defined": user_defined,
        "user_defined_coordinates": user_defined_coordinates,
        "url_coordinate_candidates": url_coordinate_candidates,
        "mtg_home_elder_flag": _mtg_home_elder_flag_from_user_defined(user_defined),
        "meets_in_state": _meets_in_state_from_user_defined(user_defined),
        "do_not_print": _do_not_print_from_user_defined(user_defined),
    }


def _normalize_google_formatted_address(value: str) -> str:
    from app.services.address_format import enrich_address_parts

    text = _clean(str(value or "").replace("\r", "\n"))
    if not text:
        return text
    enriched = enrich_address_parts(formatted_address=text)
    return enriched["formatted_address"] or text


def _google_address_structured_fields(address: dict) -> dict[str, str]:
    enriched = enrich_address_parts(
        street_address=_clean(address.get("streetAddress")),
        extended_address=_clean(address.get("extendedAddress")),
        city=_clean(address.get("city")),
        region=_clean(address.get("region")),
        postal_code=_clean(address.get("postalCode")),
        formatted_address=_normalize_google_formatted_address(address.get("formattedValue")),
    )
    return enriched


def _insert_contact_child_rows_from_google(
    conn,
    contact_id: int,
    fields: dict,
    *,
    local_relationships: list | None = None,
) -> None:
    stored_labels = _cfs_relation_labels_from_user_defined(fields.get("user_defined") or [])
    local_labels = _cfs_relation_labels_from_local(local_relationships or [])
    for index, relation in enumerate(fields.get("relations") or [], start=1):
        google_type = _clean(relation.get("type") or relation.get("formattedType"))
        relation_value = _clean(relation.get("person"))
        complementary_label = ""
        google_key = google_type.lower().replace(" ", "").replace("_", "")
        if google_key in _LOSSY_GOOGLE_RELATION_TYPES:
            complementary_label = _lookup_complementary_spouse_label(conn, contact_id, relation_value)
        relation_type = _restore_imported_relation_type(
            google_type,
            relation_value,
            stored_labels=stored_labels,
            local_labels=local_labels,
            complementary_label=complementary_label,
        )
        if relation_type or relation_value:
            conn.execute(
                """
                INSERT INTO relationships (contact_id, position, relation_type, relation_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, relation_type, relation_value),
            )

    for index, phone in enumerate(fields.get("phones") or [], start=1):
        phone_type = _clean(phone.get("formattedType") or phone.get("type"))
        phone_value = _clean(phone.get("value"))
        if phone_type or phone_value:
            conn.execute(
                """
                INSERT INTO phones (contact_id, position, phone_type, phone_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, phone_type, phone_value),
            )

    for index, email in enumerate(fields.get("emails") or [], start=1):
        email_type = _clean(email.get("formattedType") or email.get("type"))
        email_value = _clean(email.get("value"))
        if email_type or email_value:
            conn.execute(
                """
                INSERT INTO emails (contact_id, position, email_type, email_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, email_type, email_value),
            )

    addresses = fields.get("addresses") or []
    user_defined_coordinates = fields.get("user_defined_coordinates") or {}
    url_coordinate_candidates = fields.get("url_coordinate_candidates") or []
    for index, address in enumerate(addresses, start=1):
        address_type = _clean(address.get("formattedType") or address.get("type"))
        structured = _google_address_structured_fields(address)
        formatted_address = structured["formatted_address"]
        street_address = structured["street_address"]
        extended_address = structured["extended_address"]
        city = structured["city"]
        region = structured["region"]
        postal_code = structured["postal_code"]
        coordinates = _google_address_coordinates(
            address,
            user_defined_coordinates,
            url_coordinate_candidates,
            index,
            len(addresses),
        )
        if address_type or formatted_address or coordinates or any(
            (street_address, extended_address, city, region, postal_code)
        ):
            conn.execute(
                """
                INSERT INTO addresses (
                  contact_id, position, address_type,
                  street_address, extended_address, city, region, postal_code,
                  formatted_address, coordinates
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id,
                    index,
                    address_type,
                    street_address,
                    extended_address,
                    city,
                    region,
                    postal_code,
                    formatted_address,
                    coordinates,
                ),
            )

    for index, item in enumerate(fields.get("user_defined") or [], start=1):
        field_type = _clean(item.get("key"))
        field_value = _clean(item.get("value"))
        if _is_cfs_hidden_user_defined_field(field_type):
            continue
        if _is_coordinate_custom_field_key(field_type) and _normalize_coordinates(field_value):
            continue
        if field_type or field_value:
            conn.execute(
                """
                INSERT INTO custom_fields (contact_id, position, field_type, field_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, field_type, field_value),
            )


def _insert_contact_from_google_person(conn, fields: dict, *, status: str = "imported") -> int:
    cursor = conn.execute(
        """
        INSERT INTO contacts (
          selected,
          google_contact_id,
          etag,
          last_updated,
          status,
          fields_text,
          meetings_text,
          group_membership,
          family_name,
          given_name,
          photo,
          birthday,
          notes,
          mtg_home_elder_flag,
          meets_in_state,
          do_not_print
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "",
            fields["resource_name"],
            fields["etag"],
            _now_text(),
            status,
            fields["fields_text"],
            fields["meetings_text"],
            _group_membership_text(
                _shared_group_membership_names(fields["fields_text"], fields["meetings_text"])
                or fields["group_names"]
            ),
            fields["family_name"],
            fields["given_name"],
            fields["photo"],
            fields["birthday"],
            fields["notes"],
            _clean(fields.get("mtg_home_elder_flag"))
            or _mtg_home_elder_flag_from_user_defined(fields.get("user_defined")),
            _clean(fields.get("meets_in_state"))
            or _meets_in_state_from_user_defined(fields.get("user_defined")),
            _do_not_print_flag_from_value(fields.get("do_not_print"))
            or _do_not_print_from_user_defined(fields.get("user_defined")),
        ),
    )
    contact_id = int(cursor.lastrowid)
    _insert_contact_child_rows_from_google(conn, contact_id, fields)
    return contact_id


def _update_contact_from_google_person(conn, contact_id: int, fields: dict) -> None:
    existing = conn.execute(
        """
        SELECT photo, photo_drive_file_id, mtg_home_elder_flag, meets_in_state, do_not_print, notes
        FROM contacts
        WHERE id = ?
        """,
        (int(contact_id),),
    ).fetchone()
    local_relationships = _load_contact_children(conn, "relationships", contact_id)
    photo = fields["photo"]
    existing_row = dict(existing) if existing else {}
    if existing_row and _remote_has_custom_drive_photo(existing_row):
        photo = _clean_contact_photo_path(existing_row["photo"])
    incoming_mtg = _clean(fields.get("mtg_home_elder_flag")) or _mtg_home_elder_flag_from_user_defined(
        fields.get("user_defined")
    )
    mtg_home_elder_flag = _clean(existing_row.get("mtg_home_elder_flag")) or incoming_mtg
    incoming_meets = _clean(fields.get("meets_in_state")) or _meets_in_state_from_user_defined(
        fields.get("user_defined")
    )
    meets_in_state = _clean(existing_row.get("meets_in_state")) or incoming_meets
    incoming_do_not_print = _do_not_print_flag_from_value(fields.get("do_not_print")) or _do_not_print_from_user_defined(
        fields.get("user_defined")
    )
    do_not_print = _do_not_print_flag_from_value(existing_row.get("do_not_print")) or incoming_do_not_print
    conn.execute(
        """
        UPDATE contacts
        SET
          google_contact_id = ?,
          etag = ?,
          last_updated = ?,
          status = ?,
          fields_text = ?,
          meetings_text = ?,
          group_membership = ?,
          family_name = ?,
          given_name = ?,
          photo = ?,
          birthday = ?,
          notes = ?,
          mtg_home_elder_flag = ?,
          meets_in_state = ?,
          do_not_print = ?
        WHERE id = ?
        """,
        (
            fields["resource_name"],
            fields["etag"],
            _now_text(),
            "imported",
            fields["fields_text"],
            fields["meetings_text"],
            _group_membership_text(
                _shared_group_membership_names(fields["fields_text"], fields["meetings_text"])
                or fields["group_names"]
            ),
            fields["family_name"],
            fields["given_name"],
            photo,
            fields["birthday"],
            _prefer_local_print_code_notes(existing_row.get("notes"), fields["notes"]),
            mtg_home_elder_flag,
            meets_in_state,
            do_not_print,
            contact_id,
        ),
    )
    _clear_contact_child_rows(conn, contact_id)
    _insert_contact_child_rows_from_google(
        conn,
        contact_id,
        fields,
        local_relationships=local_relationships,
    )


def _delete_local_contact_by_google_id(conn, google_contact_id: str) -> bool:
    gid = _clean(google_contact_id)
    if not gid:
        return False
    rows = conn.execute(
        "SELECT id FROM contacts WHERE google_contact_id = ? ORDER BY id",
        (gid,),
    ).fetchall()
    if not rows:
        return False
    for row in rows:
        _delete_local_contact_record(conn, int(row["id"]))
    return True


def _google_import_repeatable_rows(fields: dict) -> dict[str, list[tuple[str, str]]]:
    phones = [
        (_clean(phone.get("formattedType") or phone.get("type")), _clean(phone.get("value")))
        for phone in (fields.get("phones") or [])
        if isinstance(phone, dict)
    ]
    emails = [
        (_clean(email.get("formattedType") or email.get("type")), _clean(email.get("value")))
        for email in (fields.get("emails") or [])
        if isinstance(email, dict)
    ]
    relationships = [
        (_clean(relation.get("type") or relation.get("formattedType")), _clean(relation.get("person")))
        for relation in (fields.get("relations") or [])
        if isinstance(relation, dict)
    ]
    addresses = [
        (_clean(address.get("formattedType") or address.get("type")), _clean(address.get("formattedValue")))
        for address in (fields.get("addresses") or [])
        if isinstance(address, dict)
    ]
    return {
        "phones": [row for row in phones if any(row)],
        "emails": [row for row in emails if any(row)],
        "relationships": [row for row in relationships if any(row)],
        "addresses": [row for row in addresses if any(row)],
    }


def _local_import_repeatable_rows(contact: dict) -> dict[str, list[tuple[str, str]]]:
    return {
        "phones": [
            (_clean(row.get("phone_type")), _clean(row.get("phone_value")))
            for row in (contact.get("phones") or [])
            if isinstance(row, dict) and (_clean(row.get("phone_type")) or _clean(row.get("phone_value")))
        ],
        "emails": [
            (_clean(row.get("email_type")), _clean(row.get("email_value")))
            for row in (contact.get("emails") or [])
            if isinstance(row, dict) and (_clean(row.get("email_type")) or _clean(row.get("email_value")))
        ],
        "relationships": [
            (_clean(row.get("relation_type")), _clean(row.get("relation_value")))
            for row in (contact.get("relationships") or [])
            if isinstance(row, dict) and (_clean(row.get("relation_type")) or _clean(row.get("relation_value")))
        ],
        "addresses": [
            (_clean(row.get("address_type")), _clean(row.get("formatted_address")))
            for row in (contact.get("addresses") or [])
            if isinstance(row, dict) and (_clean(row.get("address_type")) or _clean(row.get("formatted_address")))
        ],
    }


def _contact_matches_google_person_fields(conn, contact_id: int, fields: dict) -> bool:
    contact = _load_contact_for_sync(conn, contact_id)
    if not contact:
        return False
    if _clean(contact.get("google_contact_id")) != _clean(fields.get("resource_name")):
        return False
    scalar_fields = (
        "family_name",
        "given_name",
        "fields_text",
        "meetings_text",
        "birthday",
        "notes",
    )
    for field_name in scalar_fields:
        if _clean(contact.get(field_name)) != _clean(fields.get(field_name)):
            return False
    expected_group_membership = _group_membership_text(
        _shared_group_membership_names(fields.get("fields_text"), fields.get("meetings_text"))
        or fields.get("group_names")
    )
    if _clean(contact.get("group_membership")) != expected_group_membership:
        return False
    local_rows = _local_import_repeatable_rows(contact)
    google_rows = _google_import_repeatable_rows(fields)
    if (
        local_rows["phones"] != google_rows["phones"]
        or local_rows["emails"] != google_rows["emails"]
        or local_rows["addresses"] != google_rows["addresses"]
        or [_relation_compare_key(*row) for row in local_rows["relationships"]]
        != [_relation_compare_key(*row) for row in google_rows["relationships"]]
    ):
        return False
    if _remote_has_custom_drive_photo(contact):
        return True
    return _clean(contact.get("photo")) == _clean(fields.get("photo"))


def _google_connections_sync_token_invalid(exc: HTTPError, *, had_sync_token: bool) -> bool:
    """True when Google rejected an incremental sync token (retry without syncToken)."""
    if not had_sync_token:
        return False
    if exc.code == 410:
        return True
    if exc.code != 400:
        return False
    # Google often returns 400 (not 410) when syncToken is expired or params drifted.
    return True


def _fetch_google_connections_for_sync(access_token: str, sync_token: str = "") -> tuple[list[dict], str, bool]:
    people: list[dict] = []
    next_sync_token = ""
    token_invalid = False
    page_token = ""
    stored_sync_token = _clean(sync_token)
    while True:
        params = {
            "personFields": GOOGLE_IMPORT_PERSON_FIELDS,
            "pageSize": 1000,
            "sources": "READ_SOURCE_TYPE_CONTACT",
            "requestSyncToken": "true",
        }
        if stored_sync_token and not page_token:
            params["syncToken"] = stored_sync_token
        elif page_token:
            params["pageToken"] = page_token
        url = f"{GOOGLE_PEOPLE_BASE_URL}/people/me/connections?{urlencode(params)}"
        try:
            payload = _api_get_json(url, access_token)
        except HTTPError as exc:
            if _google_connections_sync_token_invalid(exc, had_sync_token=bool(stored_sync_token)):
                token_invalid = True
                return [], "", True
            raise
        people.extend(payload.get("connections") or [])
        page_token = str(payload.get("nextPageToken") or "")
        if payload.get("nextSyncToken"):
            next_sync_token = _clean(payload.get("nextSyncToken"))
        if not page_token:
            break
    return people, next_sync_token, token_invalid


def sync_google_contact_changes(
    *,
    before_upload: bool = False,
    force_full_fetch: bool = False,
    skip_drive_reconcile: bool = False,
) -> dict:
    ensure_google_sync_records()
    access_token, account_email = _get_valid_access_token()
    groups_map = _contact_groups_map(access_token)
    with get_connection() as conn:
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        stored_sync_token = "" if force_full_fetch else _clean(state.get("google_contacts_sync_token"))

    people, next_sync_token, token_invalid = _fetch_google_connections_for_sync(
        access_token,
        stored_sync_token,
    )
    used_full_fetch = not bool(stored_sync_token)
    if token_invalid and not force_full_fetch:
        people, next_sync_token, token_invalid = _fetch_google_connections_for_sync(access_token, "")
        used_full_fetch = True

    created_count = 0
    updated_count = 0
    deleted_count = 0
    unchanged_count = 0
    skipped_conflict_count = 0
    conflict_names: list[str] = []
    touched_contact_ids: list[int] = []

    with get_connection() as conn:
        for person in people:
            metadata = person.get("metadata") or {}
            resource_name = _clean(person.get("resourceName"))
            if metadata.get("deleted"):
                if resource_name and _delete_local_contact_by_google_id(conn, resource_name):
                    deleted_count += 1
                continue

            fields = _google_person_import_fields(person, groups_map)
            if not fields:
                continue

            existing_rows = conn.execute(
                """
                SELECT id, etag, status, shared_drive_revision
                FROM contacts
                WHERE google_contact_id = ?
                ORDER BY shared_drive_revision DESC, id ASC
                """,
                (fields["resource_name"],),
            ).fetchall()
            existing = existing_rows[0] if existing_rows else None
            if existing:
                contact_id = int(existing["id"])
                if len(existing_rows) > 1:
                    _collapse_contacts_sharing_google_id(conn, fields["resource_name"], contact_id)
                local_etag = _clean(existing["etag"])
                if local_etag and local_etag == fields["etag"] and _contact_matches_google_person_fields(
                    conn, contact_id, fields
                ):
                    unchanged_count += 1
                    continue
                if not local_etag and _contact_matches_google_person_fields(conn, contact_id, fields):
                    _update_local_contact_google_state(
                        conn,
                        contact_id,
                        fields["resource_name"],
                        fields["etag"],
                        _clean(existing["status"]) or "synced",
                    )
                    unchanged_count += 1
                    continue
                if _contact_has_pending_google_sync(
                    conn,
                    contact_id=contact_id,
                    google_contact_id=fields["resource_name"],
                ):
                    skipped_conflict_count += 1
                    name_row = conn.execute(
                        "SELECT family_name, given_name FROM contacts WHERE id = ?",
                        (contact_id,),
                    ).fetchone()
                    conflict_names.append(
                        _format_queue_contact_name(dict(name_row) if name_row else {}, {})
                    )
                    continue
                _update_contact_from_google_person(conn, contact_id, fields)
                touched_contact_ids.append(contact_id)
                updated_count += 1
            else:
                identity_id = _find_local_contact_id_by_identity(
                    conn,
                    fields.get("given_name"),
                    fields.get("family_name"),
                    fields.get("phones") or [],
                    fields.get("emails") or [],
                )
                if identity_id:
                    _update_contact_from_google_person(conn, identity_id, fields)
                    touched_contact_ids.append(identity_id)
                    updated_count += 1
                else:
                    contact_id = _insert_contact_from_google_person(conn, fields, status="synced")
                    touched_contact_ids.append(contact_id)
                    created_count += 1

        stale_duplicate_ids = collapse_stale_identity_duplicate_contacts(conn)
        if used_full_fetch:
            live_google_ids = {
                _clean(person.get("resourceName"))
                for person in people
                if _clean(person.get("resourceName")) and not (person.get("metadata") or {}).get("deleted")
            }
            stale_duplicate_ids.extend(_prune_local_contacts_missing_google_ids(conn, live_google_ids))
        if stale_duplicate_ids:
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  contacts_manifest_needs_drive_export = 1,
                  updated_at = ?
                WHERE id = 1
                """,
                (_now_text(),),
            )

        if next_sync_token:
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  google_contacts_sync_token = ?,
                  last_import_at = ?,
                  last_import_account_email = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (next_sync_token, _now_text(), account_email, _now_text()),
            )
        conn.commit()

    if touched_contact_ids and not skip_drive_reconcile:
        with get_connection() as conn:
            sync_writes_enabled = _google_sync_is_enabled_for_writes(conn)
        if sync_writes_enabled:
            try:
                storage = _ensure_shared_drive_storage(access_token, contact_only=True)
                _reconcile_shared_contact_drive_export_flags(
                    touched_contact_ids,
                    access_token=access_token,
                    storage=storage,
                    compare_app_fields_only=True,
                )
            except (HTTPError, URLError, RuntimeError):
                with get_connection() as conn:
                    if not _google_sync_is_enabled_for_writes(conn):
                        conn.commit()
                    else:
                        for contact_id in touched_contact_ids:
                            row = conn.execute(
                                "SELECT shared_drive_bootstrapped FROM contacts WHERE id = ?",
                                (int(contact_id),),
                            ).fetchone()
                            if row and not int(row["shared_drive_bootstrapped"] or 0):
                                conn.execute(
                                    "UPDATE contacts SET shared_drive_needs_export = 1 WHERE id = ?",
                                    (int(contact_id),),
                                )
                        _sync_manifest_drive_export_flag(conn)
                        conn.commit()

    return {
        "before_upload": before_upload,
        "created_count": created_count,
        "updated_count": updated_count,
        "deleted_count": deleted_count,
        "unchanged_count": unchanged_count,
        "skipped_conflict_count": skipped_conflict_count,
        "conflict_names": conflict_names,
        "changed_count": created_count + updated_count + deleted_count,
    }


def upload_google_contacts_only(*, finish_progress: bool = True) -> dict:
    ensure_google_sync_records()
    contact_upload_count = upload_google_contacts(finish_progress=finish_progress)
    return {
        "contact_upload_count": contact_upload_count,
        "drive_exported": False,
        "processed_count": contact_upload_count,
        "total_count": contact_upload_count,
    }


def _import_membership_names(conn, fields_text: str, meetings_text: str, group_names: list[str]) -> list[str]:
    """Build group labels on the open connection.

    Opening a second connection here deadlocks the mobile database, which uses
    DELETE journal mode, and the download never saves the page Google already sent.
    """
    if not fields_text or not meetings_text:
        return list(group_names)
    preset_row = conn.execute(
        """
        SELECT shared_contacts_group_name
        FROM book_layout_presets
        WHERE is_default = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    shared_label = _clean(preset_row["shared_contacts_group_name"] if preset_row else "")
    if not shared_label:
        from app.services.preset_service import DEFAULT_SHARED_CONTACTS_GROUP_NAME

        shared_label = DEFAULT_SHARED_CONTACTS_GROUP_NAME
    return [
        f"{fields_text} - {meetings_text} (Shared)",
        shared_label,
        "myContacts",
    ]


def _insert_imported_google_person(conn, person: dict, groups_map: dict[str, str]) -> bool:
    names = person.get("names") or []
    display_name = _primary_text(names, "displayName")
    family_name = _primary_text(names, "familyName")
    given_name = _primary_text(names, "givenName")
    group_names = []
    for membership in person.get("memberships") or []:
        group_membership = membership.get("contactGroupMembership") or {}
        resource_name = _clean(group_membership.get("contactGroupResourceName"))
        if resource_name and resource_name in groups_map:
            group_names.append(groups_map[resource_name])
    fields_text, meetings_text = _resolve_field_meeting(group_names, person.get("organizations") or [])
    birthdays = person.get("birthdays") or []
    birthday_text = ""
    for birthday in birthdays:
        date = birthday.get("date") or {}
        year = date.get("year")
        month = date.get("month")
        day = date.get("day")
        if month and day:
            if year:
                birthday_text = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                birthday_text = f"{month:02d}-{day:02d}"
            break
    biographies = person.get("biographies") or []
    notes = _primary_text(biographies, "value")
    user_defined = person.get("userDefined") or []
    user_defined_coordinates = _google_user_defined_coordinates(user_defined)
    addresses = person.get("addresses") or []
    url_coordinate_candidates = _google_url_coordinate_candidates(person.get("urls") or [])
    metadata = person.get("metadata") or {}
    sources = metadata.get("sources") or []
    etag = _clean(person.get("etag"))
    if not etag:
        for source in sources:
            etag = _clean(source.get("etag"))
            if etag:
                break
    resource_name = _clean(person.get("resourceName"))
    if not resource_name:
        return False
    existing = conn.execute(
        "SELECT id FROM contacts WHERE google_contact_id = ?",
        (resource_name,),
    ).fetchone()
    if existing:
        return False

    cursor = conn.execute(
        """
        INSERT INTO contacts (
          selected,
          google_contact_id,
          etag,
          last_updated,
          status,
          fields_text,
          meetings_text,
          group_membership,
          family_name,
          given_name,
          photo,
          birthday,
          notes,
          mtg_home_elder_flag,
          meets_in_state,
          do_not_print
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "",
            resource_name,
            etag,
            _now_text(),
            "imported",
            fields_text,
            meetings_text,
            _group_membership_text(_import_membership_names(conn, fields_text, meetings_text, group_names)),
            family_name or display_name,
            given_name,
            _extract_photo_url(person),
            birthday_text,
            notes,
            _mtg_home_elder_flag_from_user_defined(user_defined),
            _meets_in_state_from_user_defined(user_defined),
            _do_not_print_from_user_defined(user_defined),
        ),
    )
    contact_id = int(cursor.lastrowid)

    stored_labels = _cfs_relation_labels_from_user_defined(user_defined)
    for index, relation in enumerate(person.get("relations") or [], start=1):
        google_type = _clean(relation.get("type") or relation.get("formattedType"))
        relation_value = _clean(relation.get("person"))
        complementary_label = ""
        google_key = google_type.lower().replace(" ", "").replace("_", "")
        if google_key in _LOSSY_GOOGLE_RELATION_TYPES:
            complementary_label = _lookup_complementary_spouse_label(conn, contact_id, relation_value)
        relation_type = _restore_imported_relation_type(
            google_type,
            relation_value,
            stored_labels=stored_labels,
            complementary_label=complementary_label,
        )
        if relation_type or relation_value:
            conn.execute(
                """
                INSERT INTO relationships (contact_id, position, relation_type, relation_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, relation_type, relation_value),
            )

    for index, phone in enumerate(person.get("phoneNumbers") or [], start=1):
        phone_type = _clean(phone.get("formattedType") or phone.get("type"))
        phone_value = _clean(phone.get("value"))
        if phone_type or phone_value:
            conn.execute(
                """
                INSERT INTO phones (contact_id, position, phone_type, phone_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, phone_type, phone_value),
            )

    for index, email in enumerate(person.get("emailAddresses") or [], start=1):
        email_type = _clean(email.get("formattedType") or email.get("type"))
        email_value = _clean(email.get("value"))
        if email_type or email_value:
            conn.execute(
                """
                INSERT INTO emails (contact_id, position, email_type, email_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, email_type, email_value),
            )

    for index, address in enumerate(addresses, start=1):
        address_type = _clean(address.get("formattedType") or address.get("type"))
        structured = _google_address_structured_fields(address)
        formatted_address = structured["formatted_address"]
        street_address = structured["street_address"]
        extended_address = structured["extended_address"]
        city = structured["city"]
        region = structured["region"]
        postal_code = structured["postal_code"]
        coordinates = _google_address_coordinates(
            address,
            user_defined_coordinates,
            url_coordinate_candidates,
            index,
            len(addresses),
        )
        if address_type or formatted_address or coordinates or any(
            (street_address, extended_address, city, region, postal_code)
        ):
            conn.execute(
                """
                INSERT INTO addresses (
                  contact_id, position, address_type,
                  street_address, extended_address, city, region, postal_code,
                  formatted_address, coordinates
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id,
                    index,
                    address_type,
                    street_address,
                    extended_address,
                    city,
                    region,
                    postal_code,
                    formatted_address,
                    coordinates,
                ),
            )

    for index, item in enumerate(user_defined, start=1):
        field_type = _clean(item.get("key"))
        field_value = _clean(item.get("value"))
        if _is_cfs_hidden_user_defined_field(field_type):
            continue
        if _is_coordinate_custom_field_key(field_type) and _normalize_coordinates(field_value):
            continue
        if field_type or field_value:
            conn.execute(
                """
                INSERT INTO custom_fields (contact_id, position, field_type, field_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, index, field_type, field_value),
            )

    return True


def _finish_google_contacts_import(
    conn,
    *,
    imported: int,
    account_email: str,
    sync_token: str = "",
) -> None:
    now = _now_text()
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          pending_upload_count = 0,
          needs_upload_reminder = 0,
          needs_drive_export = 0,
          upload_in_progress = 0,
          upload_phase = '',
          upload_total_count = 0,
          upload_processed_count = 0,
          upload_current_contact = '',
          upload_started_at = '',
          bootstrap_status = 'idle',
          bootstrap_total_contacts = ?,
          bootstrap_processed_contacts = 0,
          bootstrap_last_contact_id = 0,
          bootstrap_started_at = '',
          bootstrap_updated_at = ?,
          bootstrap_error = '',
          last_import_at = ?,
          last_import_account_email = ?,
          last_sync_error = '',
          google_contacts_sync_token = CASE WHEN ? != '' THEN ? ELSE google_contacts_sync_token END,
          updated_at = ?
        WHERE id = 1
        """,
        (imported, now, now, account_email, sync_token, sync_token, now),
    )
    _set_signin_sync_state(
        conn,
        in_progress=False,
        phase="",
        error="",
    )


def people_import_in_progress() -> bool:
    ensure_google_sync_records()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT signin_sync_in_progress, signin_sync_phase
            FROM google_sync_state
            WHERE id = 1
            """
        ).fetchone()
    if not row:
        return False
    return bool(row["signin_sync_in_progress"]) and _clean(row["signin_sync_phase"]) == _PEOPLE_IMPORT_PHASE


def _is_mobile_data_root() -> bool:
    return str(os.environ.get("DATA_ROOT") or "") == "/data"


def import_google_contacts_chunk(
    access_token: str | None = None,
    account_email: str | None = None,
) -> dict:
    """Import one People API page and persist it so a long first download can resume."""
    ensure_google_sync_records()
    with get_connection() as conn:
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        in_progress = (
            bool(state.get("signin_sync_in_progress"))
            and _clean(state.get("signin_sync_phase")) == _PEOPLE_IMPORT_PHASE
        )
        page_token = _clean(state.get("signin_sync_current_item")) if in_progress else ""
        if not in_progress:
            _clear_local_contact_data(conn)
            _set_signin_sync_state(
                conn,
                in_progress=True,
                phase=_PEOPLE_IMPORT_PHASE,
                error="",
                total_count=0,
                processed_count=0,
                current_item="",
            )
            conn.commit()

    if not access_token:
        access_token, account_email = _get_valid_access_token()
    # Mobile Field/Meeting comes from Job Title / Department. Listing every
    # Google label first is what made the first batch sit on "waiting on Google".
    mobile = _is_mobile_data_root()
    groups_map = {} if mobile else _contact_groups_map(access_token)

    params = {
        "personFields": _MOBILE_IMPORT_PERSON_FIELDS if mobile else GOOGLE_IMPORT_PERSON_FIELDS,
        "pageSize": 25 if mobile else _PEOPLE_IMPORT_PAGE_SIZE,
        "sources": "READ_SOURCE_TYPE_CONTACT",
    }
    if page_token:
        params["pageToken"] = page_token
    url = f"{GOOGLE_PEOPLE_BASE_URL}/people/me/connections?{urlencode(params)}"
    _LOGGER.warning("people import chunk start page_token=%s mobile=%s", bool(page_token), mobile)
    payload = (
        _mobile_people_get_json(url, access_token)
        if mobile
        else _api_get_json(url, access_token)
    )
    _LOGGER.warning(
        "people import chunk page people=%s next=%s",
        len(payload.get("connections") or []),
        bool(payload.get("nextPageToken")),
    )

    people = []
    for person in payload.get("connections") or []:
        if (person.get("metadata") or {}).get("deleted"):
            continue
        people.append(person)
    next_page = str(payload.get("nextPageToken") or "")
    next_sync_token = _clean(payload.get("nextSyncToken"))
    total_available = int(payload.get("totalPeople") or payload.get("totalItems") or 0)

    with get_connection() as conn:
        page_imported = 0
        for person in people:
            if _insert_imported_google_person(conn, person, groups_map):
                page_imported += 1
        total_imported = int(
            conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] or 0
        )
        if next_page:
            _set_signin_sync_state(
                conn,
                in_progress=True,
                phase=_PEOPLE_IMPORT_PHASE,
                error="",
                total_count=total_available or total_imported,
                processed_count=total_imported,
                current_item=next_page,
            )
            conn.commit()
            _LOGGER.warning("people import chunk saved imported=%s done=False", total_imported)
            return {
                "page_imported": page_imported,
                "imported": total_imported,
                "done": False,
                "total_available": total_available,
            }
        _finish_google_contacts_import(
            conn,
            imported=total_imported,
            account_email=account_email,
            sync_token=next_sync_token,
        )
        conn.commit()
    _LOGGER.warning("people import chunk saved imported=%s done=%s", total_imported, not next_page)

    return {
        "page_imported": page_imported,
        "imported": total_imported,
        "done": True,
        "total_available": total_available or total_imported,
    }


def import_google_contacts() -> int:
    ensure_google_sync_records()
    access_token, account_email = _get_valid_access_token()
    groups_map = _contact_groups_map(access_token)
    people: list[dict] = []
    page_token = ""
    while True:
        params = {
            "personFields": GOOGLE_IMPORT_PERSON_FIELDS,
            "pageSize": 500,
            "sources": "READ_SOURCE_TYPE_CONTACT",
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"{GOOGLE_PEOPLE_BASE_URL}/people/me/connections?{urlencode(params)}"
        payload = _api_get_json(url, access_token)
        for person in payload.get("connections", []):
            if (person.get("metadata") or {}).get("deleted"):
                continue
            people.append(person)
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break

    with get_connection() as conn:
        _clear_local_contact_data(conn)

        imported = 0
        for person in people:
            if _insert_imported_google_person(conn, person, groups_map):
                imported += 1

        _finish_google_contacts_import(
            conn,
            imported=imported,
            account_email=account_email,
        )
        conn.commit()

    return imported


def upload_google_contacts(
    *,
    finish_progress: bool = True,
    skip_share_sync: bool = False,
    defer_share_sync_into: dict | None = None,
) -> int:
    ensure_google_sync_records()
    access_token, account_email = _get_valid_access_token()

    with get_connection() as conn:
        pending_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM google_sync_queue
                WHERE status = 'pending'
                ORDER BY id
                """
            ).fetchall()
        ]

    if not pending_rows:
        with get_connection() as conn:
            _refresh_pending_upload_count(conn)
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                total_count=0,
                processed_count=0,
                current_contact="",
                started_at="",
            )
            _clear_last_sync_error(conn)
            conn.commit()
        return 0

    resource_to_name, name_to_resource = _contact_groups_lookup(access_token)
    processed = 0
    total_count = len(pending_rows)
    uploaded_google_contact_ids: list[str] = []

    with get_connection() as conn:
        _set_upload_progress(
            conn,
            in_progress=True,
            phase="google_contacts",
            total_count=total_count,
            processed_count=0,
            current_contact="",
            started_at=_now_text(),
        )
        conn.commit()

    max_quota_attempts = 4
    max_auth_attempts = 2
    group_sync_batch: dict[str, dict[str, set[str]]] = {}
    processed_upsert_queue_ids: list[int] = []
    for queue_entry in pending_rows:
        payload = _safe_load_json(queue_entry.get("payload_json"))
        current_contact_name = _format_queue_contact_name(queue_entry, payload)
        quota_attempt = 1
        auth_attempt = 1
        while True:
            try:
                access_token, account_email = _get_valid_access_token()
                with get_connection() as conn:
                    _set_upload_progress(
                        conn,
                        in_progress=True,
                        phase="google_contacts",
                        total_count=total_count,
                        processed_count=processed,
                        current_contact=current_contact_name,
                    )
                    conn.commit()

                operation = _clean(queue_entry.get("operation")) or "update"
                google_contact_id = _queue_entry_google_contact_id(queue_entry, payload)
                with get_connection() as conn:
                    resolved_contact_id = _resolve_queue_entry_contact_id(conn, queue_entry, payload)
                if resolved_contact_id is None and not google_contact_id:
                    with get_connection() as conn:
                        _discard_stale_queue_entry(conn, queue_entry)
                        _set_upload_progress(
                            conn,
                            in_progress=True,
                            phase="google_contacts",
                            total_count=total_count,
                            processed_count=processed + 1,
                            current_contact=current_contact_name,
                        )
                        _clear_last_sync_error(conn)
                        conn.commit()
                    processed += 1
                    break
                queue_entry_for_sync = dict(queue_entry)
                if resolved_contact_id is not None:
                    queue_entry_for_sync["contact_id"] = resolved_contact_id
                if google_contact_id and not _clean(queue_entry_for_sync.get("google_contact_id")):
                    queue_entry_for_sync["google_contact_id"] = google_contact_id
                if operation == "delete" or (google_contact_id and resolved_contact_id is None):
                    _process_delete_queue_entry(
                        access_token,
                        queue_entry_for_sync,
                        resource_to_name,
                        name_to_resource,
                    )
                    with get_connection() as conn:
                        _delete_queue_entry(conn, int(queue_entry["id"]))
                        _refresh_pending_upload_count(conn)
                        _set_upload_progress(
                            conn,
                            in_progress=True,
                            phase="google_contacts",
                            total_count=total_count,
                            processed_count=processed + 1,
                            current_contact=current_contact_name,
                        )
                        _clear_last_sync_error(conn)
                        conn.commit()
                else:
                    with get_connection() as conn:
                        _process_upsert_queue_entry(
                            conn,
                            access_token,
                            queue_entry_for_sync,
                            resource_to_name,
                            name_to_resource,
                            group_sync_batch=group_sync_batch,
                        )
                        if resolved_contact_id:
                            row = conn.execute(
                                "SELECT google_contact_id FROM contacts WHERE id = ?",
                                (resolved_contact_id,),
                            ).fetchone()
                            person_id = _clean(row["google_contact_id"] if row else "")
                            if person_id:
                                uploaded_google_contact_ids.append(person_id)
                        queue_id = int(queue_entry["id"])
                        processed_upsert_queue_ids.append(queue_id)
                        # Remove each successful upload from the queue immediately so a later
                        # group-sync or Drive step cannot leave already-uploaded contacts pending.
                        _delete_queue_entry(conn, queue_id)
                        _refresh_pending_upload_count(conn)
                        _set_upload_progress(
                            conn,
                            in_progress=True,
                            phase="google_contacts",
                            total_count=total_count,
                            processed_count=processed + 1,
                            current_contact=current_contact_name,
                        )
                        _clear_last_sync_error(conn)
                        conn.commit()
                processed += 1
                if processed % GOOGLE_GROUP_SYNC_FLUSH_EVERY == 0:
                    group_error = _flush_pending_group_sync(
                        group_sync_batch, resource_to_name, name_to_resource
                    )
                    if group_error:
                        with get_connection() as conn:
                            _set_last_sync_error(
                                conn,
                                "Contact details were uploaded, but Google contact groups could not be updated. "
                                f"{group_error}",
                            )
                            conn.commit()
                break
            except HTTPError as exc:
                message = _http_error_message(exc) or f"Google upload failed with HTTP {exc.code}."
                if current_contact_name:
                    message = f"{current_contact_name}: {message}"
                if _looks_like_expired_access_token_error(exc, message) and auth_attempt < max_auth_attempts:
                    with get_connection() as conn:
                        _set_upload_progress(
                            conn,
                            in_progress=True,
                            phase="google_contacts",
                            total_count=total_count,
                            processed_count=processed,
                            current_contact=f"{current_contact_name} - refreshing Google sign-in",
                        )
                        _set_last_sync_error(conn, "Google sign-in expired. Refreshing access and continuing...")
                        conn.commit()
                    access_token, account_email = _get_valid_access_token(force_refresh=True)
                    auth_attempt += 1
                    continue
                if _looks_like_google_quota_error(exc, message) and quota_attempt < max_quota_attempts:
                    delay_seconds = _google_quota_retry_delay(exc, quota_attempt)
                    with get_connection() as conn:
                        _update_queue_error(conn, int(queue_entry["id"]), message)
                        _refresh_pending_upload_count(conn)
                        _set_upload_progress(
                            conn,
                            in_progress=True,
                            phase="google_contacts",
                            total_count=total_count,
                            processed_count=processed,
                            current_contact=f"{current_contact_name} - waiting for Google quota",
                        )
                        _set_last_sync_error(conn, f"Google asked us to slow down. Retrying in {delay_seconds} seconds.")
                        conn.commit()
                    time.sleep(delay_seconds)
                    quota_attempt += 1
                    continue
                with get_connection() as conn:
                    _update_queue_error(conn, int(queue_entry["id"]), message)
                    _refresh_pending_upload_count(conn)
                    _set_upload_progress(
                        conn,
                        in_progress=False,
                        phase="google_contacts",
                        total_count=total_count,
                        processed_count=processed,
                        current_contact=current_contact_name,
                    )
                    _set_last_sync_error(conn, message)
                    conn.commit()
                group_error = _flush_pending_group_sync(
                    group_sync_batch, resource_to_name, name_to_resource
                )
                if group_error:
                    message = f"{message} Google labels for already-uploaded contacts also could not be updated."
                raise RuntimeError(message) from exc
            except Exception as exc:
                message = _clean(str(exc)) or SYNC_NOTICE_MESSAGES["upload_failed"]
                if current_contact_name and not message.startswith(f"{current_contact_name}:"):
                    message = f"{current_contact_name}: {message}"
                with get_connection() as conn:
                    _update_queue_error(conn, int(queue_entry["id"]), message)
                    _refresh_pending_upload_count(conn)
                    _set_upload_progress(
                        conn,
                        in_progress=False,
                        phase="google_contacts",
                        total_count=total_count,
                        processed_count=processed,
                        current_contact=current_contact_name,
                    )
                    _set_last_sync_error(conn, message)
                    conn.commit()
                _flush_pending_group_sync(group_sync_batch, resource_to_name, name_to_resource)
                raise RuntimeError(message) from exc

    group_sync_error = ""
    if group_sync_batch:
        with get_connection() as conn:
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="google_contacts",
                total_count=total_count,
                processed_count=processed,
                current_contact="Updating Google contact groups",
            )
            conn.commit()
        group_sync_error = _flush_pending_group_sync(
            group_sync_batch, resource_to_name, name_to_resource
        )

    with get_connection() as conn:
        for queue_id in processed_upsert_queue_ids:
            _delete_queue_entry(conn, queue_id)
        _refresh_pending_upload_count(conn)
        if finish_progress:
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                total_count=total_count,
                processed_count=processed,
                current_contact="",
            )
        else:
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=total_count + 1,
                processed_count=processed,
                current_contact="Shared app data",
            )
        last_sync_error_value = ""
        if group_sync_error:
            last_sync_error_value = (
                "Contact details were uploaded, but Google contact groups could not be updated. "
                f"{group_sync_error}"
            )
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              last_upload_at = ?,
              needs_upload_reminder = ?,
              last_sync_error = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(), 0 if finish_progress else 1, last_sync_error_value, _now_text()),
        )
        conn.commit()

    unique_person_ids = list(dict.fromkeys(uploaded_google_contact_ids))
    if unique_person_ids and not skip_share_sync:
        if defer_share_sync_into is not None:
            # Combined Upload still has Drive work ahead. Do not race Share sync against
            # Drive export on the same SQLite DB (that left Google+Drive work unfinished).
            defer_share_sync_into["account_email"] = account_email
            defer_share_sync_into["person_ids"] = unique_person_ids
        elif finish_progress:
            from app.services.share_sync_service import sync_share_app_contact_changes

            try:
                share_result = sync_share_app_contact_changes(account_email, unique_person_ids)
            except Exception as exc:
                share_result = {"ok": False, "reason": _clean(str(exc)) or "share_sync_failed"}
            record_share_sync_result(account_email, unique_person_ids, share_result)
        else:
            _schedule_share_app_contact_sync(account_email, unique_person_ids)

    return processed


def _touch_upload_app_field_queue(processed_count: int, total_count: int) -> None:
    with get_connection() as conn:
        state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
        _set_upload_progress(
            conn,
            in_progress=True,
            phase="drive_shared_data",
            total_count=max(int(total_count or 0), 1),
            processed_count=min(max(int(processed_count or 0), 0), max(int(total_count or 0), 1)),
            current_contact=f"Drive: checking app fields {min(max(int(processed_count or 0), 0), max(int(total_count or 0), 1))} of {max(int(total_count or 0), 1)}",
            started_at=str(state.get("upload_started_at") or _now_text()),
        )
        conn.commit()


def upload_pending_changes(
    *,
    allow_bootstrap: bool = True,
    sync_google_before_upload: bool | None = None,
    skip_share_sync: bool = False,
) -> dict:
    deferred_share_sync: dict = {}
    try:
        return _upload_pending_changes_body(
            allow_bootstrap=allow_bootstrap,
            sync_google_before_upload=sync_google_before_upload,
            skip_share_sync=skip_share_sync,
            deferred_share_sync=deferred_share_sync,
        )
    finally:
        person_ids = deferred_share_sync.get("person_ids") or []
        account_email = str(deferred_share_sync.get("account_email") or "").strip()
        if person_ids and account_email and not skip_share_sync:
            _schedule_share_app_contact_sync(account_email, list(person_ids))


def _upload_pending_changes_body(
    *,
    allow_bootstrap: bool = True,
    sync_google_before_upload: bool | None = None,
    skip_share_sync: bool = False,
    deferred_share_sync: dict | None = None,
) -> dict:
    with get_connection() as conn:
        pending_before_sync = _read_pending_upload_count(conn)
    if sync_google_before_upload is None:
        # Local queued edits take priority; pulling from Google first can lock the DB
        # and is unnecessary when contacts are already waiting to upload.
        with get_connection() as conn:
            state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
            local_push_pending = _state_has_pending_drive_export(conn, state)
            settings_only_drive_pending = (
                pending_before_sync <= 0
                and _count_pending_shared_contact_exports(conn) <= 0
                and bool(state.get("needs_drive_export") or 0)
                and not bool(state.get("contacts_manifest_needs_drive_export") or 0)
                and not bool(state.get("book_layouts_needs_drive_export") or 0)
                and not bool(state.get("meetingdata_needs_drive_export") or 0)
                and not bool(state.get("field_list_needs_drive_export") or 0)
                and not bool(state.get("changes_list_needs_drive_export") or 0)
            )
        sync_google_before_upload = (
            is_multi_editor_enabled()
            and pending_before_sync <= 0
            and not settings_only_drive_pending
            and not local_push_pending
        )
    if sync_google_before_upload:
        try:
            sync_google_contact_changes(before_upload=True)
        except sqlite3.OperationalError:
            pass
        except (RuntimeError, HTTPError, URLError):
            pass

    sync_work = _current_sync_work()
    bootstrap_incomplete = bool(sync_work["bootstrap_incomplete"])
    pending_contact_count = int(sync_work["pending_contact_count"])
    drive_export_needed = bool(sync_work["drive_export_needed"])
    meetingdata_drive_export_needed = bool(sync_work["meetingdata_drive_export_needed"])
    total_count = int(sync_work["total_count"])
    processed_count = 0
    contact_upload_count = 0

    # Push queued Google People edits before Drive bootstrap/export so contact
    # uploads are not skipped when bootstrap still has remaining work.
    if pending_contact_count > 0:
        contact_upload_count = upload_google_contacts(
            finish_progress=False,
            skip_share_sync=skip_share_sync,
            defer_share_sync_into=None if skip_share_sync else deferred_share_sync,
        )
        processed_count += contact_upload_count
        sync_work = _current_sync_work()
        bootstrap_incomplete = bool(sync_work["bootstrap_incomplete"])
        pending_contact_count = int(sync_work["pending_contact_count"])
        drive_export_needed = bool(sync_work["drive_export_needed"])
        meetingdata_drive_export_needed = bool(sync_work["meetingdata_drive_export_needed"])
        total_count = max(int(sync_work["total_count"]), processed_count)

    if bootstrap_incomplete and allow_bootstrap:
        with get_connection() as conn:
            state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
            contact_count = _count_contacts(conn)
            bootstrap_remaining = _count_unbootstrapped_contacts(conn)
            bootstrap_status = str(state.get("bootstrap_status") or "idle")
            stored_total = int(state.get("bootstrap_total_contacts") or 0)
            stored_processed = int(state.get("bootstrap_processed_contacts") or 0)
            if bootstrap_status == "completed" and bootstrap_remaining > 0:
                bootstrap_total = bootstrap_remaining
                bootstrap_processed = 0
            elif bootstrap_remaining > 0 and bootstrap_remaining < contact_count and stored_total >= contact_count:
                bootstrap_total = bootstrap_remaining
                bootstrap_processed = 0
            elif stored_total > 0 and bootstrap_status in {"running", "failed"}:
                bootstrap_total = stored_total
                bootstrap_processed = max(stored_total - bootstrap_remaining, stored_processed, 0)
            else:
                bootstrap_total = bootstrap_remaining if 0 < bootstrap_remaining < contact_count else contact_count
                bootstrap_processed = max(bootstrap_total - bootstrap_remaining, stored_processed, 0)
            _set_bootstrap_state(
                conn,
                status="running",
                total_contacts=bootstrap_total,
                processed_contacts=bootstrap_processed,
                updated_at=_now_text(),
                error="",
            )
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_bootstrap",
                total_count=bootstrap_total,
                processed_count=_visible_bootstrap_processed(bootstrap_processed, bootstrap_total),
                current_contact="",
                started_at=str(state.get("bootstrap_started_at") or _now_text()),
            )
            _clear_last_sync_error(conn)
            conn.commit()
        contacts_complete = export_shared_contacts_to_google_drive()
        if not contacts_complete or not drive_export_needed:
            with get_connection() as conn:
                if not contacts_complete:
                    _set_last_sync_error(
                        conn,
                        "Drive contact export did not finish. Click Upload again to continue.",
                    )
                if not _state_has_resumable_upload_work(
                    conn,
                    dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {}),
                ):
                    _set_upload_progress(
                        conn,
                        in_progress=False,
                        phase="",
                        total_count=total_count,
                        processed_count=processed_count,
                        current_contact="",
                        started_at="",
                    )
                conn.commit()
            return {
                "contact_upload_count": contact_upload_count,
                "drive_exported": contacts_complete,
                "processed_count": processed_count + int(contacts_complete),
                "total_count": max(total_count, processed_count + (1 if contacts_complete else 0)),
            }
        drive_export_complete = export_shared_data_to_google_drive()
        with get_connection() as conn:
            if not drive_export_complete:
                _set_last_sync_error(
                    conn,
                    "Drive export did not finish. Click Upload again to continue.",
                )
            else:
                _clear_last_sync_error(conn)
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                total_count=total_count,
                processed_count=total_count if drive_export_complete else processed_count,
                current_contact="",
                started_at="",
            )
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  last_upload_at = ?,
                  needs_upload_reminder = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (_now_text(), 0 if drive_export_complete else 1, _now_text()),
            )
            conn.commit()
        return {
            "contact_upload_count": contact_upload_count,
            "drive_exported": drive_export_complete,
            "processed_count": processed_count + total_count if drive_export_complete else processed_count,
            "total_count": total_count,
        }

    if pending_contact_count <= 0 and not drive_export_needed:
        with get_connection() as conn:
            _refresh_pending_upload_count(conn)
            _set_upload_progress(
                conn,
                in_progress=False,
                total_count=0,
                processed_count=0,
                current_contact="",
                started_at="",
            )
            _clear_last_sync_error(conn)
            conn.commit()
        return {
            "contact_upload_count": contact_upload_count,
            "drive_exported": False,
            "processed_count": processed_count,
            "total_count": processed_count,
        }

    drive_exported = False
    if drive_export_needed:
        with get_connection() as conn:
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="drive_shared_data",
                total_count=max(total_count, processed_count + 1),
                processed_count=processed_count,
                current_contact="Shared app data",
            )
            conn.commit()
        drive_export_complete = export_shared_data_to_google_drive()
        drive_exported = drive_export_complete
        processed_count += 1 if drive_export_complete else 0
        with get_connection() as conn:
            if not drive_export_complete:
                _set_last_sync_error(
                    conn,
                    "Drive export did not finish. Click Upload again to continue.",
                )
            else:
                _clear_last_sync_error(conn)
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                total_count=total_count,
                processed_count=processed_count,
                current_contact="",
            )
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  last_upload_at = ?,
                  needs_upload_reminder = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (_now_text(), 0 if drive_export_complete else 1, _now_text()),
            )
            conn.commit()
    elif contact_upload_count > 0:
        with get_connection() as conn:
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                total_count=total_count,
                processed_count=processed_count,
                current_contact="",
            )
            conn.execute(
                """
                UPDATE google_sync_state
                SET
                  last_upload_at = ?,
                  needs_upload_reminder = 0,
                  last_sync_error = '',
                  updated_at = ?
                WHERE id = 1
                """,
                (_now_text(), _now_text()),
            )
            conn.commit()
    return {
        "contact_upload_count": contact_upload_count,
        "drive_exported": drive_exported,
        "processed_count": processed_count,
        "total_count": total_count,
    }


def _run_google_upload_job() -> None:
    global _UPLOAD_JOB_THREAD
    try:
        upload_pending_changes()
    except sqlite3.OperationalError as exc:
        message = _clean(str(exc)) or "The local database was busy. Close other editors and click Upload again."
        with get_connection() as conn:
            state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
            if str(state.get("bootstrap_status") or "") == "running":
                _mark_bootstrap_failed(conn, message)
            _refresh_pending_upload_count(conn)
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                current_contact="",
                started_at="",
            )
            _set_last_sync_error(conn, message)
            conn.commit()
    except Exception as exc:
        message = _clean(str(exc)) or SYNC_NOTICE_MESSAGES["upload_failed"]
        with get_connection() as conn:
            state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
            if str(state.get("bootstrap_status") or "") == "running":
                _mark_bootstrap_failed(conn, message)
            _refresh_pending_upload_count(conn)
            _set_upload_progress(
                conn,
                in_progress=False,
                phase="",
                current_contact="",
                started_at="",
            )
            _set_last_sync_error(conn, message)
            conn.commit()
    finally:
        with _UPLOAD_JOB_LOCK:
            _UPLOAD_JOB_THREAD = None


def get_google_upload_progress() -> dict:
    summary = get_google_sync_summary()
    state = summary["state"]
    last_error = str(state.get("last_sync_error") or "")
    in_progress = bool(state.get("upload_in_progress") or 0)
    processed_count = int(state.get("upload_processed_count") or 0)
    total_count = int(state.get("upload_total_count") or 0)
    phase = str(state.get("upload_phase") or "")
    pending_count = int(state.get("pending_upload_count") or 0)
    settings_needs_drive_export = bool(state.get("settings_needs_drive_export") or state.get("needs_drive_export") or 0)
    book_layouts_needs_drive_export = bool(state.get("book_layouts_needs_drive_export") or 0)
    contacts_manifest_needs_drive_export = bool(state.get("contacts_manifest_needs_drive_export") or 0)
    contact_records_needs_drive_export = bool(state.get("contact_records_needs_drive_export") or 0)
    meetingdata_needs_drive_export = bool(state.get("meetingdata_needs_drive_export") or 0)
    field_list_needs_drive_export = bool(state.get("field_list_needs_drive_export") or 0)
    changes_list_needs_drive_export = bool(state.get("changes_list_needs_drive_export") or 0)
    any_drive_export_needed = (
        settings_needs_drive_export
        or book_layouts_needs_drive_export
        or contacts_manifest_needs_drive_export
        or contact_records_needs_drive_export
        or meetingdata_needs_drive_export
        or field_list_needs_drive_export
        or changes_list_needs_drive_export
    )
    bootstrap = _bootstrap_status_dict(state)
    bootstrap_active = bootstrap["status"] in {"running", "failed"} or (
        bootstrap["remaining"] > 0 and (pending_count > 0 or any_drive_export_needed or in_progress)
    )
    if bootstrap_active:
        if bootstrap["status"] == "running":
            notice = "upload_in_progress"
        elif bootstrap["status"] == "failed":
            notice = "upload_failed"
        else:
            notice = "upload_pending"
        return {
            "in_progress": bootstrap["status"] == "running" or (in_progress and bootstrap["remaining"] > 0),
            "total_count": bootstrap["total"],
            "processed_count": _visible_bootstrap_processed(bootstrap["processed"], bootstrap["total"]),
            "remaining_count": bootstrap["remaining"],
            "phase": "drive_bootstrap",
            "current_contact": str(state.get("upload_current_contact") or ""),
            "started_at": bootstrap["started_at"],
            "pending_count": pending_count,
            "settings_needs_drive_export": settings_needs_drive_export,
            "shared_drive_needs_export": any_drive_export_needed or bootstrap["remaining"] > 0,
            "needs_drive_export": any_drive_export_needed or bootstrap["remaining"] > 0,
            "contact_records_needs_drive_export": contact_records_needs_drive_export,
            "contacts_manifest_needs_drive_export": contacts_manifest_needs_drive_export,
            "book_layouts_needs_drive_export": book_layouts_needs_drive_export,
            "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
            "field_list_needs_drive_export": field_list_needs_drive_export,
            "changes_list_needs_drive_export": changes_list_needs_drive_export,
            "last_error": bootstrap["error"] or last_error,
            "notice": notice,
            "bootstrap_status": bootstrap["status"],
            "bootstrap_total": bootstrap["total"],
            "bootstrap_processed": bootstrap["processed"],
            "bootstrap_remaining": bootstrap["remaining"],
            "bootstrap_error": bootstrap["error"],
        }
    if in_progress:
        notice = "upload_in_progress"
    elif last_error and _looks_like_reconnect_error(last_error):
        notice = "upload_not_connected"
    elif last_error and (pending_count > 0 or any_drive_export_needed):
        notice = "upload_failed"
    elif processed_count > 0 and pending_count == 0 and not any_drive_export_needed:
        notice = "upload_success"
    elif pending_count > 0 or any_drive_export_needed:
        notice = "upload_pending"
    else:
        notice = "upload_queue_empty"
    return {
        "in_progress": in_progress,
        "total_count": total_count,
        "processed_count": processed_count,
        "remaining_count": max(total_count - processed_count, 0),
        "phase": phase,
        "current_contact": str(state.get("upload_current_contact") or ""),
        "started_at": str(state.get("upload_started_at") or ""),
        "pending_count": pending_count,
        "settings_needs_drive_export": settings_needs_drive_export,
        "shared_drive_needs_export": any_drive_export_needed,
        "needs_drive_export": any_drive_export_needed,
        "contact_records_needs_drive_export": contact_records_needs_drive_export,
        "contacts_manifest_needs_drive_export": contacts_manifest_needs_drive_export,
        "book_layouts_needs_drive_export": book_layouts_needs_drive_export,
        "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
        "field_list_needs_drive_export": field_list_needs_drive_export,
        "changes_list_needs_drive_export": changes_list_needs_drive_export,
        "last_error": last_error,
        "notice": notice,
        "bootstrap_status": bootstrap["status"],
        "bootstrap_total": bootstrap["total"],
        "bootstrap_processed": bootstrap["processed"],
        "bootstrap_remaining": bootstrap["remaining"],
        "bootstrap_error": bootstrap["error"],
    }


def start_google_upload_job() -> dict:
    global _UPLOAD_JOB_THREAD
    ensure_google_sync_records()
    with _UPLOAD_JOB_LOCK:
        if _upload_thread_running():
            with get_connection() as conn:
                _refresh_pending_upload_count(conn)
                state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
                state = dict(state_row) if state_row else {}
                contact_records_drive_export_needed = _count_pending_shared_contact_exports(conn) > 0
                conn.commit()
            if not bool(state.get("upload_in_progress") or 0):
                _UPLOAD_JOB_THREAD = None
            else:
                return {
                    "started": False,
                    "in_progress": True,
                    "pending_count": int(state.get("pending_upload_count") or 0),
                    "needs_drive_export": contact_records_drive_export_needed or bool(state.get("needs_drive_export") or 0) or bool(state.get("book_layouts_needs_drive_export") or 0) or bool(state.get("contacts_manifest_needs_drive_export") or 0) or bool(state.get("meetingdata_needs_drive_export") or 0) or bool(state.get("field_list_needs_drive_export") or 0) or bool(state.get("changes_list_needs_drive_export") or 0),
                    "contact_records_needs_drive_export": contact_records_drive_export_needed,
                    "meetingdata_needs_drive_export": bool(state.get("meetingdata_needs_drive_export") or 0),
                    "field_list_needs_drive_export": bool(state.get("field_list_needs_drive_export") or 0),
                    "changes_list_needs_drive_export": bool(state.get("changes_list_needs_drive_export") or 0),
                    "total_count": int(state.get("upload_total_count") or 0),
                    "processed_count": int(state.get("upload_processed_count") or 0),
                    "bootstrap_status": str(state.get("bootstrap_status") or "idle"),
                    "bootstrap_total": int(state.get("bootstrap_total_contacts") or 0),
                    "bootstrap_processed": int(state.get("bootstrap_processed_contacts") or 0),
                }
        with get_connection() as conn:
            state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
            state = dict(state_row) if state_row else {}
            pending_count = _refresh_pending_upload_count(conn)
            needs_drive_export = bool(state.get("needs_drive_export") or 0)
            contacts_manifest_needs_drive_export = bool(state.get("contacts_manifest_needs_drive_export") or 0)
            book_layouts_needs_drive_export = bool(state.get("book_layouts_needs_drive_export") or 0)
            contact_records_drive_export_needed = _count_pending_shared_contact_exports(conn) > 0
            meetingdata_needs_drive_export = bool(state.get("meetingdata_needs_drive_export") or 0)
            field_list_needs_drive_export = bool(state.get("field_list_needs_drive_export") or 0)
            changes_list_needs_drive_export = bool(state.get("changes_list_needs_drive_export") or 0)
            bootstrap_remaining = _count_unbootstrapped_contacts(conn)
            if (bootstrap_remaining > 0 or str(state.get("bootstrap_status") or "") in {"running", "failed"}) and pending_count <= 0:
                contact_count = _count_contacts(conn)
                bootstrap_total = int(state.get("bootstrap_total_contacts") or 0)
                if bootstrap_total != contact_count:
                    bootstrap_total = contact_count
                bootstrap_processed = max(bootstrap_total - bootstrap_remaining, 0)
                _set_bootstrap_state(
                    conn,
                    status="running",
                    total_contacts=bootstrap_total,
                    processed_contacts=bootstrap_processed,
                    started_at=str(state.get("bootstrap_started_at") or _now_text()),
                    updated_at=_now_text(),
                    error="",
                )
                _set_upload_progress(
                    conn,
                    in_progress=True,
                    phase="drive_bootstrap",
                    total_count=bootstrap_total,
                    processed_count=_visible_bootstrap_processed(bootstrap_processed, bootstrap_total),
                    current_contact="",
                    started_at=str(state.get("bootstrap_started_at") or _now_text()),
                )
                _clear_last_sync_error(conn)
                conn.commit()
                _UPLOAD_JOB_THREAD = threading.Thread(target=_run_google_upload_job, daemon=True)
                _UPLOAD_JOB_THREAD.start()
                return {
                    "started": True,
                    "in_progress": True,
                    "pending_count": pending_count,
                    "needs_drive_export": True,
                    "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
                    "field_list_needs_drive_export": field_list_needs_drive_export,
                    "changes_list_needs_drive_export": changes_list_needs_drive_export,
                    "total_count": bootstrap_total,
                    "processed_count": _visible_bootstrap_processed(bootstrap_processed, bootstrap_total),
                    "bootstrap_status": "running",
                    "bootstrap_total": bootstrap_total,
                    "bootstrap_processed": bootstrap_processed,
                }
            if bool(state.get("upload_in_progress") or 0) and _state_has_resumable_upload_work(conn, state):
                if not str(state.get("upload_started_at") or "").strip():
                    _set_upload_progress(conn, in_progress=True, started_at=_now_text())
                    state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone() or {})
                _clear_last_sync_error(conn)
                conn.commit()
                _UPLOAD_JOB_THREAD = threading.Thread(target=_run_google_upload_job, daemon=True)
                _UPLOAD_JOB_THREAD.start()
                return {
                    "started": True,
                    "in_progress": True,
                    "pending_count": pending_count,
                    "needs_drive_export": contact_records_drive_export_needed or bool(state.get("needs_drive_export") or 0) or bool(state.get("book_layouts_needs_drive_export") or 0) or bool(state.get("contacts_manifest_needs_drive_export") or 0) or bool(state.get("meetingdata_needs_drive_export") or 0) or bool(state.get("field_list_needs_drive_export") or 0) or bool(state.get("changes_list_needs_drive_export") or 0),
                    "contact_records_needs_drive_export": contact_records_drive_export_needed,
                    "meetingdata_needs_drive_export": bool(state.get("meetingdata_needs_drive_export") or 0),
                    "field_list_needs_drive_export": bool(state.get("field_list_needs_drive_export") or 0),
                    "changes_list_needs_drive_export": bool(state.get("changes_list_needs_drive_export") or 0),
                    "total_count": int(state.get("upload_total_count") or 0),
                    "processed_count": int(state.get("upload_processed_count") or 0),
                    "bootstrap_status": str(state.get("bootstrap_status") or "idle"),
                    "bootstrap_total": int(state.get("bootstrap_total_contacts") or 0),
                    "bootstrap_processed": int(state.get("bootstrap_processed_contacts") or 0),
                }
            _clear_stale_upload_progress_if_needed(conn)
            _clear_orphaned_upload_progress_if_needed(conn)
            state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
            state = dict(state_row) if state_row else {}
            pending_count = _refresh_pending_upload_count(conn)
            needs_drive_export = bool(state.get("needs_drive_export") or 0)
            contacts_manifest_needs_drive_export = bool(state.get("contacts_manifest_needs_drive_export") or 0)
            book_layouts_needs_drive_export = bool(state.get("book_layouts_needs_drive_export") or 0)
            contact_records_drive_export_needed = _count_pending_shared_contact_exports(conn) > 0
            meetingdata_needs_drive_export = bool(state.get("meetingdata_needs_drive_export") or 0)
            field_list_needs_drive_export = bool(state.get("field_list_needs_drive_export") or 0)
            changes_list_needs_drive_export = bool(state.get("changes_list_needs_drive_export") or 0)
            any_drive_export_needed = (
                needs_drive_export
                or contacts_manifest_needs_drive_export
                or book_layouts_needs_drive_export
                or contact_records_drive_export_needed
                or meetingdata_needs_drive_export
                or field_list_needs_drive_export
                or changes_list_needs_drive_export
            )
            total_count = pending_count + (1 if any_drive_export_needed else 0)
            if bool(state.get("upload_in_progress") or 0):
                if _state_has_resumable_upload_work(conn, state):
                    _UPLOAD_JOB_THREAD = threading.Thread(target=_run_google_upload_job, daemon=True)
                    _UPLOAD_JOB_THREAD.start()
                    conn.commit()
                    return {
                        "started": True,
                        "in_progress": True,
                        "pending_count": pending_count,
                        "needs_drive_export": any_drive_export_needed,
                        "contact_records_needs_drive_export": contact_records_drive_export_needed,
                        "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
                        "field_list_needs_drive_export": field_list_needs_drive_export,
                        "changes_list_needs_drive_export": changes_list_needs_drive_export,
                        "total_count": int(state.get("upload_total_count") or total_count),
                        "processed_count": int(state.get("upload_processed_count") or 0),
                        "bootstrap_status": str(state.get("bootstrap_status") or "idle"),
                        "bootstrap_total": int(state.get("bootstrap_total_contacts") or 0),
                        "bootstrap_processed": int(state.get("bootstrap_processed_contacts") or 0),
                    }
                _set_upload_progress(
                    conn,
                    in_progress=False,
                    phase="",
                    total_count=0,
                    processed_count=0,
                    current_contact="",
                    started_at="",
                )
                state_row = conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone()
                state = dict(state_row) if state_row else {}
                pending_count = _refresh_pending_upload_count(conn)
                total_count = pending_count + (1 if any_drive_export_needed else 0)
            if total_count <= 0:
                _set_upload_progress(
                    conn,
                    in_progress=False,
                    phase="",
                    total_count=0,
                    processed_count=0,
                    current_contact="",
                    started_at="",
                )
                _clear_last_sync_error(conn)
                conn.commit()
                return {
                    "started": False,
                    "in_progress": False,
                    "pending_count": 0,
                    "needs_drive_export": False,
                    "contact_records_needs_drive_export": False,
                    "meetingdata_needs_drive_export": False,
                    "field_list_needs_drive_export": False,
                    "changes_list_needs_drive_export": False,
                    "total_count": 0,
                    "processed_count": 0,
                    "bootstrap_status": str(state.get("bootstrap_status") or "idle"),
                    "bootstrap_total": int(state.get("bootstrap_total_contacts") or 0),
                    "bootstrap_processed": int(state.get("bootstrap_processed_contacts") or 0),
                }
            account_row = conn.execute("SELECT account_status FROM google_sync_accounts WHERE id = 1").fetchone()
            if str(account_row["account_status"] if account_row else "") != "connected":
                message = SYNC_NOTICE_MESSAGES["upload_not_connected"]
                _set_upload_progress(
                    conn,
                    in_progress=False,
                    phase="",
                    total_count=total_count,
                    processed_count=0,
                    current_contact="",
                    started_at="",
                )
                _set_last_sync_error(conn, message)
                conn.commit()
                raise RuntimeError(message)
            _set_upload_progress(
                conn,
                in_progress=True,
                phase="google_contacts" if pending_count > 0 else "drive_shared_data",
                total_count=total_count,
                processed_count=0,
                current_contact="",
                started_at=_now_text(),
            )
            _clear_last_sync_error(conn)
            conn.commit()

        _UPLOAD_JOB_THREAD = threading.Thread(target=_run_google_upload_job, daemon=True)
        _UPLOAD_JOB_THREAD.start()
        return {
            "started": True,
            "in_progress": True,
            "pending_count": pending_count,
            "needs_drive_export": any_drive_export_needed,
            "contact_records_needs_drive_export": contact_records_drive_export_needed,
            "meetingdata_needs_drive_export": meetingdata_needs_drive_export,
            "field_list_needs_drive_export": field_list_needs_drive_export,
            "changes_list_needs_drive_export": changes_list_needs_drive_export,
            "total_count": total_count,
            "processed_count": 0,
            "bootstrap_status": str(state.get("bootstrap_status") or "idle"),
            "bootstrap_total": int(state.get("bootstrap_total_contacts") or 0),
            "bootstrap_processed": int(state.get("bootstrap_processed_contacts") or 0),
        }


def get_notice_message(notice_key: str, **kwargs) -> str:
    template = SYNC_NOTICE_MESSAGES.get(str(notice_key or "").strip(), "")
    if not template:
        return ""
    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template
