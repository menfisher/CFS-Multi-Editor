# Architecture — contact sharing (standalone module)

This repo is **separate** from your production ContactsFreeShare desktop app until you deliberately merge it.

**Production app (do not edit for sharing yet):**

`/Users/charlesvaughn/Desktop/VScode_Test/CFS code`

---

## Who uses what

| Role | How they access | Where it runs |
|------|-----------------|---------------|
| **Owner** (you) | Today: this repo’s web UI at `/` | Local dev or Cloud Run while building |
| **Owner** (future) | ContactsFreeShare **desktop** app | Mac / Windows — calls same share APIs or shared DB |
| **Recipient** | **Link in invite email** → browser | **Always** the public web URL (`?mode=shared`) |

Recipients never install the desktop app. They only need a browser and a Google account.

---

## Server-side push model (`testing_code`)

Owner **Sync** stages contact payloads in SQLite and **enqueues background push jobs** for every connected recipient. The in-process worker (`push_worker.py`) drains `share_push_jobs` and writes to recipient Google accounts using stored OAuth tokens — the recipient browser is **not** required for import.

```text
Owner Sync (syncSharedGroup)
  → write shared_contacts + sync_mappings prep
  → enqueue share_push_jobs (full_group) per connected recipient
  → return immediately to owner UI

Background worker
  → process_shared_import_for_group (People API create/update)
  → persist sync_mappings (owner people/xxx ↔ recipient people/yyy + hashes)

Incremental edits (future CFS hook)
  → POST /api/app-call { method: syncContactChanges, args: [owner_email, ["people/c123", ...]] }
  → contact_sync.py finds all mappings for each owner person (cross-group)
  → update/create/skip per recipient; sibling mapping propagation
```

Recipient page shows **Pushing** progress and **Retry push** (re-enqueues job). Browser import remains a fallback for edge cases.

---

## Invite email flow

```text
Owner shares a group
    → Backend stores shared contact data (SQLite; later Drive in CFS)
    → Gmail sends HTML email to each recipient
    → Link: https://YOUR-PUBLIC-HOST/?mode=shared

Recipient opens link
    → Signs in with Google (recipient OAuth)
    → “Connect account” if needed (one-time consent)
    → Server pushes shared groups in the background
    → Optional: Retry push from recipient UI
```

Set **`PUBLIC_WEB_URL`** in `.env` to the URL recipients use (Cloud Run in production). Invite links always use that value, not `localhost`.

---

## Future CFS hook (not implemented in CFS yet)

After `upload_pending_changes()` succeeds in the desktop app, call:

```http
POST /api/app-call
{
  "method": "syncContactChanges",
  "args": ["owner@gmail.com", ["people/c1234567890", "people/c9876543210"]]
}
```

CFS contacts already store `google_contact_id` — that value maps directly to `owner_person_id` in `sync_mappings`.

Owner **Sync** (`syncSharedGroup`) remains the manual full-group fallback.

---

## OAuth (one Google Cloud client)

Use the **same** verified OAuth client as production ContactsFreeShare:

- `contacts` — read/write contact groups and people  
- `drive.file` — when merged, shared payloads on Drive (production pattern)  
- Optional: `gmail.send` — invite emails  

Redirect URIs must include your **public** host:

- `https://your-service.run.app/auth/owner/callback`
- `https://your-service.run.app/auth/recipient/callback`

(Plus `http://127.0.0.1:8080/...` for local dev.)

---

## Deployment shape

```text
                    ┌─────────────────────────────┐
                    │  Cloud Run (or similar)      │
                    │  share-web (this python/)    │
                    │  - Vue UI                    │
                    │  - /?mode=shared recipients  │
                    │  - / owner UI (dev only)     │
                    │  - push worker (daemon)      │
                    └──────────────┬──────────────┘
                                   │
         invite email link         │  future: syncContactChanges from desktop
                    ┌──────────────┴──────────────┐
                    │                             │
              Recipient browser          CFS desktop (later)
```

---

## Code layout (this repo)

| Path | Purpose |
|------|---------|
| `python/` | FastAPI + SQLite — share/import/sync logic |
| `python/app/services/push_worker.py` | Job queue + background full/incremental push |
| `python/app/services/contact_sync.py` | Incremental owner→recipient sync via mappings |
| `python/app/services/contact_hash.py` | GAS-compatible contact/photo hashes |
| `frontend/public/` | Vue UI (from GAS), uses `api-bridge.js` |
| `legacy/` | GAS export + original `.gs` reference |
| `docs/MERGE_INTO_CFS.md` | Checklist when moving into production app |

---

## RPC methods (app-call)

| Method | Purpose |
|--------|---------|
| `syncSharedGroup` | Stage contacts + enqueue full push jobs |
| `getSharePushStatus` | Owner/recipient push job + group status |
| `syncContactChanges` | Incremental push for specific owner `people/xxx` IDs |
| `retrySharePush` | Recipient re-enqueues full group push |
| `processSharedImportForGroup` | Manual browser import fallback |

---

## Phase 2 (still in this repo before merge)

Port from `legacy/` / `gsWebApp.gs`:

1. Recipient **import** (`createSharedGroups`) — now also driven by server push worker
2. Owner **sync** to recipients (`syncSharedGroup`) — enqueues push jobs
3. **Incremental sync** (`syncContactChanges`) — CFS-ready API
4. Skipped contacts, cleanup jobs
5. Optional: store share payloads on **Drive** (match CFS `google_sync_service` folders)

Only after Phase 2 is solid should you merge into `CFS code` as `app/services/contact_share_service.py` (or similar).
