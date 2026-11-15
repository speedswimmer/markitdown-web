"""Measure how much of a PDF page's text survives the Markdown conversion.

Reference is the page text PyMuPDF extracts, the same source PyMuPDF4LLM builds
its Markdown from. The comparison counts words and numbers, not layout.
"""
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

# A page below this share of recovered tokens is reported as weak.
WEAK_PAGE_PERCENT = 90
# Pages with fewer reference tokens are not judged; documents with fewer get no value.
MIN_WORDS = 20

# Letter, hyphen, line break, letter: a word split by hyphenation.
_HYPHENATION = re.compile(r"(?<=[^\W\d_])-\n(?=[^\W\d_])")
# Words of at least three letters, or runs of digits.
_TOKEN = re.compile(r"[^\W\d_]{3,}|\d+")


def tokens(text: str) -> Counter[str]:
    """Normalized word and number counts of a text."""
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    text = _HYPHENATION.sub("", text).lower()
    return Counter(_TOKEN.findall(text))


@dataclass(frozen=True)
class PageCoverage:
    found: int
    total: int


@dataclass(frozen=True)
class Coverage:
    percent: int
    weak_pages: list[int]


def page_coverage(reference: str, markdown: str) -> PageCoverage:
    """How many reference tokens the Markdown contains, each capped at its reference count."""
    wanted = tokens(reference)
    got = tokens(markdown)
    found = sum(min(count, got[token]) for token, count in wanted.items())
    return PageCoverage(found=found, total=sum(wanted.values()))


def summarize(pages: list[PageCoverage]) -> Coverage | None:
    """Document-wide share and the 1-based numbers of weak pages; None for too little text."""
    total = sum(page.total for page in pages)
    if total < MIN_WORDS:
        return None
    found = sum(page.found for page in pages)
    weak = [
        number
        for number, page in enumerate(pages, start=1)
        if page.total >= MIN_WORDS and 100 * page.found < WEAK_PAGE_PERCENT * page.total
    ]
    # Round half up (round() would round 94.5 down to 94).
    percent = (200 * found + total) // (2 * total)
    return Coverage(percent=percent, weak_pages=weak)
