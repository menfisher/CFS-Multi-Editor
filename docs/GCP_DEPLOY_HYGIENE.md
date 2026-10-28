# GCP deploy hygiene (set and forget)

Cloud Run deploys (`gcloud run deploy --source`) upload **source zip files** to Cloud Storage and **Docker images** to Artifact Registry. Without cleanup, old deploy artifacts accumulate and show up on your GCP bill under **Cloud Storage** and **Artifact Registry**.

**Desktop app zip updates** (`scripts/publish_app_update.sh` → GitHub Pages) do **not** use GCP and do not need this setup.

## One-time setup (per GCP project)

```bash
gcloud config set project YOUR_PROJECT_ID
bash scripts/setup_gcp_deploy_hygiene.sh --prune-now
```

- **`--prune-now`** — optional; deletes old source zips immediately (keeps the newest zip per service). Safe to omit if the bucket is empty.
- Re-running the script is safe; it updates the same policies.

## What gets configured

| Target | Policy |
|--------|--------|
| `gs://run-sources-PROJECT-REGION` | Delete objects **older than 7 days** |
| Artifact Registry `cloud-run-source-deploy` | **Keep 5** most recent image versions; delete **untagged** images older than 7 days |

## Smaller future deploy zips

`.gcloudignore` excludes large dev folders (`vendor/wheels/`, `dist/`, `.pip_packages/`, `tests/`, `node_modules/`, etc.) from the upload sent to Cloud Build. Each deploy should add a much smaller zip than before.

## When to run again

| Event | Action |
|-------|--------|
| First Cloud Run deploy in a **new** GCP project | Run `setup_gcp_deploy_hygiene.sh` once |
| Change deploy **region** | Re-run with `GCP_DEPLOY_REGION=your-region` |
| Desktop zip publish to GitHub | **Nothing** |
| Routine mobile/share redeploy | **Nothing** — lifecycle and registry policies handle leftovers |

## Environment variables

```bash
GOOGLE_CLOUD_PROJECT=almslacontactsfreeshare   # optional if gcloud config is set
GCP_DEPLOY_REGION=us-central1                  # default
GCP_ARTIFACT_REPO=cloud-run-source-deploy      # default
```

## Related scripts

- Mobile deploy: `mobilecontacts/scripts/deploy_cloud_run.sh`
- Share deploy: `sharecontactsfree/scripts/deploy_cloud_run.sh`
