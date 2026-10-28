from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.config import APP_DATA_DIR, DB_PATH, ERROR_LOG_PATH, PROJECT_ROOT
from app.services.app_info_service import (
    get_app_edition,
    get_current_app_version,
    get_expected_update_manifest_url,
    update_manifest_url_matches_edition,
)
from app.services.google_sync_service import (
    download_app_update_package_from_google_drive,
    get_google_sync_summary,
    import_app_update_manifest_from_google_drive,
)


MANIFEST_FORMAT = "contactsfreeshare.app_update.v1"
UPDATE_MANIFEST_URL_ENV = "CONTACTSFREESHARE_UPDATE_MANIFEST_URL"
UPDATE_MANIFEST_URL_FILE = PROJECT_ROOT / "UPDATE_MANIFEST_URL"
PLACEHOLDER_MANIFEST_URL_PARTS = ("your-public-host", "example.com")
UPDATE_STAGING_DIR = APP_DATA_DIR / "updates"
UPDATE_INSTALL_LOG_PATH = UPDATE_STAGING_DIR / "install_update.log"


def get_update_install_log_path() -> Path:
    return UPDATE_INSTALL_LOG_PATH


def get_app_support_paths() -> dict[str, str]:
    return {
        "app_data_dir": str(APP_DATA_DIR.resolve()),
        "updates_dir": str(UPDATE_STAGING_DIR.resolve()),
        "update_install_log": str(UPDATE_INSTALL_LOG_PATH.resolve()),
        "error_log": str(ERROR_LOG_PATH.resolve()),
        "database": str(DB_PATH.resolve()),
    }


def app_update_failure_help_text(extra_error: str = "") -> str:
    paths = get_app_support_paths()
    lines = [
        "Automatic app update failed. Download the update package and open it manually.",
        f"Update install log: {paths['update_install_log']}",
        f"App error log: {paths['error_log']}",
        f"App data folder: {paths['app_data_dir']}",
    ]
    if extra_error.strip():
        lines.insert(1, f"Error: {extra_error.strip()}")
    return "\n".join(lines)


def app_update_install_progress_text() -> str:
    paths = get_app_support_paths()
    return (
        "If ContactsFreeShare does not reopen after a minute, open the update install log at:\n"
        f"{paths['update_install_log']}"
    )


def _version_parts(version: str) -> list[int | str]:
    parts: list[int | str] = []
    for part in re.split(r"[^A-Za-z0-9]+", str(version or "").strip()):
        if not part:
            continue
        parts.append(int(part) if part.isdigit() else part.lower())
    return parts or [0]


def _compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts))
    for index in range(max_len):
        left_part = left_parts[index] if index < len(left_parts) else 0
        right_part = right_parts[index] if index < len(right_parts) else 0
        if left_part == right_part:
            continue
        if isinstance(left_part, int) and isinstance(right_part, int):
            return 1 if left_part > right_part else -1
        return 1 if str(left_part) > str(right_part) else -1
    return 0


def _current_platform_key() -> str:
    if sys.platform == "darwin":
        return "mac"
    if os.name == "nt":
        return "windows"
    return "other"


def _load_manifest_from_url(url: str) -> dict:
    # GitHub Pages / Fastly cache the manifest for up to 10 minutes. Bust that so
    # Editor sign-in notices see a just-published version immediately.
    cache_buster = str(int(time.time()))
    separator = "&" if "?" in str(url or "") else "?"
    request_url = f"{url}{separator}_cb={cache_buster}"
    request = Request(
        request_url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_dotenv_value(key: str) -> str:
    for dotenv_path in [PROJECT_ROOT / ".env", APP_DATA_DIR / ".env"]:
        try:
            lines = dotenv_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            raw_key, raw_value = line.split("=", 1)
            if raw_key.strip() == key:
                return raw_value.strip().strip('"').strip("'")
    return ""


def _is_placeholder_manifest_url(url: str) -> bool:
    normalized_url = str(url or "").strip().lower()
    return any(part in normalized_url for part in PLACEHOLDER_MANIFEST_URL_PARTS)


def _candidate_manifest_urls() -> list[str]:
    urls: list[str] = []
    dotenv_manifest_url = _read_dotenv_value(UPDATE_MANIFEST_URL_ENV)
    if dotenv_manifest_url:
        urls.append(dotenv_manifest_url)
    env_manifest_url = str(os.getenv(UPDATE_MANIFEST_URL_ENV) or "").strip()
    if env_manifest_url:
        urls.append(env_manifest_url)
    try:
        file_manifest_url = UPDATE_MANIFEST_URL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        file_manifest_url = ""
    if file_manifest_url:
        urls.append(file_manifest_url)
    urls.append(get_expected_update_manifest_url())
    return urls


def _get_public_manifest_url() -> str:
    # Never follow the other edition's feed, even if a leftover .env or shell
    # variable still points at it.
    for manifest_url in _candidate_manifest_urls():
        if _is_placeholder_manifest_url(manifest_url):
            continue
        if update_manifest_url_matches_edition(manifest_url):
            return manifest_url
    return get_expected_update_manifest_url()


def _load_update_manifest() -> tuple[dict, str]:
    candidates: list[tuple[dict, str]] = []
    url_error: Exception | None = None
    manifest_url = _get_public_manifest_url()
    if manifest_url:
        if _is_placeholder_manifest_url(manifest_url):
            raise RuntimeError("Set CONTACTSFREESHARE_UPDATE_MANIFEST_URL to the real public app_update_manifest.json URL before checking for app updates.")
        try:
            manifest = _load_manifest_from_url(manifest_url)
            manifest["_manifest_url"] = manifest_url
            candidates.append((manifest, "url"))
        except (RuntimeError, HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            url_error = exc

    # When Google is connected, also read the Drive App Updates copy and keep the newer
    # of URL vs Drive so a stale CDN response cannot hide a published release.
    try:
        google_summary = get_google_sync_summary()
        account = google_summary.get("account") or {}
        if str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready")):
            drive_payload = import_app_update_manifest_from_google_drive()
            drive_manifest = dict(drive_payload.get("manifest") or {})
            if drive_manifest:
                drive_edition = str(drive_manifest.get("edition") or "").strip().lower()
                if drive_edition and drive_edition != get_app_edition():
                    drive_manifest = {}
            if drive_manifest:
                if manifest_url and not _is_placeholder_manifest_url(manifest_url):
                    drive_manifest["_manifest_url"] = manifest_url
                candidates.append((drive_manifest, "google_drive"))
    except (RuntimeError, HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        pass

    if not candidates:
        if url_error is not None:
            raise url_error
        raise RuntimeError("Connect Google Drive before checking for app updates.")

    best_manifest, best_source = candidates[0]
    for manifest, source in candidates[1:]:
        if _compare_versions(
            str(manifest.get("latest_version") or ""),
            str(best_manifest.get("latest_version") or ""),
        ) > 0:
            best_manifest, best_source = manifest, source
    return best_manifest, best_source


def normalize_update_manifest(manifest: dict) -> dict:
    manifest_url = str(manifest.get("_manifest_url") or "").strip()
    packages = manifest.get("packages") if isinstance(manifest.get("packages"), dict) else {}
    normalized_packages: dict[str, dict] = {}
    for platform_key, package in packages.items():
        if not isinstance(package, dict):
            continue
        download_url = str(package.get("download_url") or "").strip()
        if download_url and manifest_url:
            download_url = urljoin(manifest_url, download_url)
        normalized_packages[str(platform_key)] = {
            "platform": str(platform_key),
            "label": str(package.get("label") or platform_key).strip(),
            "filename": str(package.get("filename") or "").strip(),
            "file_id": str(package.get("file_id") or "").strip(),
            "download_url": download_url,
            "mime_type": str(package.get("mime_type") or "application/zip").strip(),
        }
    return {
        "format": str(manifest.get("format") or "").strip(),
        "latest_version": str(manifest.get("latest_version") or "").strip(),
        "release_date": str(manifest.get("release_date") or "").strip(),
        "notes": str(manifest.get("notes") or "").strip(),
        "edition": str(manifest.get("edition") or "").strip().lower(),
        "packages": normalized_packages,
    }


def get_app_update_status() -> dict:
    current_version = get_current_app_version()
    platform_key = _current_platform_key()
    try:
        raw_manifest, source = _load_update_manifest()
        manifest = normalize_update_manifest(raw_manifest)
    except (RuntimeError, HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "source": "",
            "current_version": current_version,
            "latest_version": "",
            "platform": platform_key,
            "update_available": False,
            "packages": {},
            "message": str(exc),
        }

    latest_version = manifest["latest_version"]
    manifest_edition = str(manifest.get("edition") or "").strip().lower()
    current_edition = get_app_edition()
    if manifest_edition and manifest_edition != current_edition:
        return {
            "ok": False,
            "source": source,
            "current_version": current_version,
            "latest_version": latest_version,
            "platform": platform_key,
            "update_available": False,
            "packages": {},
            "message": "This update belongs to a different ContactsFreeShare edition and will not be installed.",
        }
    version_comparison = _compare_versions(latest_version, current_version) if latest_version else 0
    update_available = bool(latest_version) and version_comparison > 0
    installed_newer_than_latest = bool(latest_version) and version_comparison < 0
    package = manifest["packages"].get(platform_key) or {}
    return {
        "ok": True,
        "source": source,
        "format": manifest["format"] or MANIFEST_FORMAT,
        "current_version": current_version,
        "latest_version": latest_version,
        "release_date": manifest["release_date"],
        "notes": manifest["notes"],
        "platform": platform_key,
        "current_platform_package": package,
        "packages": manifest["packages"],
        "update_available": update_available,
        "installed_newer_than_latest": installed_newer_than_latest,
    }


def get_update_package(platform_key: str) -> dict:
    status = get_app_update_status()
    if not status.get("ok"):
        raise RuntimeError(str(status.get("message") or "App update check failed."))
    packages = status.get("packages") if isinstance(status.get("packages"), dict) else {}
    package = packages.get(str(platform_key or "").strip())
    if not isinstance(package, dict):
        raise RuntimeError("No update package is available for that platform.")
    return package


def download_google_drive_update_package(platform_key: str) -> dict:
    package = get_update_package(platform_key)
    file_id = str(package.get("file_id") or "").strip()
    if not file_id:
        raise RuntimeError("This update package does not have a Google Drive file ID.")
    downloaded = download_app_update_package_from_google_drive(file_id=file_id)
    filename = str(package.get("filename") or "").strip() or str(downloaded.get("filename") or "").strip()
    return {
        **downloaded,
        "filename": filename or "ContactsFreeShare-update.zip",
        "mime_type": str(package.get("mime_type") or downloaded.get("mime_type") or "application/zip"),
    }


def _is_packaged_build() -> bool:
    return (PROJECT_ROOT / "BUILD_INFO.txt").exists()


def _safe_update_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(filename or "").strip())
    return cleaned or "ContactsFreeShare-update.zip"


def _download_update_package_to_staging(platform_key: str) -> tuple[Path, dict]:
    package = get_update_package(platform_key)
    UPDATE_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_update_filename(str(package.get("filename") or "ContactsFreeShare-update.zip"))
    zip_path = UPDATE_STAGING_DIR / filename
    download_url = str(package.get("download_url") or "").strip()
    if download_url:
        request = Request(download_url, headers={"Accept": "application/zip,application/octet-stream,*/*"}, method="GET")
        with urlopen(request, timeout=600) as response:
            with zip_path.open("wb") as output:
                shutil.copyfileobj(response, output)
    else:
        downloaded = download_google_drive_update_package(platform_key)
        zip_path.write_bytes(downloaded.get("content") or b"")
    if not zip_path.is_file() or zip_path.stat().st_size <= 0:
        raise RuntimeError("The update package download was empty.")
    return zip_path, package


def _read_zip_app_edition(zip_path: Path) -> str:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open("ContactsFreeShare/APP_EDITION") as handle:
                return handle.read().decode("utf-8").strip().lower()
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError):
        return ""


def _assert_update_zip_matches_edition(zip_path: Path) -> None:
    package_edition = _read_zip_app_edition(zip_path)
    current_edition = get_app_edition()
    if package_edition and package_edition != current_edition:
        raise RuntimeError(
            f"Refusing to install {package_edition} over {current_edition}. "
            "Single Editor and Multi-Editor updates cannot replace each other."
        )


def _write_mac_update_script(zip_path: Path) -> Path:
    script_path = UPDATE_STAGING_DIR / f"install_contactsfreeshare_update_{int(time.time())}.command"
    log_path = UPDATE_STAGING_DIR / "install_update.log"
    expected_edition = get_app_edition()
    script_path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
APP_DIR={str(PROJECT_ROOT)!r}
ZIP_PATH={str(zip_path)!r}
LOG_PATH={str(log_path)!r}
EXPECTED_EDITION={expected_edition!r}
exec > "$LOG_PATH" 2>&1
sleep 3
PARENT_DIR="$(dirname "$APP_DIR")"
APP_NAME="$(basename "$APP_DIR")"
BACKUP_DIR="$PARENT_DIR/$APP_NAME.backup-$(date +%Y%m%d%H%M%S)"
TMP_DIR="$(mktemp -d "$PARENT_DIR/.contactsfreeshare-update.XXXXXX")"
unzip -q "$ZIP_PATH" -d "$TMP_DIR"
NEW_APP_DIR="$TMP_DIR/ContactsFreeShare"
if [ ! -d "$NEW_APP_DIR" ]; then
  echo "Update package did not contain ContactsFreeShare."
  exit 1
fi
PACKAGE_EDITION="$(tr -d '[:space:]' < "$NEW_APP_DIR/APP_EDITION" 2>/dev/null || true)"
if [ -n "$PACKAGE_EDITION" ] && [ "$PACKAGE_EDITION" != "$EXPECTED_EDITION" ]; then
  echo "Refusing update: package is $PACKAGE_EDITION, this app is $EXPECTED_EDITION."
  rm -rf "$TMP_DIR"
  exit 1
fi
mv "$APP_DIR" "$BACKUP_DIR"
mv "$NEW_APP_DIR" "$APP_DIR"
if [ -d "$BACKUP_DIR/runtime" ]; then
  rm -rf "$APP_DIR/runtime"
  cp -R "$BACKUP_DIR/runtime" "$APP_DIR/runtime"
fi
if [ -f "$BACKUP_DIR/.env" ]; then
  cp "$BACKUP_DIR/.env" "$APP_DIR/.env"
fi
if [ -f "$APP_DIR/UPDATE_MANIFEST_URL" ]; then
  FEED="$(tr -d '[:space:]' < "$APP_DIR/UPDATE_MANIFEST_URL")"
  if [ -n "$FEED" ]; then
    python3 - "$APP_DIR/.env" "$FEED" <<'PY'
from pathlib import Path
import sys
env_path = Path(sys.argv[1])
feed = sys.argv[2].strip()
key = "CONTACTSFREESHARE_UPDATE_MANIFEST_URL="
lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
out = []
found = False
for line in lines:
    if line.strip().startswith(key):
        out.append(key + feed)
        found = True
    else:
        out.append(line)
if not found:
    out.append(key + feed)
env_path.write_text("\\n".join(out) + "\\n", encoding="utf-8")
PY
  fi
fi
chmod +x "$APP_DIR/Start ContactsFreeShare.command" || true
rm -rf "$TMP_DIR"
open "$APP_DIR/Start ContactsFreeShare.command"
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def _write_windows_update_script(zip_path: Path) -> Path:
    script_path = UPDATE_STAGING_DIR / f"install_contactsfreeshare_update_{int(time.time())}.bat"
    log_path = UPDATE_STAGING_DIR / "install_update.log"
    app_dir = PROJECT_ROOT.resolve().as_posix()
    zip_file = zip_path.resolve().as_posix()
    log_file = log_path.resolve().as_posix()
    expected_edition = get_app_edition()
    script_path.write_text(
        f"""@echo off
setlocal EnableExtensions
set "APP_DIR={app_dir}"
set "ZIP_PATH={zip_file}"
set "LOG_PATH={log_file}"
set "APP_PORT=8000"
for %%I in ("%LOG_PATH%") do if not exist "%%~dpI" mkdir "%%~dpI" >nul 2>nul
echo ContactsFreeShare Windows update started at %DATE% %TIME% > "%LOG_PATH%"
echo APP_DIR=%APP_DIR% >> "%LOG_PATH%"
echo ZIP_PATH=%ZIP_PATH% >> "%LOG_PATH%"
set /a WAIT_COUNT=0
:wait_for_shutdown
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GTR 90 goto wait_for_shutdown_done
powershell -NoProfile -ExecutionPolicy Bypass -Command "try {{ $client = New-Object System.Net.Sockets.TcpClient; $client.Connect('127.0.0.1', $env:APP_PORT); $client.Close(); exit 1 }} catch {{ exit 0 }}" >> "%LOG_PATH%" 2>&1
if errorlevel 1 (
  echo Waiting for ContactsFreeShare to stop on port %APP_PORT%... >> "%LOG_PATH%"
  timeout /t 1 /nobreak >nul
  goto wait_for_shutdown
)
:wait_for_shutdown_done
timeout /t 5 /nobreak >nul
echo Stopping ContactsFreeShare Python processes tied to %APP_DIR% >> "%LOG_PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$app = $env:APP_DIR; Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -and $_.CommandLine -like ('*' + $app + '*') -and ($_.Name -match 'python(w?\\.exe)?') }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}" >> "%LOG_PATH%" 2>&1
timeout /t 3 /nobreak >nul
echo %APP_DIR% | findstr /I "\\\\psf\\ \\Desktop\\" >nul 2>&1
if not errorlevel 1 (
  echo NOTE: The app folder is on a shared or Desktop path. If update fails, move ContactsFreeShare to C:\\Users\\%USERNAME%\\ContactsFreeShare and run updates from there. >> "%LOG_PATH%"
)
for %%I in ("%APP_DIR%") do set "PARENT_DIR=%%~dpI"
for %%I in ("%APP_DIR%") do set "APP_NAME=%%~nxI"
set "BACKUP_DIR=%PARENT_DIR%%APP_NAME%.backup-%RANDOM%%RANDOM%"
set "TMP_DIR=%PARENT_DIR%.contactsfreeshare-update-%RANDOM%%RANDOM%"
set "NEW_APP_DIR=%TMP_DIR%\\ContactsFreeShare"
set "USE_ROBOCOPY=0"
echo Expanding update zip into %TMP_DIR% >> "%LOG_PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:ZIP_PATH -DestinationPath $env:TMP_DIR -Force" >> "%LOG_PATH%" 2>&1
if errorlevel 1 (
echo Failed to expand update zip. >> "%LOG_PATH%"
echo Update failed. Full log file: %LOG_PATH% >> "%LOG_PATH%"
exit /b 1
)
if not exist "%NEW_APP_DIR%" (
  echo Update package did not contain ContactsFreeShare folder. >> "%LOG_PATH%"
  echo Update failed. Full log file: %LOG_PATH% >> "%LOG_PATH%"
exit /b 1
)
if exist "%NEW_APP_DIR%\\APP_EDITION" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "if ((Get-Content -LiteralPath (Join-Path $env:NEW_APP_DIR 'APP_EDITION') -TotalCount 1).Trim().ToLower() -ne '{expected_edition}') {{ throw 'wrong edition' }}" >> "%LOG_PATH%" 2>&1
  if errorlevel 1 (
    echo Refusing update: package is a different ContactsFreeShare edition. >> "%LOG_PATH%"
    echo Update failed. Full log file: %LOG_PATH% >> "%LOG_PATH%"
    exit /b 1
  )
)
set /a MOVE_TRY=0
:retry_move_out
set /a MOVE_TRY+=1
echo Moving current app to %BACKUP_DIR% (try %MOVE_TRY%) >> "%LOG_PATH%"
move "%APP_DIR%" "%BACKUP_DIR%" >> "%LOG_PATH%" 2>&1
if not errorlevel 1 goto move_out_ok
if %MOVE_TRY% LSS 20 (
  timeout /t 2 /nobreak >nul
  goto retry_move_out
)
echo Failed to move current app folder to backup. Applying update in place with robocopy. >> "%LOG_PATH%"
set "USE_ROBOCOPY=1"
goto apply_update_files
:move_out_ok
echo Moving new app into %APP_DIR% >> "%LOG_PATH%"
move "%NEW_APP_DIR%" "%APP_DIR%" >> "%LOG_PATH%" 2>&1
if errorlevel 1 (
  echo Failed to move new app folder into place. >> "%LOG_PATH%"
  echo Update failed. Full log file: %LOG_PATH% >> "%LOG_PATH%"
  if exist "%BACKUP_DIR%" move "%BACKUP_DIR%" "%APP_DIR%" >> "%LOG_PATH%" 2>&1
  exit /b 1
)
goto finalize_update
:apply_update_files
echo Updating files in place from %NEW_APP_DIR% to %APP_DIR% >> "%LOG_PATH%"
robocopy "%NEW_APP_DIR%" "%APP_DIR%" /E /IS /IT /R:15 /W:3 /XD .venv /XF .env /NFL /NDL /NJH /NJS /NC /NS /NP >> "%LOG_PATH%" 2>&1
if errorlevel 8 (
  echo Robocopy failed while applying the update in place. >> "%LOG_PATH%"
  echo Update failed. Full log file: %LOG_PATH% >> "%LOG_PATH%"
  exit /b 1
)
:finalize_update
if "%USE_ROBOCOPY%"=="0" (
  if exist "%BACKUP_DIR%\\runtime" xcopy "%BACKUP_DIR%\\runtime" "%APP_DIR%\\runtime" /E /I /Y >> "%LOG_PATH%" 2>&1
  if exist "%BACKUP_DIR%\\.env" copy /Y "%BACKUP_DIR%\\.env" "%APP_DIR%\\.env" >> "%LOG_PATH%" 2>&1
  if exist "%BACKUP_DIR%\\.venv" xcopy "%BACKUP_DIR%\\.venv" "%APP_DIR%\\.venv" /E /I /Y >> "%LOG_PATH%" 2>&1
) else (
  if exist "%NEW_APP_DIR%\\runtime" xcopy "%NEW_APP_DIR%\\runtime" "%APP_DIR%\\runtime" /E /I /Y >> "%LOG_PATH%" 2>&1
)
rmdir /S /Q "%TMP_DIR%" >nul 2>nul
echo Update installed. Starting ContactsFreeShare. >> "%LOG_PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath (Join-Path $env:APP_DIR 'Start ContactsFreeShare Windows.bat') -WorkingDirectory $env:APP_DIR -ArgumentList '--update-relaunch'" >> "%LOG_PATH%" 2>&1
if errorlevel 1 (
  echo PowerShell Start-Process failed. Trying cmd start fallback. >> "%LOG_PATH%"
  start "ContactsFreeShare" /D "%APP_DIR%" cmd /c ""%APP_DIR%\\Start ContactsFreeShare Windows.bat" --update-relaunch"
)
exit /b 0
""",
        encoding="utf-8",
    )
    return script_path


def _launch_windows_update_script(script_path: Path) -> None:
    launch_command = f'start "ContactsFreeShare Update" /MIN cmd /c ""{script_path}""'
    creationflags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS
    subprocess.Popen(
        launch_command,
        shell=True,
        cwd=str(UPDATE_STAGING_DIR),
        creationflags=creationflags,
        close_fds=True,
    )


def stage_app_update_install(platform_key: str) -> dict:
    current_platform = _current_platform_key()
    requested_platform = str(platform_key or "").strip()
    if requested_platform != current_platform:
        raise RuntimeError("Automatic install is only available for this computer's platform.")
    if not _is_packaged_build():
        raise RuntimeError("Automatic install is only available from a packaged ContactsFreeShare app.")
    zip_path, package = _download_update_package_to_staging(requested_platform)
    _assert_update_zip_matches_edition(zip_path)
    if current_platform == "mac":
        script_path = _write_mac_update_script(zip_path)
        subprocess.Popen(["/bin/bash", str(script_path)], cwd=str(UPDATE_STAGING_DIR), start_new_session=True)
    elif current_platform == "windows":
        script_path = _write_windows_update_script(zip_path)
        _launch_windows_update_script(script_path)
    else:
        raise RuntimeError("Automatic install is not available on this platform.")
    return {
        "package": package,
        "zip_path": str(zip_path),
        "script_path": str(script_path),
        "log_path": str(get_update_install_log_path().resolve()),
    }
