# Handoff: Field List Creation Branch

## Current Branch And State

- Repo path: `/Users/charlesvaughn/Desktop/VScode_Test/PythonDB4-3-26`
- Current branch: `field_list_creation`
- `google_connect_UI` was already merged into `main`, pushed, and deleted locally/remotely.
- This branch currently has uncommitted Editor-secret changes plus generated runtime files.
- Runtime/generated dirty files are present and should not be treated as intentional source edits unless the user asks:
  - `db/app.sqlite3-shm`
  - `db/app.sqlite3-wal`
  - `logs/clicks.log`
  - `logs/errors.log`

## Completed Work In This Branch

### Editor Secret Sign-In

Implemented an Editor-only secret gate on Google Connect:

- Default temporary Editor secret: `Editor2026`
- Google Connect requires Editor users to tap a small lock button to reveal the `Editor secret` password field.
- Editor sign-in validates the secret before setting pending Editor name, before starting OAuth, and before acquiring the Editor lock.
- Wrong or missing secret returns a warning notice and keeps the app in NO-EDIT mode.
- Secret can be changed from ContactsFreeShare Settings.
- Blank secret field on settings save keeps the current secret.
- Secret is stored and synced as hash + salt only, not raw text.
- If a connected computer has an old local secret, Editor sign-in tries refreshing shared settings from Drive before rejecting the submitted secret.

### Google Connect / Settings UI Fixes

- Google Connect now treats an active Editor lock as a signed-in state so the button shows `Sign out`, not `Sign in`.
- ContactsFreeShare Settings now groups each text box with its message and adds separator lines so help text clearly belongs to the box above it.
- CSS cache was bumped to `v=125`.

## Files Changed So Far

Intentional source/schema edits:

- `app/database.py`
- `app/routes/google_sync.py`
- `app/routes/presets.py`
- `app/services/google_sync_service.py`
- `app/static/css/app.css`
- `app/templates/base.html`
- `app/templates/presets/app_settings.html`
- `app/templates/presets/google_sync_settings.html`
- `db/schema.sql`

Runtime/generated dirty files:

- `db/app.sqlite3-shm`
- `db/app.sqlite3-wal`
- `logs/clicks.log`
- `logs/errors.log`

Verification already run successfully after Editor-secret work:

- `.venv/bin/python -B -m py_compile app/database.py app/routes/google_sync.py app/routes/presets.py app/services/google_sync_service.py`
- Jinja parse check for:
  - `presets/google_sync_settings.html`
  - `presets/app_settings.html`
  - `base.html`
- `git diff --check`
- Secret behavior smoke check:
  - `Editor2026` worked as default
  - wrong secret failed
  - changed secret worked
  - restored default worked

## Field List Builder Decisions

The next task is to implement the Field List builder and Address Book Settings.

Locked decisions:

- V1 is screen-layout only. No PDF or printed output yet.
- Page size is fixed at 8.5x11 inches.
- Add `Address Book Settings` under the top Settings dropdown.
- ContactsFreeShare > `Field List` should open the new Field List canvas builder page.
- Google Connect role labels should change:
  - `Field List creation` -> `Field List`
  - `Address Book creation` -> `Address Book`
- Keep internal role keys unless cleanup is required:
  - `field_list_creation`
  - `address_book_creation`
- Successful `Field List` access-role sign-in should redirect directly to the Field List canvas page.
- Users signed in with `Field List` can create, name, edit, and save Field List templates without acquiring the Editor lock.
- Editing is allowed when:
  - Multi-Editor mode is off, or
  - an active Editor is signed in, or
  - current access role is `field_list_creation`
- Other non-Editor roles may view but should not edit.
- Field List layout/settings should sync through Drive.
- Save buttons are used instead of auto-save.
- Sidebar palette uses fixed common item types, not discovered database values.
- Palette numbers are stable per item type, not placement order.
- Use visible snap grid.
- Phone number blocks enforce a minimum width equal to 12 characters in the selected font, but can be wider and grow vertically.
- `View Templates` can be a disabled/placeholder button in V1.

## Planned Field List Features

Add a new Field List canvas page with:

- Top controls:
  - `Create Field List`
  - `View Templates`
  - template name text box
  - `Save Template`
- Left sidebar palette:
  - `1 Field name`
  - `2 Meeting name`
  - `3 Phone number`
  - `4 Address`
  - `5 Relationships`
  - `6 Emails`
  - `7 Page number`
- 8.5x11 canvas with margins and typography from Address Book Settings.
- Clicking a palette item adds a numbered block to the canvas.
- Blocks can be dragged, resized, snapped to grid, and deleted with an `x`.
- Saving stores the named template and marks Field List data dirty for Drive sync.

Add `Address Book Settings` page with:

- Contacts-style title bar titled `Address Book Settings`.
- Fields:
  - left margin
  - right margin
  - top margin
  - bottom margin
  - font family
  - base font size
  - line height
  - preview scale
- Fixed page size: 8.5x11 inches.

## Suggested Implementation Plan

1. Add database schema/migrations:
   - `address_book_settings`
   - `field_list_templates`
   - `field_list_template_items`
   - `field_list_needs_drive_export`
   - `field_list_sync_revision`

2. Add a new service, likely `app/services/field_list_service.py`, responsible for:
   - ensuring default settings/template
   - checking edit permission for Field List
   - loading settings/template/items
   - saving settings
   - saving template name and items
   - exporting/importing Drive payload
   - marking Field List dirty

3. Add routes:
   - `GET /field-list`
   - `POST /field-list/templates/save`
   - `GET /presets/address-book`
   - `POST /presets/address-book/save`

4. Add templates:
   - `app/templates/field_list.html`
   - `app/templates/presets/address_book_settings.html`

5. Add JS/CSS:
   - new `app/static/js/field_list_builder.js`
   - Field List canvas and palette styling in `app/static/css/app.css`
   - bump CSS cache in `app/templates/base.html`

6. Wire navigation:
   - ContactsFreeShare > `Field List` links to `/field-list`
   - Settings dropdown includes `Address Book`
   - Current-path active states include `/field-list` and `/presets/address-book`

7. Wire Google Connect roles:
   - display `Field List` and `Address Book` labels
   - keep current role values
   - redirect `field_list_creation` sign-in to `/field-list`

8. Wire Drive sync in `app/services/google_sync_service.py`:
   - add Field List Drive file name
   - include `field_list_sync_revision` in shared sync state
   - ensure shared Drive storage creates the Field List JSON file
   - export Field List payload when dirty
   - import when remote revision is newer
   - clear dirty flag after successful export/import

## Important Caution

An attempted Field List schema patch was started but failed before applying. Treat the Field List implementation as not yet started in source files. The visible source changes are from the Editor-secret work unless a later diff shows otherwise.

Before continuing, run:

```bash
git status --short
git diff -- app/database.py app/routes/google_sync.py app/routes/presets.py app/services/google_sync_service.py app/static/css/app.css app/templates/base.html app/templates/presets/app_settings.html app/templates/presets/google_sync_settings.html db/schema.sql
```

Then continue from the planned Field List steps above.
