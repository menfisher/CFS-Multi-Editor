from __future__ import annotations

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.services.field_list_service import (
    can_edit_field_list,
    apply_field_list_bible_font_resolution,
    create_field_list_template,
    delete_field_list_template,
    get_field_list_builder_context,
    inspect_field_list_bible_font_conflicts,
    render_field_list_print_html,
    save_address_book_settings,
    save_field_list_bible_font_size_setting,
    save_field_list_template,
)
from app.services.google_sync_service import (
    get_google_sync_summary,
    get_notice_message,
    mark_meetingdata_for_drive_export,
)
from app.web_templates import templates


router = APIRouter(tags=["field-list"])

def _signin_sync_redirect_if_blocked():
    state = (get_google_sync_summary().get("state") or {})
    if bool(state.get("signin_sync_in_progress") or 0):
        return RedirectResponse(url="/presets/google-connect?notice=signin_sync_blocked", status_code=303)
    if str(state.get("signin_sync_error") or "").strip():
        return RedirectResponse(url="/presets/google-connect?notice=signin_sync_failed_blocked", status_code=303)
    return None


def _field_list_settings_from_form(form) -> dict:
    return {
        "margin_left_in": form.get("margin_left_in"),
        "margin_right_in": form.get("margin_right_in"),
        "margin_top_in": form.get("margin_top_in"),
        "margin_bottom_in": form.get("margin_bottom_in"),
        "font_family": form.get("font_family"),
        "base_font_size_pt": form.get("base_font_size_pt"),
        "base_font_bold": form.get("base_font_bold"),
        "base_font_italic": form.get("base_font_italic"),
        "base_font_underline": form.get("base_font_underline"),
        "line_height": form.get("line_height"),
        "preview_scale": form.get("preview_scale"),
        "page_column_count": form.get("page_column_count"),
        "include_bible_study_union_info": form.get("include_bible_study_union_info"),
        "two_column_gap_ch": form.get("two_column_gap_ch"),
        "column_width_in": form.get("column_width_in"),
        "address_align": form.get("address_align"),
        "title_font_family": form.get("title_font_family"),
        "title_font_size_pt": form.get("title_font_size_pt"),
        "title_font_bold": form.get("title_font_bold"),
        "title_font_italic": form.get("title_font_italic"),
        "title_font_underline": form.get("title_font_underline"),
        "title_align": form.get("title_align"),
        "meeting_name_font_family": form.get("meeting_name_font_family"),
        "meeting_name_font_size_pt": form.get("meeting_name_font_size_pt"),
        "meeting_name_font_bold": form.get("meeting_name_font_bold"),
        "meeting_name_font_italic": form.get("meeting_name_font_italic"),
        "meeting_name_font_underline": form.get("meeting_name_font_underline"),
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


@router.get("/field-list", response_class=HTMLResponse)
def field_list_page(
    request: Request,
    template_id: int | None = Query(default=None),
    notice: str = Query(default=""),
):
    sync_redirect = _signin_sync_redirect_if_blocked()
    if sync_redirect:
        return sync_redirect
    context = {
        "notice_message": get_notice_message(notice),
        "field_list_notice": notice,
        **get_field_list_builder_context(template_id),
    }
    return templates.TemplateResponse(
        request=request,
        name="field_list.html",
        context=context,
    )


@router.post("/field-list/print", response_class=HTMLResponse)
async def print_field_list_page(request: Request):
    form = await request.form()
    html = render_field_list_print_html(
        template_id=int(str(form.get("template_id") or 0) or 0),
        items_json=str(form.get("items_json") or "[]"),
        field_name=str(form.get("selected_field_name") or form.get("field_name") or ""),
        order_mode=str(form.get("print_field_list_order") or "template"),
        settings_values=_field_list_settings_from_form(form),
        print_mode=str(form.get("print_mode") or "template"),
        selected_meeting_names=form.getlist("selected_meeting_names"),
        meeting_home_first=bool(form.get("print_meeting_home_first")),
        page_numbers=bool(form.get("print_page_numbers")),
    )
    return HTMLResponse(content=html)


@router.post("/field-list/bible-font/conflicts")
async def field_list_bible_font_conflicts(request: Request):
    if not can_edit_field_list():
        return JSONResponse({"error": "This session can view Field List templates but cannot edit them."}, status_code=403)
    payload = await request.json()
    selected_meeting_names = payload.get("selected_meeting_names") or []
    if isinstance(selected_meeting_names, str):
        selected_meeting_names = [selected_meeting_names]
    result = inspect_field_list_bible_font_conflicts(
        str(payload.get("field_name") or ""),
        [str(name) for name in selected_meeting_names],
        float(payload.get("font_size_pt") or 11),
    )
    return JSONResponse(result)


@router.post("/field-list/bible-font/apply")
async def field_list_bible_font_apply(request: Request):
    if not can_edit_field_list():
        return JSONResponse({"error": "This session can view Field List templates but cannot edit them."}, status_code=403)
    payload = await request.json()
    selected_meeting_names = payload.get("selected_meeting_names") or []
    if isinstance(selected_meeting_names, str):
        selected_meeting_names = [selected_meeting_names]
    result = apply_field_list_bible_font_resolution(
        str(payload.get("field_name") or ""),
        [str(name) for name in selected_meeting_names],
        float(payload.get("font_size_pt") or 11),
        str(payload.get("mode") or "all"),
        payload.get("row_actions") or [],
    )
    save_field_list_bible_font_size_setting(
        int(payload.get("template_id") or 0),
        float(payload.get("font_size_pt") or 11),
        str(payload.get("items_json") or "[]"),
        str(payload.get("template_name") or ""),
    )
    mark_meetingdata_for_drive_export()
    return JSONResponse(result)


@router.post("/field-list/templates/new")
async def create_field_list_template_page(request: Request):
    if not can_edit_field_list():
        return RedirectResponse(url="/field-list?notice=editor_lock_blocked", status_code=303)
    form = await request.form()
    settings = save_address_book_settings(_field_list_settings_from_form(form))
    template_name = str(form.get("template_name") or "")
    template_id = create_field_list_template(template_name, "[]", settings)
    return RedirectResponse(url=f"/field-list?template_id={template_id}", status_code=303)


@router.post("/field-list/templates/save")
async def save_field_list_template_page(request: Request):
    if not can_edit_field_list():
        return RedirectResponse(url="/field-list?notice=editor_lock_blocked", status_code=303)
    form = await request.form()
    template_id = int(str(form.get("template_id") or 0) or 0)
    template_name = str(form.get("template_name") or "")
    items_json = str(form.get("items_json") or "[]")
    settings = save_address_book_settings(_field_list_settings_from_form(form))
    saved_id = save_field_list_template(template_id, template_name, items_json, settings)
    return RedirectResponse(url=f"/field-list?template_id={saved_id}&notice=field_list_saved", status_code=303)


@router.post("/field-list/templates/delete")
def delete_field_list_template_page(template_id: int | None = Form(default=None)):
    if not can_edit_field_list():
        return RedirectResponse(url="/field-list?notice=editor_lock_blocked", status_code=303)
    next_id = delete_field_list_template(template_id)
    if not next_id:
        return RedirectResponse(url="/field-list", status_code=303)
    return RedirectResponse(url=f"/field-list?template_id={next_id}&notice=field_list_saved", status_code=303)
