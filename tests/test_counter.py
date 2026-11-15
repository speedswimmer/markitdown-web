"""Tests for the conversion counter."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.convert_pdf import PdfResult
from app.counter import Counter


def test_counter_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "converted.txt"
    first = Counter(path)
    first.increment()
    first.increment()
    assert path.read_text(encoding="ascii") == "2\n"
    assert Counter(path).value == 2


def test_counter_starts_at_zero_without_file(tmp_path: Path) -> None:
    assert Counter(tmp_path / "missing.txt").value == 0
    assert Counter(None).increment() == 1


@pytest.mark.parametrize("content", ["", "abc", "-3", "1e5"])
def test_counter_ignores_garbage(tmp_path: Path, content: str) -> None:
    path = tmp_path / "converted.txt"
    path.write_text(content, encoding="ascii")
    assert Counter(path).value == 0


def test_unwritable_file_keeps_counting_in_memory(tmp_path: Path) -> None:
    counter = Counter(tmp_path / "no-such-dir" / "converted.txt")
    assert counter.increment() == 1
    assert counter.increment() == 2


def test_successful_conversions_are_counted(
    tmp_path: Path, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(main.create_app(None, counter=Counter(tmp_path / "c.txt")))
    client.post("/convert", files={"file": ("a.pdf", digital_pdf)})
    client.post("/convert", files={"file": ("a.pdf", digital_pdf)}, headers={"accept": main.NDJSON})
    assert client.get("/health").json()["converted"] == 2
    # Failed and empty conversions do not count.
    client.post("/convert", files={"file": ("a.txt", b"x")})
    monkeypatch.setattr(main, "convert_pdf_checked", lambda data, progress: PdfResult("", None))
    client.post("/convert", files={"file": ("a.pdf", digital_pdf)})
    assert client.get("/health").json()["converted"] == 2


def test_counter_file_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "converted.txt"
    path.write_text("41\n", encoding="ascii")
    monkeypatch.setenv("COUNTER_FILE", str(path))
    monkeypatch.setenv("OCR_ENGINE", "tesseract")
    assert TestClient(main._default_app()).get("/health").json()["converted"] == 41


@pytest.mark.parametrize("path", ["/", "/en"])
def test_pages_have_counter(path: str) -> None:
    html = TestClient(main.create_app(None)).get(path).text
    assert '<p class="counter" id="counter" hidden></p>' in html
