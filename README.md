welcome

Phase 1 scaffold for a local Python + SQLite contacts website.

## Phase 1 scope

- Run a local web app at `http://127.0.0.1:8000`
- Initialize a SQLite database
- Import Contacts and MeetingData from CSV exports
- View a basic home page and contact list

## CSV export from Google Sheets

Export these sheets as CSV files and place them in `imports/`:

- `contacts_export.csv`
- `meetingdata_export.csv`

The Contacts export should preserve the current sheet columns, including repeated
fields like `Phone 1 - Type`, `Address 2 - Formatted`, and `Custom Field 3 - Value`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Google OAuth setup for Contacts sync

Create a local `.env` file in the project root using `.env.example` as your starting point:

```bash
cp .env.example .env
```

Then fill in:

- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `GOOGLE_OAUTH_REDIRECT_URI`

Default redirect URI:

```text
http://127.0.0.1:8000/google/connect/callback
```

The `.env` file is loaded automatically by the app at startup.

## Initialize the database from CSV

```bash
python3 db/seed_from_google_export.py
```

## Run locally

```bash
python3 run_local.py
```

Then open `http://127.0.0.1:8000`.

## Portable test install for another Mac

Build a copyable app folder:

```bash
bash scripts/build_portable_app.sh
```

To embed public update checks in the packaged app, build with the manifest URL:

```bash
CONTACTSFREESHARE_UPDATE_MANIFEST_URL="https://example.com/contactsfreeshare/app_update_manifest.json" bash scripts/build_portable_app.sh
```

Upload `dist/app_update_manifest.example.json` as `app_update_manifest.json` beside
the Mac and Windows zip files. The generated manifest uses relative download URLs,
so hosting all three files in the same public folder is enough.

After GitHub Pages is configured, publish an update with one command:

```bash
scripts/publish_app_update.sh 1.1 "Describe what changed in this release."
```

Use straight quotes in Terminal. This matters when notes contain `&`, because
unquoted `&` tells the shell to run the command in the background.

The publish script updates `APP_VERSION`, adds the release to the App Revisions
table, builds the Mac and Windows zip files, renames the manifest to
`app_update_manifest.json`, commits the files to `menfisher/contactsfreeshare-updates`,
and pushes them to GitHub Pages. The About modal reads the same `APP_VERSION`, so
new packages show the published version automatically. By default, old update zip
files are removed from local `dist/` and from the GitHub Pages update repo so only
the current Mac/Windows packages remain. To keep older packages, set
`CONTACTSFREESHARE_KEEP_OLD_UPDATE_PACKAGES=1` before running the publish command.

The portable app is created at:

```text
dist/ContactsFreeShare
```

Copy that folder to the other Mac. On that Mac, double-click:

```text
Start ContactsFreeShare.command
```

If Python 3.12 or newer is not installed, the launcher looks up the latest stable
Python release on python.org, downloads that installer on first run, caches it under
`runtime/python-installers/`, and opens it for you. Re-run the launcher after Python
finishes installing. On Windows, if a newer Python is already installed (for example
3.14 or 3.15), the launcher uses that version and does not install an older one.

To add a Mac Desktop app icon, open the `ContactsFreeShare` folder once and
double-click:

```text
Install Desktop Shortcut Mac.command
```

That creates `ContactsFS.app` on the user's Desktop. Keep the unzipped
`ContactsFreeShare` folder in place because the Desktop app starts the app from
that folder.

On Windows, unzip the Windows package, open the `ContactsFreeShare` folder once,
and double-click:

```text
Install Desktop Shortcut Windows.bat
```

That creates a `ContactsFS` icon on the user's Desktop. After that, open
the app by double-clicking the Desktop icon instead of finding the start script
inside the folder.

The launcher creates per-user runtime data at:

```text
~/Library/Application Support/ContactsFreeShare
```

That folder holds the local SQLite database, uploaded contact photos, imports, and
the packaged-app `.env` file. Fill in the Google OAuth values in:

```text
~/Library/Application Support/ContactsFreeShare/.env
```

The redirect URI should remain:

```text
http://127.0.0.1:8000/google/connect/callback
```

For multi-editor testing, both Macs should use the same Google account and Drive
folder during this phase.

## MeetingData v2 design

The current `meeting_data_rows` table is a legacy import table that mirrors the old
Google Sheets D-AE layout. It is useful for import verification, but it is not the
long-term editing model.

The normalized app-owned MeetingData design lives in:

- `db/meeting_layout_v2.sql`
- `app/services/meeting_editor_model.py`

The target model is:

- `meeting_sections_v2`
  - one record per Field + Meeting section
- `meeting_section_rows_v2`
  - one record per visible row inside a section
- `meeting_row_cells_v2`
  - one record per populated cell block, with `col_start` and `col_span`
- `meeting_layout_presets_v2`
  - optional screen/print layout settings per section

This lets the new framework own the data instead of depending on Google Sheets.

### Editor model

The browser editor should work with section-shaped data, not raw D-AE columns:

- `MeetingEditorSection`
- `MeetingEditorRow`
- `MeetingEditorCell`
- `MeetingCellStyle`

This means the UI can:

- edit one meeting section as a coherent table block
- move rows up/down
- resize cell spans
- mark rows as title, month, underlined, group, or content
- render differently for screen vs print

### Transitional plan

1. Import CSV into `meeting_data_rows`
2. Convert legacy rows into `meeting_sections_v2` / `meeting_section_rows_v2` / `meeting_row_cells_v2`
3. Build the section editor against the v2 schema
4. Stop treating Google Sheets as the source of truth
