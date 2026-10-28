#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/dist"
UPDATE_REPO="menfisher/contactsfreeshare-updates"
UPDATE_BASE_URL="https://menfisher.github.io/contactsfreeshare-updates"
if [[ "${CONTACTSFREESHARE_UPDATE_REPO:-}" == "menfisher/contactsfreeshare-updates" ]]; then
  UPDATE_REPO="$CONTACTSFREESHARE_UPDATE_REPO"
fi
if [[ "${CONTACTSFREESHARE_UPDATE_BASE_URL:-}" == *"contactsfreeshare-updates"* && "${CONTACTSFREESHARE_UPDATE_BASE_URL:-}" != *"contactsfreeshare-se-updates"* ]]; then
  UPDATE_BASE_URL="$CONTACTSFREESHARE_UPDATE_BASE_URL"
fi
UPDATE_REPO_URL="${CONTACTSFREESHARE_UPDATE_REPO_URL:-git@github.com:${UPDATE_REPO}.git}"
if [[ "${CONTACTSFREESHARE_UPDATE_MANIFEST_URL:-}" == *"contactsfreeshare-updates"* && "${CONTACTSFREESHARE_UPDATE_MANIFEST_URL:-}" != *"contactsfreeshare-se-updates"* ]]; then
  UPDATE_MANIFEST_URL="$CONTACTSFREESHARE_UPDATE_MANIFEST_URL"
else
  UPDATE_MANIFEST_URL="$UPDATE_BASE_URL/app_update_manifest.json"
fi
KEEP_OLD_PACKAGES="${CONTACTSFREESHARE_KEEP_OLD_UPDATE_PACKAGES:-0}"
ALLOW_EXISTING_VERSION="${CONTACTSFREESHARE_ALLOW_EXISTING_VERSION:-0}"
VERSION="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi
if [[ $# -gt 0 ]]; then
  RELEASE_NOTES="$*"
else
  RELEASE_NOTES="${CONTACTSFREESHARE_RELEASE_NOTES:-ContactsFreeShare app update.}"
fi
RELEASE_NOTES="${RELEASE_NOTES#“}"
RELEASE_NOTES="${RELEASE_NOTES%”}"

usage() {
  cat <<'TEXT'
Usage:
  scripts/publish_app_update.sh VERSION "Release notes"

Example:
  scripts/publish_app_update.sh 1.1 "Bug fixes and app update publishing improvements."

Optional environment variables:
  CONTACTSFREESHARE_UPDATE_REPO=menfisher/contactsfreeshare-updates
  CONTACTSFREESHARE_UPDATE_REPO_URL=git@github.com:menfisher/contactsfreeshare-updates.git
  CONTACTSFREESHARE_UPDATE_BASE_URL=https://menfisher.github.io/contactsfreeshare-updates
  CONTACTSFREESHARE_UPDATE_MANIFEST_URL=https://menfisher.github.io/contactsfreeshare-updates/app_update_manifest.json
  CONTACTSFREESHARE_KEEP_OLD_UPDATE_PACKAGES=1
  CONTACTSFREESHARE_ALLOW_EXISTING_VERSION=1
TEXT
}

if [[ "${VERSION:-}" == "-h" || "${VERSION:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$VERSION" ]]; then
  echo "Error: version is required."
  usage
  exit 1
fi

command -v git >/dev/null 2>&1 || {
  echo "Error: git is required."
  exit 1
}

REMOTE_MANIFEST="$(mktemp)"
if curl -fsSL "$UPDATE_MANIFEST_URL" -o "$REMOTE_MANIFEST" >/dev/null 2>&1; then
  PUBLISHED_VERSION="$(python3 - "$REMOTE_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

try:
    print(str(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("latest_version") or ""))
except Exception:
    print("")
PY
)"
  if [[ "$PUBLISHED_VERSION" == "$VERSION" && "$ALLOW_EXISTING_VERSION" != "1" ]]; then
    echo "Error: version $VERSION is already published at $UPDATE_MANIFEST_URL."
    echo "Use a new version number so browsers and GitHub Pages do not serve a cached zip."
    echo "To intentionally replace the same version, set CONTACTSFREESHARE_ALLOW_EXISTING_VERSION=1."
    rm -f "$REMOTE_MANIFEST"
    exit 1
  fi
fi
rm -f "$REMOTE_MANIFEST"

printf '%s\n' "$VERSION" > "$ROOT_DIR/APP_VERSION"

CONTACTSFREESHARE_REVISION_VERSION="$VERSION" \
CONTACTSFREESHARE_REVISION_NOTES="$RELEASE_NOTES" \
python3 - <<'PY'
import os
from datetime import datetime

from app.services.app_revision_service import record_app_revision

today = datetime.now()
record_app_revision(
    version=os.environ["CONTACTSFREESHARE_REVISION_VERSION"],
    description=os.environ["CONTACTSFREESHARE_REVISION_NOTES"],
    date_display=f"{today.month}/{today.day}/{today.year}",
    export_to_drive=False,
)
PY

CONTACTSFREESHARE_UPDATE_MANIFEST_URL="$UPDATE_MANIFEST_URL" bash "$ROOT_DIR/scripts/build_portable_app.sh"

MANIFEST_TEMPLATE="$BUILD_DIR/app_update_manifest.example.json"
MANIFEST_FILE="$BUILD_DIR/app_update_manifest.json"
APP_EDITION="$(tr -d '[:space:]' < "$ROOT_DIR/APP_EDITION" 2>/dev/null || true)"
if [[ "$APP_EDITION" == "single-editor" ]]; then
  PACKAGE_PREFIX="ContactsFreeShare-SingleEditor"
elif [[ "$APP_EDITION" == "multi-editor" ]]; then
  PACKAGE_PREFIX="ContactsFreeShare-MultiEditor"
else
  PACKAGE_PREFIX="ContactsFreeShare"
fi
MAC_PACKAGE="$BUILD_DIR/${PACKAGE_PREFIX}-${VERSION}-mac.zip"
WINDOWS_PACKAGE="$BUILD_DIR/${PACKAGE_PREFIX}-${VERSION}-windows.zip"

if [[ ! -f "$MANIFEST_TEMPLATE" || ! -f "$MAC_PACKAGE" || ! -f "$WINDOWS_PACKAGE" ]]; then
  echo "Error: expected build output was not found in $BUILD_DIR."
  exit 1
fi

python3 - "$MANIFEST_TEMPLATE" "$MANIFEST_FILE" "$RELEASE_NOTES" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
notes = sys.argv[3]

payload = json.loads(source.read_text(encoding="utf-8"))
payload["notes"] = notes
target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

WORK_DIR="$(mktemp -d)"
UPDATE_REPO_DIR="$WORK_DIR/update-repo"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

README_BACKUP="$(mktemp)"
PREV_CLONE="$WORK_DIR/update-repo.prev"
if git clone --depth 1 --quiet "$UPDATE_REPO_URL" "$PREV_CLONE" 2>/dev/null; then
  if [[ -f "$PREV_CLONE/README.md" ]]; then
    cp "$PREV_CLONE/README.md" "$README_BACKUP"
  fi
fi

mkdir -p "$UPDATE_REPO_DIR"
if [[ -s "$README_BACKUP" ]]; then
  cp "$README_BACKUP" "$UPDATE_REPO_DIR/README.md"
fi
rm -f "$README_BACKUP"

if [[ "$KEEP_OLD_PACKAGES" == "1" && -d "$PREV_CLONE" ]]; then
  find "$PREV_CLONE" -maxdepth 1 -type f \( -name 'ContactsFreeShare-*-mac.zip' -o -name 'ContactsFreeShare-*-windows.zip' \) -exec cp {} "$UPDATE_REPO_DIR/" \;
fi
rm -rf "$PREV_CLONE"

cp "$MANIFEST_FILE" "$UPDATE_REPO_DIR/app_update_manifest.json"
cp "$MAC_PACKAGE" "$UPDATE_REPO_DIR/"
cp "$WINDOWS_PACKAGE" "$UPDATE_REPO_DIR/"

# Host Help screenshots on Pages so app zips stay under GitHub size limits.
mkdir -p "$UPDATE_REPO_DIR/help/manual-images" "$UPDATE_REPO_DIR/help/share-images"
if [[ -d "$ROOT_DIR/manual-images" ]]; then
  rsync -a --exclude '.DS_Store' "$ROOT_DIR/manual-images/" "$UPDATE_REPO_DIR/help/manual-images/"
fi
if [[ -d "$ROOT_DIR/share-images" ]]; then
  rsync -a --exclude '.DS_Store' "$ROOT_DIR/share-images/" "$UPDATE_REPO_DIR/help/share-images/"
fi

# Publish as a single-commit repo so old zip blobs are not kept in git history.
git -C "$UPDATE_REPO_DIR" init --quiet
git -C "$UPDATE_REPO_DIR" checkout -B main
git -C "$UPDATE_REPO_DIR" add --all

if git -C "$UPDATE_REPO_DIR" diff --cached --quiet; then
  echo "No update file changes to publish."
  exit 0
fi

git -C "$UPDATE_REPO_DIR" commit --quiet -m "Publish ContactsFreeShare ${VERSION} update"
git -C "$UPDATE_REPO_DIR" remote add origin "$UPDATE_REPO_URL"
git -C "$UPDATE_REPO_DIR" push --force --quiet origin main

echo "Published ContactsFreeShare $VERSION update (single-commit repo):"
echo "$UPDATE_MANIFEST_URL"
