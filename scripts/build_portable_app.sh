#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/dist"
APP_DIR="$BUILD_DIR/ContactsFreeShare"
WHEEL_DIR="$ROOT_DIR/vendor/wheels"
PORTABLE_BUNDLE_REQUIREMENTS="$APP_DIR/requirements-portable-bundle.txt"
BUNDLE_WHEELS="${CONTACTSFREESHARE_BUNDLE_WHEELS:-0}"
BUNDLE_WHEEL_EXCLUDE_PACKAGES="${CONTACTSFREESHARE_BUNDLE_WHEEL_EXCLUDE_PACKAGES:-pymupdf}"

remove_stale_update_manifests() {
  # Google Drive/iCloud leave copies like "app_update_manifest [conflicted 2].json".
  find "$BUILD_DIR" -maxdepth 1 -type f -name 'app_update_manifest*conflicted*.json' -delete
  if [ "${1:-}" = "all" ]; then
    rm -f "$BUILD_DIR/app_update_manifest.json"
  fi
}

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
remove_stale_update_manifests

if [ "$BUNDLE_WHEELS" != "0" ] && [ -f "$ROOT_DIR/requirements.txt" ]; then
  mkdir -p "$WHEEL_DIR"
  if ! ls "$WHEEL_DIR"/fastapi-*.whl >/dev/null 2>&1; then
    echo "Bundled Python dependency wheels are missing. Downloading them now..."
    python3 -m pip download -r "$ROOT_DIR/requirements.txt" -d "$WHEEL_DIR"
  fi
  for windows_python in 312 313 314; do
    windows_wheels_missing=0
    for wheel_glob in \
      "markupsafe-*-cp${windows_python}-*-win_amd64.whl" \
      "pydantic_core-*-cp${windows_python}-*-win_amd64.whl" \
      "cffi-*-cp${windows_python}-*-win_amd64.whl" \
      "charset_normalizer-*-cp${windows_python}-*-win_amd64.whl"
    do
      if ! ls "$WHEEL_DIR"/$wheel_glob >/dev/null 2>&1; then
        windows_wheels_missing=1
        break
      fi
    done
    if ! ls "$WHEEL_DIR"/cryptography-*-win_amd64.whl >/dev/null 2>&1 \
      || ! ls "$WHEEL_DIR"/protobuf-*-win_amd64.whl >/dev/null 2>&1; then
      windows_wheels_missing=1
    fi
    if [ "$windows_wheels_missing" -eq 1 ]; then
      echo "Bundled Windows Python 3.${windows_python#3} wheels are missing. Downloading them now..."
      python3 -m pip download \
        --only-binary=:all: \
        --platform win_amd64 \
        --implementation cp \
        --python-version "$windows_python" \
        --abi "cp${windows_python}" \
        -r "$ROOT_DIR/requirements.txt" \
        -d "$WHEEL_DIR"
    fi
  done
  for mac_python in 312 313 314; do
    for mac_platform in macosx_11_0_arm64 macosx_10_13_x86_64; do
      mac_platform_glob="$mac_platform"
      if [ "$mac_platform" = "macosx_10_13_x86_64" ]; then
        mac_platform_glob="macosx_10_*_x86_64"
      fi
      mac_wheels_missing=0
      for wheel_glob in \
        "markupsafe-*-cp${mac_python}-*-${mac_platform_glob}.whl" \
        "pydantic_core-*-cp${mac_python}-*-${mac_platform_glob}.whl" \
        "cffi-*-cp${mac_python}-*-${mac_platform_glob}.whl"
      do
        if ! ls "$WHEEL_DIR"/$wheel_glob >/dev/null 2>&1; then
          mac_wheels_missing=1
          break
        fi
      done
      if ! ls "$WHEEL_DIR"/cryptography-*-macosx*.whl >/dev/null 2>&1 \
        || ! ls "$WHEEL_DIR"/protobuf-*-macosx*.whl >/dev/null 2>&1 \
        || ! ls "$WHEEL_DIR"/charset_normalizer-*-cp${mac_python}-*-${mac_platform_glob}.whl >/dev/null 2>&1; then
        mac_wheels_missing=1
      fi
      if [ "$mac_wheels_missing" -eq 1 ]; then
        echo "Bundled macOS Python 3.${mac_python#3} $mac_platform wheels are missing. Downloading them now..."
        python3 -m pip download \
          --only-binary=:all: \
          --platform "$mac_platform" \
          --implementation cp \
          --python-version "$mac_python" \
          --abi "cp${mac_python}" \
          -r "$ROOT_DIR/requirements.txt" \
          -d "$WHEEL_DIR"
      fi
    done
  done
  python3 "$ROOT_DIR/scripts/prune_vendor_wheels.py" --dedupe "$WHEEL_DIR"
fi

rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '/.pip_packages/' \
  --exclude '/contactsfreeshare-site/' \
  --exclude '/mobilecontacts/' \
  --exclude '/tests/' \
  --exclude '/sharecontactsfree/node_modules/' \
  --exclude '/dist/' \
  --exclude '/imports/' \
  --exclude '/logs/' \
  --exclude '/manual-images/' \
  --exclude '/share-images/' \
  --exclude '/ContactsFreeShare-*.html' \
  --exclude '/ContactsFreeShare-*.pdf' \
  --exclude '/ShareContacts-App-Manual.*' \
  --exclude '/SELF_HOSTING_GUIDE.md' \
  --exclude '/vendor/python/' \
  --exclude '/vendor/wheels/' \
  --exclude '/runtime/' \
  --exclude '/uploads/' \
  --exclude '/*.gs' \
  --exclude '/appsscript.json' \
  --exclude '/9.sidebar.html' \
  --exclude '/10.sidebar_addContact.html' \
  --exclude '/11.CreateAddressBooksDialog.html' \
  --exclude '/12.fieldList.html' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'UPDATE_MANIFEST_URL' \
  --exclude 'app.db' \
  --exclude 'db/*.sqlite3' \
  --exclude 'db/*.sqlite3-*' \
  --exclude 'db/contacts.db' \
  --exclude 'app/static/uploads' \
  --exclude '/app/static/help/manual-images/' \
  --exclude '/app/static/help/share-images/' \
  "$ROOT_DIR/" "$APP_DIR/"


# Help screenshots are hosted on GitHub Pages (not inside the zip) to stay under
# GitHub's package size limits. Rewrite manuals to absolute image URLs.
HELP_IMAGE_BASE_URL="${CONTACTSFREESHARE_HELP_IMAGE_BASE_URL:-https://menfisher.github.io/contactsfreeshare-updates/help}"
mkdir -p "$APP_DIR/app/static/help"
python3 - "$ROOT_DIR/ContactsFreeShare-App-Manual.html" "$APP_DIR/app/static/help/cfs-manual.html" "$HELP_IMAGE_BASE_URL/manual-images" <<'PY'
from pathlib import Path
import sys
src, dst, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].rstrip("/")
text = src.read_text(encoding="utf-8")
text = text.replace('src="manual-images/', f'src="{base}/')
text = text.replace('<code>manual-images/', f'<code>{base}/')
dst.write_text(text, encoding="utf-8")
PY
python3 - "$ROOT_DIR/ShareContacts-App-Manual.html" "$APP_DIR/app/static/help/share-manual.html" "$HELP_IMAGE_BASE_URL/share-images" <<'PY'
from pathlib import Path
import sys
src, dst, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].rstrip("/")
text = src.read_text(encoding="utf-8")
text = text.replace('src="share-images/', f'src="{base}/')
text = text.replace('<code>share-images/', f'<code>{base}/')
dst.write_text(text, encoding="utf-8")
PY
# Ensure no local screenshot folders ride along in the package.
rm -rf "$APP_DIR/app/static/help/manual-images" "$APP_DIR/app/static/help/share-images"

awk '!/^#/ && NF && $1 != "pymupdf" { print }' "$ROOT_DIR/requirements.txt" > "$PORTABLE_BUNDLE_REQUIREMENTS"

{
  echo "ContactsFreeShare portable build"
  echo "built_at=$(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "source_dir=$ROOT_DIR"
} > "$APP_DIR/BUILD_INFO.txt"

mkdir -p "$APP_DIR/runtime"
cp "$ROOT_DIR/scripts/find_python_windows.ps1" "$APP_DIR/runtime/find_python_windows.ps1"

if [ -f "$ROOT_DIR/.env" ]; then
  awk -F= '
    BEGIN {
      keys["GOOGLE_OAUTH_CLIENT_ID"] = 1
      keys["GOOGLE_OAUTH_CLIENT_SECRET"] = 1
      keys["GOOGLE_OAUTH_REDIRECT_URI"] = 1
      keys["GOOGLE_PICKER_API_KEY"] = 1
      keys["GOOGLE_PICKER_APP_ID"] = 1
      keys["CONTACTSFREESHARE_UPDATE_MANIFEST_URL"] = 1
      keys["SHARE_WEB_API_KEY"] = 1
      keys["CFS_API_KEY"] = 1
    }
    $1 in keys { print }
  ' "$ROOT_DIR/.env" > "$APP_DIR/.env"
fi

if [ -f "$ROOT_DIR/.env" ]; then
  for required_key in GOOGLE_PICKER_API_KEY GOOGLE_PICKER_APP_ID; do
    if grep -q "^${required_key}=" "$ROOT_DIR/.env" && ! grep -q "^${required_key}=" "$APP_DIR/.env"; then
      echo "Error: $required_key was not copied into the portable package .env."
      exit 1
    fi
  done
fi

cat > "$APP_DIR/Start ContactsFreeShare.command" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

resolve_latest_stable_python_version() {
  local version=""
  version="$(
    curl -fsSL 'https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false&limit=300' \
      | tr '{' '\n' \
      | grep '"name":"Python [0-9]' \
      | grep -v 'Python install manager' \
      | sed -n 's/.*"name":"Python \([0-9][0-9.]*\)".*/\1/p' \
      | awk -F. 'NF == 3 && $1 == 3 { print }' \
      | sort -t. -k1,1n -k2,2n -k3,3n \
      | tail -1
  )"
  if [ -z "$version" ]; then
    return 1
  fi
  printf '%s\n' "$version"
}

find_compatible_python() {
  local candidates=() path ver best_cmd="" best_ver=""
  while IFS= read -r path; do
    [ -n "$path" ] && candidates+=("$path")
  done < <(
    {
      command -v python3 2>/dev/null || true
      which -a python3 2>/dev/null || true
      for path in \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3"
      do
        [ -x "$path" ] && printf '%s\n' "$path"
      done
      for path in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3; do
        [ -x "$path" ] && printf '%s\n' "$path"
      done
      for path in /opt/homebrew/opt/python@3.*/bin/python3 /usr/local/opt/python@3.*/bin/python3; do
        [ -x "$path" ] && printf '%s\n' "$path"
      done
    } | awk 'NF && !seen[$0]++'
  )

  for path in "${candidates[@]}"; do
    ver="$("$path" - <<'PY' 2>/dev/null || true
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
)"
    [ -z "$ver" ] && continue
    case "$ver" in
      3.[0-9]*)
        minor="${ver#3.}"
        minor="${minor%%.*}"
        if [ "$minor" -ge 12 ] 2>/dev/null; then
          if [ -z "$best_ver" ] || [ "$(printf '%s\n' "$best_ver" "$ver" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" = "$ver" ]; then
            best_cmd="$path"
            best_ver="$ver"
          fi
        fi
        ;;
    esac
  done

  if [ -n "$best_cmd" ]; then
    printf '%s\n' "$best_cmd"
    return 0
  fi
  return 1
}

prompt_python_install() {
  PYTHON_CMD="$(find_compatible_python || true)"
  if [ -n "$PYTHON_CMD" ]; then
    PYTHON_VERSION_FOUND="$("$PYTHON_CMD" - <<'PY' 2>/dev/null || true
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
)"
    echo
    echo "Found compatible Python ${PYTHON_VERSION_FOUND} at:"
    echo "  ${PYTHON_CMD}"
    echo "Skipping Python download/install."
    return 0
  fi

  PYTHON_INSTALLER_DIR="$APP_DIR/runtime/python-installers"
  PYTHON_VERSION="$(resolve_latest_stable_python_version || true)"
  if [ -z "$PYTHON_VERSION" ]; then
    echo
    echo "ContactsFreeShare needs Python 3.12 or newer."
    echo "Could not determine the latest Python release from python.org."
    echo "Install Python from https://www.python.org/downloads/macos/ and run this file again."
    echo
    read -r -p "Press Return to close."
    exit 1
  fi
  PYTHON_INSTALLER="$PYTHON_INSTALLER_DIR/python-${PYTHON_VERSION}-macos11.pkg"
  PYTHON_INSTALLER_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-macos11.pkg"

  echo
  echo "ContactsFreeShare needs Python 3.12 or newer."
  mkdir -p "$PYTHON_INSTALLER_DIR"
  if [ ! -f "$PYTHON_INSTALLER" ]; then
    echo "Downloading Python ${PYTHON_VERSION} installer from python.org..."
    if ! curl -fL "$PYTHON_INSTALLER_URL" -o "$PYTHON_INSTALLER"; then
      echo
      echo "Could not download the Python installer."
      echo "Install Python from https://www.python.org/downloads/macos/ and run this file again."
      echo
      read -r -p "Press Return to close."
      exit 1
    fi
  fi
  echo "Opening the Python ${PYTHON_VERSION} installer now."
  echo "After the installer finishes, run Start ContactsFreeShare.command again."
  open "$PYTHON_INSTALLER"
  echo
  read -r -p "Press Return to close."
  exit 1
}

if [ -f ".venv/pyvenv.cfg" ]; then
  VENV_HOME="$(awk -F= '/^home = / { gsub(/^ /, "", $2); print $2; exit }' ".venv/pyvenv.cfg")"
  if [ -n "$VENV_HOME" ] && [ ! -d "$VENV_HOME" ]; then
    rm -rf ".venv"
  fi
fi

if [ ! -x ".venv/bin/python" ]; then
  PYTHON_CMD="$(find_compatible_python || true)"
  if [ -z "$PYTHON_CMD" ]; then
    prompt_python_install
    if [ -z "$PYTHON_CMD" ]; then
      exit 1
    fi
  fi
  "$PYTHON_CMD" -m venv .venv
fi

PYTHON=".venv/bin/python"
if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
then
  rm -rf ".venv"
  PYTHON_CMD="$(find_compatible_python || true)"
  if [ -z "$PYTHON_CMD" ]; then
    prompt_python_install
    if [ -z "$PYTHON_CMD" ]; then
      exit 1
    fi
  fi
  "$PYTHON_CMD" -m venv .venv
fi
if ! "$PYTHON" -c "import fastapi, uvicorn, jinja2, multipart" >/dev/null 2>&1; then
  echo "Installing ContactsFreeShare Python dependencies..."
  if [ -d "vendor/wheels" ]; then
    if ! "$PYTHON" -m pip install --no-index --find-links "vendor/wheels" -r requirements-portable-bundle.txt; then
      echo "Some bundled wheels did not match this Python version. Trying online install..."
      "$PYTHON" -m pip install -r requirements-portable-bundle.txt
    fi
  else
    "$PYTHON" -m pip install -r requirements-portable-bundle.txt
  fi
  if ! "$PYTHON" -c "import fastapi, uvicorn, jinja2, multipart" >/dev/null 2>&1; then
    echo
    echo "ContactsFreeShare dependencies could not be installed."
    echo "Connect to the Internet and run this file again."
    echo
    read -r -p "Press Return to close."
    exit 1
  fi
fi
"$PYTHON" run_contactsfreeshare.py
SCRIPT

chmod +x "$APP_DIR/Start ContactsFreeShare.command"

cat > "$APP_DIR/Install Desktop Shortcut Mac.command" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$APP_DIR/Start ContactsFreeShare.command"
ICON_SRC="$APP_DIR/app/static/img/contactsfreeshare-icon.icns"
DESKTOP_DIR="$HOME/Desktop"
APP_BUNDLE="$DESKTOP_DIR/ContactsFS.app"

if [ ! -f "$TARGET" ]; then
  echo "Start ContactsFreeShare.command was not found in:"
  echo "$APP_DIR"
  echo
  echo "Keep this installer inside the ContactsFreeShare folder and run it again."
  read -r -p "Press Return to close."
  exit 1
fi

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APP_BUNDLE/Contents/Resources/ContactsFreeShare.icns"
fi

cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>ContactsFS</string>
  <key>CFBundleExecutable</key>
  <string>ContactsFreeShare</string>
  <key>CFBundleIconFile</key>
  <string>ContactsFreeShare</string>
  <key>CFBundleIdentifier</key>
  <string>local.contactsfreeshare.launcher</string>
  <key>CFBundleName</key>
  <string>ContactsFS</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>10.13</string>
</dict>
</plist>
PLIST

{
  echo '#!/usr/bin/env bash'
  printf 'TARGET=%q\n' "$TARGET"
  echo 'if [ ! -f "$TARGET" ]; then'
  echo '  osascript -e "display dialog \"ContactsFreeShare could not find its start file. Re-run Install Desktop Shortcut Mac.command from the ContactsFreeShare folder.\" buttons {\"OK\"} default button \"OK\" with icon caution" >/dev/null 2>&1 || true'
  echo '  exit 1'
  echo 'fi'
  echo 'open "$TARGET"'
} > "$APP_BUNDLE/Contents/MacOS/ContactsFreeShare"
chmod +x "$APP_BUNDLE/Contents/MacOS/ContactsFreeShare"
touch "$APP_BUNDLE"

echo
echo "ContactsFS desktop app installed."
echo "Double-click the ContactsFS icon on your Desktop to open the app."
echo "Keep the unzipped ContactsFreeShare folder in this location:"
echo "$APP_DIR"
echo
read -r -p "Press Return to close."
SCRIPT

chmod +x "$APP_DIR/Install Desktop Shortcut Mac.command"

cat > "$APP_DIR/Start ContactsFreeShare Windows.bat" <<'SCRIPT'
@echo off
setlocal

set "UPDATE_RELAUNCH=0"
if /I "%~1"=="--update-relaunch" set "UPDATE_RELAUNCH=1"

pushd "%~dp0"
if errorlevel 1 (
  echo.
  echo ContactsFreeShare could not open its app folder.
  echo Copy the ContactsFreeShare folder to a local Windows folder and run this file again.
  echo.
  pause
  exit /b 1
)

call :find_compatible_python

if "%PYTHON_CMD%"=="" (
  echo Python 3.12 or newer was not found on this computer.
  echo Checked common locations such as:
  echo   %LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
  echo   %LOCALAPPDATA%\Programs\Python\Python313\python.exe
  echo   %ProgramFiles%\Python313\python.exe
  set "INSTALLER_DIR=runtime\python-installers"
  set "PYTHON_LATEST="
  for /f "usebackq delims=" %%V in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$releases = Invoke-RestMethod 'https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false&limit=300'; $latest = $releases ^| Where-Object { $_.name -match '^Python [0-9]' -and $_.name -notmatch 'install manager' } ^| ForEach-Object { [pscustomobject]@{ Version = [version]($_.name -replace '^Python ','') } } ^| Sort-Object Version -Descending ^| Select-Object -First 1; if ($null -eq $latest) { exit 1 }; $latest.Version.ToString()"`) do set "PYTHON_LATEST=%%V"
  if "%PYTHON_LATEST%"=="" (
    echo.
    echo ContactsFreeShare needs Python 3.12 or newer.
    echo Could not determine the latest Python release from python.org.
    echo Install Python from https://www.python.org/downloads/windows/ and run this file again.
    echo.
    pause
    exit /b 1
  )
  set "INSTALLER=%INSTALLER_DIR%\python-%PYTHON_LATEST%-amd64.exe"
  set "INSTALLER_URL=https://www.python.org/ftp/python/%PYTHON_LATEST%/python-%PYTHON_LATEST%-amd64.exe"
  if not exist "%INSTALLER_DIR%" mkdir "%INSTALLER_DIR%"
  if not exist "%INSTALLER%" (
    echo Downloading Python %PYTHON_LATEST% installer from python.org...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri '%INSTALLER_URL%' -OutFile '%INSTALLER%' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 (
      echo.
      echo Could not download the Python installer.
      echo Install Python from https://www.python.org/downloads/windows/ and run this file again.
      echo.
      pause
      exit /b 1
    )
  )
  for /f "tokens=1,2 delims=." %%A in ("%PYTHON_LATEST%") do set "PYTHON_TAG=%%A%%B"
  set "PYTHON_HOME=%LOCALAPPDATA%\Programs\Python\Python%PYTHON_TAG%"
  if exist "%PYTHON_HOME%\python.exe" (
    set "PYTHON_CMD=%PYTHON_HOME%\python.exe"
  ) else (
    echo Installing Python %PYTHON_LATEST%...
    "%INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%PYTHON_HOME%" Include_launcher=1 Include_pip=1 Include_test=0 PrependPath=1
    if errorlevel 1 (
      echo.
      echo Python installation did not finish successfully.
      echo.
      pause
      exit /b 1
    )
    if exist "%PYTHON_HOME%\python.exe" (
      set "PYTHON_CMD=%PYTHON_HOME%\python.exe"
    )
  )
  call :find_compatible_python
)

if "%PYTHON_CMD%"=="" (
  echo.
  echo Python was installed, but it could not be found yet.
  echo Close this window and run Start ContactsFreeShare Windows.bat again.
  echo.
  pause
  exit /b 1
)

if not exist "%PYTHON_CMD%" (
  echo.
  echo ContactsFreeShare found Python at:
  echo   %PYTHON_CMD%
  echo but that file does not exist.
  echo.
  pause
  exit /b 1
)

"%PYTHON_CMD%" --version
if errorlevel 1 (
  echo.
  echo Python could not be started.
  echo Attempted command:
  echo   "%PYTHON_CMD%" --version
  echo.
  pause
  exit /b 1
)

for /f "tokens=2 delims= " %%V in ('"%PYTHON_CMD%" --version 2^>^&1') do set "PYTHON_VERSION=%%V"
echo Using Python %PYTHON_VERSION%

powershell -NoProfile -ExecutionPolicy Bypass -Command "$v=[version]('%PYTHON_VERSION%'); if ($v.Major -lt 3 -or ($v.Major -eq 3 -and $v.Minor -lt 12)) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  call :find_compatible_python
  if not "%PYTHON_CMD%"=="" (
    for /f "tokens=2 delims= " %%V in ('"%PYTHON_CMD%" --version 2^>^&1') do set "PYTHON_VERSION=%%V"
    echo Using Python %PYTHON_VERSION%
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$v=[version]('%PYTHON_VERSION%'); if ($v.Major -lt 3 -or ($v.Major -eq 3 -and $v.Minor -lt 12)) { exit 1 } else { exit 0 }"
  )
  if errorlevel 1 (
    echo.
    echo ContactsFreeShare needs Python 3.12 or newer.
    echo Install a supported Python version, or run this launcher while connected to the Internet.
    echo.
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  "%PYTHON_CMD%" -m venv .venv
  if errorlevel 1 (
    echo.
    echo Python is installed, but the virtual environment could not be created.
    echo Close this window and run Start ContactsFreeShare Windows.bat again.
    echo.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "print('ok')" >nul 2>nul
if errorlevel 1 (
  rmdir /s /q ".venv"
  "%PYTHON_CMD%" -m venv .venv
  if errorlevel 1 (
    echo.
    echo Python is installed, but the virtual environment could not be created.
    echo Close this window and run Start ContactsFreeShare Windows.bat again.
    echo.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, jinja2, multipart" >nul 2>nul
if errorlevel 1 (
  echo Installing ContactsFreeShare Python dependencies...
  if exist "vendor\wheels" (
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links "vendor\wheels" -r requirements-portable-bundle.txt
    if errorlevel 1 (
      ".venv\Scripts\python.exe" -m pip install -r requirements-portable-bundle.txt
    )
  ) else (
    ".venv\Scripts\python.exe" -m pip install -r requirements-portable-bundle.txt
  )
  if errorlevel 1 (
    echo.
    echo ContactsFreeShare dependencies could not be installed.
    echo Connect to the Internet and run this file again, or use a package that already includes a prepared .venv folder.
    echo.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" run_contactsfreeshare.py
if "%UPDATE_RELAUNCH%"=="0" pause
exit /b 0

:find_compatible_python
set "PYTHON_CMD="

for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Python\pythoncore-3*" 2^>nul') do (
  if not defined PYTHON_CMD call :try_python_exe "%LOCALAPPDATA%\Python\%%D\python.exe"
)
if not defined PYTHON_CMD call :try_python_exe "%LOCALAPPDATA%\Python\bin\python.exe"

for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do (
  if not defined PYTHON_CMD call :try_python_exe "%LOCALAPPDATA%\Programs\Python\%%D\python.exe"
)

for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\Python3*" 2^>nul') do (
  if not defined PYTHON_CMD call :try_python_exe "%ProgramFiles%\%%D\python.exe"
)

for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles(x86)%\Python3*" 2^>nul') do (
  if not defined PYTHON_CMD call :try_python_exe "%ProgramFiles(x86)%\%%D\python.exe"
)

where py >nul 2>nul
if not errorlevel 1 if not defined PYTHON_CMD (
  for %%T in (315 314 313 312) do (
    if not defined PYTHON_CMD (
      py -3.%%T -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
      if not errorlevel 1 (
        for /f "delims=" %%E in ('py -3.%%T -c "import sys; print(sys.executable)" 2^>nul') do (
          if not defined PYTHON_CMD call :try_python_exe "%%E"
        )
      )
    )
  )
)

if not defined PYTHON_CMD if exist "%~dp0runtime\find_python_windows.ps1" (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\find_python_windows.ps1" 2^>nul`) do (
    if not defined PYTHON_CMD call :try_python_exe "%%P"
  )
)

exit /b 0

:try_python_exe
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
echo %~1 | findstr /I /C:"\WindowsApps\" >nul 2>&1
if not errorlevel 1 exit /b 0
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYTHON_CMD=%~1"
exit /b 0
SCRIPT

cat > "$APP_DIR/Install Desktop Shortcut Windows.bat" <<'SCRIPT'
@echo off
setlocal

set "SOURCE_DIR=%~dp0"
pushd "%~dp0"
if errorlevel 1 (
  echo.
  echo ContactsFreeShare could not open its app folder.
  echo Copy the ContactsFreeShare folder to a local Windows folder and run this file again.
  echo.
  pause
  exit /b 1
)

set "APP_DIR=%SOURCE_DIR%"
set "TARGET=%APP_DIR%Start ContactsFreeShare Windows.bat"
set "ICON=%APP_DIR%app\static\img\contactsfreeshare-icon.ico"
set "LOCAL_ICON_DIR=%LOCALAPPDATA%\ContactsFreeShare"
set "LOCAL_ICON=%LOCAL_ICON_DIR%\contactsfreeshare-icon-transparent.ico"
set "SHORTCUT_NAME=ContactsFS.lnk"

if not exist "%TARGET%" (
  echo Start ContactsFreeShare Windows.bat was not found in:
  echo %APP_DIR%
  echo.
  echo Keep this installer inside the ContactsFreeShare folder and run it again.
  pause
  exit /b 1
)

if exist "%ICON%" (
  if not exist "%LOCAL_ICON_DIR%" mkdir "%LOCAL_ICON_DIR%" >nul 2>nul
  copy /Y "%ICON%" "%LOCAL_ICON%" >nul 2>nul
  if exist "%LOCAL_ICON%" set "ICON=%LOCAL_ICON%"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop=[Environment]::GetFolderPath('Desktop');" ^
  "$shortcutPath=Join-Path $desktop '%SHORTCUT_NAME%';" ^
  "if (Test-Path $shortcutPath) { Remove-Item $shortcutPath -Force };" ^
  "$shell=New-Object -ComObject WScript.Shell;" ^
  "$shortcut=$shell.CreateShortcut($shortcutPath);" ^
  "$shortcut.TargetPath=$env:TARGET;" ^
  "$shortcut.WorkingDirectory=$desktop;" ^
  "$shortcut.Description='Open ContactsFreeShare';" ^
  "if (Test-Path $env:ICON) { $shortcut.IconLocation=($env:ICON + ',0') };" ^
  "$shortcut.Save()"

if errorlevel 1 (
  echo.
  echo The desktop shortcut could not be created.
  echo You can still start the app by double-clicking Start ContactsFreeShare Windows.bat.
  echo.
  pause
  exit /b 1
)

echo.
echo ContactsFS desktop shortcut installed.
echo Double-click the ContactsFS icon on your Desktop to open the app.
echo.
pause
SCRIPT

APP_VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/APP_VERSION")"
APP_VERSION="${APP_VERSION:-1.0}"
APP_EDITION="$(tr -d '[:space:]' < "$ROOT_DIR/APP_EDITION" 2>/dev/null || true)"
if [ "$APP_EDITION" = "single-editor" ]; then
  PACKAGE_PREFIX="ContactsFreeShare-SingleEditor"
elif [ "$APP_EDITION" = "multi-editor" ]; then
  PACKAGE_PREFIX="ContactsFreeShare-MultiEditor"
else
  PACKAGE_PREFIX="ContactsFreeShare"
fi
printf '%s\n' "${APP_EDITION:-multi-editor}" > "$APP_DIR/APP_EDITION"
MAC_PACKAGE="${PACKAGE_PREFIX}-${APP_VERSION}-mac.zip"
WINDOWS_PACKAGE="${PACKAGE_PREFIX}-${APP_VERSION}-windows.zip"
PUBLIC_MANIFEST_URL="${CONTACTSFREESHARE_UPDATE_MANIFEST_URL:-}"
CLEAN_UPDATE_PACKAGES="${CONTACTSFREESHARE_CLEAN_UPDATE_PACKAGES:-1}"
BUILD_MAC_PACKAGE="${CONTACTSFREESHARE_BUILD_MAC_PACKAGE:-1}"
BUILD_WINDOWS_PACKAGE="${CONTACTSFREESHARE_BUILD_WINDOWS_PACKAGE:-1}"

if [ -n "$PUBLIC_MANIFEST_URL" ]; then
  printf '%s\n' "$PUBLIC_MANIFEST_URL" > "$APP_DIR/UPDATE_MANIFEST_URL"
fi

if [ "$CLEAN_UPDATE_PACKAGES" != "0" ]; then
  if [ "$BUILD_MAC_PACKAGE" != "0" ]; then
    find "$BUILD_DIR" -maxdepth 1 -type f -name 'ContactsFreeShare-*-mac.zip' -delete
  fi
  if [ "$BUILD_WINDOWS_PACKAGE" != "0" ]; then
    find "$BUILD_DIR" -maxdepth 1 -type f -name 'ContactsFreeShare-*-windows.zip' -delete
  fi
  remove_stale_update_manifests all
fi
if [ "$BUILD_MAC_PACKAGE" != "0" ]; then
  rm -f "$BUILD_DIR/$MAC_PACKAGE"
  if [ "$BUNDLE_WHEELS" != "0" ]; then
    python3 "$ROOT_DIR/scripts/prune_vendor_wheels.py" \
      --platform mac \
      --source "$WHEEL_DIR" \
      --dest "$APP_DIR/vendor/wheels" \
      --exclude-package "$BUNDLE_WHEEL_EXCLUDE_PACKAGES"
  fi
  (cd "$BUILD_DIR" && zip -qr "$MAC_PACKAGE" "ContactsFreeShare")
fi
if [ "$BUILD_WINDOWS_PACKAGE" != "0" ]; then
  rm -f "$BUILD_DIR/$WINDOWS_PACKAGE"
  if [ "$BUNDLE_WHEELS" != "0" ]; then
    python3 "$ROOT_DIR/scripts/prune_vendor_wheels.py" \
      --platform windows \
      --source "$WHEEL_DIR" \
      --dest "$APP_DIR/vendor/wheels" \
      --exclude-package "$BUNDLE_WHEEL_EXCLUDE_PACKAGES"
  fi
  (cd "$BUILD_DIR" && zip -qr "$WINDOWS_PACKAGE" "ContactsFreeShare")
fi

{
cat <<SCRIPT
{
  "format": "contactsfreeshare.app_update.v1",
  "latest_version": "$APP_VERSION",
  "edition": "${APP_EDITION:-multi-editor}",
  "release_date": "$(date '+%Y-%m-%d')",
  "notes": "Describe what changed in this release.",
  "packages": {
SCRIPT
package_separator=""
if [ "$BUILD_MAC_PACKAGE" != "0" ]; then
  if [ -n "$package_separator" ]; then
    printf ',\n'
  fi
  cat <<SCRIPT
    "mac": {
      "label": "Mac",
      "filename": "$MAC_PACKAGE",
      "download_url": "$MAC_PACKAGE",
      "file_id": "",
      "mime_type": "application/zip"
    }
SCRIPT
  package_separator=","
fi
if [ "$BUILD_WINDOWS_PACKAGE" != "0" ]; then
  if [ -n "$package_separator" ]; then
    printf ',\n'
  fi
  cat <<SCRIPT
    "windows": {
      "label": "Windows",
      "filename": "$WINDOWS_PACKAGE",
      "download_url": "$WINDOWS_PACKAGE",
      "file_id": "",
      "mime_type": "application/zip"
    }
SCRIPT
fi
cat <<SCRIPT
  }
}
SCRIPT
} > "$BUILD_DIR/app_update_manifest.example.json"

echo "Portable app created at:"
echo "$APP_DIR"
echo "Update packages created at:"
if [ "$BUILD_MAC_PACKAGE" != "0" ]; then
  echo "$BUILD_DIR/$MAC_PACKAGE"
fi
if [ "$BUILD_WINDOWS_PACKAGE" != "0" ]; then
  echo "$BUILD_DIR/$WINDOWS_PACKAGE"
fi
echo "Manifest template created at:"
echo "$BUILD_DIR/app_update_manifest.example.json"
if [ -n "$PUBLIC_MANIFEST_URL" ]; then
  echo "Public update manifest URL embedded at:"
  echo "$APP_DIR/UPDATE_MANIFEST_URL"
fi
