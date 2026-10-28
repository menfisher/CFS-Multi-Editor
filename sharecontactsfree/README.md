# Share Google Contacts (standalone web app)

Host **one** public site for contact-group sharing. Works for many owners (different Google accounts) and their recipients.

**ContactsFreeShare (CFS)** is the separate desktop app for editing contacts and Google/Drive sync. See `../docs/SHARE_APP.md` in the CFS repo.

---

## Who uses what

| Role | Access |
|------|--------|
| **Owner** | Browser → share web app `/` — sign in with Google, share groups, sync, invite |
| **Recipient** | Invite link → `/?mode=shared` — sign in with Google, Connect account |
| **CFS user** | Desktop app for editing; nav link **Share Contacts** opens this web app |

Recipients never install CFS. Owners do not deploy Cloud Run per person — **one operator hosts once**.

---

## Local development

```bash
cd python
cp .env.example .env
# GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — same OAuth client as CFS
chmod +x run.sh && ./run.sh
```

- Owner: http://127.0.0.1:8080/
- Recipient: http://127.0.0.1:8080/?mode=shared

Add redirect URIs in Google Cloud Console:

```text
http://127.0.0.1:8080/auth/owner/callback
http://127.0.0.1:8080/auth/recipient/callback
```

---

## Deploy to Cloud Run (once per organization)

```bash
cd sharecontactsfree
bash scripts/deploy_cloud_run.sh
```

Then set **CFS → App Settings → Share web app URL** to the Cloud Run URL for every desktop user.

---

## Layout

| Path | Role |
|------|------|
| `python/` | FastAPI backend + SQLite |
| `frontend/public/` | Vue owner/recipient UI |
| `scripts/deploy_cloud_run.sh` | Cloud Run deploy |
| `docs/ARCHITECTURE.md` | Push/sync model |
| `legacy/` | Original Apps Script export |

---

## Status

Owner sync, recipient import, push worker, and invite links (`PUBLIC_WEB_URL`) are implemented in `python/`.

**Merge into CFS desktop was reverted** — sharing stays in this web app only. See `docs/MERGE_INTO_CFS.md` (historical).
