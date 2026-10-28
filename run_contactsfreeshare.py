import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


os.environ.setdefault("CONTACTSFREESHARE_USE_APP_DATA", "1")


def _candidate_data_dirs() -> list[Path]:
    candidates = []
    configured = os.getenv("CONTACTSFREESHARE_DATA_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    if sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "ContactsFreeShare")
    elif os.name == "nt":
        candidates.append(Path(os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")) / "ContactsFreeShare")
    else:
        candidates.append(Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "ContactsFreeShare")
    candidates.append(Path.home() / "ContactsFreeShareData")
    candidates.append(Path(__file__).resolve().parent / "runtime")
    return candidates


def _select_writable_data_dir() -> Path:
    errors = []
    for candidate in _candidate_data_dirs():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("No writable ContactsFreeShare data folder was found. Tried: " + " | ".join(errors))


os.environ["CONTACTSFREESHARE_DATA_DIR"] = str(_select_writable_data_dir())

RUNTIME_ENV_KEYS = [
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "GOOGLE_PICKER_API_KEY",
    "GOOGLE_PICKER_APP_ID",
    "CONTACTSFREESHARE_UPDATE_MANIFEST_URL",
]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={values.get(key, '')}" for key in RUNTIME_ENV_KEYS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_runtime_env_file() -> None:
    app_data_dir = Path(os.environ["CONTACTSFREESHARE_DATA_DIR"])
    app_data_dir.mkdir(parents=True, exist_ok=True)
    bundled_env = _read_env_file(Path(__file__).resolve().parent / ".env")
    runtime_env_path = app_data_dir / ".env"
    runtime_env = _read_env_file(runtime_env_path)
    changed = False

    for key in RUNTIME_ENV_KEYS:
        if runtime_env.get(key):
            continue
        if bundled_env.get(key):
            runtime_env[key] = bundled_env[key]
            changed = True

    if not runtime_env.get("GOOGLE_OAUTH_REDIRECT_URI"):
        runtime_env["GOOGLE_OAUTH_REDIRECT_URI"] = "http://127.0.0.1:8000/google/connect/callback"
        changed = True

    if changed or not runtime_env_path.exists():
        _write_env_file(runtime_env_path, runtime_env)


_seed_runtime_env_file()

try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

from app.config import APP_DATA_DIR, HOST, PORT  # noqa: E402

START_PATH = "/presets/google-connect"


def _ensure_runtime_files() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (APP_DATA_DIR / "db").mkdir(parents=True, exist_ok=True)
    (APP_DATA_DIR / "imports").mkdir(parents=True, exist_ok=True)
    (APP_DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (APP_DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)

    env_path = APP_DATA_DIR / ".env"
    if not env_path.exists():
        env_path.write_text(
            "\n".join(
                [
                    "GOOGLE_OAUTH_CLIENT_ID=",
                    "GOOGLE_OAUTH_CLIENT_SECRET=",
                    f"GOOGLE_OAUTH_REDIRECT_URI=http://{HOST}:{PORT}/google/connect/callback",
                    "CONTACTSFREESHARE_UPDATE_MANIFEST_URL=",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def _open_browser() -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://{HOST}:{PORT}{START_PATH}")


def _port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        return probe.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    _ensure_runtime_files()
    if _port_is_in_use():
        print(f"ContactsFreeShare already appears to be running at http://{HOST}:{PORT}")
        print("Opening the existing app window instead of starting a second server.")
        webbrowser.open(f"http://{HOST}:{PORT}{START_PATH}")
        raise SystemExit(0)

    threading.Thread(target=_open_browser, daemon=True).start()

    import uvicorn

    uvicorn.run("asgi:app", host=HOST, port=PORT, reload=False)
