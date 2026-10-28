from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.services.app_info_service import get_app_edition_label, get_current_app_version
from app.services.field_list_service import contact_notes_html
from app.services.google_sync_service import get_google_sync_summary, is_share_contacts_nav_visible
from app.services.share_web_app_service import share_web_app_url


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_version"] = get_current_app_version
templates.env.globals["app_edition_label"] = get_app_edition_label
templates.env.globals["google_sync"] = get_google_sync_summary
templates.env.globals["share_contacts_nav_visible"] = is_share_contacts_nav_visible
templates.env.globals["share_web_app_url"] = share_web_app_url
templates.env.globals["contact_notes_html"] = contact_notes_html
