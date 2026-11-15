"""Render the social preview image (Open Graph, 1200x630) to app/static/og-image.png."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
BG = (247, 248, 250)
FG = (22, 24, 29)
MUTED = (100, 106, 118)
ACCENT = (37, 99, 235)
GREEN = (22, 163, 74)
OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "og-image.png"

# Regular/bold candidates: Windows first, then Debian/Ubuntu.
FONTS = {
    False: ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
    True: ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in FONTS[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit("No TrueType font found (needs umlauts).")


def logo(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    """The site logo: rounded square with a white "M" and down arrow."""
    scale = size / 32
    draw.rounded_rectangle((x, y, x + size, y + size), radius=8 * scale, fill=ACCENT)

    def pt(px: float, py: float) -> tuple[float, float]:
        return x + px * scale, y + py * scale

    letter = [(5, 22), (5, 10), (8, 10), (11, 14), (14, 10), (17, 10), (17, 22), (14, 22),
              (14, 15), (11, 19), (8, 15), (8, 22)]
    arrow = [(20, 10), (20, 17), (17.5, 17), (22, 22), (26.5, 17), (24, 17), (24, 10)]
    draw.polygon([pt(*p) for p in letter], fill="white")
    draw.polygon([pt(*p) for p in arrow], fill="white")


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 12), fill=ACCENT)

    logo(draw, 80, 80, 72)
    draw.text((176, 92), "KI-Künstler · Markdown", font=font(40, bold=True), fill=FG)

    draw.text((80, 210), "PDF, Word, PowerPoint und Excel", font=font(64, bold=True), fill=FG)
    draw.text((80, 290), "in Markdown umwandeln", font=font(64, bold=True), fill=ACCENT)
    draw.text((80, 395), "Kostenlos, ohne Anmeldung, Dateien werden nicht gespeichert.",
              font=font(34), fill=MUTED)

    label, label_font = "Weniger Tokens für ChatGPT & Claude", font(30, bold=True)
    label_width = draw.textlength(label, font=label_font)
    draw.rounded_rectangle((80, 480, 80 + label_width + 64, 548), radius=34, fill=GREEN)
    draw.text((112, 492), label, font=label_font, fill="white")
    draw.text((80, 575), "markdown.ki-kuenstler.com", font=font(26), fill=MUTED)

    image.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
