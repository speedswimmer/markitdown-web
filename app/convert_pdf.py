"""Convert digital PDFs to Markdown with PyMuPDF4LLM, page by page."""
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

import pymupdf
from pymupdf4llm.helpers.document_layout import OCRMode, parse_document

from app.coverage import Coverage, PageCoverage, page_coverage, summarize

log = logging.getLogger("markitdown-web")

# Called with (pages done, pages total) after each page.
Progress = Callable[[int, int], None]

# PyMuPDF is not thread-safe; conversions in parallel take turns page by page.
_lock = threading.Lock()

# Dot leaders in tables of contents: four or more dots or two or more ellipses,
# optionally spaced ("Overview ........ 2"). Three dots stay, they are an ellipsis.
_LEADER = re.compile(r"[ \t]*(?:(?:\.[ \t]?){4,}|(?:…[ \t]?){2,})[ \t]*")
# PyMuPDF4LLM often wraps the leader in <sup> because the dots sit on the baseline.
_SUP = re.compile(r"<sup>([^<]*)</sup>")
# A leader with nothing after it (no page number) leaves a dash at the end of a line or cell.
_DANGLING = re.compile(r" – (?=[ \t]*(?:\||$))", re.MULTILINE)
_FENCED = re.compile(r"(```.*?```)", re.DOTALL)


def _unwrap_leader_sup(match: re.Match[str]) -> str:
    inner = match.group(1)
    return inner if _LEADER.search(inner) else match.group(0)


def strip_dot_leaders(markdown: str) -> str:
    """Replace table-of-contents dot leaders with " – ", outside code blocks."""
    parts = _FENCED.split(markdown)
    for index in range(0, len(parts), 2):  # odd indices are fenced code blocks
        text = _SUP.sub(_unwrap_leader_sup, parts[index])
        parts[index] = _DANGLING.sub("", _LEADER.sub(" – ", text))
    return "".join(parts)


def _page_markdown(doc: pymupdf.Document, index: int) -> str:
    """Layout analysis without OCR (scans go to Claude), the way to_markdown runs it.

    Running page headers and footers are dropped, so they don't repeat throughout
    the Markdown. The layout model sometimes takes a page's only text for a header;
    such a page is rendered again with them, from the same analysis.
    """
    parsed = parse_document(doc, pages=[index], force_text=True, use_ocr=OCRMode.NEVER)
    markdown = parsed.to_markdown(header=False, footer=False).strip()
    markdown = markdown or parsed.to_markdown(header=True, footer=True).strip()
    return strip_dot_leaders(markdown)


@dataclass(frozen=True)
class PdfResult:
    """Markdown plus the coverage check; coverage is None when the check was
    skipped, failed, or the document has too little text to judge."""

    markdown: str
    coverage: Coverage | None


def _convert(data: bytes, on_progress: Progress | None, check: bool) -> PdfResult:
    with _lock:
        doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        total = doc.page_count
        parts: list[str] = []
        # None once the check failed on any page: no half-measured value is reported.
        pages: list[PageCoverage] | None = [] if check else None
        for index in range(total):
            with _lock:
                markdown = _page_markdown(doc, index)
                if pages is not None:
                    try:
                        pages.append(page_coverage(doc[index].get_text("text"), markdown))
                    except Exception as exc:  # the check must never break conversion
                        log.warning("coverage check failed on page %d: %s", index + 1, type(exc).__name__)
                        pages = None
            parts.append(markdown)
            if on_progress:
                on_progress(index + 1, total)
    finally:
        with _lock:
            doc.close()
    coverage = summarize(pages) if pages is not None else None
    return PdfResult("\n\n".join(part for part in parts if part), coverage)


def convert_pdf(data: bytes, on_progress: Progress | None = None) -> str:
    """Convert a PDF to Markdown; on_progress is called after every page."""
    return _convert(data, on_progress, check=False).markdown


def convert_pdf_checked(data: bytes, on_progress: Progress | None = None) -> PdfResult:
    """Like convert_pdf, plus the text coverage check against the page text layer."""
    return _convert(data, on_progress, check=True)
