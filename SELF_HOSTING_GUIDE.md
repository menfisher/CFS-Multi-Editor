# ContactsFreeShare — Self-Hosting Guide (for friends)

This guide shows you how to run **your own** copy of ContactsFreeShare (CFS) instead of
using someone else's server. There are two pieces:

1. **The CFS desktop app** — runs on *your own* Mac or Windows computer. You use it to
   view and edit your contacts and keep them in sync with your Google account.
2. **The Share server** — a small private website you run in **your own Google Cloud
   account**. It is what lets you *share* a group of contacts with other people
   ("recipients") and push updates to them automatically.

You can use the desktop app by itself. You only need the Share server if you want to
**share contact groups with other people**.

> **Honest heads-up about difficulty and cost**
> - Installing the **desktop app** is easy (Part 2). Most people can do it in 10 minutes.
> - Setting up the **Share server** (Part 3) is the advanced part. It involves Google
>   Cloud, billing, and copy/pasting commands into a terminal. Budget 1–2 hours the
>   first time. If you've never used a terminal, ask a more technical friend to sit
>   with you for this part.
> - The Share server runs 24/7 so it can push updates in the background. That means
>   Google Cloud will charge you a **small monthly amount** (often only a few dollars,
>   but you must turn on billing with a credit card). You can shut it down anytime.

---

## Part 1 — What you'll need

- A **Google account** (the one whose contacts you want to manage).
- A computer running **macOS** or **Windows**.
- For the Share server only:
  - A **credit/debit card** to enable Google Cloud billing.
  - About **1–2 hours** and willingness to copy/paste a few commands.

---

## Part 2 — Install the CFS desktop app (the easy part)

### 2.1 Get the app files

Ask the person who sent you this guide for the **download link** to the app package
(a `.zip` file). There is one for **Mac** and one for **Windows** — get the right one.

### 2.2 Unzip and start it

**On a Mac:**
1. Double-click the downloaded `.zip` to unzip it. You'll get a folder named
   `ContactsFreeShare`.
2. Open that folder and double-click **`Start ContactsFreeShare.command`**.
3. The first time, macOS may say the file is from an unidentified developer. If so:
   right-click the file → **Open** → **Open** again to confirm.
4. If Python isn't installed, the launcher will download the installer for you and open
   it. Install Python, then double-click **`Start ContactsFreeShare.command`** again.
5. (Optional) Double-click **`Install Desktop Shortcut Mac.command`** to put a
   `ContactsFS` icon on your Desktop. Keep the unzipped folder where it is.

**On Windows:**
1. Right-click the downloaded `.zip` → **Extract All** → you'll get a `ContactsFreeShare`
   folder.
2. Open that folder and double-click **`Start ContactsFreeShare`**.
3. If Windows SmartScreen warns you, click **More info → Run anyway**.
4. If Python isn't installed, the launcher downloads it for you. Install it, then start
   the app again.
5. (Optional) Double-click **`Install Desktop Shortcut Windows.bat`** to put a
   `ContactsFS` icon on your Desktop.

When it starts, your web browser opens to **`http://127.0.0.1:8000`**. That's the app.
(It runs entirely on your own computer — nothing is published to the internet.)

### 2.3 Connect it to your Google account

The app needs permission to read and write *your* Google Contacts. Google requires you
to create a small "OAuth client" for this. This is free.

> If you are **also** setting up the Share server (Part 3), you can create **one** Google
> Cloud project and **one** OAuth client and use it for both. In that case, do Part 3
> steps 3.1–3.4 first, then come back here and reuse the same Client ID and Secret.

1. Go to **https://console.cloud.google.com/** and sign in with your Google account.
2. At the top, create a new project (click the project dropdown → **New Project** →
   give it a name like `my-contacts` → **Create**).
3. In the search bar, search for **"Google People API"**, open it, and click **Enable**.
4. In the search bar, go to **"OAuth consent screen"**:
   - Choose **External** → **Create**.
   - Fill in an app name (e.g. `My Contacts`), your email for the support email and
     developer contact email. Leave the rest as defaults → **Save and Continue**.
   - On the **Scopes** page, just click **Save and Continue**.
   - On **Test users**, click **Add Users**, add **your own Google email**, then
     **Save and Continue**.
5. In the search bar, go to **"Credentials"** → **Create Credentials** →
   **OAuth client ID**:
   - Application type: **Web application**.
   - Name: anything (e.g. `Desktop app`).
   - Under **Authorized redirect URIs**, click **Add URI** and paste **exactly**:

     ```
     http://127.0.0.1:8000/google/connect/callback
     ```

   - Click **Create**. A box pops up with your **Client ID** and **Client secret**.
     Copy both somewhere safe.

### 2.4 Put your Client ID and Secret into the app

The app reads these from a settings file called `.env`. It is created automatically the
first time you launch the app, in your personal app-data folder:

- **Mac:** `~/Library/Application Support/ContactsFreeShare/.env`
- **Windows:** `C:\Users\<your-name>\AppData\Roaming\ContactsFreeShare\.env`

> Tip (Mac): in Finder press **Cmd+Shift+G** and paste
> `~/Library/Application Support/ContactsFreeShare` to open the folder.
> Tip (Windows): in the File Explorer address bar type `%APPDATA%\ContactsFreeShare`
> and press Enter.

Open `.env` in a plain text editor (TextEdit on Mac, Notepad on Windows) and set:

```
GOOGLE_OAUTH_CLIENT_ID=paste-your-client-id-here
GOOGLE_OAUTH_CLIENT_SECRET=paste-your-client-secret-here
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/google/connect/callback
```

Save the file, then **fully quit and restart** the app. In the app, use the
**Connect Google** option and sign in. Because the app is "unverified," Google shows a
warning screen — click **Advanced → Go to (your app name) (unsafe)** and allow access.
This is expected for a personal app you built yourself.

You can now use the desktop app on its own. **If you don't need to share contacts with
other people, you're done.** Continue to Part 3 only if you want sharing.

---

## Part 3 — Set up your own Share server (the advanced part)

This publishes a tiny private website to **your** Google Cloud account. It lets you share
a contact group with recipients and push updates to them automatically.

### 3.1 Create a Google Cloud project and turn on billing

1. Go to **https://console.cloud.google.com/** and sign in.
2. Create a project (or reuse the one from Part 2). Note its **Project ID** (shown under
   the project name, e.g. `my-contacts-123456`).
3. In the search bar, go to **"Billing"** and link a billing account (add a card). The
   Share server needs billing enabled to run continuously.

> **Cost control:** You can set a **budget alert** (Billing → Budgets & alerts) so Google
> emails you if spending passes, say, $10/month. You can delete the service anytime to
> stop charges (see Part 6).

### 3.2 Install the Google Cloud command-line tool (gcloud)

This is a program that lets your computer talk to Google Cloud.

- **Mac:** Open the **Terminal** app and run:

  ```bash
  curl https://sdk.cloud.google.com | bash
  ```

  Then close and reopen Terminal. (Alternatively, follow
  https://cloud.google.com/sdk/docs/install for a clickable installer.)

- **Windows:** Download and run the installer from
  https://cloud.google.com/sdk/docs/install (look for the **Windows installer**).
  After it finishes, open the **"Google Cloud SDK Shell"** from the Start menu — use
  that window for the commands below.

Verify it works by running:

```bash
gcloud --version
```

You should see version numbers (not an error).

### 3.3 Sign in and pick your project

In the terminal, run these one at a time (replace `YOUR_PROJECT_ID` with your real
Project ID from step 3.1):

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

`gcloud auth login` opens a browser to confirm — sign in with the same Google account.

### 3.4 Create the OAuth consent screen and OAuth client (if you didn't already)

If you already did Part 2 steps 3–5, you can **reuse the same OAuth client** — just add
more redirect URIs to it in step 3.8 below. Otherwise, create one now:

1. Console → **"Google People API"** → **Enable** (if not done).
2. Console → **"OAuth consent screen"** → **External** → fill in app name + your email →
   add **yourself** as a Test user.
3. Console → **"Credentials"** → **Create Credentials** → **OAuth client ID** →
   **Web application**. Copy the **Client ID** and **Client secret**. (You'll add the
   web redirect URIs in step 3.8, after you know your server's URL.)

### 3.5 Get the app code onto your computer

You need the project's source code folder (the one that contains the `sharecontactsfree`
folder). The person sharing this guide should give it to you — either as a `.zip` to
unzip, or a link to download/clone it. Put it somewhere easy to find, e.g. your home
folder.

In the terminal, move into the project folder. For example:

```bash
cd ~/PythonDB4-3-26
```

(Use the actual folder name/path where you put it.)

### 3.6 Create the server's settings file

The server reads its settings from `sharecontactsfree/python/.env`. Create it from the
provided example:

```bash
cd sharecontactsfree/python
cp .env.example .env
```

Now you need a random **secret key**. Generate one:

- **Mac / Linux:**

  ```bash
  openssl rand -hex 32
  ```

- **Windows (Google Cloud SDK Shell):**

  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

Copy the long random string it prints. Then open
`sharecontactsfree/python/.env` in a text editor and fill in:

```
GOOGLE_CLIENT_ID=paste-your-client-id-here
GOOGLE_CLIENT_SECRET=paste-your-client-secret-here
APP_SECRET_KEY=paste-the-random-string-you-generated
CFS_API_KEY=paste-the-same-random-string-again
```

> Keep `APP_SECRET_KEY` safe and **don't change it later** — changing it logs out all
> recipients. Using the same value for `CFS_API_KEY` is what lets your desktop app push
> updates to the server.

Leave the other lines as they are (the deploy script fills in the rest automatically).

### 3.7 Deploy the server

From the project folder, run the deploy script:

```bash
cd ../..              # back to the project root (where the sharecontactsfree folder is)
bash sharecontactsfree/scripts/deploy_cloud_run.sh
```

The first run takes several minutes. It will:
- turn on the required Google Cloud services,
- create a storage bucket for the database,
- build and publish your server.

If it asks you to confirm enabling APIs or creating things, say **yes**.

When it finishes, it prints something like:

```
Deployed: https://share-contacts-xxxxxxxx-uc.a.run.app
...
Next steps:
  1. Google Cloud Console → OAuth client → Authorized redirect URIs:
       https://share-contacts-xxxxxxxx-uc.a.run.app/auth/owner/callback
       https://share-contacts-xxxxxxxx-uc.a.run.app/auth/recipient/callback
```

**Copy that `https://...run.app` URL — you'll need it twice below.**

### 3.8 Add the server's redirect URIs to your OAuth client

1. Console → **"Credentials"** → click your **OAuth client** (the Web application one).
2. Under **Authorized redirect URIs**, click **Add URI** and add **both** of these
   (replace the address with *your* real URL from step 3.7):

   ```
   https://share-contacts-xxxxxxxx-uc.a.run.app/auth/owner/callback
   https://share-contacts-xxxxxxxx-uc.a.run.app/auth/recipient/callback
   ```

   (If this is the same client you use for the desktop app, the
   `http://127.0.0.1:8000/google/connect/callback` URI from Part 2 should stay too.)
3. Click **Save**.

### 3.9 Make recipient logins reliable (important)

While your OAuth consent screen is in **"Testing"** mode, Google **expires permission
after 7 days**, which breaks automatic updates to recipients. Two options:

- **Recommended for friends/family:** Console → **"OAuth consent screen"** →
  **Publish App** → confirm to move it to **"In production."** You can do this without
  formal Google verification; users (you and your recipients) will just see an
  "unverified app" warning they click past. This avoids the 7-day expiry. There's a limit
  of ~100 users, which is plenty for personal use.
- **Or, keep it in Testing:** then add **every recipient's** Google email as a
  **Test user** under the OAuth consent screen, and be aware permissions need
  re-approving every 7 days.

### 3.10 Connect your desktop app to your server

1. Open the **CFS desktop app** → **App Settings**.
2. Set **Share web app URL** to your server URL from step 3.7
   (`https://share-contacts-xxxxxxxx-uc.a.run.app`).
3. Set **Share web API key** to the **same random string** you put in `APP_SECRET_KEY` /
   `CFS_API_KEY` in step 3.6.
4. Save.

Your desktop app is now wired to your own Share server.

---

## Part 4 — Sharing contacts with recipients

1. In your browser, open your server's **owner page**:
   `https://share-contacts-xxxxxxxx-uc.a.run.app/` and sign in with Google.
2. Choose a contact group to share and add a recipient's email.
3. Send the recipient this link (they open it in any browser — they do **not** install
   anything):

   ```
   https://share-contacts-xxxxxxxx-uc.a.run.app/?mode=shared
   ```

4. The recipient signs in with their Google account and clicks **Connect account**. Your
   server then imports/updates the shared contacts into their Google account in the
   background. When you later edit those contacts in the desktop app and upload, your
   server pushes the changes to them automatically.

---

## Part 5 — Quick reference (what goes where)

| Thing | Value |
|------|-------|
| Desktop app address | `http://127.0.0.1:8000` |
| Desktop `.env` (Mac) | `~/Library/Application Support/ContactsFreeShare/.env` |
| Desktop `.env` (Windows) | `%APPDATA%\ContactsFreeShare\.env` |
| Desktop redirect URI | `http://127.0.0.1:8000/google/connect/callback` |
| Server settings file | `sharecontactsfree/python/.env` |
| Server redirect URIs | `<server-url>/auth/owner/callback` and `<server-url>/auth/recipient/callback` |
| Server owner page | `<server-url>/` |
| Recipient invite link | `<server-url>/?mode=shared` |
| Share web API key (in desktop) | same as `APP_SECRET_KEY` / `CFS_API_KEY` |

---

## Part 6 — Updating, costs, and shutting down

**Update the server later** (after getting newer code): re-run

```bash
bash sharecontactsfree/scripts/deploy_cloud_run.sh
```

**See/limit costs:** Google Cloud Console → **Billing → Budgets & alerts**. Set a small
budget so you get emailed if it grows.

**Pause/stop charges:** Console → **Cloud Run** → click the **share-contacts** service →
**Delete** (you can redeploy later). Or set min instances to 0 to reduce cost — but note
background pushes become less reliable.

---

## Part 7 — Troubleshooting

- **"gcloud: command not found"** → gcloud isn't installed or the terminal wasn't
  reopened after install. Redo Part 3.2 and open a fresh terminal.
- **Deploy says "APP_SECRET_KEY is required"** → you didn't fill in
  `sharecontactsfree/python/.env`. Redo step 3.6.
- **Deploy says "GOOGLE_CLIENT_ID ... required"** → the Client ID/Secret lines in that
  `.env` are blank. Paste them from your OAuth client.
- **Google sign-in says "redirect_uri_mismatch"** → the exact URL isn't listed in your
  OAuth client's **Authorized redirect URIs**. Re-check Part 2.3 / step 3.8; the address
  must match character-for-character (including `http`/`https` and no trailing slash
  difference).
- **"This app is unverified" warning** → expected for a personal build. Click
  **Advanced → Go to … (unsafe)** to continue.
- **Recipients stop syncing after about a week** → your consent screen is still in
  "Testing." Publish it to production (step 3.9).
- **Desktop app's "Connect Google" fails** → confirm the three `GOOGLE_OAUTH_*` lines in
  the desktop `.env` are correct and that you restarted the app.

---

### Don't want to run a server at all?

If a friend would rather not deal with Google Cloud, they don't have to. They can use the
**desktop app only** (Part 2) to manage their own contacts, and you (or whoever already
runs a Share server) can simply add them as another **owner** on an existing server — the
Share server is designed to host many different owners at once.
