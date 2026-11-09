"""Small, atomic per-user settings store."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def settings_path() -> Path:
    """Return the writable settings location without creating directories."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "LishaBalancer" / "settings.json"


def load_settings() -> dict[str, Any]:
    """Missing, unreadable, malformed, or non-object settings use UI defaults."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    """Replace the complete settings file atomically; report write errors to UI."""
    if not isinstance(settings, dict):
        raise TypeError("Settings must be a dictionary.")
    content = json.dumps(settings, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    target = settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".settings-", suffix=".tmp", dir=target.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
