from __future__ import annotations

from app.services.google_sync_service import (
    get_current_access_role,
    get_google_sync_summary,
    is_editing_allowed,
    is_multi_editor_enabled,
    is_non_editor_access_role,
    normalize_access_role,
)


def _mobile_editor_needs_sync() -> bool:
    state = (get_google_sync_summary().get("state") or {})
    if bool(state.get("upload_in_progress") or 0):
        return False
    pending_upload = int(state.get("pending_upload_count") or 0)
    pending_drive_contacts = int(state.get("pending_contact_drive_export_count") or 0)
    return pending_upload > 0 or pending_drive_contacts > 0


def _access_name() -> str:
    state = (get_google_sync_summary().get("state") or {})
    return str(state.get("access_name") or "").strip()


def mobile_multi_editor_enabled() -> bool:
    return bool(is_multi_editor_enabled())


def mobile_is_non_editor(request=None) -> bool:
    if request is not None:
        session_role = normalize_access_role(str(request.session.get("mobile_access_role") or ""))
        session_name = str(request.session.get("mobile_access_name") or "").strip()
        if session_role == "non_editor" and session_name:
            return True
    if is_non_editor_access_role() and bool(_access_name()):
        return True
    return False


def mobile_is_editor(request=None) -> bool:
    if mobile_is_non_editor(request):
        return False
    if not mobile_multi_editor_enabled():
        return True
    return is_editing_allowed()


def mobile_can_open_contact_edit(request=None) -> bool:
    if not mobile_multi_editor_enabled():
        return True
    if mobile_is_non_editor(request):
        return True
    return is_editing_allowed()


def mobile_records_edits_to_pending_list(request=None) -> bool:
    return mobile_is_non_editor(request)


def mobile_access_context(request=None) -> dict:
    state = (get_google_sync_summary().get("state") or {})
    role = normalize_access_role(get_current_access_role())
    access_name = _access_name()
    if request is not None:
        session_role = normalize_access_role(str(request.session.get("mobile_access_role") or ""))
        session_name = str(request.session.get("mobile_access_name") or "").strip()
        if session_role:
            role = session_role
        if session_name:
            access_name = session_name
    non_editor = mobile_is_non_editor(request)
    return {
        "multi_editor_enabled": mobile_multi_editor_enabled(),
        "access_role": role,
        "access_name": access_name,
        "is_mobile_non_editor": non_editor,
        "is_mobile_editor": mobile_is_editor(request),
        "show_pending_edits_nav": non_editor,
        "sync_needs_attention": mobile_is_editor(request) and _mobile_editor_needs_sync(),
    }
