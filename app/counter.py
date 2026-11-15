"""Count successful conversions, kept in a small file so the number survives restarts.

Only the number is stored, nothing about the files or who converted them.
"""
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("markitdown-web")


class Counter:
    """Thread-safe counter; without a usable file it only counts in memory."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._value = self._load()

    def _load(self) -> int:
        if self._path is None:
            return 0
        try:
            text = self._path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return 0
        except OSError as exc:
            log.warning("counter file unreadable: %s", type(exc).__name__)
            return 0
        return int(text) if text.isdigit() else 0

    def _save(self, value: int) -> None:
        if self._path is None:
            return
        # Write next to the target and rename, so a crash never leaves half a number.
        temporary = self._path.with_name(self._path.name + ".tmp")
        try:
            temporary.write_text(f"{value}\n", encoding="ascii")
            os.replace(temporary, self._path)
        except OSError as exc:
            log.warning("counter file not writable: %s", type(exc).__name__)

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def increment(self) -> int:
        with self._lock:
            self._value += 1
            self._save(self._value)
            return self._value
