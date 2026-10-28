#!/usr/bin/env bash
echo "Share app deploy moved to sharecontactsfree/scripts/deploy_cloud_run.sh"
exec "$(cd "$(dirname "$0")/.." && pwd)/sharecontactsfree/scripts/deploy_cloud_run.sh" "$@"
