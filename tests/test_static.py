"""Tests for the upload page and its static files."""
import re

import pytest
from fastapi.testclient import TestClient

from app import coverage, main

ASSETS = {
    "/static/style.css": "text/css",
    "/static/app.js": "javascript",
    "/static/vendor/marked.min.js": "javascript",
    "/static/vendor/purify.min.js": "javascript",
    "/static/favicon.svg": "image/svg+xml",
}


@pytest.fixture
def html() -> str:
    return TestClient(main.create_app(lambda chunk: "")).get("/").text


def test_index_references_static_files(html: str) -> None:
    for path in ASSETS:
        assert re.search(rf'"{re.escape(path)}(\?v=\w+)?"', html), path


def test_index_has_no_inline_code(html: str) -> None:
    assert not re.search(r"<style", html, re.IGNORECASE)
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
    # JSON-LD is data for search engines, never executed; all code must be external.
    code = [body for attrs, body in scripts if 'type="application/ld+json"' not in attrs]
    assert code and all(body == "" for body in code)
    assert not re.search(r"\sstyle\s*=", html, re.IGNORECASE)
    assert not re.search(r"\son\w+\s*=", html, re.IGNORECASE)


@pytest.mark.parametrize(("path", "content_type"), ASSETS.items())
def test_static_file_is_served(path: str, content_type: str) -> None:
    response = TestClient(main.create_app(lambda chunk: "")).get(path)
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert response.content


def test_index_has_ocr_option_and_note(html: str) -> None:
    assert 'id="ocr-option" hidden' in html
    assert 'id="note"' in html
    assert "nicht gespeichert" in html
    assert 'id="note-ocr" hidden' in html
    # The Claude sentence is added by app.js only when /health reports ocr_engine=claude,
    # so a Tesseract instance never serves it to crawlers.
    assert "Anthropic" not in html
    js = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "Claude (Anthropic)" in js


def test_app_reads_health() -> None:
    js = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'fetch("/health"' in js
    assert "max_upload_mb" in js
    assert "MAX_UPLOAD_BYTES" not in js  # limit comes from the server now


def test_app_shows_coffees_from_health() -> None:
    js = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "health.coffees" in js
    assert "Kaffee spendiert" in js
    assert "coffee so far" in js


@pytest.mark.parametrize("path", ["/", "/en"])
def test_tip_has_hidden_coffees_slot(path: str) -> None:
    html = TestClient(main.create_app(None)).get(path).text
    assert 'id="coffees" hidden' in html


def test_index_has_upload_zone_and_view_switch(html: str) -> None:
    assert "<title>PDF in Markdown umwandeln – kostenlos online · KI-Künstler</title>" in html
    for element_id in (
        "drop", "picked", "limits", "view-preview", "view-raw", "copy", "download"
    ):
        assert f'id="{element_id}"' in html


def test_index_has_facts_list(html: str) -> None:
    assert '<dl id="facts" class="facts" hidden></dl>' in html
    # Asset URLs carry a version so caches pick up new releases.
    assert re.search(r"style\.css\?v=\d+", html)
    assert re.search(r"app\.js\?v=\d+", html)


def test_app_builds_facts_without_innerhtml() -> None:
    js = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "showFacts" in js
    assert "innerHTML" not in js


def test_support_links_open_safely(html: str) -> None:
    links = re.findall(r'<a href="https://paypal\.me/MHartmannEU"[^>]*>', html)
    assert len(links) == 2
    assert all('rel="noopener noreferrer"' in link for link in links)


def test_intro_has_conversion_animation(html: str) -> None:
    assert '<svg class="flow"' in html
    css = (main.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "@keyframes flow-dot" in css
    assert "prefers-reduced-motion" in css


@pytest.mark.parametrize(
    ("path", "needle"),
    [("/impressum", "Angaben gemäß § 5 DDG"), ("/datenschutz", "Datenschutzerklärung")],
)
def test_legal_pages(path: str, needle: str) -> None:
    response = TestClient(main.create_app(None)).get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "content-security-policy" in response.headers
    body = response.text
    assert needle in body
    assert "63452 Hanau" in body
    assert not re.search(r"<script|<style|\sstyle\s*=|\son\w+\s*=", body, re.IGNORECASE)


def test_index_links_legal_pages(html: str) -> None:
    assert 'href="/impressum"' in html
    assert 'href="/datenschutz"' in html


def test_index_has_progress_bar(html: str) -> None:
    assert '<div class="progress" id="progress" hidden>' in html
    assert '<progress id="progress-bar"' in html
    assert 'id="progress-label"' in html


def test_app_streams_conversion_with_upload_progress() -> None:
    js = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "XMLHttpRequest" in js
    assert "xhr.upload" in js
    assert '"application/x-ndjson"' in js


def test_footer_credits_both_engines(html: str) -> None:
    assert "PyMuPDF4LLM" in html
    assert "MarkItDown" in html


def test_index_has_no_reset_button(html: str) -> None:
    assert 'id="reset"' not in html
    assert "resetBtn" not in (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_privacy_page_describes_local_ocr() -> None:
    body = TestClient(main.create_app(None)).get("/datenschutz").text
    assert "Tesseract" in body
    assert "Anthropic" not in body


def test_app_has_coverage_labels_in_both_languages() -> None:
    js = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'coverage: "Textabdeckung"' in js
    assert 'coverage: "Text coverage"' in js
    assert 'weakPages: "Seiten mit Verlust"' in js
    assert 'weakPages: "Pages with loss"' in js
    assert "info.coverage" in js and "info.weak_pages" in js


@pytest.mark.parametrize(
    ("path", "needle", "threshold"),
    [
        ("/", "Was bedeutet „Textabdeckung“?", "Seiten unter {} %"),
        ("/en", "What does “text coverage” mean?", "Pages below {} %"),
    ],
)
def test_faq_explains_coverage(path: str, needle: str, threshold: str) -> None:
    html = TestClient(main.create_app(lambda chunk: "")).get(path).text
    assert needle in html
    # The prose must follow the constant in the code, not drift away from it.
    assert threshold.format(coverage.WEAK_PAGE_PERCENT) in html


def test_both_pages_load_the_same_app_js_version() -> None:
    client = TestClient(main.create_app(lambda chunk: ""))
    tags = [re.search(r'src="/static/app\.js\?v=(\d+)"', client.get(p).text) for p in ("/", "/en")]
    assert all(tags)
    assert tags[0].group(1) == tags[1].group(1) == "14"
