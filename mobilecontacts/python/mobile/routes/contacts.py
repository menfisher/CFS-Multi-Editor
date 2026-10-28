from __future__ import annotations

import base64
import binascii
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from app.services.changes_list_service import record_contact_edits_to_pending_list
from app.services.contact_service import (
    add_contact_assignment_option,
    delete_contact_assignment_option,
    get_contact_detail,
    get_contact_filter_options,
    get_latest_contact_edit_log_entries,
    list_contacts,
    remove_contact_photo,
    rename_contact_assignment_option,
    update_contact,
)
from app.services.google_sync_service import (
    get_current_changes_list_initials,
    people_import_in_progress,
)

from mobile.access_session import (
    mobile_access_context,
    mobile_can_open_contact_edit,
    mobile_is_editor,
    mobile_is_non_editor,
    mobile_records_edits_to_pending_list,
)
from mobile.sync_adapter import (
    has_cached_contacts,
    mobile_sync_message,
    refresh_contact_app_fields_from_google_drive,
    refresh_contacts_from_google,
    sync_contact_changes,
    upload_contacts_only,
)
from mobile.user_db import (
    _push_local_db_to_cloud,
    clear_user_db_writer,
    mark_user_db_writer,
    use_user_database,
    user_db_write_lock,
)
from mobile.web_templates import templates

router = APIRouter(prefix="/m/contacts", tags=["mobile-contacts"])

MOBILE_CONTACTS_REFRESHED_KEY = "mobile_contacts_google_refreshed"
LIST_AVATAR_PHOTO_SIZE = 96
LIST_PHOTO_LARGE_SIZE = 2048
CONTACT_PHOTO_ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
CONTACT_PHOTO_MAX_BYTES = 8 * 1024 * 1024


def _contact_photo_display_url(photo: str | None, *, size: int) -> str:
    from app.services.google_sync_service import CONTACT_PHOTO_STATIC_PREFIX, google_photo_display_url

    text = str(photo or "").strip()
    if not text:
        return ""
    if text.startswith(CONTACT_PHOTO_STATIC_PREFIX):
        return text
    return google_photo_display_url(text, size=size)


def _contact_list_avatar_initial(given_name: str | None, family_name: str | None) -> str:
    label = str(given_name or family_name or "?").strip()
    return label[:1].upper() if label else "?"


def _attach_list_avatar_fields(contacts: list[dict]) -> None:
    for contact in contacts:
        raw_photo = contact.get("photo")
        contact["list_photo_url"] = _contact_photo_display_url(raw_photo, size=LIST_AVATAR_PHOTO_SIZE)
        contact["list_photo_large_url"] = _contact_photo_display_url(raw_photo, size=LIST_PHOTO_LARGE_SIZE)
        contact["list_avatar_initial"] = _contact_list_avatar_initial(
            contact.get("given_name"),
            contact.get("family_name"),
        )


async def _save_uploaded_contact_photo(form) -> str:
    from app.services.google_sync_service import CONTACT_PHOTO_STATIC_PREFIX, get_contact_photo_storage_dir

    def write_bytes(content: bytes, extension: str) -> str:
        if len(content) > CONTACT_PHOTO_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Contact photo upload must be 8 MB or smaller.")
        storage_dir = get_contact_photo_storage_dir()
        storage_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}{extension}"
        (storage_dir / filename).write_bytes(content)
        return f"{CONTACT_PHOTO_STATIC_PREFIX}{filename}"

    cropped_photo = str(form.get("cropped_contact_photo") or "").strip()
    if cropped_photo:
        header, separator, payload = cropped_photo.partition(",")
        if separator and header.startswith("data:image/"):
            image_type = header.removeprefix("data:image/").split(";", 1)[0].lower()
            extension = ".jpg" if image_type == "jpeg" else f".{image_type}"
            if extension not in CONTACT_PHOTO_ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail="Cropped contact photo must be JPG, PNG, GIF, or WebP.")
            try:
                content = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Cropped contact photo data is not valid.") from exc
            return write_bytes(content, extension)

    upload = form.get("contact_photo_upload")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return ""

    content_type = str(upload.content_type or "")
    if content_type and not content_type.lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="Contact photo upload must be an image file.")

    extension = Path(str(upload.filename)).suffix.lower()
    if extension not in CONTACT_PHOTO_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Contact photo upload must be JPG, PNG, GIF, or WebP.")

    content = await upload.read()
    if not content:
        return ""
    return write_bytes(content, extension)


def _owner_email(request: Request) -> str | None:
    email = str(request.session.get("owner_email") or "").strip()
    return email or None


def _mobile_list_query(q: str = "", field: str = "", meeting: str = "") -> dict[str, str]:
    params: dict[str, str] = {}
    normalized_q = (q or "").strip()
    normalized_field = (field or "").strip()
    normalized_meeting = (meeting or "").strip()
    if normalized_q:
        params["q"] = normalized_q
    if normalized_field:
        params["field"] = normalized_field
    if normalized_meeting:
        params["meeting"] = normalized_meeting
    return params


def _mobile_list_url(
    q: str = "",
    field: str = "",
    meeting: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    params = _mobile_list_query(q, field, meeting)
    if extra:
        params.update({key: value for key, value in extra.items() if value})
    if params:
        return f"/m/contacts/?{urlencode(params)}"
    return "/m/contacts/"


def _mobile_contact_url(
    contact_id: int,
    suffix: str = "",
    q: str = "",
    field: str = "",
    meeting: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    base = f"/m/contacts/{contact_id}{suffix}"
    params = _mobile_list_query(q, field, meeting)
    if extra:
        params.update({key: value for key, value in extra.items() if value})
    if params:
        return f"{base}?{urlencode(params)}"
    return base


def _normalize_birthday_input(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("/", "-").replace(".", "-")
    parts = [part.strip() for part in normalized.split("-") if part.strip()]
    try:
        if len(parts) == 3:
            first, second, third = (int(part) for part in parts)
            if len(parts[0]) == 4:
                year, month, day = first, second, third
            else:
                month, day, year = first, second, third
            if month and day:
                return f"{year:04d}-{month:02d}-{day:02d}"
        if len(parts) == 2:
            month, day = (int(part) for part in parts)
            if month and day:
                return f"{month:02d}-{day:02d}"
    except ValueError:
        return text
    return text


_BLANK_LABEL_VALUES = frozenset({"", "__blank__", "no label", "—", "-", "–"})
_ASSIGNMENT_SENTINELS = frozenset({
    "__add_field__",
    "__delete_field__",
    "__rename_field__",
    "__add_meeting__",
    "__delete_meeting__",
    "__rename_meeting__",
})


def _normalize_label_type(value: str | None) -> str:
    text = str(value or "").strip()
    if text.lower() in _BLANK_LABEL_VALUES:
        return ""
    return text


def _normalize_label_type_list(values: list[str]) -> list[str]:
    return [_normalize_label_type(value) for value in values]


def _normalize_assignment_value(value: str | None) -> str:
    text = str(value or "").strip()
    if text in _ASSIGNMENT_SENTINELS:
        return ""
    return text


def _build_contact_payload(form, uploaded_photo_url: str = "") -> dict:
    return {
        "selected": str(form.get("selected") or ""),
        "google_contact_id": str(form.get("google_contact_id") or ""),
        "etag": str(form.get("etag") or ""),
        "last_updated": str(form.get("last_updated") or ""),
        "status": str(form.get("status") or ""),
        "do_not_print": str(form.get("do_not_print") or ""),
        "fields_text": _normalize_assignment_value(form.get("fields_text")),
        "meetings_text": _normalize_assignment_value(form.get("meetings_text")),
        "group_membership": str(form.get("group_membership") or ""),
        "family_name": str(form.get("family_name") or ""),
        "given_name": str(form.get("given_name") or ""),
        "photo": uploaded_photo_url or str(form.get("photo") or ""),
        "birthday": _normalize_birthday_input(form.get("birthday")),
        "notes": str(form.get("notes") or ""),
        "mtg_home_elder_flag": str(form.get("mtg_home_elder_flag") or ""),
        "meets_in_state": str(form.get("meets_in_state") or ""),
        "relationship_type": _normalize_label_type_list(form.getlist("relationship_type")),
        "relationship_value": form.getlist("relationship_value"),
        "phone_type": _normalize_label_type_list(form.getlist("phone_type")),
        "phone_value": form.getlist("phone_value"),
        "email_type": _normalize_label_type_list(form.getlist("email_type")),
        "email_value": form.getlist("email_value"),
        "address_type": _normalize_label_type_list(form.getlist("address_type")),
        "street_address": form.getlist("street_address"),
        "extended_address": form.getlist("extended_address"),
        "city": form.getlist("city"),
        "region": form.getlist("region"),
        "postal_code": form.getlist("postal_code"),
        "formatted_address": form.getlist("formatted_address"),
        "coordinates": form.getlist("coordinates"),
        "custom_field_type": form.getlist("custom_field_type"),
        "custom_field_value": form.getlist("custom_field_value"),
    }


def _require_owner(request: Request) -> str:
    email = _owner_email(request)
    if not email:
        raise RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    return email


def _needs_google_contacts_refresh(request: Request) -> bool:
    if people_import_in_progress():
        return True
    if bool(request.session.get(MOBILE_CONTACTS_REFRESHED_KEY)):
        return False
    # Pending Edits always download on first list view. Single-editor / Editor
    # also download when the phone copy is still empty after sign-in.
    if mobile_is_non_editor(request):
        return True
    return not has_cached_contacts()


def _mark_google_contacts_refreshed(request: Request) -> None:
    request.session[MOBILE_CONTACTS_REFRESHED_KEY] = "1"


def mark_google_contacts_refreshed(request: Request) -> None:
    _mark_google_contacts_refreshed(request)


def clear_google_contacts_refresh_flag(request: Request) -> None:
    request.session.pop(MOBILE_CONTACTS_REFRESHED_KEY, None)


@router.get("/")
def contacts_list(
    request: Request,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    contacts = list_contacts(q, field, meeting)
    _attach_list_avatar_fields(contacts)
    filters = get_contact_filter_options()
    list_url = _mobile_list_url(q, field, meeting)
    list_query = urlencode(_mobile_list_query(q, field, meeting))
    filters_active = bool((q or "").strip() or (field or "").strip() or (meeting or "").strip())
    return templates.TemplateResponse(
        request=request,
        name="contacts/list.html",
        context={
            "contacts": contacts,
            "q": q,
            "field_filter": field,
            "meeting_filter": meeting,
            "filters": filters,
            "filters_active": filters_active,
            "list_query": list_query,
            "list_url": list_url,
            "owner_email": email,
            "needs_google_refresh": _needs_google_contacts_refresh(request),
            **mobile_access_context(request),
        },
    )


@router.post("/refresh-from-google")
def contacts_refresh_from_google(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    prefer_cached = request.headers.get("x-mobile-sync") == "1"
    with user_db_write_lock(email):
        mark_user_db_writer(email)
        try:
            result = refresh_contacts_from_google(
                include_app_fields=False,
                upload=False,
                prefer_cached=prefer_cached,
            )
            if result.get("ok"):
                _push_local_db_to_cloud(email)
        finally:
            clear_user_db_writer(email)
    if not result.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "error": str(result.get("error") or "Could not download contacts from Google."),
            },
            status_code=500,
        )
    download_done = result.get("done", True)
    if download_done:
        _mark_google_contacts_refreshed(request)
    download_changed = int(result.get("changed_count") or 0)
    return JSONResponse(
        {
            "ok": True,
            "downloaded": download_changed,
            "done": bool(download_done),
            "total": int(result.get("total_available") or 0),
            "message": mobile_sync_message(result),
        }
    )


@router.get("/download-status")
def contacts_download_status(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    from app.database import fetch_one

    count_row = fetch_one("SELECT COUNT(*) AS count FROM contacts")
    state = fetch_one(
        """
        SELECT
          signin_sync_in_progress,
          signin_sync_phase,
          signin_sync_processed_count,
          signin_sync_total_count,
          last_import_at
        FROM google_sync_state
        WHERE id = 1
        """
    )
    downloaded = int(count_row["count"] or 0) if count_row else 0
    total = int((state["signin_sync_total_count"] if state else 0) or 0)
    in_progress = people_import_in_progress()
    last_import_at = str(state["last_import_at"] or "") if state else ""
    return JSONResponse(
        {
            "ok": True,
            "downloaded": downloaded,
            "total": total,
            "in_progress": in_progress,
            "done": bool(last_import_at) and not in_progress,
        }
    )


@router.post("/assignment-options")
async def mobile_add_assignment_option(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"error": "Not signed in."}, status_code=401)
    use_user_database(email)
    if not mobile_can_open_contact_edit(request):
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    parent_value = str(form.get("parent_value") or form.get("field_name") or "")
    try:
        saved_value = add_contact_assignment_option(kind, value, parent_value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"value": saved_value})


@router.post("/assignment-options/remove")
async def mobile_remove_assignment_option(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"error": "Not signed in."}, status_code=401)
    use_user_database(email)
    if not mobile_can_open_contact_edit(request):
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    try:
        removed_value = delete_contact_assignment_option(kind, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"value": removed_value})


@router.post("/assignment-options/rename")
async def mobile_rename_assignment_option(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"error": "Not signed in."}, status_code=401)
    use_user_database(email)
    if not mobile_can_open_contact_edit(request):
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    old_value = str(form.get("old_value") or "")
    new_value = str(form.get("new_value") or "")
    try:
        result = rename_contact_assignment_option(kind, old_value, new_value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result)


def _contact_needs_drive_app_fields_sync(contact: dict) -> bool:
    if int(contact.get("photo_needs_export") or 0):
        return True
    if int(contact.get("shared_drive_revision") or 0) <= 0:
        return True
    if not str(contact.get("birthday") or "").strip():
        return True
    if not str(contact.get("mtg_home_elder_flag") or "").strip():
        return True
    if not str(contact.get("photo") or "").strip():
        return True
    return False


def _contact_drive_app_fields_payload(contact_id: int) -> dict:
    contact = get_contact_detail(contact_id)
    if not contact:
        return {"ok": False, "error": "not_found"}
    if not _contact_needs_drive_app_fields_sync(contact):
        return {
            "ok": True,
            "updated": False,
            "birthday": str(contact.get("birthday") or ""),
            "mtg_home_elder_flag": str(contact.get("mtg_home_elder_flag") or ""),
            "meets_in_state": str(contact.get("meets_in_state") or ""),
            "photo": str(contact.get("photo") or ""),
            "photo_large": str(contact.get("photo_large") or contact.get("photo") or ""),
        }
    updated = refresh_contact_app_fields_from_google_drive(contact_id)
    contact = get_contact_detail(contact_id)
    if not contact:
        return {"ok": False, "error": "not_found"}
    return {
        "ok": True,
        "updated": updated,
        "birthday": str(contact.get("birthday") or ""),
        "mtg_home_elder_flag": str(contact.get("mtg_home_elder_flag") or ""),
        "meets_in_state": str(contact.get("meets_in_state") or ""),
        "photo": str(contact.get("photo") or ""),
        "photo_large": str(contact.get("photo_large") or contact.get("photo") or ""),
    }


@router.post("/{contact_id}/drive-app-fields")
def contact_drive_app_fields_sync(request: Request, contact_id: int):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    payload = _contact_drive_app_fields_payload(contact_id)
    if not payload.get("ok"):
        return JSONResponse(payload, status_code=404)
    return JSONResponse(payload)


@router.get("/{contact_id}")
def contact_detail(
    request: Request,
    contact_id: int,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    contact = get_contact_detail(contact_id)
    if not contact:
        return RedirectResponse(url=_mobile_list_url(q, field, meeting), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="contacts/detail.html",
        context={
            "contact": contact,
            "list_url": _mobile_list_url(q, field, meeting),
            "edit_url": _mobile_contact_url(contact_id, "/edit", q, field, meeting),
            "needs_drive_app_fields_sync": _contact_needs_drive_app_fields_sync(contact),
            "owner_email": email,
            **mobile_access_context(request),
        },
    )


@router.get("/{contact_id}/edit")
def contact_edit_form(
    request: Request,
    contact_id: int,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    if not mobile_can_open_contact_edit(request):
        return RedirectResponse(
            url=_mobile_contact_url(contact_id, "", q, field, meeting, {"notice": "edit_blocked"}),
            status_code=303,
        )
    contact = get_contact_detail(contact_id)
    if not contact:
        return RedirectResponse(url=_mobile_list_url(q, field, meeting), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="contacts/edit.html",
        context={
            "contact": contact,
            "filters": get_contact_filter_options(),
            "list_url": _mobile_list_url(q, field, meeting),
            "detail_url": _mobile_contact_url(contact_id, "", q, field, meeting),
            "edit_action_url": _mobile_contact_url(contact_id, "/edit", q, field, meeting),
            "remove_photo_action_url": _mobile_contact_url(contact_id, "/photo/remove", q, field, meeting),
            "owner_email": email,
            **mobile_access_context(request),
        },
    )



@router.post("/{contact_id}/photo/remove")
async def contact_photo_remove(
    request: Request,
    contact_id: int,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    if not mobile_can_open_contact_edit(request):
        return RedirectResponse(
            url=_mobile_contact_url(contact_id, "", q, field, meeting, {"notice": "edit_blocked"}),
            status_code=303,
        )
    records_pending = mobile_records_edits_to_pending_list(request)
    existing = get_contact_detail(contact_id)
    if not existing:
        return RedirectResponse(url=_mobile_list_url(q, field, meeting), status_code=303)
    contact_name = f"{existing.get('family_name') or ''}, {existing.get('given_name') or ''}".strip(", ").strip()
    notice_key = remove_contact_photo(
        contact_id,
        queue_google_sync=not records_pending,
        delete_from_google=not records_pending,
    )
    if notice_key in {"contact_photo_remove_success", "contact_photo_remove_local_only"} and records_pending:
        from app.services.google_sync_service import mark_contact_for_drive_export

        mark_contact_for_drive_export(contact_id)
        initials = get_current_changes_list_initials()
        edit_entries = get_latest_contact_edit_log_entries(contact_id)
        record_contact_edits_to_pending_list(
            contact_name=contact_name or f"Contact {contact_id}",
            edit_entries=edit_entries,
            initials=initials,
        )
    notice_map = {
        "contact_photo_remove_success": "photo_removed",
        "contact_photo_remove_local_only": "photo_removed_local",
        "contact_photo_remove_already_clear": "photo_already_clear",
        "contact_photo_remove_failed": "photo_error",
    }
    return RedirectResponse(
        url=_mobile_contact_url(
            contact_id,
            "/edit",
            q,
            field,
            meeting,
            {
                "notice": notice_map.get(notice_key, "photo_error"),
                **({"error": "Could not remove photo."} if notice_key == "contact_photo_remove_failed" else {}),
            },
        ),
        status_code=303,
    )


@router.post("/{contact_id}/edit")
async def contact_edit_save(
    request: Request,
    contact_id: int,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    if not mobile_can_open_contact_edit(request):
        return RedirectResponse(
            url=_mobile_contact_url(contact_id, "", q, field, meeting, {"notice": "edit_blocked"}),
            status_code=303,
        )
    form = await request.form()
    records_pending = mobile_records_edits_to_pending_list(request)
    existing = get_contact_detail(contact_id)
    if not existing:
        return RedirectResponse(url=_mobile_list_url(q, field, meeting), status_code=303)
    contact_name = f"{existing.get('family_name') or ''}, {existing.get('given_name') or ''}".strip(", ").strip()
    try:
        uploaded_photo_url = await _save_uploaded_contact_photo(form)
    except HTTPException as exc:
        return RedirectResponse(
            url=_mobile_contact_url(
                contact_id,
                "/edit",
                q,
                field,
                meeting,
                {"notice": "photo_error", "error": str(exc.detail or "Could not save photo.")[:180]},
            ),
            status_code=303,
        )
    saved = update_contact(
        contact_id,
        _build_contact_payload(form, uploaded_photo_url),
        queue_google_sync=not records_pending,
    )
    if not saved:
        return RedirectResponse(url=_mobile_list_url(q, field, meeting), status_code=303)
    if records_pending:
        from app.services.google_sync_service import mark_contact_for_drive_export

        mark_contact_for_drive_export(contact_id)
        initials = get_current_changes_list_initials()
        edit_entries = get_latest_contact_edit_log_entries(contact_id)
        pending_entry = record_contact_edits_to_pending_list(
            contact_name=contact_name or f"Contact {contact_id}",
            edit_entries=edit_entries,
            initials=initials,
        )
        notice = "pending_edits_recorded" if pending_entry else "saved"
    else:
        notice = "saved"
    return RedirectResponse(
        url=_mobile_list_url(q, field, meeting, {"notice": notice}),
        status_code=303,
    )


@router.post("/sync")
def contacts_sync(request: Request):
    email = _owner_email(request)
    if not email:
        if request.headers.get("X-Mobile-Sync") == "1":
            return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    wants_json = request.headers.get("X-Mobile-Sync") == "1"
    if not mobile_is_editor(request):
        if wants_json:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Only an Editor can sync contacts to Google. Use Pending Edits to submit changes.",
                },
                status_code=403,
            )
        return RedirectResponse(url="/m/contacts/?notice=sync_blocked", status_code=303)
    if mobile_records_edits_to_pending_list(request):
        if wants_json:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Pending Edits sign-in does not upload to Google Contacts. Your edits belong on the Pending Edits list.",
                },
                status_code=403,
            )
        return RedirectResponse(url="/m/pending-edits/?notice=sync_blocked", status_code=303)
    upload_changes = True
    try:
        with user_db_write_lock(email):
            mark_user_db_writer(email)
            try:
                if people_import_in_progress() or not has_cached_contacts():
                    result = refresh_contacts_from_google(
                        include_app_fields=False,
                        upload=False,
                        prefer_cached=True,
                    )
                else:
                    result = sync_contact_changes(upload=upload_changes)
            finally:
                clear_user_db_writer(email)
    except Exception as exc:
        error_message = str(exc).strip() or "Sync failed."
        if wants_json:
            return JSONResponse(
                {"ok": False, "error": error_message[:240]},
                status_code=500,
            )
        return RedirectResponse(
            url=f"/m/contacts/?{urlencode({'notice': 'sync_failed', 'error': error_message[:240]})}",
            status_code=303,
        )
    upload_count = int(result.get("contact_upload_count") or 0)
    download_changed = int(result.get("changed_count") or 0)
    download_done = result.get("done", True)
    if wants_json and download_done:
        _mark_google_contacts_refreshed(request)
    message = mobile_sync_message(result)
    if wants_json:
        return JSONResponse(
            {
                "ok": True,
                "uploaded": upload_count,
                "downloaded": download_changed,
                "done": bool(download_done),
                "total": int(result.get("total_available") or 0),
                "message": message,
            }
        )
    params: dict[str, str] = {"notice": "synced", "message": message}
    if upload_count > 0:
        params["uploaded"] = str(upload_count)
    if download_changed > 0:
        params["downloaded"] = str(download_changed)
    return RedirectResponse(url=f"/m/contacts/?{urlencode(params)}", status_code=303)


@router.post("/upload")
def contacts_upload(request: Request):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    if not mobile_is_editor(request):
        return RedirectResponse(url="/m/contacts/?notice=sync_blocked", status_code=303)
    upload_contacts_only()
    return RedirectResponse(url="/m/contacts/?notice=uploaded", status_code=303)
