"""OCRmyPDF plugin: report progress as plain lines on stderr.

Loaded by the ocrmypdf command (Debian's system Python), not by the app itself.
Each update prints "PROGRESS <stage> <done> <total>", which convert_tesseract parses.
"""
import sys

from ocrmypdf import hookimpl


class LineProgress:
    """Stands in for OCRmyPDF's progress bar (same constructor and methods)."""

    def __init__(self, *, total=None, desc=None, unit=None, disable=False, **kwargs):
        self.total = total
        self.stage = (desc or "").replace(" ", "_")
        self.done = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def update(self, n=1, *, completed=None):
        self.done = completed if completed is not None else self.done + n
        print(f"PROGRESS {self.stage} {self.done} {self.total}", file=sys.stderr, flush=True)


@hookimpl
def get_progressbar_class():
    return LineProgress
