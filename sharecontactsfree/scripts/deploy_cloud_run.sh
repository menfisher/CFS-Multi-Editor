#!/usr/bin/env bash
# Deploy Share Google Contacts to Google Cloud Run (one host for all owners + recipients).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFS_ENV="$ROOT/../.env"
SERVICE_NAME="${SHARE_WEB_SERVICE:-share-contacts}"
REGION="${SHARE_WEB_REGION:-us-central1}"
PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
# Local SQLite (synced to GCS) requires a SINGLE instance — multiple instances each
# keep their own local DB copy, which splits import progress and risks duplicate
# pushes. So MAX_INSTANCES must stay at 1.
#
# MIN_INSTANCES=0 scales the service to zero when idle (much lower cost). The
# background push worker is resilient to this: on cold start it reclaims running
# jobs and re-enqueues orphaned imports (_resume_orphaned_imports), and a per-job
# watchdog prevents wedging. Tradeoff: an import left running with the page closed
# pauses until the instance next wakes (any page load), then auto-resumes; and
# owner->recipient updates apply the next time the recipient opens the app. While
# the recipient page is open it polls ~4x/sec, which keeps the instance warm so
# imports run to completion normally. Set SHARE_WEB_MIN_INSTANCES=1 to keep it
# always-on for instant background propagation (higher cost).
MIN_INSTANCES="${SHARE_WEB_MIN_INSTANCES:-0}"
MAX_INSTANCES="${SHARE_WEB_MAX_INSTANCES:-1}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Install Google Cloud SDK (gcloud) first." >&2
  exit 1
fi

if [[ -z "$PROJECT" ]]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT" ]]; then
  echo "Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

ENV_FILE="${SHARE_WEB_ENV:-$ROOT/python/.env}"
if [[ ! -f "$ENV_FILE" && -f "$CFS_ENV" ]]; then
  ENV_FILE="$CFS_ENV"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Create $ROOT/python/.env from python/.env.example (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)." >&2
  exit 1
fi

# Load .env (process-substitution source fails on macOS bash — use set -a instead).
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Accept CFS variable names too.
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-${GOOGLE_OAUTH_CLIENT_ID:-}}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-${GOOGLE_OAUTH_CLIENT_SECRET:-}}"

if [[ -z "${GOOGLE_CLIENT_ID:-}" || -z "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  echo "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET required in $ENV_FILE" >&2
  echo "(CFS .env uses GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET — also accepted.)" >&2
  exit 1
fi

if [[ -z "${APP_SECRET_KEY:-}" ]]; then
  echo "APP_SECRET_KEY is required in $ENV_FILE for Cloud Run deploys." >&2
  echo "Generate one with: openssl rand -hex 32" >&2
  echo "Reusing the same value across deploys keeps recipient sessions valid." >&2
  exit 1
fi

PUBLIC_WEB_URL="${PUBLIC_WEB_URL:-}"
SHARE_DB_BUCKET="${SHARE_DB_BUCKET:-${PROJECT}-share-contacts-db}"
MOUNT_DB_VOLUME="${SHARE_MOUNT_DB_VOLUME:-0}"
if [[ "$MOUNT_DB_VOLUME" == "1" ]]; then
  DATABASE_PATH="/data/sharecontacts.db"
  SHARE_DB_GCS_URI=""
else
  DATABASE_PATH="/tmp/sharecontacts.db"
  SHARE_DB_GCS_URI="${SHARE_DB_GCS_URI:-gs://${SHARE_DB_BUCKET}/sharecontacts.db}"
fi

echo "Using env file: $ENV_FILE"
echo "Enabling APIs (if needed)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com --project "$PROJECT"

DEPLOY_ARGS=(
  --source "$ROOT"
  --region "$REGION"
  --project "$PROJECT"
  --allow-unauthenticated
  --execution-environment gen2
  --min-instances "$MIN_INSTANCES"
  --max-instances "$MAX_INSTANCES"
  # Request-based billing: CPU (and billing) only while a request is active, so the
  # instance can scale to zero between requests. Cloud Scheduler share-contacts-kick
  # runs every 12 minutes as a backup queue drain; primary pushes come from CFS
  # Upload /api/cfs/sync-contact-changes and from opening Share.
  --cpu-throttling
  --set-env-vars "GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET},APP_SECRET_KEY=${APP_SECRET_KEY},CFS_API_KEY=${CFS_API_KEY:-$APP_SECRET_KEY},SEND_SHARE_EMAILS=false,PUBLIC_WEB_URL=${PUBLIC_WEB_URL},DATABASE_PATH=${DATABASE_PATH},SHARE_DB_GCS_URI=${SHARE_DB_GCS_URI},SHARE_DB_LOCAL_PATH=${DATABASE_PATH},RECIPIENT_IMPORT_PHOTO_BATCH_SIZE=${RECIPIENT_IMPORT_PHOTO_BATCH_SIZE:-20},RECIPIENT_IMPORT_PHOTO_HTTP_BATCH_SIZE=${RECIPIENT_IMPORT_PHOTO_HTTP_BATCH_SIZE:-20},RECIPIENT_IMPORT_PHOTO_FETCH_WORKERS=${RECIPIENT_IMPORT_PHOTO_FETCH_WORKERS:-16},RECIPIENT_IMPORT_TIME_BUDGET_MS=${RECIPIENT_IMPORT_TIME_BUDGET_MS:-180000},SHARE_KICK_DRAIN_SECONDS=${SHARE_KICK_DRAIN_SECONDS:-200},SHARE_JOB_HARD_TIMEOUT=${SHARE_JOB_HARD_TIMEOUT:-200}${SHARE_JOB_PRIORITY_EMAIL:+,SHARE_JOB_PRIORITY_EMAIL=${SHARE_JOB_PRIORITY_EMAIL}}"
)

if [[ "$MOUNT_DB_VOLUME" == "1" ]]; then
  echo "Ensuring Cloud Storage bucket for SQLite: gs://${SHARE_DB_BUCKET}"
  if ! gcloud storage buckets describe "gs://${SHARE_DB_BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${SHARE_DB_BUCKET}" \
      --project "$PROJECT" \
      --location "$REGION" \
      --uniform-bucket-level-access
  fi
  DEPLOY_ARGS+=(
    --add-volume "name=share-db,type=cloud-storage,bucket=${SHARE_DB_BUCKET}"
    --add-volume-mount "volume=share-db,mount-path=/data"
  )
else
  echo "Using local SQLite at ${DATABASE_PATH} with GCS backup ${SHARE_DB_GCS_URI}"
  if ! gcloud storage buckets describe "gs://${SHARE_DB_BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${SHARE_DB_BUCKET}" \
      --project "$PROJECT" \
      --location "$REGION" \
      --uniform-bucket-level-access
  fi
fi

echo "Deploying $SERVICE_NAME to Cloud Run..."
if [[ "$MOUNT_DB_VOLUME" == "1" ]]; then
  gcloud run deploy "$SERVICE_NAME" "${DEPLOY_ARGS[@]}"
else
  gcloud run deploy "$SERVICE_NAME" "${DEPLOY_ARGS[@]}" --clear-volumes --clear-volume-mounts
fi

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT" --format='value(status.url)')"

gcloud run services update "$SERVICE_NAME" \
  --region "$REGION" \
  --project "$PROJECT" \
  --update-env-vars "APP_BASE_URL=${SERVICE_URL},PUBLIC_WEB_URL=${SERVICE_URL}"

gcloud run services update-traffic "$SERVICE_NAME" \
  --region "$REGION" \
  --project "$PROJECT" \
  --to-latest

echo ""
echo "Deployed: $SERVICE_URL"
echo "Owner UI:   ${SERVICE_URL}/"
echo "Recipient:  ${SERVICE_URL}/?mode=shared"
if [[ "$MOUNT_DB_VOLUME" == "1" ]]; then
  echo "Database:   ${DATABASE_PATH} on gs://${SHARE_DB_BUCKET}"
fi
echo ""
echo "Next steps:"
echo "  1. Google Cloud Console → OAuth client → Authorized redirect URIs:"
echo "       ${SERVICE_URL}/auth/owner/callback"
echo "       ${SERVICE_URL}/auth/recipient/callback"
echo "  2. In CFS App Settings, set Share web app URL to: $SERVICE_URL"
echo "  3. Optional: map share.contactsfreeshare.org to this Cloud Run service"
echo "  4. One-time GCP bill hygiene (lifecycle + registry cleanup): bash $(cd "$ROOT/.." && pwd)/scripts/setup_gcp_deploy_hygiene.sh"
