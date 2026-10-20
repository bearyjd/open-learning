"""Validation and normalisation for user-provided plain text/Markdown notes.

Every rejection uses a fixed message: submitted text must never be echoed back.
"""

import re
import unicodedata
from dataclasses import dataclass

from fastapi import HTTPException

MIN_TEXT_CHARS = 100
MAX_TITLE_CHARS = 500
MAX_LABEL_CHARS = 100
NOTE_KEY_PREFIX = "note:"

# C0 controls except tab/newline, DEL, and C1 controls.
_TEXT_CONTROLS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Single-line fields reject every control character, including tab and newline.
_LINE_CONTROLS = re.compile("[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Note:
    title: str
    text: str
    label: str | None


def _reject(message: str):
    raise HTTPException(422, message)


def _require_utf8(value: str, field: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _reject(f"{field} must be valid UTF-8")


def _normalise_text(text: str, max_chars: int) -> str:
    _require_utf8(text, "Text")
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    # Check before strip(): str.strip() would silently drop edge controls such as U+0085.
    if _TEXT_CONTROLS.search(text):
        _reject("Text contains disallowed control characters")
    text = text.strip()
    if not MIN_TEXT_CHARS <= len(text) <= max_chars:
        _reject(f"Text must be between {MIN_TEXT_CHARS} and {max_chars} characters")
    return text


def _normalise_title(title: str) -> str:
    _require_utf8(title, "Title")
    title = unicodedata.normalize("NFC", title)
    if _LINE_CONTROLS.search(title):
        _reject("Title must be a single line without control characters")
    title = title.strip()
    if not 1 <= len(title) <= MAX_TITLE_CHARS:
        _reject(f"Title must be between 1 and {MAX_TITLE_CHARS} characters")
    return title


def _normalise_label(label: str | None) -> str | None:
    if label is None:
        return None
    _require_utf8(label, "Label")
    label = unicodedata.normalize("NFC", label)
    if _LINE_CONTROLS.search(label):
        _reject("Label must be a single line without control characters")
    label = label.strip()
    if len(label) > MAX_LABEL_CHARS:
        _reject(f"Label must be at most {MAX_LABEL_CHARS} characters")
    return label or None


def validate_note(title: str, text: str, label: str | None, max_chars: int) -> Note:
    return Note(
        title=_normalise_title(title),
        text=_normalise_text(text, max_chars),
        label=_normalise_label(label),
    )
