import os
import sys
from pathlib import Path

os.environ.setdefault("CONTACTSFREESHARE_USE_APP_DATA", "1")
if sys.platform == "darwin":
    os.environ.setdefault(
        "CONTACTSFREESHARE_DATA_DIR",
        str(Path.home() / "Library" / "Application Support" / "ContactsFreeShare"),
    )

import uvicorn

from app.config import HOST, PORT


if __name__ == "__main__":
    uvicorn.run("asgi:app", host=HOST, port=PORT, reload=True)
