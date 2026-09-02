import os
from pathlib import Path


def _read_version() -> str:
    env = os.environ.get("XVPN_PANEL_VERSION", "").strip()
    if env:
        return env.lstrip("vV")

    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value.lstrip("vV")
    except OSError:
        pass
    return "1.1"


APP_VERSION = _read_version()
