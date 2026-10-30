from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.field_list_service import (
    get_address_book_settings,
    save_address_book_settings,
    save_field_list_template,
)
from app.services.preset_service import (
    delete_book_layout_preset,
    duplicate_book_layout_preset,
    get_book_layout_preset_editor,
    get_shared_contacts_group_name,
    save_book_layout_preset,
    set_default_book_layout_preset,
)
from app.services.google_sync_service import (
    export_book_layouts_to_google_drive,
    export_shared_settings_to_google_drive,
    get_current_access_role,
    get_editor_lock_status,
    get_edit_block_notice_key,
    get_google_sync_summary,
    get_notice_message,
    is_editing_allowed,
    is_non_editor_access_role,
    mark_book_layouts_for_drive_export,
    mark_field_list_for_drive_export,
    mark_shared_data_for_drive_export,
    queue_assigned_contacts_for_shared_group_label,
    queue_uploaded_contacts_for_organization_rewrite,
    save_address_book_pdf_share_setting,
    save_editor_secret,
    save_google_sync_settings,
    save_multi_editor_setting,
    save_public_web_url,
    save_share_web_api_key,
    save_single_editor_sync_setting,
)
from app.web_templates import templates


router = APIRouter(prefix="/presets", tags=["presets"])

def _log_preset_route(message: str) -> None:
    return None


def _mark_settings_for_drive_export() -> None:
    mark_shared_data_for_drive_export()


def _mark_book_layouts_for_drive_export() -> None:
    mark_book_layouts_for_drive_export()


def _should_sync_app_settings_to_drive(state: dict, account: dict) -> bool:
    if str(account.get("status") or "") != "connected" or not bool(account.get("drive_scope_ready")):
        return False
    if bool(state.get("multi_editor_enabled")) or bool(state.get("multi_editor_account_locked")):
        return True
    return bool(state.get("sync_enabled"))


def _try_immediate_app_settings_drive_export(
    *,
    core_settings_changed: bool,
    book_layouts_changed: bool,
) -> tuple[bool, bool]:
    if not core_settings_changed and not book_layouts_changed:
        return False, False
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if not _should_sync_app_settings_to_drive(state, account):
        if core_settings_changed:
            _mark_settings_for_drive_export()
        if book_layouts_changed:
            _mark_book_layouts_for_drive_export()
        return False, False

    settings_exported = False
    book_layouts_exported = False
    if core_settings_changed:
        try:
            export_shared_settings_to_google_drive()
            settings_exported = True
        except (RuntimeError, HTTPError, URLError):
            _mark_settings_for_drive_export()
    if book_layouts_changed:
        try:
            export_book_layouts_to_google_drive()
            book_layouts_exported = True
        except (RuntimeError, HTTPError, URLError):
            _mark_book_layouts_for_drive_export()
    return settings_exported, book_layouts_exported


def _app_settings_save_notice(
    *,
    secret_saved: bool,
    settings_exported: bool,
    book_layouts_exported: bool,
    allow_multi_editor_unlock: bool,
) -> str:
    if allow_multi_editor_unlock:
        if settings_exported:
            return "multi_editor_settings_drive_saved"
        return "multi_editor_settings_saved"
    if secret_saved and not settings_exported and not book_layouts_exported:
        return "editor_secret_saved"
    if settings_exported and book_layouts_exported:
        return "app_settings_drive_saved"
    if settings_exported:
        return "multi_editor_settings_drive_saved"
    if book_layouts_exported:
        return "book_layout_settings_drive_saved"
    return "multi_editor_settings_saved"


def _no_edit_redirect(return_to: str = "/presets/app-settings") -> RedirectResponse:
    requested_return_to = str(return_to or "")
    safe_return_to = requested_return_to if requested_return_to.startswith(("/presets", "/address-book/")) else "/presets/app-settings"
    separator = "&" if "?" in safe_return_to else "?"
    return RedirectResponse(url=f"{safe_return_to}{separator}notice={get_edit_block_notice_key()}", status_code=303)


def _save_shared_group_name_only(name: str) -> bool:
    cleaned = str(name or "").strip()
    if not cleaned:
        return False
    preset_context = get_book_layout_preset_editor(None, False)
    selected_preset = preset_context.get("selected_preset") or {}
    preset_id = selected_preset.get("id")
    if not preset_id:
        return False
    payload = dict(selected_preset)
    payload["shared_contacts_group_name"] = cleaned
    payload["is_default"] = "1" if selected_preset.get("is_default") else ""
    save_book_layout_preset(int(preset_id), payload)
    return True


def _save_current_book_layout_colors(form) -> bool:
    preset_context = get_book_layout_preset_editor(None, False)
    selected_preset = preset_context.get("selected_preset") or {}
    preset_id = selected_preset.get("id")
    if not preset_id:
        return False
    color_fields = [
        "meetingdata_title_bar_color",
        "meetingdata_primary_row_color",
        "meetingdata_secondary_row_color",
        "meetingdata_highlight_row_color",
        "contacts_title_bar_color",
        "contacts_primary_row_color",
        "contacts_secondary_row_color",
        "contacts_highlight_row_color",
        "shared_contacts_group_name",
    ]
    payload = dict(selected_preset)
    for field in color_fields:
        payload[field] = form.get(field) or selected_preset.get(field)
    payload["is_default"] = "1" if selected_preset.get("is_default") else ""
    changed = any(str(payload.get(field) or "") != str(selected_preset.get(field) or "") for field in color_fields)
    if not changed:
        return False
    save_book_layout_preset(int(preset_id), payload)
    return True


def _app_settings_core_changed(
    form,
    state: dict,
    *,
    multi_editor_enabled: bool,
    single_editor_google_sync: bool,
    timeout_minutes: int,
) -> bool:
    if bool(state.get("multi_editor_enabled")) != bool(multi_editor_enabled):
        return True
    if int(state.get("editor_lock_timeout_minutes") or 60) != int(timeout_minutes):
        return True
    if not multi_editor_enabled and bool(state.get("sync_enabled")) != bool(single_editor_google_sync):
        return True
    if str(form.get("editor_secret") or "").strip():
        return True
    share_enabled = (multi_editor_enabled or single_editor_google_sync) and str(form.get("address_book_pdf_share_enabled") or "").lower() in {"1", "true", "yes", "on"}
    if bool(state.get("address_book_pdf_share_enabled")) != bool(share_enabled):
        return True
    if str(state.get("address_book_pdf_share_folder_id") or "") != str(form.get("address_book_pdf_share_folder_id") or "").strip():
        return True
    if str(state.get("address_book_pdf_share_folder_name") or "") != str(form.get("address_book_pdf_share_folder_name") or "").strip():
        return True
    if str(state.get("public_web_url") or "") != str(form.get("public_web_url") or "").strip().rstrip("/"):
        return True
    if str(form.get("share_web_api_key") or "").strip():
        return True
    return False


@router.get("/book-layouts", response_class=HTMLResponse)
def book_layout_presets_page(
    request: Request,
    preset_id: int | None = Query(default=None),
    new: int = Query(default=0),
):
    if preset_id:
        return RedirectResponse(url=f"/address-book/pdf?preset_id={preset_id}", status_code=303)
    return RedirectResponse(url="/address-book/pdf", status_code=303)


@router.get("/contacts", response_class=HTMLResponse)
def contact_settings_page(
    request: Request,
    preset_id: int | None = Query(default=None),
):
    return RedirectResponse(url="/presets/app-settings", status_code=303)


@router.get("/meetingdata", response_class=HTMLResponse)
def meetingdata_settings_page(
    request: Request,
    preset_id: int | None = Query(default=None),
):
    return RedirectResponse(url="/presets/app-settings", status_code=303)


@router.get("/meetingdata-editor", response_class=HTMLResponse)
def meetingdata_editor_settings_page(
    request: Request,
    preset_id: int | None = Query(default=None),
):
    return RedirectResponse(url="/meetings/v2/sections", status_code=303)


@router.get("/google-connect", response_class=HTMLResponse)
def google_connect_settings_page(
    request: Request,
    notice: str = Query(default=""),
):
    notice_key = str(notice or "").strip()
    notice_message = get_notice_message(
        notice_key,
        count=str(request.query_params.get("count") or "0"),
    )
    if notice_key == "editor_signed_in_update_available":
        from app.services.app_update_service import get_app_update_status

        current_version = str(request.query_params.get("current_version") or "").strip()
        latest_version = str(request.query_params.get("latest_version") or "").strip()
        if not current_version or not latest_version:
            status = get_app_update_status()
            current_version = current_version or str(status.get("current_version") or "unknown")
            latest_version = latest_version or str(status.get("latest_version") or "unknown")
        notice_message = get_notice_message(
            notice_key,
            current_version=current_version or "unknown",
            latest_version=latest_version or "unknown",
        )
    context = {
        "notice_key": notice_key,
        "notice_message": notice_message,
        "editor_lock_status": get_editor_lock_status(),
        **get_google_sync_summary(),
    }
    return templates.TemplateResponse(
        request=request,
        name="presets/google_sync_settings.html",
        context=context,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/app-settings", response_class=HTMLResponse)
def app_settings_page(
    request: Request,
    notice: str = Query(default=""),
    count: str = Query(default=""),
    group_name: str = Query(default=""),
):
    context = {
        "notice_message": get_notice_message(
            notice,
            count=str(count or "0"),
            group_name=str(group_name or get_shared_contacts_group_name()),
        ),
        **get_book_layout_preset_editor(None, False),
        **get_google_sync_summary(),
    }
    return templates.TemplateResponse(
        request=request,
        name="presets/app_settings.html",
        context=context,
    )


@router.get("/address-book", response_class=HTMLResponse)
def address_book_settings_page(
    request: Request,
    notice: str = Query(default=""),
):
    context = {
        "notice_message": get_notice_message(notice),
        "address_book_settings": get_address_book_settings(),
        **get_google_sync_summary(),
    }
    return templates.TemplateResponse(
        request=request,
        name="presets/address_book_settings.html",
        context=context,
    )


@router.post("/app-settings/save")
async def save_app_settings_page(request: Request):
    current_summary = get_google_sync_summary()
    current_state = current_summary.get("state") or {}
    was_multi_editor_enabled = bool(current_state.get("multi_editor_enabled"))
    multi_editor_account_locked = bool(current_state.get("multi_editor_account_locked"))
    if (was_multi_editor_enabled or multi_editor_account_locked) and not is_editing_allowed():
        return _no_edit_redirect("/presets/app-settings")
    form = await request.form()
    multi_editor_enabled = str(form.get("multi_editor_enabled") or "").lower() in {"1", "true", "yes", "on"}
    allow_multi_editor_unlock = bool(multi_editor_account_locked and not multi_editor_enabled and is_editing_allowed())
    if not is_editing_allowed() and multi_editor_enabled:
        return _no_edit_redirect("/presets/app-settings")
    try:
        timeout_hours = int(str(form.get("editor_timeout_hours") or "1") or 1)
        timeout_minutes = int(str(form.get("editor_timeout_minutes") or "0") or 0)
    except ValueError:
        timeout_hours = 1
        timeout_minutes = 0
    single_editor_google_sync = str(form.get("single_editor_sync_mode") or "") == "google"
    normalized_timeout_minutes = timeout_hours * 60 + timeout_minutes
    core_settings_changed = _app_settings_core_changed(
        form,
        current_state,
        multi_editor_enabled=multi_editor_enabled,
        single_editor_google_sync=single_editor_google_sync,
        timeout_minutes=normalized_timeout_minutes,
    )
    save_multi_editor_setting(
        multi_editor_enabled,
        normalized_timeout_minutes,
        allow_unlock=allow_multi_editor_unlock,
    )
    if not multi_editor_enabled:
        save_single_editor_sync_setting(single_editor_google_sync)
    save_address_book_pdf_share_setting(
        enabled=(multi_editor_enabled or single_editor_google_sync) and str(form.get("address_book_pdf_share_enabled") or "").lower() in {"1", "true", "yes", "on"},
        folder_id=str(form.get("address_book_pdf_share_folder_id") or ""),
        folder_name=str(form.get("address_book_pdf_share_folder_name") or ""),
    )
    current_share_url = str(current_state.get("public_web_url") or "").strip().rstrip("/")
    requested_share_url = str(form.get("public_web_url") or "").strip().rstrip("/")
    share_settings_unlocked = str(form.get("share_settings_unlocked") or "").lower() in {"1", "true", "yes", "on"}
    if current_share_url and requested_share_url != current_share_url and not share_settings_unlocked:
        requested_share_url = current_share_url
    save_public_web_url(requested_share_url)
    if not current_share_url or share_settings_unlocked or not bool(current_state.get("share_web_api_key_configured")):
        save_share_web_api_key(str(form.get("share_web_api_key") or ""))
    previous_shared_group_name = get_shared_contacts_group_name()
    if _save_current_book_layout_colors(form):
        book_layouts_changed = True
    else:
        book_layouts_changed = False
    secret_saved = save_editor_secret(str(form.get("editor_secret") or ""))
    settings_exported = False
    book_layouts_exported = False
    if core_settings_changed or book_layouts_changed:
        settings_exported, book_layouts_exported = _try_immediate_app_settings_drive_export(
            core_settings_changed=core_settings_changed,
            book_layouts_changed=book_layouts_changed,
        )
    notice = _app_settings_save_notice(
        secret_saved=secret_saved,
        settings_exported=settings_exported,
        book_layouts_exported=book_layouts_exported,
        allow_multi_editor_unlock=allow_multi_editor_unlock,
    )
    next_shared_group_name = get_shared_contacts_group_name()
    from app.services.shared_group_rename_service import (
        apply_shared_contacts_group_rename,
        get_pending_shared_group_rename,
        sync_share_invite_display_name,
    )

    rename_old_name = ""
    rename_new_name = ""
    if previous_shared_group_name.casefold() != next_shared_group_name.casefold():
        rename_old_name = previous_shared_group_name
        rename_new_name = next_shared_group_name
    else:
        pending = get_pending_shared_group_rename()
        pending_new = str(pending.get("new_name") or "").strip()
        pending_old = str(pending.get("old_name") or "").strip()
        if pending_new and pending_new.casefold() == next_shared_group_name.casefold():
            rename_old_name = pending_old or previous_shared_group_name
            rename_new_name = pending_new
    if rename_old_name and rename_new_name:
        rename_result = apply_shared_contacts_group_rename(rename_old_name, rename_new_name)
        rename_notice = str(rename_result.get("notice_key") or "").strip()
        if rename_notice:
            notice = rename_notice
    else:
        sync_share_invite_display_name(next_shared_group_name)
    return RedirectResponse(url=f"/presets/app-settings?notice={notice}", status_code=303)


@router.post("/google-connect/queue-organization-rewrite")
async def queue_organization_rewrite_page(request: Request):
    if not is_editing_allowed():
        return _no_edit_redirect("/presets/google-connect")
    result = queue_uploaded_contacts_for_organization_rewrite()
    if not result.get("sync_enabled") or int(result.get("queued") or 0) <= 0:
        notice = "organization_rewrite_none"
    else:
        notice = "organization_rewrite_queued"
    query = urlencode({"notice": notice, "count": int(result.get("queued") or 0)})
    return RedirectResponse(url=f"/presets/google-connect?{query}", status_code=303)


@router.post("/app-settings/apply-shared-group-label")
async def apply_shared_group_label_page(request: Request):
    if not is_editing_allowed():
        return _no_edit_redirect("/presets/app-settings")
    form = await request.form()
    requested_name = str(form.get("shared_contacts_group_name") or "").strip()
    previous_name = get_shared_contacts_group_name()
    if requested_name:
        _save_shared_group_name_only(requested_name)
        next_name = get_shared_contacts_group_name()
        from app.services.shared_group_rename_service import (
            apply_shared_contacts_group_rename,
            sync_share_invite_display_name,
        )

        if previous_name.casefold() != next_name.casefold():
            apply_shared_contacts_group_rename(previous_name, next_name)
        else:
            sync_share_invite_display_name(next_name)
        _mark_book_layouts_for_drive_export()
    result = queue_assigned_contacts_for_shared_group_label()
    group_name = str(result.get("group_name") or get_shared_contacts_group_name() or "the shared group")
    if not result.get("sync_enabled"):
        notice = "shared_group_label_sync_off"
    elif int(result.get("queued") or 0) <= 0:
        notice = "shared_group_label_none"
    else:
        notice = "shared_group_label_queued"
    query = urlencode(
        {
            "notice": notice,
            "count": int(result.get("queued") or 0),
            "group_name": group_name,
        }
    )
    return RedirectResponse(url=f"/presets/app-settings?{query}", status_code=303)


@router.post("/address-book/save")
async def save_address_book_settings_page(request: Request):
    if not is_editing_allowed():
        return _no_edit_redirect("/presets/address-book")
    form = await request.form()
    settings = save_address_book_settings(
        {
            "margin_left_in": form.get("margin_left_in"),
            "margin_right_in": form.get("margin_right_in"),
            "margin_top_in": form.get("margin_top_in"),
            "margin_bottom_in": form.get("margin_bottom_in"),
            "font_family": form.get("font_family"),
            "base_font_size_pt": form.get("base_font_size_pt"),
            "line_height": form.get("line_height"),
            "preview_scale": form.get("preview_scale"),
            "page_column_count": form.get("page_column_count"),
            "include_bible_study_union_info": form.get("include_bible_study_union_info"),
            "two_column_gap_ch": form.get("two_column_gap_ch"),
            "column_width_in": form.get("column_width_in"),
            "address_align": form.get("address_align"),
            "address_map_provider": form.get("address_map_provider"),
            "address_italic": form.get("address_italic"),
            "title_font_family": form.get("title_font_family"),
            "title_font_size_pt": form.get("title_font_size_pt"),
            "title_align": form.get("title_align"),
            "meeting_name_font_family": form.get("meeting_name_font_family"),
            "meeting_name_font_size_pt": form.get("meeting_name_font_size_pt"),
            "meeting_name_align": form.get("meeting_name_align"),
            "bible_study_font_size_pt": form.get("bible_study_font_size_pt"),
            "bible_study_union_align": form.get("bible_study_union_align"),
            "separator_lines": form.get("separator_lines"),
            "separator_vertical_lines": form.get("separator_vertical_lines"),
            "manual_palette_items": form.get("manual_palette_items"),
            "selected_field_name": form.get("selected_field_name"),
            "selected_meeting_name": form.get("selected_meeting_name"),
            "selected_contact_id": form.get("selected_contact_id"),
        }
    )
    field_list_template_id = int(str(form.get("field_list_template_id") or 0) or 0)
    if field_list_template_id and form.get("field_list_items_json"):
        save_field_list_template(
            field_list_template_id,
            str(form.get("field_list_template_name") or ""),
            str(form.get("field_list_items_json") or "[]"),
            settings,
        )
    mark_field_list_for_drive_export()
    return_to = str(form.get("return_to") or "").strip()
    if return_to.startswith("/field-list"):
        separator = "&" if "?" in return_to else "?"
        return RedirectResponse(url=f"{return_to}{separator}notice=address_book_settings_saved", status_code=303)
    return RedirectResponse(url="/presets/address-book?notice=address_book_settings_saved", status_code=303)


@router.post("/google-connect/save")
async def save_google_connect_settings_page(request: Request):
    if not is_editing_allowed():
        return _no_edit_redirect("/presets/google-connect")
    form = await request.form()
    save_google_sync_settings(
        str(form.get("account_email") or ""),
        True,
    )
    return RedirectResponse(url="/presets/google-connect?notice=settings_saved", status_code=303)


@router.post("/book-layouts/save")
async def save_book_layout_preset_page(request: Request):
    form = await request.form()
    preset_id_raw = str(form.get("preset_id", "") or "").strip()
    preset_id = int(preset_id_raw) if preset_id_raw else None
    return_to = str(form.get("return_to", "") or "").strip().lower()
    if not is_editing_allowed() and not (return_to == "address-book-pdf" and is_non_editor_access_role()):
        return _no_edit_redirect("/address-book/pdf")
    saved_id = save_book_layout_preset(
        preset_id,
        {
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
            "index_font_size_pt": form.get("index_font_size_pt"),
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
        },
    )
    redirect_url = f"/address-book/pdf?preset_id={saved_id}"
    if return_to == "contacts":
        redirect_url = "/presets/app-settings"
    elif return_to == "meetingdata":
        redirect_url = "/presets/app-settings"
    elif return_to == "meetingdata-editor":
        redirect_url = "/meetings/v2/sections"
    elif return_to == "address-book-pdf":
        redirect_url = f"/address-book/pdf?preset_id={saved_id}"
    _mark_book_layouts_for_drive_export()
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/book-layouts/new")
async def new_book_layout_preset_page():
    return RedirectResponse(url="/address-book/pdf", status_code=303)


@router.post("/book-layouts/{preset_id}/duplicate")
async def duplicate_book_layout_preset_page(preset_id: int):
    if not is_editing_allowed():
        return _no_edit_redirect(f"/address-book/pdf?preset_id={preset_id}")
    new_id = duplicate_book_layout_preset(preset_id)
    _mark_book_layouts_for_drive_export()
    return RedirectResponse(url=f"/address-book/pdf?preset_id={new_id}", status_code=303)


@router.post("/book-layouts/{preset_id}/set-default")
async def set_default_book_layout_preset_page(preset_id: int):
    if not is_editing_allowed():
        return _no_edit_redirect(f"/address-book/pdf?preset_id={preset_id}")
    selected_id = set_default_book_layout_preset(preset_id)
    _mark_book_layouts_for_drive_export()
    return RedirectResponse(url=f"/address-book/pdf?preset_id={selected_id}", status_code=303)


@router.post("/book-layouts/{preset_id}/delete")
async def delete_book_layout_preset_page(preset_id: int):
    if not is_editing_allowed():
        return _no_edit_redirect(f"/address-book/pdf?preset_id={preset_id}")
    selected_id = delete_book_layout_preset(preset_id)
    _mark_book_layouts_for_drive_export()
    return RedirectResponse(url=f"/address-book/pdf?preset_id={selected_id}", status_code=303)
