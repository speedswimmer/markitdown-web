"""Convert CSV exports (bank statements, Excel "save as CSV") to a Markdown table.

German exports are usually Windows-1252 with semicolons and decimal commas, which
generic readers split into a single column. Encoding and delimiter are detected here.
"""
import csv
import io
from collections import Counter

DELIMITERS = ";,\t|"
# Enough lines to judge the delimiter; the rest of the file does not change the vote.
SNIFF_CHARS = 64 * 1024
SNIFF_ROWS = 50
# Windows-1252 covers € and umlauts in German exports; Latin-1 accepts any byte.
_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


class CsvError(ValueError):
    """The upload is not a CSV file that can be read; the message is shown to the user."""


def decode(data: bytes) -> str:
    """Text of the file; UTF-16 only with a byte order mark (Excel "Unicode text")."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise CsvError("Die Datei ist keine Textdatei.") from exc
    else:
        text = _decode_8bit(data)
    if "\x00" in text:
        raise CsvError("Die Datei ist keine Textdatei.")
    return text


def _decode_8bit(data: bytes) -> str:
    for encoding in _ENCODINGS[:-1]:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode(_ENCODINGS[-1])


def detect_delimiter(text: str) -> str:
    """The candidate that splits the first lines into the most rows of equal width."""
    sample = text[:SNIFF_CHARS]
    if len(text) > SNIFF_CHARS:
        sample = sample[: sample.rfind("\n") + 1] or sample  # drop the cut-off line
    best, best_score = ",", (0, 0)
    for delimiter in DELIMITERS:
        try:
            rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter))[:SNIFF_ROWS]
        except csv.Error:
            continue
        widths = Counter(len(row) for row in rows if row)
        if not widths:
            continue
        width, count = widths.most_common(1)[0]
        # Rows that agree count first; more columns break ties ("1,5" in every row).
        if width > 1 and (count, width) > best_score:
            best, best_score = delimiter, (count, width)
    return best


def parse_csv(data: bytes) -> list[list[str]]:
    """Rows of the file with empty rows dropped; the first row is the header."""
    text = decode(data)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=detect_delimiter(text)))
    except csv.Error as exc:
        raise CsvError("Die CSV-Datei ist fehlerhaft.") from exc
    return [row for row in rows if any(cell.strip() for cell in row)]


def _cell(value: str) -> str:
    # A table cell is one line; pipes would end the cell early.
    lines = value.strip().replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "<br>".join(line.strip() for line in lines).replace("|", "\\|")


def to_markdown(rows: list[list[str]]) -> str:
    """One Markdown table; columns without any value below the header are left out."""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    table = [[_cell(value) for value in row] + [""] * (width - len(row)) for row in rows]
    header, body = table[0], table[1:]
    keep = [i for i in range(width) if not body or any(row[i] for row in body)]
    lines = [
        "| " + " | ".join(header[i] for i in keep) + " |",
        "| " + " | ".join("---" for _ in keep) + " |",
    ]
    lines += ["| " + " | ".join(row[i] for i in keep) + " |" for row in body]
    return "\n".join(lines)
