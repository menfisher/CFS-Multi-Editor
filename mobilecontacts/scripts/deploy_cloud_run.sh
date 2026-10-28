#!/usr/bin/env bash
# Deploy Contacts Mobile to Google Cloud Run (parallel to share-contacts; separate service + bucket).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFS_ROOT="$(cd "$ROOT/.." && pwd)"
CFS_ENV="$CFS_ROOT/.env"
SERVICE_NAME="${MOBILE_WEB_SERVICE:-mobile-contacts}"
REGION="${MOBILE_WEB_REGION:-us-central1}"
PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
MIN_INSTANCES="${MOBILE_WEB_MIN_INSTANCES:-0}"
MAX_INSTANCES="${MOBILE_WEB_MAX_INSTANCES:-1}"
CUSTOM_DOMAIN="${MOBILE_CUSTOM_DOMAIN:-mobile.contactsfreeshare.org}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Install Google Cloud SDK (gcloud) first." >&2
  exit 1
fi

if [[ -z "$PROJECT" ]]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT" ]]; then
  echo "Run: gcloud config set project <your-project-id>" >&2
  exit 1
fi

ENV_FILE="${MOBILE_WEB_ENV:-$ROOT/python/.env}"
if [[ ! -f "$ENV_FILE" && -f "$CFS_ENV" ]]; then
  ENV_FILE="$CFS_ENV"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Create $ROOT/python/.env from python/.env.example (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_SECRET_KEY)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-${GOOGLE_OAUTH_CLIENT_ID:-}}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-${GOOGLE_OAUTH_CLIENT_SECRET:-}}"

if [[ -z "${GOOGLE_CLIENT_ID:-}" || -z "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  echo "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET required in $ENV_FILE" >&2
  echo "(CFS .env uses GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET — also accepted.)" >&2
  exit 1
fi

if [[ -z "${APP_SECRET_KEY:-}" || "$APP_SECRET_KEY" == "dev-only-change-me" ]]; then
  echo "APP_SECRET_KEY is required in $ENV_FILE for Cloud Run deploys." >&2
  echo "Generate one with: openssl rand -hex 32" >&2
  echo "Reusing the same value across deploys keeps sign-in sessions valid." >&2
  exit 1
fi

MOUNT_DB_VOLUME="${MOBILE_MOUNT_DB_VOLUME:-1}"
DATA_ROOT="/data"
MOBILE_DB_BUCKET="${MOBILE_DB_BUCKET:-${PROJECT}-mobile-contacts-db}"
STAGING_DOCKERFILE="$CFS_ROOT/Dockerfile"
RESTORE_STAGING_DOCKERFILE=0

cleanup() {
  if [[ "$RESTORE_STAGING_DOCKERFILE" == "1" ]]; then
    rm -f "$STAGING_DOCKERFILE"
  fi
}
trap cleanup EXIT

echo "Using env file: $ENV_FILE"
echo "Build context:  $CFS_ROOT"
echo "Dockerfile:     mobilecontacts/Dockerfile"
echo "Enabling APIs (if needed)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com --project "$PROJECT"

if [[ -f "$STAGING_DOCKERFILE" ]]; then
  echo "Refusing to overwrite existing $STAGING_DOCKERFILE" >&2
  echo "Move it aside temporarily, then rerun this script." >&2
  exit 1
fi
cp "$ROOT/Dockerfile" "$STAGING_DOCKERFILE"
RESTORE_STAGING_DOCKERFILE=1

DEPLOY_ARGS=(
  --source "$CFS_ROOT"
  --region "$REGION"
  --project "$PROJECT"
  --allow-unauthenticated
  --execution-environment gen2
  --min-instances "$MIN_INSTANCES"
  --max-instances "$MAX_INSTANCES"
  --timeout "${MOBILE_WEB_TIMEOUT:-600}"
  --port 8080
  --set-env-vars "GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET},GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_CLIENT_ID},GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET},APP_SECRET_KEY=${APP_SECRET_KEY},DATA_ROOT=${DATA_ROOT},CFS_PROJECT_ROOT=/srv,APP_BASE_URL=${APP_BASE_URL:-https://mobile.contactsfreeshare.org}"
)

if [[ "$MOUNT_DB_VOLUME" == "1" ]]; then
  echo "Ensuring Cloud Storage bucket for mobile SQLite + uploads: gs://${MOBILE_DB_BUCKET}"
  if ! gcloud storage buckets describe "gs://${MOBILE_DB_BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${MOBILE_DB_BUCKET}" \
      --project "$PROJECT" \
      --location "$REGION" \
      --uniform-bucket-level-access
  fi
  DEPLOY_ARGS+=(
    --add-volume "name=mobile-db,type=cloud-storage,bucket=${MOBILE_DB_BUCKET}"
    --add-volume-mount "volume=mobile-db,mount-path=/data"
  )
else
  echo "MOBILE_MOUNT_DB_VOLUME=0 — user databases will be ephemeral inside the container." >&2
fi

echo "Deploying $SERVICE_NAME to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" "${DEPLOY_ARGS[@]}"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT" --format='value(status.url)')"

if [[ -n "${MOBILE_APP_BASE_URL:-}" ]]; then
  APP_PUBLIC_URL="${MOBILE_APP_BASE_URL%/}"
elif [[ -n "$CUSTOM_DOMAIN" ]]; then
  APP_PUBLIC_URL="https://${CUSTOM_DOMAIN}"
else
  APP_PUBLIC_URL="${SERVICE_URL}"
fi

gcloud run services update "$SERVICE_NAME" \
  --region "$REGION" \
  --project "$PROJECT" \
  --update-env-vars "APP_BASE_URL=${APP_PUBLIC_URL}"

echo ""
echo "Deployed: $SERVICE_URL"
echo "Public URL: $APP_PUBLIC_URL"
echo "Mobile UI: ${APP_PUBLIC_URL}/"
if [[ "$MOUNT_DB_VOLUME" == "1" ]]; then
  echo "Data:      ${DATA_ROOT}/users/... on gs://${MOBILE_DB_BUCKET}"
fi
echo ""
echo "Next steps (add — do not replace existing desktop/share/ngrok URIs):"
echo "  1. Google Cloud Console → OAuth client → Authorized redirect URIs:"
echo "       ${APP_PUBLIC_URL}/auth/callback"
echo "  2. Same OAuth client → Authorized JavaScript origins:"
echo "       ${APP_PUBLIC_URL}"
echo "  3. On your phone, bookmark: ${APP_PUBLIC_URL}/"
echo "  4. Optional custom domain: Cloud Run → ${SERVICE_NAME} → Manage custom domains → ${CUSTOM_DOMAIN}"
echo "     APP_BASE_URL is set to ${APP_PUBLIC_URL} when CUSTOM_DOMAIN is configured."
echo "     Override with MOBILE_APP_BASE_URL=https://your-domain if needed."
echo "  5. One-time GCP bill hygiene (lifecycle + registry cleanup): bash ${CFS_ROOT}/scripts/setup_gcp_deploy_hygiene.sh"
