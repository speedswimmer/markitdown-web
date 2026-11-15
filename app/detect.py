"""Open PDFs and decide whether they have a usable text layer or are scans."""
from dataclasses import dataclass
from io import BytesIO

from pypdf import PageObject, PdfReader

# A page counts as "has text" with at least this many characters.
MIN_CHARS_PER_PAGE = 50
# Upper bound for accepted documents (untrusted input).
MAX_PAGES = 500


class PdfOpenError(Exception):
    """The PDF is encrypted, empty, too large or cannot be parsed."""


@dataclass(frozen=True)
class PdfInfo:
    pages: int
    scanned: bool


def open_pdf(data: bytes) -> PdfReader:
    """Parse the PDF once; raise PdfOpenError if it is encrypted, broken, empty or too long."""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfOpenError("PDF ist passwortgeschützt")
        pages = len(reader.pages)  # forces parsing of the page tree
    except PdfOpenError:
        raise
    except Exception as exc:  # pypdf raises many types on malformed input
        raise PdfOpenError("PDF lässt sich nicht öffnen") from exc
    if pages == 0:
        raise PdfOpenError("PDF enthält keine Seiten")
    if pages > MAX_PAGES:
        raise PdfOpenError(f"PDF hat zu viele Seiten, höchstens {MAX_PAGES}")
    return reader


def _has_text(page: PageObject) -> bool:
    try:
        return len((page.extract_text() or "").strip()) >= MIN_CHARS_PER_PAGE
    except Exception:  # malformed pages must not crash; treat as no text
        return False


def analyze_pdf(reader: PdfReader) -> PdfInfo:
    """Scanned means fewer than half of the pages carry real text; stops once decided."""
    pages = len(reader.pages)
    needed = (pages + 1) // 2  # text pages required to count as digital
    with_text = 0
    for index, page in enumerate(reader.pages):
        if _has_text(page):
            with_text += 1
            if with_text >= needed:
                return PdfInfo(pages=pages, scanned=False)
        elif with_text + (pages - index - 1) < needed:
            return PdfInfo(pages=pages, scanned=True)
    return PdfInfo(pages=pages, scanned=with_text < needed)
