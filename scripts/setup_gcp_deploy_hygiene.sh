#!/usr/bin/env bash
# Apply set-and-forget GCP cleanup for Cloud Run source deploys:
#   - Cloud Storage lifecycle on run-sources bucket (delete objects after 7 days)
#   - Artifact Registry cleanup policy (keep 5 recent images, drop old untagged)
#
# Run once per GCP project (safe to re-run; updates the same policies):
#   bash scripts/setup_gcp_deploy_hygiene.sh
#
# Optional one-time prune of existing leftover source zips (keeps newest per service):
#   bash scripts/setup_gcp_deploy_hygiene.sh --prune-now
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HYGIENE_DIR="$ROOT_DIR/scripts/gcp/deploy_hygiene"
LIFECYCLE_FILE="$HYGIENE_DIR/run-sources-lifecycle.json"
ARTIFACT_POLICY_FILE="$HYGIENE_DIR/artifact-registry-cleanup-policy.json"

PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GCP_DEPLOY_REGION:-us-central1}"
ARTIFACT_REPO="${GCP_ARTIFACT_REPO:-cloud-run-source-deploy}"
PRUNE_NOW=0

usage() {
  cat <<'TEXT'
Usage:
  bash scripts/setup_gcp_deploy_hygiene.sh [--prune-now]

Environment (optional):
  GOOGLE_CLOUD_PROJECT   GCP project id (defaults to gcloud config)
  GCP_DEPLOY_REGION      Cloud Run region (default: us-central1)
  GCP_ARTIFACT_REPO      Artifact Registry repo (default: cloud-run-source-deploy)

What this configures:
  1. gs://run-sources-PROJECT-REGION — delete deploy source zips after 7 days
  2. Artifact Registry cleanup — keep 5 newest images; delete untagged after 7 days

Desktop GitHub zip updates are not affected (they do not use GCP).
TEXT
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --prune-now)
      PRUNE_NOW=1
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Install Google Cloud SDK (gcloud) first." >&2
  exit 1
fi

if [[ -z "$PROJECT" ]]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT" ]]; then
  echo "Set a project: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

RUN_SOURCES_BUCKET="run-sources-${PROJECT}-${REGION}"

echo "Project:              $PROJECT"
echo "Region:               $REGION"
echo "Run sources bucket:   gs://${RUN_SOURCES_BUCKET}"
echo "Artifact Registry:    ${REGION}/${ARTIFACT_REPO}"
echo ""

gcloud services enable storage.googleapis.com artifactregistry.googleapis.com --project "$PROJECT" >/dev/null

if gcloud storage buckets describe "gs://${RUN_SOURCES_BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
  echo "Applying 7-day lifecycle on gs://${RUN_SOURCES_BUCKET} ..."
  gcloud storage buckets update "gs://${RUN_SOURCES_BUCKET}" \
    --project "$PROJECT" \
    --lifecycle-file="$LIFECYCLE_FILE"
else
  echo "Bucket gs://${RUN_SOURCES_BUCKET} was not found yet."
  echo "It is created on the first 'gcloud run deploy --source' in ${REGION}."
  echo "Re-run this script after your first Cloud Run deploy, or create the bucket manually."
fi

if gcloud artifacts repositories describe "$ARTIFACT_REPO" \
  --location="$REGION" \
  --project="$PROJECT" >/dev/null 2>&1; then
  echo "Applying Artifact Registry cleanup policy on ${ARTIFACT_REPO} ..."
  gcloud artifacts repositories set-cleanup-policies "$ARTIFACT_REPO" \
    --location="$REGION" \
    --project="$PROJECT" \
    --policy="$ARTIFACT_POLICY_FILE" \
    --no-dry-run
else
  echo "Artifact Registry repo ${ARTIFACT_REPO} was not found in ${REGION}."
  echo "It is created on the first Cloud Run source deploy. Re-run this script afterward."
fi

if [[ "$PRUNE_NOW" == "1" ]]; then
  if ! gcloud storage buckets describe "gs://${RUN_SOURCES_BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
    echo "Skipping --prune-now: run-sources bucket does not exist yet." >&2
  else
    echo ""
    echo "Pruning old Cloud Run source zips (keeping newest per service) ..."
    python3 - "$RUN_SOURCES_BUCKET" <<'PY'
import subprocess
import sys

bucket = sys.argv[1]
services = ("mobile-contacts", "share-contacts")
for service in services:
    prefix = f"gs://{bucket}/services/{service}/"
    list_cmd = ["gcloud", "storage", "ls", prefix]
    try:
        result = subprocess.run(list_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        print(f"  {service}: no objects (skipped)")
        continue
    objects = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(objects) <= 1:
        print(f"  {service}: {len(objects)} object(s), nothing to prune")
        continue
    to_delete = objects[:-1]
    for obj in to_delete:
        subprocess.run(["gcloud", "storage", "rm", obj], check=True)
    print(f"  {service}: deleted {len(to_delete)} old zip(s), kept {objects[-1]}")
PY
    echo "Artifact Registry old images will be trimmed by the cleanup policy (may take up to 24 hours)."
  fi
fi

echo ""
echo "GCP deploy hygiene is configured."
echo "Re-run anytime after changing regions or projects."
echo "See docs/GCP_DEPLOY_HYGIENE.md for details."
