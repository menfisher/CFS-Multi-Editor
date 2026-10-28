# Merging into production ContactsFreeShare (historical)

> **Superseded (2026):** Sharing was un-merged from CFS. Keep this web app hosted separately.
> CFS links to it via **Share Contacts** nav + App Settings URL. See `../../docs/SHARE_APP.md`.

**Do not merge until** recipient import + owner sync work in this standalone repo.

Production path: `/Users/charlesvaughn/Desktop/VScode_Test/CFS code`

---

## What stays separate today

- All sharing code lives in **`~/Projects/sharecontactsfree`**
- Production app keeps: local SQLite editor, Google sync, Drive backup, meetings, field list, etc.
- Recipients always use the **hosted** share web app URL from email (not the desktop installer)

---

## Merge checklist (when ready)

### 1. Database

Add to `CFS code/db/schema.sql` (names can match this repo):

- `share_groups` / recipient `shared_groups` metadata (today: JSON in `app_data` table)
- `shared_contacts` rows (or Drive files only — align with `export_shared_contacts_to_google_drive`)
- `sync_mappings` (owner person ↔ recipient person)

Prefer **one SQLite** database in the desktop app data dir; Cloud Run instance may use the same schema with Postgres or sync via Drive — decide at merge time.

### 2. Google integration

**Reuse** from `app/services/google_sync_service.py`:

- `GOOGLE_SCOPES` (already has `contacts` + `drive.file`)
- Token storage in `google_sync_state`
- Drive folder layout under `ContactsFreeShare/Shared/...`
- HTTP helpers for People API

**Do not** duplicate OAuth clients.

### 3. New module in production

Suggested layout:

```text
app/services/contact_share_service.py   # port from sharecontactsfree/python/app/services/
app/routes/contact_share.py             # owner actions from desktop (optional thin API)
```

Hosted recipient UI can remain a **sub-deployable** from `sharecontactsfree/frontend` + `python/` or a slim Cloud Run service that shares DB/Drive with desktop exports.

### 4. Owner UX in desktop

- New menu: **Share contact group** (or share current Google group)
- Calls share service → sends invite emails with `PUBLIC_WEB_URL/?mode=shared`
- **Sync** button → `sync_shared_group` (after port)

### 5. Recipient UX (unchanged)

- Email link → public URL only
- No change to portable Mac/Windows installer for recipients

### 6. Environment

| Variable | Desktop (CFS) | Cloud (share web) |
|----------|---------------|-------------------|
| `GOOGLE_OAUTH_*` | Same client ID | Same |
| `PUBLIC_WEB_URL` | Used when sending invites | Service URL |
| `DATABASE_PATH` | `~/Library/.../ContactsFreeShare/db/` | Cloud DB or Drive-backed |

### 7. Files to copy/adapt

From this repo → CFS:

- `python/app/services/app_data.py` → share + app state
- `python/app/people_api.py` → or merge into `google_sync_service`
- `python/app/db.py` share tables → `schema.sql`
- `frontend/public/*` + `api-bridge.js` → only if embedding web UI in Cloud Run bundle

### 8. Decommission

- Retire Google Apps Script deployment
- Archive `legacy/ContactsFreeShare.json`
- Remove duplicate `~/Projects/sharecontactsfree` after code lives in CFS + one Cloud Run deploy

---

## Order of work (recommended)

1. Finish **Phase 2** in this repo (import + sync).  
2. Deploy Cloud Run with `PUBLIC_WEB_URL`; test full recipient path via email.  
3. Wire **Share** from desktop to call share API (or shared Drive export).  
4. Delete standalone repo or keep as `share-web/` git submodule.
