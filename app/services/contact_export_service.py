import csv
import io
from datetime import datetime

from app.database import fetch_all
from app.services.contact_service import get_contact_detail


EXPORT_DATE_FORMAT = "%-m-%-d-%Y"


def export_contacts_filename(file_kind: str) -> str:
    normalized_kind = str(file_kind or "").strip().lower()
    extension = "csv" if normalized_kind == "csv" else "vcf"
    stamped_date = datetime.now().strftime(EXPORT_DATE_FORMAT)
    if normalized_kind == "google_csv":
        return f"ContactsFreeShare_GoogleContacts_{stamped_date}.csv"
    return f"ContactsFreeShare_Contacts_{stamped_date}.{extension}"


def _all_contact_details() -> list[dict]:
    rows = fetch_all(
        """
        SELECT id
        FROM contacts
        ORDER BY family_name, given_name, id
        """
    )
    contacts: list[dict] = []
    for row in rows:
        contact = get_contact_detail(int(row["id"]))
        if contact:
            contacts.append(contact)
    return contacts


def _join_values(items: list[str]) -> str:
    return "\n".join(str(item or "").strip() for item in items if str(item or "").strip())


def build_contacts_csv_bytes() -> tuple[str, bytes]:
    contacts = _all_contact_details()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "Family Name",
            "Given Name",
            "Fields",
            "Meetings",
            "Group Membership",
            "Birthday",
            "Notes",
            "Meeting/Home/Elder",
            "Meets in different state",
            "Photo URL",
            "Relationships",
            "Phones",
            "Emails",
            "Addresses",
            "Custom Fields",
            "Google Contact ID",
            "Google Etag",
            "Last Updated",
            "Status",
        ],
    )
    writer.writeheader()
    for contact in contacts:
        writer.writerow(
            {
                "Family Name": str(contact.get("family_name") or ""),
                "Given Name": str(contact.get("given_name") or ""),
                "Fields": _join_values(contact.get("fields_list") or []),
                "Meetings": _join_values(contact.get("meetings_list") or []),
                "Group Membership": _join_values(contact.get("group_membership_list") or []),
                "Birthday": str(contact.get("birthday") or ""),
                "Notes": str(contact.get("notes") or ""),
                "Meeting/Home/Elder": str(contact.get("mtg_home_elder_flag") or ""),
                "Meets in different state": str(contact.get("meets_in_state") or ""),
                "Photo URL": str(contact.get("photo") or ""),
                "Relationships": _join_values(
                    [
                        " | ".join(
                            part
                            for part in [
                                str(item.get("relation_type") or "").strip(),
                                str(item.get("relation_value") or "").strip(),
                            ]
                            if part
                        )
                        for item in (contact.get("relationships") or [])
                    ]
                ),
                "Phones": _join_values(
                    [
                        " | ".join(
                            part
                            for part in [
                                str(item.get("phone_type") or "").strip(),
                                str(item.get("phone_value") or "").strip(),
                            ]
                            if part
                        )
                        for item in (contact.get("phones") or [])
                    ]
                ),
                "Emails": _join_values(
                    [
                        " | ".join(
                            part
                            for part in [
                                str(item.get("email_type") or "").strip(),
                                str(item.get("email_value") or "").strip(),
                            ]
                            if part
                        )
                        for item in (contact.get("emails") or [])
                    ]
                ),
                "Addresses": _join_values(
                    [
                        " | ".join(
                            part
                            for part in [
                                str(item.get("address_type") or "").strip(),
                                str(item.get("formatted_address") or "").strip(),
                                str(item.get("coordinates") or "").strip(),
                            ]
                            if part
                        )
                        for item in (contact.get("addresses") or [])
                    ]
                ),
                "Custom Fields": _join_values(
                    [
                        " | ".join(
                            part
                            for part in [
                                str(item.get("field_type") or "").strip(),
                                str(item.get("field_value") or "").strip(),
                            ]
                            if part
                        )
                        for item in (contact.get("custom_fields") or [])
                    ]
                ),
                "Google Contact ID": str(contact.get("google_contact_id") or ""),
                "Google Etag": str(contact.get("etag") or ""),
                "Last Updated": str(contact.get("last_updated") or ""),
                "Status": str(contact.get("status") or ""),
            }
        )
    return export_contacts_filename("csv"), output.getvalue().encode("utf-8")


def build_google_contacts_csv_bytes() -> tuple[str, bytes]:
    contacts = _all_contact_details()
    output = io.StringIO(newline="")
    fieldnames = [
        "Name",
        "Given Name",
        "Family Name",
        "Birthday",
        "Notes",
        "Group Membership",
        "Organization 1 - Name",
        "Organization 1 - Title",
        "Phone 1 - Type",
        "Phone 1 - Value",
        "Phone 2 - Type",
        "Phone 2 - Value",
        "Phone 3 - Type",
        "Phone 3 - Value",
        "E-mail 1 - Type",
        "E-mail 1 - Value",
        "E-mail 2 - Type",
        "E-mail 2 - Value",
        "Address 1 - Type",
        "Address 1 - Formatted",
        "Address 2 - Type",
        "Address 2 - Formatted",
        "Address 3 - Type",
        "Address 3 - Formatted",
        "Relation 1 - Type",
        "Relation 1 - Value",
        "Relation 2 - Type",
        "Relation 2 - Value",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for contact in contacts:
        row = {
            "Name": ", ".join(
                part
                for part in [
                    str(contact.get("family_name") or "").strip(),
                    str(contact.get("given_name") or "").strip(),
                ]
                if part
            ),
            "Given Name": str(contact.get("given_name") or ""),
            "Family Name": str(contact.get("family_name") or ""),
            "Birthday": str(contact.get("birthday") or ""),
            "Notes": str(contact.get("notes") or ""),
            "Group Membership": str(contact.get("group_membership") or ""),
            "Organization 1 - Name": str(contact.get("meetings_text") or "").replace("\n", "; "),
            "Organization 1 - Title": str(contact.get("fields_text") or "").replace("\n", "; "),
        }

        for index, item in enumerate((contact.get("phones") or [])[:3], start=1):
            row[f"Phone {index} - Type"] = str(item.get("phone_type") or "")
            row[f"Phone {index} - Value"] = str(item.get("phone_value") or "")
        for index, item in enumerate((contact.get("emails") or [])[:2], start=1):
            row[f"E-mail {index} - Type"] = str(item.get("email_type") or "")
            row[f"E-mail {index} - Value"] = str(item.get("email_value") or "")
        for index, item in enumerate((contact.get("addresses") or [])[:3], start=1):
            row[f"Address {index} - Type"] = str(item.get("address_type") or "")
            row[f"Address {index} - Formatted"] = str(item.get("formatted_address") or "")
        for index, item in enumerate((contact.get("relationships") or [])[:2], start=1):
            row[f"Relation {index} - Type"] = str(item.get("relation_type") or "")
            row[f"Relation {index} - Value"] = str(item.get("relation_value") or "")

        writer.writerow(row)
    return export_contacts_filename("google_csv"), output.getvalue().encode("utf-8")


def _escape_vcard_text(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", r"\;")
        .replace(",", r"\,")
    )


def _vcard_line(name: str, value: str) -> str:
    return f"{name}:{_escape_vcard_text(value)}"


def build_contacts_vcard_bytes() -> tuple[str, bytes]:
    contacts = _all_contact_details()
    cards: list[str] = []
    for contact in contacts:
        family_name = str(contact.get("family_name") or "")
        given_name = str(contact.get("given_name") or "")
        display_name = ", ".join(part for part in [family_name, given_name] if part)
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            _vcard_line("N", f"{family_name};{given_name};;;"),
            _vcard_line("FN", display_name),
        ]
        if contact.get("birthday"):
            lines.append(_vcard_line("BDAY", str(contact.get("birthday") or "")))
        if contact.get("notes"):
            lines.append(_vcard_line("NOTE", str(contact.get("notes") or "")))
        if contact.get("photo"):
            lines.append(_vcard_line("PHOTO;VALUE=URI", str(contact.get("photo") or "")))
        if contact.get("fields_text"):
            lines.append(_vcard_line("X-CONTACTSFREESHARE-FIELDS", str(contact.get("fields_text") or "")))
        if contact.get("meetings_text"):
            lines.append(_vcard_line("X-CONTACTSFREESHARE-MEETINGS", str(contact.get("meetings_text") or "")))
        if contact.get("group_membership"):
            lines.append(_vcard_line("X-CONTACTSFREESHARE-GROUPS", str(contact.get("group_membership") or "")))
        if contact.get("mtg_home_elder_flag"):
            lines.append(_vcard_line("X-CONTACTSFREESHARE-MTG-HOME-ELDER", str(contact.get("mtg_home_elder_flag") or "")))
        if contact.get("meets_in_state"):
            lines.append(_vcard_line("X-CONTACTSFREESHARE-MEETS-IN-STATE", str(contact.get("meets_in_state") or "")))
        for item in contact.get("phones") or []:
            phone_value = str(item.get("phone_value") or "").strip()
            if not phone_value:
                continue
            phone_type = str(item.get("phone_type") or "").strip().upper() or "VOICE"
            lines.append(_vcard_line(f"TEL;TYPE={phone_type}", phone_value))
        for item in contact.get("emails") or []:
            email_value = str(item.get("email_value") or "").strip()
            if not email_value:
                continue
            email_type = str(item.get("email_type") or "").strip().upper() or "INTERNET"
            lines.append(_vcard_line(f"EMAIL;TYPE={email_type}", email_value))
        for item in contact.get("addresses") or []:
            formatted_address = str(item.get("formatted_address") or "").strip()
            if not formatted_address:
                continue
            address_type = str(item.get("address_type") or "").strip().upper() or "HOME"
            address_lines = [segment.strip() for segment in formatted_address.replace("\r", "\n").split("\n") if segment.strip()]
            street = "\\n".join(address_lines)
            lines.append(_vcard_line(f"ADR;TYPE={address_type}", f";;{street};;;;"))
            if item.get("coordinates"):
                lines.append(_vcard_line("X-CONTACTSFREESHARE-COORDINATES", str(item.get("coordinates") or "")))
        for item in contact.get("relationships") or []:
            relation_value = str(item.get("relation_value") or "").strip()
            if relation_value:
                lines.append(_vcard_line("X-CONTACTSFREESHARE-RELATION", relation_value))
        for item in contact.get("custom_fields") or []:
            field_type = str(item.get("field_type") or "").strip()
            field_value = str(item.get("field_value") or "").strip()
            if field_type or field_value:
                lines.append(_vcard_line("X-CONTACTSFREESHARE-CUSTOM", f"{field_type}: {field_value}".strip()))
        lines.append("END:VCARD")
        cards.append("\r\n".join(lines))
    return export_contacts_filename("vcard"), "\r\n".join(cards).encode("utf-8")
