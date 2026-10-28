# Migrating off Google Apps Script

> **Architecture:** Recipients use a **browser link** from email. Merge into the desktop app only per **[docs/MERGE_INTO_CFS.md](docs/MERGE_INTO_CFS.md)** after Phase 2 works here.

## Why Python (and not stay on Apps Script)

| Topic | Google Apps Script | This Python app |
|--------|-------------------|-----------------|
| Runs on Mac/Windows | No (cloud only) | Yes — local or any server |
| Execution time | 6 min max | No GAS limit |
| Storage | 500 KB script properties | SQLite (or Postgres later) |
| Debugging | Limited | Full IDE, tests, logs |
| Your GCP testing | Apps Script + Cloud | **Cloud Run**, Cloud Run + Docker, or a VM |

**Python** is a good fit: same Google APIs (`google-api-python-client`), runs everywhere, and deploys cleanly to **Google Cloud Run** (which you already used).

Node.js would also work (your code is already JavaScript), but Python keeps the backend clear and separate from the Vue UI.

## What is already ported

- Google sign-in (owner)
- List contact groups (People API)
- Share group with emails + store contacts in SQLite
- Recipient “connect account” OAuth
- Same Vue UI (via `api-bridge.js` → `/api/rpc`)
- vCard-free — still uses Google Contacts API (Google account required on both sides)

## Phase 2 (not done yet — largest piece)

These are the big functions in `gsWebApp.gs` (~3,700 lines) still to port:

1. **`createSharedGroups`** — import shared contacts into recipient Google account (batched)
2. **`syncSharedGroup`** — push owner updates to all recipients (hashes, etags, mappings)
3. **Sync mappings sheet** logic — now table `sync_mappings` in SQLite
4. **Skipped contacts** reporting
5. **Scheduled cleanup** (was `gsApp.gs` triggers) — use cron or Cloud Scheduler

**Recipient import** is ported (`create_shared_groups`). **Owner sync** is still pending.

## Google Cloud setup (replace Apps Script deployment)

1. [Google Cloud Console](https://console.cloud.google.com/) — same project you used for testing.
2. **APIs enabled:** People API, Gmail API (optional invites).
3. **OAuth consent screen** — add test users if still in Testing.
4. **Credentials → OAuth client ID → Web application**
   - Authorized redirect URIs:
     - `http://127.0.0.1:8080/auth/owner/callback`
     - `http://127.0.0.1:8080/auth/recipient/callback`
     - (add production URL when you deploy Cloud Run)
5. Copy Client ID and Secret into `python/.env`.

## Run locally (Mac or Windows)

```bash
cd ~/Projects/sharecontactsfree/python
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with client id/secret and APP_SECRET_KEY
uvicorn app.main:app --reload --port 8080
```

Open http://127.0.0.1:8080 — sign in with Google.

## Deploy to Cloud Run (outline)

```bash
# From repo root, after adding a Dockerfile (see python/Dockerfile)
gcloud run deploy share-contacts --source ./python --region us-central1 --allow-unauthenticated
```

Set env vars in Cloud Run: `GOOGLE_CLIENT_*`, `APP_SECRET_KEY`, `APP_BASE_URL=https://your-service.run.app`.

## Folder layout

```
sharecontactsfree/
  gas-legacy/          # original .gs / .html (reference) — see root *.gs
  frontend/public/     # UI for Python app
  python/              # FastAPI backend
  MIGRATION.md         # this file
```

The old `.gs` files at repo root remain as reference until Phase 2 is complete.
