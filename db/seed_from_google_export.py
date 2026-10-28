from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import IMPORTS_DIR
from app.database import initialize_database
from app.services.import_service import (
    import_contacts_csv,
    import_meetingdata_csv,
    reset_import_tables,
)
from app.services.meeting_v2_service import convert_legacy_meetingdata_to_v2


def main() -> None:
    contacts_csv = IMPORTS_DIR / "contacts_export.csv"
    meetingdata_csv = IMPORTS_DIR / "meetingdata_export.csv"

    if not contacts_csv.exists():
        raise FileNotFoundError(f"Missing contacts CSV: {contacts_csv}")
    if not meetingdata_csv.exists():
        raise FileNotFoundError(f"Missing MeetingData CSV: {meetingdata_csv}")

    initialize_database()
    reset_import_tables()

    contact_count = import_contacts_csv(contacts_csv)
    meeting_row_count = import_meetingdata_csv(meetingdata_csv)
    meeting_v2_section_count = convert_legacy_meetingdata_to_v2()

    print(f"Imported contacts: {contact_count}")
    print(f"Imported MeetingData rows: {meeting_row_count}")
    print(f"Converted MeetingData v2 sections: {meeting_v2_section_count}")


if __name__ == "__main__":
    main()
