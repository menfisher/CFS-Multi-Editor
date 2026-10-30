import base64
import binascii
import os
import platform
import re
import subprocess
import traceback
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import UPLOADS_DIR
from app.services.backup_service import build_contacts_backup_bytes
from app.services.contact_export_service import build_contacts_csv_bytes, build_contacts_vcard_bytes, build_google_contacts_csv_bytes
from app.services.meeting_toc_service import list_unlisted_toc_meetings, set_meetings_unlisted_in_toc
from app.services.preset_service import get_shared_contacts_group_name
from app.services.contact_service import (
    add_contact_assignment_option,
    build_contact_group_membership,
    build_empty_contact,
    create_contact,
    delete_contact,
    delete_contact_assignment_option,
    delete_contacts,
    get_contacts_by_ids,
    get_contact_detail,
    list_contact_edit_batches,
    get_contact_filter_options,
    get_contact_row_colors,
    get_pending_photo_change_for_contact,
    is_assignment_menu_sentinel,
    is_deleted_contact,
    is_moved_contact,
    list_contacts,
    move_contacts,
    retain_known_state_filters,
    permanently_delete_contact,
    permanently_delete_contacts,
    rename_contact_assignment_option,
    apply_pending_photo_to_contact,
    remove_contact_photo,
    update_contact,
)
from app.services.google_sync_service import (
    clear_pending_contact_sync,
    contact_has_pending_google_sync,
    export_backup_file_to_google_drive,
    export_contacts_file_to_google_drive,
    export_shared_data_after_edit,
    get_edit_block_notice_key,
    load_contact_photo_bytes,
    get_google_sync_summary,
    get_notice_message,
    is_editing_allowed,
    update_google_contact_photo,
)
from urllib.error import HTTPError, URLError
from starlette.datastructures import UploadFile

from app.web_templates import templates


router = APIRouter(prefix="/contacts", tags=["contacts"])
CONTACT_PHOTO_UPLOAD_DIR = UPLOADS_DIR / "contact_photos"
CONTACT_PHOTO_ALLOWED_EXTENSIONS = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}
CONTACT_PHOTO_MAX_BYTES = 8 * 1024 * 1024
MESSAGES_APPLESCRIPT_TIMEOUT_SECONDS = 35
MESSAGES_SEND_APPLESCRIPT = """
on digitsOnly(rawText)
  set cleanedText to ""
  set digitCharacters to "0123456789"
  repeat with characterIndex from 1 to count characters of rawText
    set currentCharacter to character characterIndex of rawText
    if currentCharacter is in digitCharacters then set cleanedText to cleanedText & currentCharacter
  end repeat
  return cleanedText
end digitsOnly

on phoneDigitsMatch(leftDigits, rightDigits)
  if leftDigits is "" or rightDigits is "" then return false
  if leftDigits is rightDigits then return true
  if leftDigits is ("1" & rightDigits) then return true
  if rightDigits is ("1" & leftDigits) then return true
  if (count characters of leftDigits) is 11 and character 1 of leftDigits is "1" and text 2 thru -1 of leftDigits is rightDigits then return true
  if (count characters of rightDigits) is 11 and character 1 of rightDigits is "1" and text 2 thru -1 of rightDigits is leftDigits then return true
  return false
end phoneDigitsMatch

on run argv
  set targetNumber to item 1 of argv
  set targetMessage to item 2 of argv
  tell application "Messages"
    set targetDigits to item 3 of argv
    set smsServices to services whose service type = SMS
    if (count of smsServices) is greater than 0 then
      set smsService to item 1 of smsServices
      set targetBuddy to buddy targetNumber of smsService
      send targetMessage to targetBuddy
      return "SMS"
    end if
    set imessageSucceeded to false
    set imessageBuddy to missing value
    set imessageServices to services whose service type = iMessage
    repeat with imessageService in imessageServices
      try
        set matchingBuddies to buddies of imessageService whose handle is targetNumber
        if (count of matchingBuddies) is greater than 0 then
          set imessageBuddy to item 1 of matchingBuddies
          exit repeat
        end if
        repeat with candidateBuddy in buddies of imessageService
          set candidateHandle to handle of candidateBuddy
          set candidateDigits to my digitsOnly(candidateHandle as text)
          if my phoneDigitsMatch(candidateDigits, targetDigits) then
            set imessageBuddy to candidateBuddy
            exit repeat
          end if
        end repeat
        if imessageBuddy is not missing value then exit repeat
      end try
    end repeat
    if imessageBuddy is not missing value then
      try
        send targetMessage to imessageBuddy
        set imessageSucceeded to true
      end try
    end if
    if imessageSucceeded then return "iMessage"
    error "Messages does not have an SMS service connected and no iMessage buddy matched this number. Pair this Mac with a phone/carrier connection first."
  end tell
end run
"""


def _log_contacts_route(message: str) -> None:
    return None


def _build_contact_export_payload(file_kind: str) -> tuple[str, bytes, str]:
    normalized_kind = str(file_kind or "").strip().lower()
    if normalized_kind == "csv":
        filename, content = build_contacts_csv_bytes()
        return filename, content, "text/csv; charset=utf-8"
    if normalized_kind == "google_csv":
        filename, content = build_google_contacts_csv_bytes()
        return filename, content, "text/csv; charset=utf-8"
    if normalized_kind == "vcard":
        filename, content = build_contacts_vcard_bytes()
        return filename, content, "text/vcard; charset=utf-8"
    raise HTTPException(status_code=404, detail="Unsupported export format")


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


def _build_contact_payload(form, uploaded_photo_url: str = "") -> dict:
    return {
        "selected": str(form.get("selected") or ""),
        "google_contact_id": str(form.get("google_contact_id") or ""),
        "etag": str(form.get("etag") or ""),
        "last_updated": str(form.get("last_updated") or ""),
        "status": str(form.get("status") or ""),
        "do_not_print": str(form.get("do_not_print") or ""),
        "fields_text": "" if is_assignment_menu_sentinel(form.get("fields_text")) else str(form.get("fields_text") or ""),
        "meetings_text": "" if is_assignment_menu_sentinel(form.get("meetings_text")) else str(form.get("meetings_text") or ""),
        "group_membership": str(form.get("group_membership") or ""),
        "family_name": str(form.get("family_name") or ""),
        "given_name": str(form.get("given_name") or ""),
        "photo": uploaded_photo_url or str(form.get("photo") or ""),
        "birthday": _normalize_birthday_input(form.get("birthday")),
        "notes": str(form.get("notes") or ""),
        "mtg_home_elder_flag": str(form.get("mtg_home_elder_flag") or ""),
        "meets_in_state": str(form.get("meets_in_state") or ""),
        "relationship_type": form.getlist("relationship_type"),
        "relationship_value": form.getlist("relationship_value"),
        "phone_type": form.getlist("phone_type"),
        "phone_value": form.getlist("phone_value"),
        "email_type": form.getlist("email_type"),
        "email_value": form.getlist("email_value"),
        "address_type": form.getlist("address_type"),
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


async def _save_uploaded_contact_photo(form) -> str:
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
            if len(content) > CONTACT_PHOTO_MAX_BYTES:
                raise HTTPException(status_code=400, detail="Contact photo upload must be 8 MB or smaller.")
            CONTACT_PHOTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid4().hex}{extension}"
            destination = CONTACT_PHOTO_UPLOAD_DIR / filename
            destination.write_bytes(content)
            return f"/static/uploads/contact_photos/{filename}"

    upload = form.get("contact_photo_upload")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return ""

    content_type = str(upload.content_type or "")
    if content_type and not content_type.lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="Contact photo upload must be an image file.")

    source_name = Path(str(upload.filename)).name
    extension = Path(source_name).suffix.lower()
    if extension not in CONTACT_PHOTO_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Contact photo upload must be JPG, PNG, GIF, or WebP.")

    content = await upload.read()
    if not content:
        return ""
    if len(content) > CONTACT_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Contact photo upload must be 8 MB or smaller.")

    CONTACT_PHOTO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    destination = CONTACT_PHOTO_UPLOAD_DIR / filename
    destination.write_bytes(content)
    return f"/static/uploads/contact_photos/{filename}"


def _uploaded_contact_photo_bytes(photo_url: str) -> bytes:
    normalized_url = str(photo_url or "").strip()
    prefix = "/static/uploads/contact_photos/"
    if not normalized_url.startswith(prefix):
        return b""
    filename = Path(normalized_url).name
    if not filename:
        return b""
    photo_path = CONTACT_PHOTO_UPLOAD_DIR / filename
    if not photo_path.is_file():
        return b""
    return photo_path.read_bytes()


def _contact_edit_redirect(contact_id: int, return_to: str, google_notice: str) -> RedirectResponse:
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    encoded_return_to = quote(safe_return_to, safe="/?=&")
    return RedirectResponse(
        url=f"/contacts/{contact_id}/edit?return_to={encoded_return_to}&google_notice={google_notice}",
        status_code=303,
    )


def _no_edit_redirect(return_to: str = "/contacts/") -> RedirectResponse:
    safe_return_to = return_to if str(return_to or "").startswith("/contacts") else "/contacts/"
    separator = "&" if "?" in safe_return_to else "?"
    return RedirectResponse(url=f"{safe_return_to}{separator}google_notice={get_edit_block_notice_key()}", status_code=303)


def _normalize_sms_number(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if 7 <= len(digits) <= 15:
        return digits
    return ""


def _send_macos_message(number: str, message: str) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("macOS Messages automation is only available on a Mac.")
    digits = re.sub(r"\D+", "", str(number or ""))
    completed = subprocess.run(
        ["osascript", "-e", MESSAGES_SEND_APPLESCRIPT, number, message, digits],
        capture_output=True,
        text=True,
        timeout=MESSAGES_APPLESCRIPT_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        error_text = (completed.stderr or completed.stdout or "Messages could not send this message.").strip()
        raise RuntimeError(error_text)


def _open_windows_message(number: str, message: str) -> None:
    if platform.system() != "Windows":
        raise RuntimeError("Windows Phone Link messaging is only available on Windows.")
    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        raise RuntimeError("Windows messaging launcher is not available on this computer.")
    uri = f"ms-chat:?Addresses={quote(number, safe='')}&Body={quote(message, safe='')}"
    try:
        startfile(uri)
    except OSError as exc:
        raise RuntimeError(
            "Windows could not open the Messaging app. Pair a phone in Phone Link and make sure Messages are enabled."
        ) from exc


def _send_or_open_message(number: str, message: str) -> str:
    system_name = platform.system()
    if system_name == "Darwin":
        _send_macos_message(number, message)
        return "sent"
    if system_name == "Windows":
        _open_windows_message(number, message)
        return "opened"
    raise RuntimeError("Contact messaging is available on macOS Messages or Windows Phone Link only.")


@router.get("/", response_class=HTMLResponse)
def contacts_list(
    request: Request,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
    state: list[str] = Query(default=[]),
    google_notice: str = Query(default=""),
    google_sync_changed: str = Query(default=""),
):
    try:
        _log_contacts_route("start")
        _log_contacts_route("loading google sync summary")
        google_sync = get_google_sync_summary()
        _log_contacts_route("loading filters")
        filters = get_contact_filter_options()
        available_states = filters.get("states") or []
        available_state_keys = {str(item or "").strip().casefold() for item in available_states if str(item or "").strip()}
        state_filters = retain_known_state_filters(state, available_states)
        current_list_url = str(request.url.path)
        filtered_query_items = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key not in {"google_notice", "google_sync_changed"}
            and not (key == "state" and str(value or "").strip().casefold() not in available_state_keys)
        ]
        if filtered_query_items:
            current_list_url = f"{current_list_url}?{urlencode(filtered_query_items)}"
        _log_contacts_route("loading contacts")
        contacts = list_contacts(q, field, meeting, state_filters)
        _log_contacts_route(f"loaded contacts count={len(contacts)}")
        meeting_name = str(meeting or "").strip()
        meeting_roster_count = 0
        if (
            meeting_name
            and meeting_name.casefold() != "deleted"
            and str(field or "").strip().casefold() != "deleted"
            and not is_assignment_menu_sentinel(meeting_name)
            and not is_assignment_menu_sentinel(field)
        ):
            meeting_roster_count = len(list_contacts("", field, meeting_name))
        row_colors = get_contact_row_colors()
        _log_contacts_route("loaded row colors")
        response = templates.TemplateResponse(
            request=request,
            name="contacts/list.html",
            context={
                "contacts": contacts,
                "current_list_url": current_list_url,
                "filters": filters,
                "google_notice_message": get_notice_message(
                    google_notice,
                    count=google_sync_changed or "0",
                ),
                "google_sync": google_sync,
                "row_colors": row_colors,
                "query": q,
                "field_filter": field,
                "meeting_filter": meeting,
                "meeting_roster_count": meeting_roster_count,
                "unlisted_toc_meetings": list_unlisted_toc_meetings(),
                "state_filters": state_filters,
            },
            headers={"Cache-Control": "no-store"},
        )
        _log_contacts_route("template response created")
        return response
    except BaseException as exc:
        _log_contacts_route("failed")
        _log_contacts_route("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise


@router.get("/export/{file_kind}")
def contacts_export_download(file_kind: str):
    filename, content, media_type = _build_contact_export_payload(file_kind)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/{file_kind}/drive")
def contacts_export_drive(file_kind: str):
    filename, content, media_type = _build_contact_export_payload(file_kind)
    try:
        export_contacts_file_to_google_drive(filename, content, media_type.split(";", 1)[0])
    except RuntimeError as exc:
        message = str(exc)
        if "Drive access has not been granted" in message:
            notice = "drive_scope_missing"
        elif "not connected" in message.lower() or "refresh token" in message.lower():
            notice = "drive_not_connected"
        else:
            notice = "contacts_export_drive_failed"
        return RedirectResponse(url=f"/contacts/?google_notice={notice}", status_code=303)
    except (HTTPError, URLError):
        return RedirectResponse(url="/contacts/?google_notice=contacts_export_drive_failed", status_code=303)
    return RedirectResponse(url="/contacts/?google_notice=contacts_export_drive_success", status_code=303)


@router.get("/backup/json")
def contacts_backup_download():
    filename, content = build_contacts_backup_bytes()
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/backup/json/drive")
def contacts_backup_drive():
    filename, content = build_contacts_backup_bytes()
    try:
        export_backup_file_to_google_drive(filename, content, "application/json")
    except RuntimeError as exc:
        message = str(exc)
        if "Drive access has not been granted" in message:
            notice = "drive_scope_missing"
        elif "not connected" in message.lower() or "refresh token" in message.lower():
            notice = "drive_not_connected"
        else:
            notice = "contacts_backup_drive_failed"
        return RedirectResponse(url=f"/contacts/?google_notice={notice}", status_code=303)
    except (HTTPError, URLError):
        return RedirectResponse(url="/contacts/?google_notice=contacts_backup_drive_failed", status_code=303)
    return RedirectResponse(url="/contacts/?google_notice=contacts_backup_drive_success", status_code=303)


@router.post("/message/send")
async def contacts_message_send(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "Message request was not valid."}, status_code=400)

    message = str(payload.get("message") or "").strip()
    raw_recipients = payload.get("recipients")
    if not message:
        return JSONResponse({"ok": False, "error": "Message text is required."}, status_code=400)
    if not isinstance(raw_recipients, list) or not raw_recipients:
        return JSONResponse({"ok": False, "error": "Choose at least one Mobile or Cell number."}, status_code=400)

    recipients = []
    for item in raw_recipients:
        if not isinstance(item, dict):
            continue
        number = _normalize_sms_number(str(item.get("digits") or item.get("number") or ""))
        if not number:
            continue
        recipients.append(
            {
                "name": str(item.get("name") or "Contact").strip() or "Contact",
                "number": number,
                "display_number": str(item.get("number") or number).strip() or number,
            }
        )

    if not recipients:
        return JSONResponse({"ok": False, "error": "No valid Mobile or Cell numbers were provided."}, status_code=400)
    if len(recipients) > 100:
        return JSONResponse({"ok": False, "error": "Send 100 messages or fewer at one time."}, status_code=400)

    results = []
    sent_count = 0
    opened_count = 0
    for recipient in recipients:
        try:
            delivery_status = _send_or_open_message(recipient["number"], message)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            results.append(
                {
                    "ok": False,
                    "name": recipient["name"],
                    "number": recipient["display_number"],
                    "error": str(exc),
                }
            )
            continue
        if delivery_status == "opened":
            opened_count += 1
        else:
            sent_count += 1
        results.append(
            {
                "ok": True,
                "name": recipient["name"],
                "number": recipient["display_number"],
                "status": delivery_status,
            }
        )

    success_count = sent_count + opened_count
    failed_count = len(results) - success_count
    return JSONResponse(
        {
            "ok": failed_count == 0,
            "sent_count": sent_count,
            "opened_count": opened_count,
            "failed_count": failed_count,
            "delivery_mode": "opened" if opened_count and not sent_count else "sent",
            "results": results,
            "error": "Some messages could not be sent or opened." if failed_count else "",
        }
    )


@router.get("/new", response_class=HTMLResponse)
def contact_new_form(
    request: Request,
    return_to: str = Query(default="/contacts/"),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    return templates.TemplateResponse(
        request=request,
        name="contacts/edit.html",
        context={
            "contact": build_empty_contact(),
            "filters": get_contact_filter_options(),
            "restore_mode": False,
            "return_to": safe_return_to,
            "row_colors": get_contact_row_colors(),
            "is_new_contact": True,
            "shared_contacts_group_name": get_shared_contacts_group_name(),
            "google_sync": get_google_sync_summary(),
        },
    )


@router.get("/edit-list", response_class=HTMLResponse)
def contact_edit_list(
    request: Request,
):
    try:
        _log_contacts_route("edit list start")
        edit_batches = list_contact_edit_batches()
        _log_contacts_route(f"edit list loaded batches count={len(edit_batches)}")
        row_colors = get_contact_row_colors()
        _log_contacts_route("edit list loaded row colors")
        response = templates.TemplateResponse(
            request=request,
            name="contacts/edit_list.html",
            context={
                "edit_batches": edit_batches,
                "row_colors": row_colors,
            },
        )
        _log_contacts_route("edit list template response created")
        return response
    except BaseException as exc:
        _log_contacts_route("edit list failed")
        _log_contacts_route("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise


@router.post("/new")
async def contact_new_save(
    request: Request,
):
    form = await request.form()
    return_to = str(form.get("return_to") or "/contacts/")
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)
    uploaded_photo_url = await _save_uploaded_contact_photo(form)
    payload = _build_contact_payload(form, uploaded_photo_url)
    payload["group_membership"] = build_contact_group_membership(payload.get("fields_text", ""), payload.get("meetings_text", ""))
    contact_id = create_contact(payload)
    encoded_return_to = quote(safe_return_to, safe="/?=&")
    return RedirectResponse(url=f"/contacts/{contact_id}?return_to={encoded_return_to}", status_code=303)


@router.get("/{contact_id}", response_class=HTMLResponse)
def contact_detail(
    request: Request,
    contact_id: int,
    return_to: str = Query(default="/contacts/"),
):
    contact = get_contact_detail(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"

    return templates.TemplateResponse(
        request=request,
        name="contacts/detail.html",
        context={
            "contact": contact,
            "is_deleted_contact": is_deleted_contact(contact),
            "is_moved_contact": is_moved_contact(contact),
            "return_to": safe_return_to,
            "row_colors": get_contact_row_colors(),
            "google_sync": get_google_sync_summary(),
        },
    )


@router.get("/{contact_id}/edit", response_class=HTMLResponse)
def contact_edit_form(
    request: Request,
    contact_id: int,
    return_to: str = Query(default="/contacts/"),
    restore: int = Query(default=0),
    google_notice: str = Query(default=""),
):
    contact = get_contact_detail(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)

    pending_photo_change = get_pending_photo_change_for_contact(contact)
    # Prefer the Pending Edits photo button when that workflow is active; otherwise
    # highlight Google upload only when this contact still has a queued Google sync.
    photo_google_upload_needed = (
        not pending_photo_change
        and bool(str(contact.get("photo") or "").strip())
        and bool(str(contact.get("google_contact_id") or "").strip())
        and contact_has_pending_google_sync(
            contact_id,
            str(contact.get("google_contact_id") or ""),
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="contacts/edit.html",
        context={
            "contact": contact,
            "filters": get_contact_filter_options(),
            "restore_mode": bool(restore) or is_deleted_contact(contact) or is_moved_contact(contact),
            "return_to": safe_return_to,
            "row_colors": get_contact_row_colors(),
            "is_new_contact": False,
            "shared_contacts_group_name": get_shared_contacts_group_name(),
            "google_notice_message": get_notice_message(google_notice),
            "google_sync": get_google_sync_summary(),
            "pending_photo_change": pending_photo_change,
            "photo_google_upload_needed": photo_google_upload_needed,
        },
    )


@router.post("/{contact_id}/edit")
async def contact_edit_save(
    request: Request,
    contact_id: int,
):
    form = await request.form()
    return_to = str(form.get("return_to") or "/contacts/")
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)
    uploaded_photo_url = await _save_uploaded_contact_photo(form)

    saved = update_contact(contact_id, _build_contact_payload(form, uploaded_photo_url))
    if not saved:
        raise HTTPException(status_code=404, detail="Contact not found")

    separator = "&" if "?" in safe_return_to else "?"
    return RedirectResponse(url=f"{safe_return_to}{separator}google_notice=contact_saved", status_code=303)


@router.post("/{contact_id}/photo/google")
async def contact_photo_google_upload(
    request: Request,
    contact_id: int,
):
    form = await request.form()
    return_to = str(form.get("return_to") or "/contacts/")
    if not is_editing_allowed():
        return _no_edit_redirect(return_to)
    uploaded_photo_url = await _save_uploaded_contact_photo(form)
    saved = update_contact(contact_id, _build_contact_payload(form, uploaded_photo_url))
    if not saved:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact = get_contact_detail(contact_id)
    if not contact or not str(contact.get("google_contact_id") or "").strip():
        return _contact_edit_redirect(contact_id, return_to, "contact_photo_google_not_connected")

    # Prefer a freshly chosen local file; otherwise use any saved CFS/local/remote photo
    # still reachable as bytes (Google URL photos used to fail silently here).
    photo_bytes = _uploaded_contact_photo_bytes(str(contact.get("photo") or ""))
    if not photo_bytes:
        photo_bytes = load_contact_photo_bytes(str(contact.get("photo") or ""))
    if not photo_bytes:
        return _contact_edit_redirect(contact_id, return_to, "contact_photo_google_missing")

    google_contact_id = str(contact.get("google_contact_id") or "").strip()
    try:
        update_google_contact_photo(google_contact_id, photo_bytes)
    except (RuntimeError, HTTPError, URLError):
        return _contact_edit_redirect(contact_id, return_to, "contact_photo_google_failed")

    clear_pending_contact_sync(contact_id, google_contact_id)
    account_email = str((get_google_sync_summary().get("account") or {}).get("email") or "").strip()
    if account_email and google_contact_id:
        from app.services.google_sync_service import record_share_sync_result
        from app.services.share_sync_service import sync_share_app_contact_changes

        try:
            share_result = sync_share_app_contact_changes(account_email, [google_contact_id])
        except Exception as exc:
            share_result = {"ok": False, "reason": str(exc).strip() or "share_sync_failed"}
        record_share_sync_result(account_email, [google_contact_id], share_result)
    return _contact_edit_redirect(contact_id, return_to, "contact_photo_google_success")



@router.post("/{contact_id}/photo/remove")
async def contact_photo_remove(
    request: Request,
    contact_id: int,
):
    form = await request.form()
    return_to = str(form.get("return_to") or "/contacts/")
    if not is_editing_allowed():
        return _no_edit_redirect(return_to)
    notice = remove_contact_photo(contact_id, queue_google_sync=True, delete_from_google=True)
    if notice == "contact_photo_remove_success":
        contact = get_contact_detail(contact_id)
        if contact and str(contact.get("google_contact_id") or "").strip():
            clear_pending_contact_sync(contact_id, str(contact.get("google_contact_id") or ""))
    return _contact_edit_redirect(contact_id, return_to, notice)


@router.post("/{contact_id}/photo/pending")
async def contact_photo_pending_apply(
    request: Request,
    contact_id: int,
):
    form = await request.form()
    return_to = str(form.get("return_to") or "/contacts/")
    if not is_editing_allowed():
        return _no_edit_redirect(return_to)
    notice = apply_pending_photo_to_contact(contact_id)
    return _contact_edit_redirect(contact_id, return_to, notice)


@router.post("/{contact_id}/delete")
def contact_delete(
    contact_id: int,
    return_to: str = Form(default="/contacts/"),
    permanent_delete: str = Form(default=""),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)
    contact = get_contact_detail(contact_id)
    permanently = str(permanent_delete or "").lower() in {"1", "true", "yes", "on"} or is_deleted_contact(contact)
    deleted = permanently_delete_contact(contact_id) if permanently else delete_contact(contact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Contact not found")
    return RedirectResponse(url=safe_return_to, status_code=303)


@router.post("/delete-selected")
def contacts_delete_selected(
    selected_contact_ids: list[int] = Form(default=[]),
    return_to: str = Form(default="/contacts/"),
    permanent_delete: str = Form(default=""),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)
    permanently = str(permanent_delete or "").lower() in {"1", "true", "yes", "on"}
    deleted_count = permanently_delete_contacts(selected_contact_ids) if permanently else delete_contacts(selected_contact_ids)
    if deleted_count <= 0:
        raise HTTPException(status_code=404, detail="No contacts selected")
    return RedirectResponse(url=safe_return_to, status_code=303)


@router.post("/meeting-toc-listing")
async def contacts_meeting_toc_listing(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Choose a meeting first."}, status_code=400)
    meetings = payload.get("meetings")
    if not isinstance(meetings, list) or not meetings:
        return JSONResponse({"error": "Choose a meeting first."}, status_code=400)
    unlisted = bool(payload.get("unlisted"))
    saved = set_meetings_unlisted_in_toc(meetings, unlisted)
    try:
        from app.services.google_sync_service import mark_field_list_for_drive_export

        mark_field_list_for_drive_export()
    except Exception:
        pass
    return JSONResponse({"ok": True, "unlisted": unlisted, "meetings": saved})


@router.post("/assignment-options")
async def contacts_add_assignment_option(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    parent_value = str(form.get("parent_value") or form.get("field_name") or "")
    try:
        saved_value = add_contact_assignment_option(kind, value, parent_value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        export_shared_data_after_edit()
    except (RuntimeError, HTTPError, URLError):
        pass
    return JSONResponse({"value": saved_value})


@router.post("/assignment-options/remove")
async def contacts_remove_assignment_option(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    try:
        removed_value = delete_contact_assignment_option(kind, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        export_shared_data_after_edit()
    except (RuntimeError, HTTPError, URLError):
        pass
    return JSONResponse({"value": removed_value})


@router.post("/assignment-options/rename")
async def contacts_rename_assignment_option(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    old_value = str(form.get("old_value") or "")
    new_value = str(form.get("new_value") or "")
    try:
        result = rename_contact_assignment_option(kind, old_value, new_value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        export_shared_data_after_edit()
    except (RuntimeError, HTTPError, URLError):
        pass
    return JSONResponse(result)


@router.post("/move-selected", response_class=HTMLResponse)
def contacts_move_selected_form(
    request: Request,
    selected_contact_ids: list[int] = Form(default=[]),
    return_to: str = Form(default="/contacts/"),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)
    selected_contacts = get_contacts_by_ids(selected_contact_ids)
    if not selected_contacts:
        raise HTTPException(status_code=404, detail="No contacts selected")

    return templates.TemplateResponse(
        request=request,
        name="contacts/move.html",
        context={
            "selected_contacts": selected_contacts,
            "selected_contact_ids": [int(contact["id"]) for contact in selected_contacts],
            "selected_count": len(selected_contacts),
            "filters": get_contact_filter_options(),
            "return_to": safe_return_to,
            "row_colors": get_contact_row_colors(),
            "shared_contacts_group_name": get_shared_contacts_group_name(),
        },
    )


@router.post("/move-selected/apply")
def contacts_move_selected_apply(
    selected_contact_ids: list[int] = Form(default=[]),
    field_value: str = Form(default=""),
    meeting_value: str = Form(default=""),
    return_to: str = Form(default="/contacts/"),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return _no_edit_redirect(safe_return_to)
    moved_count = move_contacts(selected_contact_ids, field_value, meeting_value)
    if moved_count <= 0:
        raise HTTPException(status_code=404, detail="No contacts moved")
    return RedirectResponse(url=safe_return_to, status_code=303)
