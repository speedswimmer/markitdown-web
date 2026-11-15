"""Tests for search engine metadata, crawler files and the indexable page text."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import main

SITE = "https://markdown.ki-kuenstler.com"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.create_app(None))


@pytest.fixture
def html(client: TestClient) -> str:
    return client.get("/").text


def test_index_has_search_metadata(html: str) -> None:
    assert f'<link rel="canonical" href="{SITE}/">' in html
    description = re.search(r'<meta name="description" content="([^"]+)">', html)
    assert description and 120 <= len(description.group(1)) <= 250
    assert f'property="og:image" content="{SITE}/static/og-image.png"' in html


def test_index_has_valid_json_ld(html: str) -> None:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    assert len(blocks) == 1
    data = json.loads(blocks[0])
    assert data["@type"] == "WebApplication"
    assert data["url"] == f"{SITE}/"


def test_index_has_indexable_text(html: str) -> None:
    assert html.count("<h1") == 1
    for heading in ("So funktioniert's", "Warum Markdown für ChatGPT und Claude?", "Häufige Fragen"):
        assert heading in html
    assert html.count("<details>") >= 5


def test_faq_is_collapsed_but_in_the_html(html: str) -> None:
    # The closed fold keeps its heading and answers in the markup for crawlers.
    folds = re.findall(r'<details class="fold">\s*<summary><h2 id="([a-z]+)-title">', html)
    assert folds == ["faq"]
    assert '<details class="fold" open' not in html
    assert "Ist die Umwandlung kostenlos?" in html


@pytest.mark.parametrize("path", ["/impressum", "/datenschutz"])
def test_legal_pages_are_not_indexed(client: TestClient, path: str) -> None:
    assert '<meta name="robots" content="noindex, follow">' in client.get(path).text


def test_robots_txt(client: TestClient) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert f"Sitemap: {SITE}/sitemap.xml" in response.text
    assert "Disallow: /convert" in response.text


def test_sitemap_xml(client: TestClient) -> None:
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    assert f"<loc>{SITE}/</loc>" in response.text


def test_og_image_is_served(client: TestClient) -> None:
    response = client.get("/static/og-image.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("path", ["/", "/impressum", "/datenschutz", "/robots.txt", "/sitemap.xml"])
def test_pages_answer_head_requests(client: TestClient, path: str) -> None:
    response = client.head(path)
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.parametrize("path", ["/", "/static/style.css", "/health", "/nope"])
def test_hsts_on_every_response(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.headers["strict-transport-security"] == "max-age=15552000; includeSubDomains"


@pytest.mark.parametrize("path", ["/en", "/impressum", "/datenschutz"])
def test_trailing_slash_redirects_permanently_and_relative(client: TestClient, path: str) -> None:
    response = client.get(path + "/", follow_redirects=False)
    assert response.status_code == 301
    # Relative, so the browser stays on https behind the tunnel.
    assert response.headers["location"] == path
    assert response.headers["strict-transport-security"]


def test_trailing_slash_keeps_the_query(client: TestClient) -> None:
    response = client.get("/en/?ref=linkedin", follow_redirects=False)
    assert response.headers["location"] == "/en?ref=linkedin"


@pytest.mark.parametrize("path", ["/nope/", "/static/", "/health/", "/convert/", "/evil.example/"])
def test_no_redirect_for_other_paths(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers


def test_repeated_slashes_still_redirect_to_the_page(client: TestClient) -> None:
    response = client.get("/en//", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (301, "/en")
