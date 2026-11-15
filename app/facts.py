"""File facts shown next to the conversion result (format, size, pages, metadata).

Everything here reads untrusted input and must never raise: a missing or broken
value is simply left out. Nothing from this module is logged (titles are content).
"""
import re
import zipfile
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

MAX_TEXT_CHARS = 200
# docProps/app.xml is tiny in practice; don't inflate anything bigger.
MAX_APP_XML_BYTES = 64 * 1024

_PDF_HEADER = re.compile(r"^%PDF-(\d\.\d)$")
_SLIDE_ENTRY = re.compile(r"^ppt/slides/slide\d+\.xml$")
_SHEET_ENTRY = re.compile(r"^xl/worksheets/sheet\d+\.xml$")
_DOCX_PAGES = re.compile(r"<(?:\w+:)?Pages>(\d{1,6})</(?:\w+:)?Pages>")

FORMAT_NAMES = {".pdf": "PDF", ".docx": "DOCX", ".pptx": "PPTX", ".xlsx": "XLSX", ".csv": "CSV"}


def clean_text(value: object) -> str | None:
    """Metadata string without control characters, at most 200 chars; None if unusable."""
    if not isinstance(value, str):
        return None
    text = CONTROL_CHARS.sub("", value).strip()[:MAX_TEXT_CHARS].strip()
    return text or None


def pdf_facts(reader: PdfReader) -> dict[str, object]:
    """Version, encryption flag and document metadata of an opened PDF."""
    facts: dict[str, object] = {}
    try:
        match = _PDF_HEADER.match(reader.pdf_header or "")
        if match:
            facts["version"] = match.group(1)
        facts["encrypted"] = bool(reader.is_encrypted)
    except Exception:  # untrusted input, leave the fields out
        pass
    try:
        metadata = reader.metadata
        if metadata is not None:
            created = metadata.creation_date
            for key, value in (
                ("title", clean_text(metadata.title)),
                ("author", clean_text(metadata.author)),
                ("created", created.date().isoformat() if isinstance(created, datetime) else None),
            ):
                if value:
                    facts[key] = value
    except Exception:  # malformed metadata must not break the conversion
        facts.pop("title", None)
        facts.pop("author", None)
        facts.pop("created", None)
    return facts


def _docx_pages(data: bytes, entries: list[zipfile.ZipInfo]) -> int | None:
    """Page count Word stored in docProps/app.xml; it may be missing or stale."""
    entry = next((e for e in entries if e.filename == "docProps/app.xml"), None)
    if entry is None or entry.file_size > MAX_APP_XML_BYTES:
        return None
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            xml = archive.read(entry).decode("utf-8", errors="replace")
    except Exception:  # untrusted input
        return None
    match = _DOCX_PAGES.search(xml)
    return int(match.group(1)) if match else None


def office_facts(data: bytes, extension: str, entries: list[zipfile.ZipInfo]) -> dict[str, object]:
    """Format-specific counts for an Office file whose ZIP entries are already listed."""
    facts: dict[str, object] = {}
    if extension == ".docx":
        pages = _docx_pages(data, entries)
        if pages is not None:
            facts["pages"] = pages
    elif extension == ".pptx":
        facts["slides"] = sum(1 for e in entries if _SLIDE_ENTRY.match(e.filename))
    elif extension == ".xlsx":
        facts["sheets"] = sum(1 for e in entries if _SHEET_ENTRY.match(e.filename))
    return facts
