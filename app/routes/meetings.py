import traceback
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.services.backup_service import build_meetingdata_backup_bytes
from app.services.contact_service import (
    get_meeting_filter_options,
    get_meeting_row_detail,
    get_meeting_sections,
    rename_contact_assignment_option,
)
from app.services.google_sync_service import export_backup_file_to_google_drive
from app.services.google_sync_service import get_current_editor_initials
from app.services.google_sync_service import get_edit_block_notice_key
from app.services.google_sync_service import get_notice_message
from app.services.google_sync_service import get_google_sync_summary
from app.services.google_sync_service import is_editing_allowed
from app.services.google_sync_service import mark_meetingdata_for_drive_export
from app.services.field_end_list_service import (
    add_field_end_list,
    delete_field_end_list,
    list_field_end_lists,
    rename_field_end_list,
    reorder_field_end_lists,
)
from app.services.meeting_v2_service import (
    add_meeting_v2_cell,
    add_meeting_v2_stacked_cell,
    add_meeting_v2_name_option,
    create_meeting_v2_section,
    add_meeting_v2_flow_item,
    add_meeting_v2_row,
    delete_meeting_v2_name_option,
    delete_meeting_v2_section,
    delete_meeting_v2_flow_item,
    delete_meeting_v2_cell,
    delete_meeting_v2_flow_column,
    delete_meeting_v2_row,
    get_meeting_v2_filter_options,
    list_meeting_v2_edit_batches,
    get_meeting_v2_row_colors,
    get_meeting_v2_section_editor,
    get_meeting_v2_section_detail,
    get_meeting_v2_section_print_preview,
    list_meeting_v2_sections,
    move_meeting_v2_flow_column,
    move_meeting_v2_flow_item,
    save_meeting_v2_section,
    update_meeting_v2_row_kind,
)
from app.web_templates import templates


router = APIRouter(prefix="/meetings", tags=["meetings"])

def _log_meeting_route(message: str) -> None:
    return None


def _no_edit_redirect(return_to: str = "/meetings/v2/sections") -> RedirectResponse:
    safe_return_to = return_to if str(return_to or "").startswith("/meetings") else "/meetings/v2/sections"
    separator = "&" if "?" in safe_return_to else "?"
    return RedirectResponse(url=f"{safe_return_to}{separator}notice={get_edit_block_notice_key()}", status_code=303)


def _export_shared_after_meeting_edit() -> None:
    mark_meetingdata_for_drive_export()


def _parse_meeting_v2_section_form(form) -> dict:
    row_updates = []
    cell_updates = []
    flow_item_updates = []
    flow_column_updates = []

    row_ids = [value for value in form.getlist("row_ids") if str(value).strip()]
    cell_ids = [value for value in form.getlist("cell_ids") if str(value).strip()]
    flow_item_ids = [value for value in form.getlist("flow_item_ids") if str(value).strip()]
    flow_column_cell_ids = [value for value in form.getlist("flow_column_cell_ids") if str(value).strip()]
    flow_column_keys = [value for value in form.getlist("flow_column_keys") if str(value).strip()]

    for row_id in row_ids:
        row_kind_values = [str(value or "").strip() for value in form.getlist(f"row_kind_{row_id}") if str(value or "").strip()]
        row_updates.append(
            {
                "id": int(row_id),
                "row_order": int(form.get(f"row_order_{row_id}", 0) or 0),
                "row_kind": row_kind_values or [str(form.get(f"row_kind_{row_id}", "content") or "content")],
                "group_with_table_below": int(form.get(f"group_with_table_below_{row_id}", 0) or 0),
                "no_meeting_association": int(form.get(f"no_meeting_association_{row_id}", 0) or 0),
                "print_new_page": int(form.get(f"print_new_page_{row_id}", 0) or 0),
                "format_code": str(form.get(f"format_code_{row_id}", "") or ""),
                "label_text": str(form.get(f"label_text_{row_id}", "") or ""),
                "notes": str(form.get(f"row_notes_{row_id}", "") or ""),
            }
        )

    for cell_id in cell_ids:
        cell_updates.append(
            {
                "id": int(cell_id),
                "col_start": int(form.get(f"col_start_{cell_id}", 1) or 1),
                "col_span": int(form.get(f"col_span_{cell_id}", 1) or 1),
                "text_value": str(form.get(f"text_value_{cell_id}", "") or ""),
                "text_align": str(form.get(f"text_align_{cell_id}", "") or ""),
                "is_bold": int(form.get(f"is_bold_{cell_id}", 0) or 0),
                "is_italic": int(form.get(f"is_italic_{cell_id}", 0) or 0),
                "is_underlined": int(form.get(f"is_underlined_{cell_id}", 0) or 0),
                "flow_min_lines": str(form.get(f"flow_min_lines_{cell_id}", "") or "").strip(),
            }
        )

    for item_id in flow_item_ids:
        flow_item_updates.append(
            {
                "id": int(item_id),
                "text_value": str(form.get(f"flow_text_value_{item_id}", "") or ""),
                "text_align": str(form.get(f"flow_text_align_{item_id}", "left") or "left"),
                "is_bold": int(form.get(f"flow_is_bold_{item_id}", 0) or 0),
                "is_italic": int(form.get(f"flow_is_italic_{item_id}", 0) or 0),
                "is_underlined": int(form.get(f"flow_is_underlined_{item_id}", 0) or 0),
                "font_scale": float(form.get(f"flow_font_scale_{item_id}", 1.0) or 1.0),
            }
        )

    for cell_id in flow_column_cell_ids:
        flow_column_updates.append(
            {
                "cell_id": int(cell_id),
                "row_id": int(form.get(f"flow_column_row_{cell_id}", 0) or 0),
                "text_value": str(form.get(f"flow_column_text_{cell_id}", "") or ""),
            }
        )

    for key in flow_column_keys:
        row_id_text, column_index_text = str(key).split(":", 1)
        flow_column_updates.append(
            {
                "row_id": int(row_id_text),
                "column_index": int(column_index_text),
                "text_value": str(form.get(f"flow_column_text_{row_id_text}_{column_index_text}", "") or ""),
            }
        )

    return {
        "section_notes": str(form.get("section_notes", "") or ""),
        "book_layout_preset_id": int(form.get("book_layout_preset_id", 0) or 0),
        "base_font_size_pt": str(form.get("meeting_base_font_size_pt", "") or ""),
        "meeting_table_column_count": form.get("meeting_table_column_count"),
        "meeting_table_column_gap_px": form.get("meeting_table_column_gap_px"),
        "screen_preview_scale": form.get("screen_preview_scale"),
        "line_height": form.get("meeting_line_height"),
        "row_updates": row_updates,
        "cell_updates": cell_updates,
        "flow_item_updates": flow_item_updates,
        "flow_column_updates": flow_column_updates,
    }


def _save_meeting_v2_section_from_form(section_id: int, form) -> None:
    parsed = _parse_meeting_v2_section_form(form)
    save_meeting_v2_section(
        section_id=section_id,
        section_notes=parsed["section_notes"],
        book_layout_preset_id=parsed["book_layout_preset_id"],
        base_font_size_pt=parsed["base_font_size_pt"],
        meeting_table_column_count=parsed["meeting_table_column_count"],
        meeting_table_column_gap_px=parsed["meeting_table_column_gap_px"],
        screen_preview_scale=parsed["screen_preview_scale"],
        line_height=parsed["line_height"],
        editor_initials=get_current_editor_initials(),
        row_updates=parsed["row_updates"],
        cell_updates=parsed["cell_updates"],
        flow_item_updates=parsed["flow_item_updates"],
        flow_column_updates=parsed["flow_column_updates"],
    )


@router.get("/backup/json")
def meetingdata_backup_download():
    filename, content = build_meetingdata_backup_bytes()
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/backup/json/drive")
def meetingdata_backup_drive():
    filename, content = build_meetingdata_backup_bytes()
    try:
        export_backup_file_to_google_drive(filename, content, "application/json")
    except RuntimeError as exc:
        message = str(exc)
        if "Drive access has not been granted" in message:
            notice = "drive_scope_missing"
        elif "not connected" in message.lower() or "refresh token" in message.lower():
            notice = "drive_not_connected"
        else:
            notice = "meetingdata_backup_drive_failed"
        return RedirectResponse(url=f"/meetings/v2/sections?notice={notice}", status_code=303)
    except (HTTPError, URLError):
        return RedirectResponse(url="/meetings/v2/sections?notice=meetingdata_backup_drive_failed", status_code=303)
    return RedirectResponse(url="/meetings/v2/sections?notice=meetingdata_backup_drive_success", status_code=303)


@router.get("/", response_class=HTMLResponse)
def meetings_list(
    request: Request,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
):
    return templates.TemplateResponse(
        request=request,
        name="meetings/list.html",
        context={
            "sections": get_meeting_sections(q, field, meeting),
            "filters": get_meeting_filter_options(),
            "query": q,
            "field_filter": field,
            "meeting_filter": meeting,
        },
    )


@router.get("/v2/sections", response_class=HTMLResponse)
def meeting_v2_sections(
    request: Request,
    q: str = Query(default=""),
    field: str = Query(default=""),
    meeting: str = Query(default=""),
    notice: str = Query(default=""),
):
    try:
        _log_meeting_route("sections list start")
        sections = list_meeting_v2_sections(q, field, meeting)
        _log_meeting_route(f"sections list loaded sections count={len(sections)}")
        filters = get_meeting_v2_filter_options(field)
        _log_meeting_route("sections list loaded filters")
        row_colors = get_meeting_v2_row_colors()
        _log_meeting_route("sections list loaded row colors")
        google_sync = get_google_sync_summary()
        _log_meeting_route("sections list loaded google sync")
        response = templates.TemplateResponse(
            request=request,
            name="meetings/v2_sections.html",
            context={
                "sections": sections,
                "filters": filters,
                "row_colors": row_colors,
                "notice_message": get_notice_message(notice),
                "google_sync": google_sync,
                "query": q,
                "field_filter": field,
                "meeting_filter": meeting,
            },
        )
        _log_meeting_route("sections list template response created")
        return response
    except BaseException as exc:
        _log_meeting_route("sections list failed")
        _log_meeting_route("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise


@router.get("/edit-list", response_class=HTMLResponse)
def meeting_v2_edit_list(request: Request):
    try:
        _log_meeting_route("edit list start")
        edit_batches = list_meeting_v2_edit_batches()
        _log_meeting_route(f"edit list loaded batches count={len(edit_batches)}")
        row_colors = get_meeting_v2_row_colors()
        _log_meeting_route("edit list loaded row colors")
        response = templates.TemplateResponse(
            request=request,
            name="meetings/edit_list.html",
            context={
                "edit_batches": edit_batches,
                "row_colors": row_colors,
            },
        )
        _log_meeting_route("edit list template response created")
        return response
    except BaseException as exc:
        _log_meeting_route("edit list failed")
        _log_meeting_route("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise


@router.get("/v2/field-end-lists")
async def meeting_v2_list_field_end_lists(field_name: str = Query("")):
    return JSONResponse({"lists": list_field_end_lists(field_name)})


@router.post("/v2/field-end-lists")
async def meeting_v2_add_field_end_list(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    try:
        saved = add_field_end_list(str(form.get("field_name") or ""), str(form.get("list_name") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse(saved)


@router.post("/v2/field-end-lists/reorder")
async def meeting_v2_reorder_field_end_lists(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    field_name = str(form.get("field_name") or "")
    list_names = [str(value) for value in form.getlist("list_names") if str(value).strip()]
    try:
        lists = reorder_field_end_lists(field_name, list_names)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse({"lists": lists})


@router.post("/v2/field-end-lists/rename")
async def meeting_v2_rename_field_end_list(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    try:
        saved = rename_field_end_list(
            str(form.get("field_name") or ""),
            str(form.get("old_list_name") or ""),
            str(form.get("new_list_name") or ""),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse(saved)


@router.post("/v2/field-end-lists/remove")
async def meeting_v2_remove_field_end_list(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    try:
        removed = delete_field_end_list(str(form.get("field_name") or ""), str(form.get("list_name") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse(removed)


@router.post("/v2/section-name-options")
async def meeting_v2_add_name_option(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    try:
        saved_value = add_meeting_v2_name_option(kind, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse({"value": saved_value})


@router.post("/v2/sections/create")
async def meeting_v2_create_section(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    field_name = str(form.get("field_name") or "")
    meeting_name = str(form.get("meeting_name") or "")
    try:
        section_id = create_meeting_v2_section(field_name, meeting_name)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse(
        {
            "section_id": section_id,
            "editor_url": f"/meetings/v2/sections/{section_id}/editor#row-1",
        }
    )


@router.post("/v2/section-name-options/remove")
async def meeting_v2_remove_name_option(request: Request):
    if not is_editing_allowed():
        return JSONResponse({"error": "The app is in NO-EDIT mode."}, status_code=403)
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    try:
        removed_value = delete_meeting_v2_name_option(kind, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _export_shared_after_meeting_edit()
    return JSONResponse({"value": removed_value})


@router.post("/v2/section-name-options/rename")
async def meeting_v2_rename_name_option(request: Request):
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
    _export_shared_after_meeting_edit()
    return JSONResponse(result)


@router.get("/v2/sections/{section_id}", response_class=HTMLResponse)
def meeting_v2_section_detail(request: Request, section_id: int):
    section = get_meeting_v2_section_detail(section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Meeting v2 section not found")

    return templates.TemplateResponse(
        request=request,
        name="meetings/v2_section_detail.html",
        context={
            "section": section,
            "google_sync": get_google_sync_summary(),
        },
    )


@router.get("/v2/sections/{section_id}/editor", response_class=HTMLResponse)
def meeting_v2_section_editor(
    request: Request,
    section_id: int,
    last_insert_type: str = Query(default="blank"),
):
    try:
        _log_meeting_route(f"editor start section_id={section_id}")
        section = get_meeting_v2_section_editor(section_id)
        _log_meeting_route(f"editor loaded section exists={bool(section)}")
        if not section:
            raise HTTPException(status_code=404, detail="Meeting v2 section not found")
        section["last_insert_type"] = last_insert_type if last_insert_type in {"blank", "clone", "new", "divider"} else "blank"
        field_name = str(section.get("field_name") or "")
        field_meeting_sections = list_meeting_v2_sections("", field_name, "")
        current_id = int(section.get("id") or 0)
        if current_id and not any(int(item.get("id") or 0) == current_id for item in field_meeting_sections):
            field_meeting_sections = [
                {"id": current_id, "meeting_name": section.get("meeting_name") or ""},
                *field_meeting_sections,
            ]
        section["field_meeting_sections"] = field_meeting_sections
        section["field_end_lists"] = [
            str(item.get("list_name") or "").strip()
            for item in list_field_end_lists(field_name)
            if str(item.get("list_name") or "").strip()
        ]
        meeting_name = str(section.get("meeting_name") or "").strip()
        section["is_field_end_list"] = meeting_name in section["field_end_lists"]
        row_colors = get_meeting_v2_row_colors()
        google_sync = get_google_sync_summary()
        _log_meeting_route("editor loaded google sync")
        response = templates.TemplateResponse(
            request=request,
            name="meetings/v2_section_editor.html",
            context={
                "section": section,
                "row_colors": row_colors,
                "google_sync": google_sync,
            },
        )
        _log_meeting_route("editor template response created")
        return response
    except BaseException as exc:
        _log_meeting_route("editor failed")
        _log_meeting_route("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise


@router.get("/v2/sections/{section_id}/print-preview", response_class=HTMLResponse)
def meeting_v2_section_print_preview_page(request: Request, section_id: int):
    try:
        _log_meeting_route(f"print preview start section_id={section_id}")
        section = get_meeting_v2_section_print_preview(section_id)
        _log_meeting_route(f"print preview loaded section exists={bool(section)}")
        if not section:
            raise HTTPException(status_code=404, detail="Meeting v2 section not found")
        response = templates.TemplateResponse(
            request=request,
            name="meetings/v2_print_preview.html",
            context={
                "section": section,
            },
        )
        _log_meeting_route("print preview template response created")
        return response
    except BaseException as exc:
        _log_meeting_route("print preview failed")
        _log_meeting_route("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise


@router.post("/v2/sections/{section_id}/save")
async def meeting_v2_section_save(request: Request, section_id: int):
    form = await request.form()
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    return_to = str(form.get("return_to", "detail") or "detail")
    _save_meeting_v2_section_from_form(section_id, form)
    editor_scroll_y = str(form.get("editor_scroll_y", "") or "").strip()
    target = f"/meetings/v2/sections/{section_id}/editor" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    if return_to == "editor" and editor_scroll_y:
        target = f"{target}?scroll={editor_scroll_y}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/delete")
async def meeting_v2_section_delete(section_id: int):
    if not is_editing_allowed():
        return _no_edit_redirect("/meetings/v2/sections")
    delete_meeting_v2_section(section_id)
    _export_shared_after_meeting_edit()
    return RedirectResponse(url="/meetings/v2/sections", status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/insert-below/{insert_type}")
async def meeting_v2_row_insert_below(request: Request, section_id: int, row_id: int, insert_type: str):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor" if return_to == "editor" else f"/meetings/v2/sections/{section_id}")
    _save_meeting_v2_section_from_form(section_id, form)
    normalized_insert_type = "clone" if insert_type == "clone" else "new" if insert_type == "new" else "divider" if insert_type == "divider" else "blank"
    if normalized_insert_type == "clone":
        new_row_id = add_meeting_v2_row(
            section_id,
            after_row_id=row_id,
            row_kind="content",
            clone_from_row_id=row_id,
        )
    elif normalized_insert_type == "new":
        new_row_id = add_meeting_v2_row(section_id, after_row_id=row_id, row_kind="content")
    elif normalized_insert_type == "divider":
        new_row_id = add_meeting_v2_row(section_id, after_row_id=row_id, row_kind="divider")
    else:
        new_row_id = add_meeting_v2_row(section_id, after_row_id=row_id, row_kind="blank")
    if return_to == "editor":
        target = f"/meetings/v2/sections/{section_id}/editor?last_insert_type={normalized_insert_type}#row-{new_row_id}"
    else:
        target = f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/delete")
async def meeting_v2_row_delete(request: Request, section_id: int, row_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor" if return_to == "editor" else f"/meetings/v2/sections/{section_id}")
    _save_meeting_v2_section_from_form(section_id, form)
    delete_meeting_v2_row(section_id, row_id)
    editor_scroll_y = str(form.get("editor_scroll_y", "") or "").strip()
    target = f"/meetings/v2/sections/{section_id}/editor" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    if return_to == "editor" and editor_scroll_y:
        target = f"{target}?scroll={editor_scroll_y}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/cells/add")
async def meeting_v2_cell_add(request: Request, section_id: int, row_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    row_kind = str(form.get(f"row_kind_{row_id}", "content") or "content").strip() or "content"
    update_meeting_v2_row_kind(section_id, row_id, row_kind)
    add_meeting_v2_cell(section_id, row_id)
    target = f"/meetings/v2/sections/{section_id}/editor#row-{row_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/cells/add-stacked")
async def meeting_v2_stacked_cell_add(request: Request, section_id: int, row_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    row_kind = str(form.get(f"row_kind_{row_id}", "content") or "content").strip() or "content"
    update_meeting_v2_row_kind(section_id, row_id, row_kind)
    under_cell_id = int(str(form.get("under_cell_id") or "0").strip() or "0")
    if under_cell_id:
        add_meeting_v2_stacked_cell(section_id, row_id, under_cell_id)
    target = f"/meetings/v2/sections/{section_id}/editor#row-{row_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/cells/{cell_id}/delete")
async def meeting_v2_cell_delete(request: Request, section_id: int, row_id: int, cell_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    delete_meeting_v2_cell(section_id, row_id, cell_id)
    target = f"/meetings/v2/sections/{section_id}/editor#row-{row_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/columns/{cell_id}/move-{direction}")
async def meeting_v2_flow_column_move(request: Request, section_id: int, row_id: int, cell_id: int, direction: str):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    normalized_direction = "left" if direction == "left" else "right"
    move_meeting_v2_flow_column(section_id, row_id, cell_id, normalized_direction)
    target = f"/meetings/v2/sections/{section_id}/editor#row-{row_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/columns/{cell_id}/delete")
async def meeting_v2_flow_column_delete(request: Request, section_id: int, row_id: int, cell_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    delete_meeting_v2_flow_column(section_id, row_id, cell_id)
    target = f"/meetings/v2/sections/{section_id}/editor#row-{row_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/flow-columns/{column_index}/items/add")
async def meeting_v2_flow_item_add(request: Request, section_id: int, row_id: int, column_index: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    new_item_id = add_meeting_v2_flow_item(section_id, row_id, column_index)
    anchor = f"#flow-item-{new_item_id}" if new_item_id else f"#row-{row_id}"
    target = f"/meetings/v2/sections/{section_id}/editor{anchor}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/flow-items/{item_id}/add-above")
async def meeting_v2_flow_item_add_above(request: Request, section_id: int, row_id: int, item_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    column_index = int(form.get(f"flow_column_index_{item_id}", 1) or 1)
    new_item_id = add_meeting_v2_flow_item(section_id, row_id, column_index, before_item_id=item_id)
    anchor = f"#flow-item-{new_item_id}" if new_item_id else f"#row-{row_id}"
    target = f"/meetings/v2/sections/{section_id}/editor{anchor}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/flow-items/{item_id}/add-below")
async def meeting_v2_flow_item_add_below(request: Request, section_id: int, row_id: int, item_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    column_index = int(form.get(f"flow_column_index_{item_id}", 1) or 1)
    new_item_id = add_meeting_v2_flow_item(section_id, row_id, column_index, after_item_id=item_id)
    anchor = f"#flow-item-{new_item_id}" if new_item_id else f"#row-{row_id}"
    target = f"/meetings/v2/sections/{section_id}/editor{anchor}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/flow-items/{item_id}/delete")
async def meeting_v2_flow_item_delete(request: Request, section_id: int, row_id: int, item_id: int):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    delete_meeting_v2_flow_item(section_id, row_id, item_id)
    target = f"/meetings/v2/sections/{section_id}/editor#row-{row_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.post("/v2/sections/{section_id}/rows/{row_id}/flow-items/{item_id}/move-{direction}")
async def meeting_v2_flow_item_move(request: Request, section_id: int, row_id: int, item_id: int, direction: str):
    form = await request.form()
    return_to = str(form.get("return_to", "detail") or "detail")
    if not is_editing_allowed():
        return _no_edit_redirect(f"/meetings/v2/sections/{section_id}/editor")
    _save_meeting_v2_section_from_form(section_id, form)
    normalized_direction = "up" if direction == "up" else "down"
    move_meeting_v2_flow_item(section_id, row_id, item_id, normalized_direction)
    target = f"/meetings/v2/sections/{section_id}/editor#flow-item-{item_id}" if return_to == "editor" else f"/meetings/v2/sections/{section_id}"
    _export_shared_after_meeting_edit()
    return RedirectResponse(url=target, status_code=303)


@router.get("/{row_id}", response_class=HTMLResponse)
def meeting_detail(request: Request, row_id: int):
    meeting_row = get_meeting_row_detail(row_id)
    if not meeting_row:
        raise HTTPException(status_code=404, detail="Meeting row not found")

    return templates.TemplateResponse(
        request=request,
        name="meetings/detail.html",
        context={
            "meeting_row": meeting_row,
        },
    )
