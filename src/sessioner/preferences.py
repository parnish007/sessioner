"""Small persisted desktop preferences, separate from account credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Callable

from sessioner.accounts.fsutil import replace_with_retry
from sessioner.accounts.locking import FileLock


FILE = "sessioner-preferences.json"
DEFAULTS = {"statisticsEnabled": True, "notificationsEnabled": True}
MAX_BYTES = 65536


def read_json(path: Path, *, max_bytes: int = MAX_BYTES) -> dict:
    """Read a bounded object. Missing or damaged local state has no valid fields."""
    try:
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            return {}
        value = json.loads(content)
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PreferenceStore:
    def __init__(self, path: Path, *, on_change: Callable[[dict], None] | None = None):
        self.path = Path(path)
        self.on_change = on_change

    def read(self) -> dict:
        value = read_json(self.path)
        return {key: value[key] if isinstance(value.get(key), bool) else default for key, default in DEFAULTS.items()}

    def update(self, **changes: bool) -> dict:
        from sessioner.service import SessionerError

        if any(key not in DEFAULTS or not isinstance(value, bool) for key, value in changes.items()):
            raise SessionerError("Choose a boolean value for a known Sessioner preference.")
        try:
            with FileLock(self.path.with_name(f".{self.path.name}.lock"), timeout=5):
                value = read_json(self.path)
                for key, default in DEFAULTS.items():
                    if not isinstance(value.get(key), bool):
                        value[key] = default
                value.update(changes)
                atomic_json(self.path, value)
                result = {key: value[key] for key in DEFAULTS}
        except Exception as exc:
            raise SessionerError("Could not save Sessioner preferences.", "Check access to the Sessioner account folder, then retry") from exc
        # Call outside the file lock: privacy callbacks wait for any ongoing scan.
        if self.on_change:
            self.on_change(dict(result))
        return result
