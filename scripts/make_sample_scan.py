"""Create an image-only PDF with German text, for the live OCR test."""
import sys
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

LINES = [
    "Testbrief für die Texterkennung",
    "",
    "Sehr geehrte Damen und Herren,",
    "hiermit bestätigen wir den Eingang Ihrer Bestellung Nr. 4711.",
    "",
    "Artikel        Menge   Preis",
    "Schrauben M4   100     4,90 EUR",
    "Dübel 6 mm     50      3,20 EUR",
]
# Pillow's bundled default font has no umlauts; use a system font that does.
FONT_CANDIDATES = ["arial.ttf", "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    sys.exit("No font with umlauts found (tried: " + ", ".join(FONT_CANDIDATES) + ")")


def main(path: str) -> None:
    image = Image.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    font = _load_font(36)
    for i, line in enumerate(LINES):
        draw.text((100, 120 + i * 60), line, fill=0, font=font)
    png = BytesIO()
    image.save(png, format="PNG")
    png.seek(0)

    pdf = canvas.Canvas(path, pagesize=A4)
    pdf.drawImage(ImageReader(png), 0, 0, width=A4[0], height=A4[1])
    pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    main(sys.argv[1])
