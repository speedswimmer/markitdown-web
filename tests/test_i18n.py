"""Tests for the English page and the English API messages."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import main
from app.convert_claude import OcrError
from app.convert_pdf import PdfResult
from app.i18n import translate

SITE = "https://markdown.ki-kuenstler.com"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.create_app(None))


@pytest.mark.parametrize("name", [n for n in dir(main) if n.startswith("MSG_")])
def test_every_fixed_message_has_an_english_version(name: str) -> None:
    message = getattr(main, name)
    assert translate(message, "en") != message


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("Datei zu groß, höchstens 20 MB.", "File too large, at most 20 MB."),
        ("PDF hat zu viele Seiten, höchstens 100.", "The PDF has too many pages, at most 100."),
        ("PDF ist passwortgeschützt.", "The PDF is password-protected."),
        (
            "Fehler bei der Texterkennung: Texterkennung fehlgeschlagen (Code 7)",
            "Text recognition failed: text recognition failed (code 7)",
        ),
        (
            "Fehler bei der Texterkennung: API-Key ungültig (Seiten 1–20)",
            "Text recognition failed: invalid API key (pages 1–20)",
        ),
        (
            "Fehler bei der Texterkennung: Ausgabe zu lang (Seite 3)",
            "Text recognition failed: output too long (page 3)",
        ),
    ],
)
def test_translate_to_english(german: str, english: str) -> None:
    assert translate(german, "en") == english


def test_german_and_unknown_messages_pass_through() -> None:
    assert translate(main.MSG_INVALID, "de") == main.MSG_INVALID
    assert translate("Claude-Fehler 529: overloaded", "en") == "Claude error 529: overloaded"
    assert translate("something new", "en") == "something new"


def test_errors_follow_the_page_language(client: TestClient) -> None:
    files = {"file": ("a.txt", b"x")}
    assert client.post("/convert", files=files).json()["error"].startswith("Nur PDF")
    english = client.post("/convert?lang=en", files=files).json()["error"]
    assert english == "Only PDF, DOCX, PPTX, XLSX and CSV are supported."


def test_guard_rejection_is_translated(client: TestClient) -> None:
    response = client.post(
        "/convert?lang=en", files={"file": ("a.pdf", b"x")}, headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "Request from another site rejected."


def test_stream_error_event_is_translated(
    scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*args: object) -> str:
        raise OcrError("Texterkennung hat zu lange gedauert")

    monkeypatch.setattr(main, "ocr_with_tesseract", failing)
    response = TestClient(main.create_app(None, local_ocr=True)).post(
        "/convert?lang=en", files={"file": ("a.pdf", scanned_pdf)}, headers={"accept": main.NDJSON}
    )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[-1] == {
        "type": "error",
        "status": 502,
        "error": "Text recognition failed: text recognition took too long",
    }


def test_warning_is_translated(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "convert_pdf_checked", lambda data, progress: PdfResult("", None))
    body = client.post("/convert?lang=en", files={"file": ("a.pdf", digital_pdf)}).json()
    assert body["warning"] == "The result is empty. Does the file perhaps contain only images?"


def test_english_page(client: TestClient) -> None:
    response = client.get("/en")
    assert response.status_code == 200
    html = response.text
    assert '<html lang="en">' in html
    assert f'<link rel="canonical" href="{SITE}/en">' in html
    assert "<h1>Convert PDF, Word and Excel to Markdown for free</h1>" in html
    assert '<a href="/" hreflang="de" lang="de"' in html
    assert client.head("/en").status_code == 200


@pytest.mark.parametrize("path", ["/", "/en"])
def test_both_pages_name_each_other(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert f'<link rel="alternate" hreflang="de" href="{SITE}/">' in html
    assert f'<link rel="alternate" hreflang="en" href="{SITE}/en">' in html
    assert f'<link rel="alternate" hreflang="x-default" href="{SITE}/">' in html


def test_german_page_links_to_english(client: TestClient) -> None:
    assert '<a href="/en" hreflang="en" lang="en"' in client.get("/").text


def test_pages_share_the_same_elements(client: TestClient) -> None:
    # app.js finds everything by id, so both pages need the same ones.
    ids = [re.findall(r'\bid="([^"]+)"', client.get(path).text) for path in ("/", "/en")]
    assert ids[0] == ids[1]


def test_sitemap_lists_both_languages(client: TestClient) -> None:
    sitemap = client.get("/sitemap.xml").text
    assert f"<loc>{SITE}/</loc>" in sitemap
    assert f"<loc>{SITE}/en</loc>" in sitemap
    assert sitemap.count('hreflang="en"') == 2


def test_app_sends_the_page_language() -> None:
    script = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'document.documentElement.lang === "en"' in script
    assert '"/convert?lang=en"' in script
