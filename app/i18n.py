"""English versions of the messages the API sends to the browser.

The code raises German messages; the English page asks for ?lang=en and every
message is translated once, where the response leaves the app. Anything without
a translation (e.g. a detail quoted from the Claude API) is passed through.
"""
import re
from collections.abc import Callable

LANGUAGES = ("de", "en")

_EXACT = {
    "Datei ist entpackt zu groß.": "The file is too large when unpacked.",
    "Ungültige Anfrage.": "Invalid request.",
    "Interner Fehler.": "Internal error.",
    "Texterkennung ist hier nicht verfügbar.": "Text recognition (OCR) is not available here.",
    "Gescannte PDFs werden hier nicht unterstützt, nur Dateien mit echtem Text.":
        "Scanned PDFs are not supported here, only files with real text.",
    "Die Datei konnte nicht umgewandelt werden.": "The file could not be converted.",
    "Das Ergebnis ist leer. Mit „Texterkennung erzwingen“ noch einmal versuchen?":
        "The result is empty. Try again with “Force text recognition”?",
    "Das Ergebnis ist leer. Enthält die Datei vielleicht nur Bilder?":
        "The result is empty. Does the file perhaps contain only images?",
    "Nur PDF, DOCX, PPTX, XLSX und CSV werden unterstützt.": "Only PDF, DOCX, PPTX, XLSX and CSV are supported.",
    "Die Datei ist keine Textdatei.": "The file is not a text file.",
    "Die CSV-Datei ist fehlerhaft.": "The CSV file is malformed.",
    "Gerade ausgelastet, bitte gleich noch einmal versuchen.": "Busy right now, please try again in a moment.",
    "Anfrage von fremder Seite abgelehnt.": "Request from another site rejected.",
    "Länge fehlt.": "Content length missing.",
    "PDF ist passwortgeschützt.": "The PDF is password-protected.",
    "PDF lässt sich nicht öffnen.": "The PDF cannot be opened.",
    "PDF enthält keine Seiten.": "The PDF has no pages.",
    # OCR (Tesseract on the public instance, Claude on the private one)
    "Texterkennung ist nicht installiert": "text recognition is not installed",
    "Texterkennung hat zu lange gedauert": "text recognition took too long",
    "Texterkennung hat kein Ergebnis geliefert": "text recognition returned no result",
    "PDF konnte nicht aufgeteilt werden": "the PDF could not be split",
    "API-Key ungültig": "invalid API key",
    "Rate Limit erreicht, bitte gleich noch einmal versuchen": "rate limit reached, please try again in a moment",
    "Claude nicht erreichbar": "Claude is unreachable",
    "Verbindung zu Claude abgebrochen": "connection to Claude was interrupted",
    "Claude-Fehler": "Claude error",
    "Claude hat die Umwandlung abgelehnt": "Claude declined the conversion",
    "Ausgabe zu lang": "output too long",
}

# Messages with a value in them; a callable receives the match and may translate nested text.
_Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]
_PATTERNS: list[_Rule] = [
    (re.compile(r"Datei zu groß, höchstens (\d+) MB\."),
     lambda m: f"File too large, at most {m[1]} MB."),
    (re.compile(r"PDF hat zu viele Seiten, höchstens (\d+)\."),
     lambda m: f"The PDF has too many pages, at most {m[1]}."),
    (re.compile(r"Für die Texterkennung höchstens (\d+) Seiten\."),
     lambda m: f"Text recognition handles at most {m[1]} pages."),
    (re.compile(r"Fehler bei der Texterkennung: (.+)", re.DOTALL),
     lambda m: f"Text recognition failed: {_english(m[1])}"),
    (re.compile(r"Texterkennung fehlgeschlagen \(Code (-?\d+)\)"),
     lambda m: f"text recognition failed (code {m[1]})"),
    (re.compile(r"Seite (\d+) ist zu groß für die Umwandlung"),
     lambda m: f"page {m[1]} is too large to convert"),
    (re.compile(r"Anfrage abgelehnt: (.+)", re.DOTALL),
     lambda m: f"request rejected: {m[1]}"),
    (re.compile(r"Claude-Fehler (\d+): (.+)", re.DOTALL),
     lambda m: f"Claude error {m[1]}: {m[2]}"),
    # Claude chunk errors end with the affected pages, e.g. "… (Seiten 1–20)".
    (re.compile(r"(.+) \(Seiten? (\d+(?:–\d+)?)\)", re.DOTALL),
     lambda m: f"{_english(m[1])} (page{'s' if '–' in m[2] else ''} {m[2]})"),
]


def _english(message: str) -> str:
    if message in _EXACT:
        return _EXACT[message]
    for pattern, render in _PATTERNS:
        match = pattern.fullmatch(message)
        if match:
            return render(match)
    return message


def translate(message: str, lang: str) -> str:
    """The message in the requested language; German is the source language."""
    return _english(message) if lang == "en" else message
