# Share Google Contacts — separate from CFS

ContactsFreeShare (CFS) and the **Share Google Contacts** web app are **two separate programs** again.

| App | Purpose | Runs on |
|-----|---------|---------|
| **CFS** (this repo) | Edit contacts, sync to Google Contacts & Drive, PDFs, meetings | Each user's computer |
| **Share web app** (`sharecontactsfree/`) | Owner: share contact groups. Recipient: connect Google & receive shared contacts | One public website (Cloud Run) |

## For CFS users (contact editing)

1. Install and run CFS as usual.
2. **App Settings → Share web app URL** — set the hosted share app URL (e.g. `https://share.contactsfreeshare.org`).
3. Click **Share Contacts** in the nav — CFS opens the share app already signed in with the same Google account (requires **Share web API key** in Settings matching the share app secret).
4. First-time share setup may still ask Google to approve share-specific permissions (e.g. Gmail send for invites).

CFS does **not** host sharing anymore. No Part B, ngrok, or `sharecontacts.db` sync from the desktop app.

After **Upload**, CFS calls the share app’s incremental sync API for changed contacts (requires `SHARE_WEB_API_KEY` in CFS `.env`, same value as share app `APP_SECRET_KEY`).

## For recipients

Open the invite link (`…/?mode=shared`), sign in with Google, click **Connect account**. No CFS install.

## For many owners (Person A, Person B, …)

- **One** hosted share app serves **everyone**.
- Each owner signs in with their own Google account on that site.
- Each owner's recipients and groups stay separate (multi-tenant by email).
- Friends who use CFS only need the **same share URL** in App Settings — they do **not** deploy Cloud Run.

## Host the share app once (operator)

See `sharecontactsfree/README.md` and `sharecontactsfree/scripts/deploy_cloud_run.sh`.

After deploy:

1. Add OAuth redirect URIs for the Cloud Run URL in Google Cloud Console.
2. Set **Share web app URL** in CFS for all desktop users.

## Local development (share app only)

```bash
cd sharecontactsfree/python
cp .env.example .env   # add Google OAuth credentials
./run.sh
```

- Owner UI: http://127.0.0.1:8080/
- Recipient UI: http://127.0.0.1:8080/?mode=shared

## What was removed from CFS (un-merge)

- Share → Recipients / Share Group pages
- Embedded share engine (`contact_share_service`, push worker on CFS startup)
- Upload hook to sync shared contact edits from CFS

Sharing logic lives entirely in `sharecontactsfree/python/`.

## Optional future hook

After editing contacts in CFS and uploading to Google, owners can run **Sync** in the share web app to refresh shared copies — or we can add an API call from CFS later. Not required for the two-app model to work.
