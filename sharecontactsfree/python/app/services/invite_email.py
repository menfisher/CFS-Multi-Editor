from __future__ import annotations

from html import escape


KEEP_PAGE_OPEN_PLAIN = "KEEP THIS PAGE OPEN until the contacts finish downloading."
RETRY_IMPORT_PLAIN = (
    "If you close the page or open another tab, use the Retry button to restart the import."
)


def invite_display_name(
    group_name: str,
    *,
    settings_name: str = "",
    group_resource: str = "",
    settings_resource: str = "",
) -> str:
    """Prefer the Settings shared-group name when this share is that catch-all group."""
    google_name = str(group_name or "").strip()
    label = str(settings_name or "").strip()
    resource = str(group_resource or "").strip()
    settings_resource_name = str(settings_resource or "").strip()
    if label and settings_resource_name and resource == settings_resource_name:
        return label
    if label and google_name:
        from ..people_recipient import group_names_equivalent

        if group_names_equivalent(google_name, label):
            return label
    return google_name or label


def invite_email_plain_lines(
    owner_email: str,
    group_name: str,
    recipient_links: list[tuple[str, str]],
) -> list[str]:
    owner = str(owner_email or "").strip()
    label = str(group_name or "").strip() or "Shared"
    lines = [
        "Hi,",
        "",
        f'{owner} shared the contact group "{label}" with you.',
        "",
    ]
    if len(recipient_links) == 1:
        _email, url = recipient_links[0]
        lines.extend(
            [
                "Open this link to import shared contacts (sign in with the Google account that received this email):",
                url,
            ]
        )
    else:
        lines.append("Open your personal link below (sign in with the matching Google account):")
        for email, url in recipient_links:
            lines.extend(["", f"{email}:", url])
    lines.extend(["", KEEP_PAGE_OPEN_PLAIN, "", RETRY_IMPORT_PLAIN])
    return lines


def invite_email_html(
    owner_email: str,
    recipient_email: str,
    group_name: str,
    invite_url: str,
) -> str:
    owner = escape(str(owner_email or "").strip())
    recipient = escape(str(recipient_email or "").strip())
    label = escape(str(group_name or "").strip() or "Shared")
    url = escape(str(invite_url or "").strip(), quote=True)
    return (
        f"<p>Hi,</p>"
        f"<p>{owner} shared the contact group <b>{label}</b> with you.</p>"
        f"<p>Sign in with <b>{recipient}</b> when prompted.</p>"
        f'<p><a href="{url}">Open Share Google Contacts</a> to import.</p>'
        "<p><strong>Keep this page open until the contacts finish downloading.</strong></p>"
        "<p>If you close the page or open another tab, use the <strong>Retry</strong> "
        "button to restart the import.</p>"
    )
