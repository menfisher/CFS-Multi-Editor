from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.config import UPLOADS_DIR
from app.database import fetch_one
from app.services.address_book_pdf_service import (
    address_book_pdf_filename,
    build_address_book_pdf,
    order_address_book_fields,
)
from app.services.field_list_service import (
    get_address_book_settings,
    get_field_list_builder_context,
    save_address_book_settings,
)
from app.services.google_sync_service import (
    get_current_access_role,
    get_edit_block_notice_key,
    get_google_sync_summary,
    get_notice_message,
    is_editing_allowed,
    is_non_editor_access_role,
    mark_book_layouts_for_drive_export,
    mark_field_list_for_drive_export,
    upload_address_book_printed_pdf_to_google_drive,
    upload_address_book_pdf_to_google_drive,
)
from app.services.preset_service import get_book_layout_preset_editor, save_book_layout_preset, set_default_book_layout_preset
from app.web_templates import templates


router = APIRouter(prefix="/address-book", tags=["address-book"])
ADDRESS_BOOK_PDF_PREVIEW_DIR = UPLOADS_DIR / "address_book_pdfs"


def _signin_sync_redirect_if_blocked():
    state = (get_google_sync_summary().get("state") or {})
    if bool(state.get("signin_sync_in_progress") or 0):
        return RedirectResponse(url="/presets/google-connect?notice=signin_sync_blocked", status_code=303)
    if str(state.get("signin_sync_error") or "").strip():
        return RedirectResponse(url="/presets/google-connect?notice=signin_sync_failed_blocked", status_code=303)
    return None


def _address_book_pdf_preview_path(filename: str) -> Path:
    safe_name = Path(str(filename or "")).name
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"
    return ADDRESS_BOOK_PDF_PREVIEW_DIR / safe_name


def _address_book_pdf_can_save_to_drive() -> bool:
    return get_current_access_role() == "editor" and is_editing_allowed()


def _address_book_pdf_meeting_home_last_name(contacts: list[dict]) -> str:
    for contact in contacts:
        if str(contact.get("mtg_home_elder_flag") or "").strip().startswith("1"):
            family_name = str(contact.get("family_name") or "").strip()
            if family_name:
                return family_name.upper()
            name = str(contact.get("name") or contact.get("label") or "").strip()
            if "," in name:
                return name.split(",", 1)[0].strip().upper()
    return ""


def _address_book_pdf_fields_from_context(field_list_context: dict, *, preview_limit: int | None = None) -> list[dict]:
    picker_payload = json.loads(field_list_context["field_list_picker_json"])
    raw_fields = picker_payload.get("fields") if isinstance(picker_payload, dict) else []
    def meeting_payload(meeting: dict) -> dict:
        contacts = list(meeting.get("contacts", []))
        preview_contacts = contacts[:preview_limit] if preview_limit is not None else contacts
        return {
            "name": meeting.get("name", ""),
            "home_last_name": _address_book_pdf_meeting_home_last_name(contacts),
            "contacts": [
                _address_book_pdf_contact_preview(contact)
                for contact in preview_contacts
            ],
        }

    return [
        {
            "name": field.get("name", ""),
            "meetings": [meeting_payload(meeting) for meeting in field.get("meetings", [])],
        }
        for field in raw_fields
    ]


def _address_book_pdf_layout_from_form(form) -> dict:
    return {
        "name": form.get("name"),
        "book_title": form.get("book_title"),
        "trim_width_in": form.get("trim_width_in"),
        "trim_height_in": form.get("trim_height_in"),
        "margin_left_in": form.get("margin_left_in"),
        "margin_right_in": form.get("margin_right_in"),
        "margin_top_in": form.get("margin_top_in"),
        "margin_bottom_in": form.get("margin_bottom_in"),
        "font_family": form.get("font_family"),
        "base_font_size_pt": form.get("base_font_size_pt"),
        "title_font_size_pt": form.get("title_font_size_pt"),
        "toc_font_size_pt": form.get("toc_font_size_pt"),
        "line_height": form.get("line_height"),
        "meeting_table_column_count": form.get("meeting_table_column_count"),
        "meeting_table_column_gap_px": form.get("meeting_table_column_gap_px"),
        "screen_preview_scale": form.get("screen_preview_scale"),
        "meetingdata_title_bar_color": form.get("meetingdata_title_bar_color"),
        "meetingdata_primary_row_color": form.get("meetingdata_primary_row_color"),
        "meetingdata_secondary_row_color": form.get("meetingdata_secondary_row_color"),
        "meetingdata_highlight_row_color": form.get("meetingdata_highlight_row_color"),
        "contacts_title_bar_color": form.get("contacts_title_bar_color"),
        "contacts_primary_row_color": form.get("contacts_primary_row_color"),
        "contacts_secondary_row_color": form.get("contacts_secondary_row_color"),
        "contacts_highlight_row_color": form.get("contacts_highlight_row_color"),
        "is_default": form.get("is_default"),
    }


def _save_address_book_pdf_layout_preset(preset_id: int | None, values: dict) -> int:
    save_values = dict(values)
    next_name = str(save_values.get("name") or "").strip()
    existing_by_name = None
    if next_name:
        existing_by_name = fetch_one(
            """
            SELECT id, is_default
            FROM book_layout_presets
            WHERE name = ?
            """,
            (next_name,),
        )
    if not preset_id:
        if existing_by_name:
            save_values["is_default"] = int(existing_by_name["is_default"] or 0)
            return save_book_layout_preset(int(existing_by_name["id"]), save_values)
        save_values["is_default"] = 0
        return save_book_layout_preset(None, save_values)

    existing = fetch_one(
        """
        SELECT name, book_title, is_default
        FROM book_layout_presets
        WHERE id = ?
        """,
        (int(preset_id),),
    )
    if not existing:
        if existing_by_name:
            save_values["is_default"] = int(existing_by_name["is_default"] or 0)
            return save_book_layout_preset(int(existing_by_name["id"]), save_values)
        save_values["is_default"] = 0
        return save_book_layout_preset(None, save_values)

    next_title = str(save_values.get("book_title") or "").strip()
    existing_name = str(existing["name"] or "").strip()
    existing_title = str(existing["book_title"] or "").strip()
    if next_name != existing_name:
        if existing_by_name and int(existing_by_name["id"]) != int(preset_id):
            save_values["is_default"] = int(existing_by_name["is_default"] or 0)
            return save_book_layout_preset(int(existing_by_name["id"]), save_values)
        save_values["is_default"] = 0
        return save_book_layout_preset(None, save_values)

    save_values["is_default"] = int(existing["is_default"] or 0)
    return save_book_layout_preset(int(preset_id), save_values)


def _address_book_pdf_settings_from_form(form) -> dict:
    return {
        "address_map_provider": form.get("address_map_provider"),
        "address_italic": form.get("address_italic"),
        "address_align": form.get("address_align"),
        "print_order_mode": form.get("print_order_mode"),
        "print_order_json": form.get("print_order_json"),
        "meeting_page_mode": form.get("meeting_page_mode"),
        "selected_field_name": form.get("selected_field_name"),
        "selected_meeting_name": form.get("selected_meeting_name"),
    }


def _address_book_pdf_print_settings(form) -> dict:
    """Address Book PDF always includes Meeting Data after each meeting.

    The Field List page has its own checkbox for that block. The Address Book
    page does not, so a Field List import must not hide Meeting Data here.
    """
    values = _address_book_pdf_settings_from_form(form)
    values["include_bible_study_union_info"] = 1
    return save_address_book_settings(values)


def _address_book_pdf_cover_options_from_form(form) -> dict:
    return {
        "cover_image_data": str(form.get("cover_image_data") or "") if str(form.get("include_cover_image") or "") == "1" else "",
        "cover_image_width": str(form.get("cover_image_width") or "0"),
        "cover_image_height": str(form.get("cover_image_height") or "0"),
        "cover_title_enabled": str(form.get("include_cover_title") or "") == "1",
        "cover_title_text": str(form.get("cover_title_text") or ""),
        "cover_title_font_size": str(form.get("cover_title_font_size") or "30"),
        "cover_title_color": str(form.get("cover_title_color") or "#ffffff"),
        "cover_title_effect": str(form.get("cover_title_effect") or "shadow"),
        "cover_title_x": str(form.get("cover_title_x") or "0.5"),
        "cover_title_y": str(form.get("cover_title_y") or "0.24"),
        "cover_title_preview_width": str(form.get("cover_title_preview_width") or "0"),
        "cover_date_enabled": str(form.get("include_cover_date") or "") == "1",
        "cover_date_text": str(form.get("cover_date_text") or ""),
        "cover_date_font_size": str(form.get("cover_date_font_size") or "18"),
        "cover_date_color": str(form.get("cover_date_color") or "#ffffff"),
        "cover_date_x": str(form.get("cover_date_x") or "0.5"),
        "cover_date_y": str(form.get("cover_date_y") or "0.82"),
    }


def _address_book_pdf_insert_options_from_form(form) -> dict:
    has_data = bool(str(form.get("insert_file_data") or "").strip())
    enabled = str(form.get("include_insert_file") or "") == "1" and has_data
    return {
        "include_insert_file": enabled,
        "insert_file_data": str(form.get("insert_file_data") or "") if enabled else "",
        "insert_file_mime": str(form.get("insert_file_mime") or "") if enabled else "",
        "insert_file_width": str(form.get("insert_file_width") or "0") if enabled else "0",
        "insert_file_height": str(form.get("insert_file_height") or "0") if enabled else "0",
        "insert_file_crop": str(form.get("insert_file_crop") or "") if enabled else "",
    }


def _address_book_pdf_selected_field_names(form) -> list[str]:
    try:
        selected_field_names = json.loads(str(form.get("print_field_names") or "[]"))
    except json.JSONDecodeError:
        selected_field_names = []
    return [str(name) for name in selected_field_names] if isinstance(selected_field_names, list) else []


def _address_book_pdf_fields_for_print(form, field_list_context: dict) -> list[dict]:
    fields = _address_book_pdf_fields_from_context(field_list_context)
    selected_field_names = _address_book_pdf_selected_field_names(form)
    if selected_field_names:
        selected_names = set(selected_field_names)
        fields = [field for field in fields if str(field.get("name") or "") in selected_names]
    return fields


def _address_book_pdf_contact_preview(contact: dict) -> dict:
    phones = [
        {
            "value": str(phone.get("value") or "").strip(),
            "code": str(phone.get("code") or "").strip(),
            "type": str(phone.get("type") or "").strip(),
            "custom_label": str(phone.get("custom_label") or "").strip(),
        }
        for phone in contact.get("phones", [])
        if str(phone.get("value") or "").strip()
    ]
    raw_addresses = contact.get("address_entries") or [
        {"text": address, "has_coordinates": False}
        for address in contact.get("addresses", [])
    ]
    addresses = []
    for address in raw_addresses:
        if isinstance(address, dict):
            address_text = address.get("text")
            coordinates = str(address.get("coordinates") or "").strip()
            has_coordinates = bool(address.get("has_coordinates") or coordinates)
            entry = {
                "text": "\n".join(line.strip() for line in str(address_text or "").splitlines() if line.strip()),
                "address_type": str(address.get("address_type") or "").strip(),
                "street_address": str(address.get("street_address") or "").strip(),
                "extended_address": str(address.get("extended_address") or "").strip(),
                "city": str(address.get("city") or "").strip(),
                "region": str(address.get("region") or "").strip(),
                "postal_code": str(address.get("postal_code") or "").strip(),
                "coordinates": coordinates,
                "has_coordinates": has_coordinates,
            }
        else:
            entry = {
                "text": "\n".join(line.strip() for line in str(address or "").splitlines() if line.strip()),
                "address_type": "",
                "street_address": "",
                "extended_address": "",
                "city": "",
                "region": "",
                "postal_code": "",
                "coordinates": "",
                "has_coordinates": False,
            }
        if entry["text"] or entry["street_address"] or entry["extended_address"] or entry["city"]:
            if not entry["text"]:
                entry["text"] = "\n".join(
                    part for part in (
                        entry["street_address"],
                        entry["extended_address"],
                        ", ".join(part for part in (entry["city"], entry["region"], entry["postal_code"]) if part),
                    )
                    if part
                )
            addresses.append(entry)
    return {
        "name": str(contact.get("name") or contact.get("label") or "CONTACT, Name").strip(),
        "phones": phones,
        "addresses": addresses,
        "children": list(contact.get("children") or []),
        "other_relationships": list(contact.get("other_relationships") or []),
        "print_book_name": [str(item).strip() for item in (contact.get("print_book_name") or []) if str(item).strip()],
        "print_after_address": [str(item).strip() for item in (contact.get("print_after_address") or []) if str(item).strip()],
    }


@router.get("/pdf", response_class=HTMLResponse)
def address_book_pdf_page(
    request: Request,
    preset_id: int | None = Query(default=None),
    notice: str = Query(default=""),
):
    sync_redirect = _signin_sync_redirect_if_blocked()
    if sync_redirect:
        return sync_redirect
    context = get_book_layout_preset_editor(preset_id, False)
    context["address_book_settings"] = get_address_book_settings()
    field_list_context = get_field_list_builder_context()
    context["address_book_picker_fields"] = order_address_book_fields(
        _address_book_pdf_fields_from_context(field_list_context),
        "custom",
        context["address_book_settings"].get("print_order_json"),
    )
    context["address_book_picker_json"] = json.dumps({"fields": context["address_book_picker_fields"]})
    context["address_book_meeting_preview_json"] = field_list_context["field_list_meeting_preview_json"]
    context["notice_message"] = get_notice_message(notice)
    return templates.TemplateResponse(
        request=request,
        name="address_book/pdf.html",
        context=context,
    )


@router.post("/pdf/save")
async def save_address_book_pdf_page(request: Request):
    form = await request.form()
    preset_id_raw = str(form.get("preset_id", "") or "").strip()
    preset_id = int(preset_id_raw) if preset_id_raw else None
    if not is_editing_allowed() and not is_non_editor_access_role():
        return RedirectResponse(url=f"/address-book/pdf?notice={get_edit_block_notice_key()}", status_code=303)

    saved_id = _save_address_book_pdf_layout_preset(preset_id, _address_book_pdf_layout_from_form(form))
    save_address_book_settings(_address_book_pdf_settings_from_form(form))
    mark_book_layouts_for_drive_export()
    mark_field_list_for_drive_export()
    return RedirectResponse(url=f"/address-book/pdf?preset_id={saved_id}&notice=address_book_settings_saved", status_code=303)


@router.post("/pdf/set-default")
async def set_default_address_book_pdf_layout(request: Request):
    form = await request.form()
    preset_id_raw = str(form.get("preset_id", "") or "").strip()
    if not preset_id_raw:
        return RedirectResponse(url="/address-book/pdf?notice=address_book_pdf_failed", status_code=303)
    preset_id = int(preset_id_raw)
    if not is_editing_allowed() and not is_non_editor_access_role():
        return RedirectResponse(url=f"/address-book/pdf?preset_id={preset_id}&notice={get_edit_block_notice_key()}", status_code=303)

    selected_id = set_default_book_layout_preset(preset_id)
    mark_book_layouts_for_drive_export()
    return RedirectResponse(url=f"/address-book/pdf?preset_id={selected_id}&notice=address_book_settings_saved", status_code=303)


@router.post("/pdf/print-order")
async def save_address_book_pdf_print_order(request: Request):
    form = await request.form()
    if not is_editing_allowed() and not is_non_editor_access_role():
        return JSONResponse({"ok": False, "notice": get_edit_block_notice_key()}, status_code=403)

    settings = get_address_book_settings()
    settings["print_order_mode"] = str(form.get("print_order_mode") or settings.get("print_order_mode") or "custom")
    settings["print_order_json"] = str(form.get("print_order_json") or "")
    save_address_book_settings(settings)
    mark_field_list_for_drive_export()
    return JSONResponse({"ok": True})


@router.post("/pdf/print")
async def print_address_book_pdf_page(request: Request):
    form = await request.form()
    preset_id_raw = str(form.get("preset_id", "") or "").strip()
    preset_id = int(preset_id_raw) if preset_id_raw else None
    if not is_editing_allowed() and not is_non_editor_access_role():
        return RedirectResponse(url=f"/address-book/pdf?notice={get_edit_block_notice_key()}", status_code=303)

    saved_id = _save_address_book_pdf_layout_preset(preset_id, _address_book_pdf_layout_from_form(form))
    settings = _address_book_pdf_print_settings(form)
    layout = _address_book_pdf_layout_from_form(form)
    field_list_context = get_field_list_builder_context()
    fields = _address_book_pdf_fields_for_print(form, field_list_context)
    meeting_preview_payload = json.loads(field_list_context["field_list_meeting_preview_json"])
    pdf_bytes = build_address_book_pdf(
        fields=fields,
        meeting_preview_sections=meeting_preview_payload.get("sections") or [],
        settings=settings,
        layout=layout,
        print_order_mode=str(form.get("print_order_mode") or settings.get("print_order_mode") or "alphabetical"),
        print_order_json=str(form.get("print_order_json") or settings.get("print_order_json") or ""),
        **_address_book_pdf_cover_options_from_form(form),
        **_address_book_pdf_insert_options_from_form(form),
    )
    filename = address_book_pdf_filename(str(form.get("book_title") or "Address Book"))
    ADDRESS_BOOK_PDF_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    _address_book_pdf_preview_path(filename).write_bytes(pdf_bytes)
    notice = "address_book_pdf_created"
    if _address_book_pdf_can_save_to_drive():
        notice = "address_book_pdf_uploaded"
        try:
            upload_address_book_pdf_to_google_drive(filename, pdf_bytes)
        except (RuntimeError, HTTPError, URLError):
            notice = "address_book_pdf_preview_drive_failed"
    return RedirectResponse(url=f"/address-book/pdf/view/{filename}?preset_id={saved_id}&notice={notice}", status_code=303)


@router.post("/pdf/print-paper")
async def print_address_book_paper_page(request: Request):
    form = await request.form()
    preset_id_raw = str(form.get("preset_id", "") or "").strip()
    preset_id = int(preset_id_raw) if preset_id_raw else None
    if not is_editing_allowed() and not is_non_editor_access_role():
        return RedirectResponse(url=f"/address-book/pdf?notice={get_edit_block_notice_key()}", status_code=303)

    saved_id = _save_address_book_pdf_layout_preset(preset_id, _address_book_pdf_layout_from_form(form))
    settings = _address_book_pdf_print_settings(form)
    layout = _address_book_pdf_layout_from_form(form)
    field_list_context = get_field_list_builder_context()
    fields = _address_book_pdf_fields_for_print(form, field_list_context)
    meeting_preview_payload = json.loads(field_list_context["field_list_meeting_preview_json"])
    binding = str(form.get("paper_book_binding") or "side_spiral")
    if binding not in {"side_spiral", "top_spiral", "stapled_folded"}:
        binding = "side_spiral"
    pdf_bytes = build_address_book_pdf(
        fields=fields,
        meeting_preview_sections=meeting_preview_payload.get("sections") or [],
        settings=settings,
        layout=layout,
        print_order_mode=str(form.get("print_order_mode") or settings.get("print_order_mode") or "alphabetical"),
        print_order_json=str(form.get("print_order_json") or settings.get("print_order_json") or ""),
        paper_book_binding=binding,
        paper_book_cut_marks=str(form.get("paper_book_cut_marks") or "1") != "0",
        **_address_book_pdf_cover_options_from_form(form),
        **_address_book_pdf_insert_options_from_form(form),
    )
    binding_label = {
        "side_spiral": "Side_Spiral",
        "top_spiral": "Top_Spiral",
        "stapled_folded": "Stapled_Folded",
    }[binding]
    filename = address_book_pdf_filename(f"{str(form.get('book_title') or 'Address Book')} Printed {binding_label}")
    ADDRESS_BOOK_PDF_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    _address_book_pdf_preview_path(filename).write_bytes(pdf_bytes)
    notice = "address_book_pdf_created"
    if _address_book_pdf_can_save_to_drive():
        notice = "address_book_pdf_uploaded"
        try:
            upload_address_book_printed_pdf_to_google_drive(filename, pdf_bytes)
        except (RuntimeError, HTTPError, URLError):
            notice = "address_book_pdf_preview_drive_failed"
    return RedirectResponse(
        url=f"/address-book/pdf/view/{filename}?preset_id={saved_id}&notice={notice}&autoprint=1",
        status_code=303,
    )


@router.get("/pdf/view/{filename}", response_class=HTMLResponse)
def view_address_book_pdf_page(
    request: Request,
    filename: str,
    preset_id: int | None = Query(default=None),
    notice: str = Query(default=""),
):
    path = _address_book_pdf_preview_path(filename)
    if not path.exists():
        return RedirectResponse(url=f"/address-book/pdf?notice=address_book_pdf_failed", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="address_book/pdf_view.html",
        context={
            "filename": path.name,
            "preset_id": preset_id,
            "notice_message": get_notice_message(notice),
            "pdf_cache_bust": int(path.stat().st_mtime),
            "is_editor_session": _address_book_pdf_can_save_to_drive(),
        },
    )


@router.get("/pdf/file/{filename}")
def address_book_pdf_file(filename: str):
    path = _address_book_pdf_preview_path(filename)
    if not path.exists():
        return RedirectResponse(url="/address-book/pdf?notice=address_book_pdf_failed", status_code=303)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/pdf/download/{filename}")
def address_book_pdf_download(filename: str):
    path = _address_book_pdf_preview_path(filename)
    if not path.exists():
        return RedirectResponse(url="/address-book/pdf?notice=address_book_pdf_failed", status_code=303)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="attachment",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
