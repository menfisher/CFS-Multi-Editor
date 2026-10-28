# Deploy Share Google Contacts to Cloud Run

One-time setup so **every** CFS user (Person A, B, …) can use the same share URL.

**Time:** ~20–30 minutes the first time.

---

## Before you start

You need:

- A **Google Cloud project** (you already used one for OAuth client `172990673023-...`)
- **Owner** access to that project (to enable APIs and deploy Cloud Run)
- This Mac with Terminal

Your share app `.env` is already prepared at:

`sharecontactsfree/python/.env`

(same OAuth client as CFS).

---

## Step 1 — Install Google Cloud SDK

In Terminal:

```bash
brew install --cask google-cloud-sdk
```

If you don’t use Homebrew: https://cloud.google.com/sdk/docs/install

Close and reopen Terminal, then verify:

```bash
gcloud --version
```

---

## Step 2 — Log in and pick your project

```bash
gcloud auth login
```

Use the Google account that owns your Cloud project.

List projects:

```bash
gcloud projects list
```

Set the project (replace with your **Project ID**, not the number):

```bash
gcloud config set project YOUR_PROJECT_ID
```

Your OAuth client number is `172990673023` — the project ID is often shown in [Google Cloud Console](https://console.cloud.google.com/) at the top (e.g. `my-project-123456`).

---

## Step 3 — Deploy to Cloud Run

```bash
cd ~/Desktop/VScode_Test/PythonDB4-3-26/sharecontactsfree
bash scripts/deploy_cloud_run.sh
```

What this does:

- Enables Cloud Run, Cloud Build, Artifact Registry, Cloud Storage
- Builds the Docker image from `sharecontactsfree/Dockerfile`
- Deploys service name `share-contacts` in `us-central1`
- Creates **`gs://YOUR_PROJECT-share-contacts-db`** and mounts it at `/data` so SQLite survives redeploys
- Pins **`APP_SECRET_KEY`** from `python/.env` (required — do not let deploy generate a new one each time)
- Runs **one Cloud Run instance** (`min-instances=1`, `max-instances=1`) so SQLite is not split across machines
- Prints a URL like: `https://share-contacts-xxxxx-uc.a.run.app`

**Before deploy:** set a stable `APP_SECRET_KEY` in `sharecontactsfree/python/.env`:

```bash
openssl rand -hex 32
```

**Save that URL.** First deploy often takes 5–10 minutes.

If billing is not enabled, Cloud Console will prompt you to link a billing account (Cloud Run has a free tier; small share app usage is usually low cost).

### Database persistence

Share app state (invites, import progress, OAuth tokens, staged contacts) lives in SQLite at `DATABASE_PATH`.
Cloud Run containers are otherwise ephemeral; the deploy script mounts a Cloud Storage bucket at `/data` by default.

To disable the volume (not recommended for production):

```bash
SHARE_MOUNT_DB_VOLUME=0 bash scripts/deploy_cloud_run.sh
```

### Recipient recovery after server data loss

If the server lost its database but a recipient already imported contacts, the share app can **rebuild the UI list** from Google Contact groups named `Group Name (shared)` when the recipient opens `/?mode=shared` and clicks **Refresh**. Contacts remain in Google Contacts; the owner may need to re-sync from CFS for future updates.

---

## Step 4 — Add OAuth redirect URIs (required)

1. Open [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Click your **OAuth 2.0 Client ID** (Web application — same one CFS uses)
3. Under **Authorized redirect URIs**, add **both** (use your real Cloud Run URL):

```text
https://share-contacts-xxxxx-uc.a.run.app/auth/owner/callback
https://share-contacts-xxxxx-uc.a.run.app/auth/recipient/callback
```

4. **Save**

Keep existing URIs for CFS (`http://127.0.0.1:8000/...`) and local share dev (`http://127.0.0.1:8080/...`) if you still use them.

---

## Step 5 — Set Share web app URL in CFS

1. Run CFS: `python3 run_contactsfreeshare.py`
2. **Presets → App Settings**
3. **Share web app URL** → paste your Cloud Run URL (no trailing slash):

```text
https://share-contacts-xxxxx-uc.a.run.app
```

4. Save settings
5. Click **Share Contacts** in the nav — it should open the share app and show Google sign-in

---

## Step 6 — Test owner + recipient

**Owner (you):**

1. Share Contacts → sign in with your owner Google account
2. Share a contact group with a test recipient email
3. Use the share app’s invite flow (or copy recipient link `…/?mode=shared`)

**Recipient (friend / second account):**

1. Open `https://YOUR-CLOUD-RUN-URL/?mode=shared`
2. Sign in with the **invited** Google account (not the owner account)
3. Click **Connect account**
4. Shared groups should import

---

## Step 7 — Optional: custom domain `share.contactsfreeshare.org`

Instead of the long `run.app` URL:

1. Cloud Run → **share-contacts** → **Manage custom domains**
2. Add `share.contactsfreeshare.org`
3. Add the DNS records at your domain registrar (where `contactsfreeshare.org` is managed)
4. Add OAuth redirect URIs for `https://share.contactsfreeshare.org/auth/owner/callback` and `…/auth/recipient/callback`
5. Set CFS App Settings to `https://share.contactsfreeshare.org`

---

## For friends using CFS (Person B)

They **do not** run this deploy. They only set **Share web app URL** to the **same** URL you use (Step 5).

---

## Redeploy after code changes

```bash
cd ~/Desktop/VScode_Test/PythonDB4-3-26/sharecontactsfree
bash scripts/deploy_cloud_run.sh
```

OAuth URIs and CFS App Settings stay the same unless the Cloud Run URL changes.

---

## Troubleshooting

| Problem | Fix |
|--------|-----|
| `gcloud not found` | Install SDK (Step 1), restart Terminal |
| `Permission denied` on deploy | `gcloud auth login`; confirm project; enable billing |
| Google sign-in “redirect_uri_mismatch” | Add exact callback URLs (Step 4) |
| Blank page / 500 on Cloud Run | Check logs: `gcloud run services logs read share-contacts --region us-central1` |
| Recipient sees no shared groups | Owner must share from the **share web app** while signed in as owner |

---

## Quick reference

| Item | Value |
|------|--------|
| Deploy script | `sharecontactsfree/scripts/deploy_cloud_run.sh` |
| Share app env | `sharecontactsfree/python/.env` |
| CFS setting | App Settings → Share web app URL |
| Owner browser | `{URL}/` |
| Recipient invite | `{URL}/?mode=shared` |
