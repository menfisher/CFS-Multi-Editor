import csv
from pathlib import Path

from app.database import get_connection


CONTACT_REPEAT_GROUPS = {
    "relationships": [
        ("Relation 1 - Type", "Relation 1 - Value", 1),
        ("Relation 2 - Type", "Relation 2 - Value", 2),
    ],
    "phones": [
        ("Phone 1 - Type", "Phone 1 - Value", 1),
        ("Phone 2 - Type", "Phone 2 - Value", 2),
        ("Phone 3 - Type", "Phone 3 - Value", 3),
    ],
    "addresses": [
        ("Address 1 - Type", "Address 1 - Formatted", "Address 1 - Coordinates", 1),
        ("Address 2 - Type", "Address 2 - Formatted", "Address 2 - Coordinates", 2),
        ("Address 3 - Type", "Address 3 - Formatted", "Address 3 - Coordinates", 3),
    ],
    "emails": [
        ("E-mail 1 - Type", "E-mail 1 - Value", 1),
        ("E-mail 2 - Type", "E-mail 2 - Value", 2),
    ],
    "custom_fields": [
        ("Custom Field 1 - Type", "Custom Field 1 - Value", 1),
        ("Custom Field 2 - Type", "Custom Field 2 - Value", 2),
        ("Custom Field 3 - Type", "Custom Field 3 - Value", 3),
    ],
}


def _clean(value: str) -> str:
    return (value or "").strip()


def _clean_row_value(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return _clean(row[index])


def reset_import_tables() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            DELETE FROM relationships;
            DELETE FROM phones;
            DELETE FROM addresses;
            DELETE FROM emails;
            DELETE FROM custom_fields;
            DELETE FROM google_sync_queue;
            DELETE FROM meeting_data_rows;
            DELETE FROM contacts;
            UPDATE google_sync_state
            SET
              pending_upload_count = 0,
              needs_upload_reminder = 0,
              needs_drive_export = 0,
              meetingdata_needs_drive_export = 0,
              last_sync_error = '',
              updated_at = CURRENT_TIMESTAMP
            WHERE id = 1;
            """
        )
        conn.commit()


def import_contacts_csv(csv_path: Path) -> int:
    inserted = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as handle, get_connection() as conn:
        reader = csv.DictReader(handle)
        for row in reader:
            cursor = conn.execute(
                """
                INSERT INTO contacts (
                  selected,
                  google_contact_id,
                  etag,
                  last_updated,
                  status,
                  fields_text,
                  meetings_text,
                  group_membership,
                  family_name,
                  given_name,
                  photo,
                  birthday,
                  notes,
                  mtg_home_elder_flag,
                  meets_in_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _clean(row.get("Selected")),
                    _clean(row.get("Contact ID")),
                    _clean(row.get("Etag")),
                    _clean(row.get("Last Updated")),
                    _clean(row.get("Status")),
                    _clean(row.get("Fields")),
                    _clean(row.get("Meetings")),
                    _clean(row.get("Group Membership")),
                    _clean(row.get("Family Name")),
                    _clean(row.get("Given Name")),
                    _clean(row.get("Photo")),
                    _clean(row.get("Birthday")),
                    _clean(row.get("Notes")),
                    _clean(row.get("Mtg home-1/Elder-2") or row.get("Meeting/Home/Elder")),
                    _clean(row.get("Meets in different state")),
                ),
            )
            contact_id = cursor.lastrowid

            for type_key, value_key, position in CONTACT_REPEAT_GROUPS["relationships"]:
                relation_type = _clean(row.get(type_key))
                relation_value = _clean(row.get(value_key))
                if relation_type or relation_value:
                    conn.execute(
                        """
                        INSERT INTO relationships (contact_id, position, relation_type, relation_value)
                        VALUES (?, ?, ?, ?)
                        """,
                        (contact_id, position, relation_type, relation_value),
                    )

            for type_key, value_key, position in CONTACT_REPEAT_GROUPS["phones"]:
                phone_type = _clean(row.get(type_key))
                phone_value = _clean(row.get(value_key))
                if phone_type or phone_value:
                    conn.execute(
                        """
                        INSERT INTO phones (contact_id, position, phone_type, phone_value)
                        VALUES (?, ?, ?, ?)
                        """,
                        (contact_id, position, phone_type, phone_value),
                    )

            for type_key, value_key, coord_key, position in CONTACT_REPEAT_GROUPS["addresses"]:
                address_type = _clean(row.get(type_key))
                formatted_address = _clean(row.get(value_key))
                coordinates = _clean(row.get(coord_key))
                if address_type or formatted_address or coordinates:
                    conn.execute(
                        """
                        INSERT INTO addresses (
                          contact_id,
                          position,
                          address_type,
                          formatted_address,
                          coordinates
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (contact_id, position, address_type, formatted_address, coordinates),
                    )

            for type_key, value_key, position in CONTACT_REPEAT_GROUPS["emails"]:
                email_type = _clean(row.get(type_key))
                email_value = _clean(row.get(value_key))
                if email_type or email_value:
                    conn.execute(
                        """
                        INSERT INTO emails (contact_id, position, email_type, email_value)
                        VALUES (?, ?, ?, ?)
                        """,
                        (contact_id, position, email_type, email_value),
                    )

            for type_key, value_key, position in CONTACT_REPEAT_GROUPS["custom_fields"]:
                field_type = _clean(row.get(type_key))
                field_value = _clean(row.get(value_key))
                if field_type or field_value:
                    conn.execute(
                        """
                        INSERT INTO custom_fields (contact_id, position, field_type, field_value)
                        VALUES (?, ?, ?, ?)
                        """,
                        (contact_id, position, field_type, field_value),
                    )

            inserted += 1

        conn.commit()
    return inserted


def import_meetingdata_csv(csv_path: Path) -> int:
    inserted = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as handle, get_connection() as conn:
        reader = csv.reader(handle)
        next(reader, None)
        for sheet_row_number, row in enumerate(reader, start=2):
            conn.execute(
                """
                INSERT INTO meeting_data_rows (
                  sheet_row_number,
                  field_name,
                  meeting_name,
                  format_code,
                  col_d,
                  col_e,
                  col_f,
                  col_g,
                  col_h,
                  col_i,
                  col_j,
                  col_k,
                  col_l,
                  col_m,
                  col_n,
                  col_o,
                  col_p,
                  col_q,
                  col_r,
                  col_s,
                  col_t,
                  col_u,
                  col_v,
                  col_w,
                  col_x,
                  col_y,
                  col_z,
                  col_aa,
                  col_ab,
                  col_ac,
                  col_ad,
                  col_ae
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sheet_row_number,
                    _clean_row_value(row, 0),
                    _clean_row_value(row, 1),
                    _clean_row_value(row, 2),
                    _clean_row_value(row, 3),
                    _clean_row_value(row, 4),
                    _clean_row_value(row, 5),
                    _clean_row_value(row, 6),
                    _clean_row_value(row, 7),
                    _clean_row_value(row, 8),
                    _clean_row_value(row, 9),
                    _clean_row_value(row, 10),
                    _clean_row_value(row, 11),
                    _clean_row_value(row, 12),
                    _clean_row_value(row, 13),
                    _clean_row_value(row, 14),
                    _clean_row_value(row, 15),
                    _clean_row_value(row, 16),
                    _clean_row_value(row, 17),
                    _clean_row_value(row, 18),
                    _clean_row_value(row, 19),
                    _clean_row_value(row, 20),
                    _clean_row_value(row, 21),
                    _clean_row_value(row, 22),
                    _clean_row_value(row, 23),
                    _clean_row_value(row, 24),
                    _clean_row_value(row, 25),
                    _clean_row_value(row, 26),
                    _clean_row_value(row, 27),
                    _clean_row_value(row, 28),
                    _clean_row_value(row, 29),
                    _clean_row_value(row, 30),
                ),
            )
            inserted += 1

        conn.commit()
    return inserted
