"""Helpers to build small PDFs in memory."""
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PageSpec = str | None


def build_pdf(pages: list[PageSpec]) -> bytes:
    """One page per entry: text gives a text layer, None gives graphics only (like a scan)."""
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    for text in pages:
        if text:
            y = 800
            for line in text.split("\n"):
                pdf.drawString(72, y, line)
                y -= 14
        else:
            pdf.rect(72, 400, 300, 200, fill=1)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def text_page(n: int) -> str:
    return f"Dies ist eine Testseite mit genug Text, damit die Erkennung greift. Seite {n}."
