import logging

import pytest

from app import convert_pdf as convert_pdf_module
from app.convert_pdf import PdfResult, convert_pdf, convert_pdf_checked, strip_dot_leaders
from tests.pdf_factory import build_pdf, text_page


def test_digital_pdf_to_markdown(digital_pdf: bytes) -> None:
    markdown = convert_pdf(digital_pdf)
    assert "Testseite" in markdown
    assert "Seite 1." in markdown and "Seite 2." in markdown


def test_progress_is_reported_per_page() -> None:
    calls: list[tuple[int, int]] = []
    convert_pdf(
        build_pdf([text_page(1), text_page(2), text_page(3)]),
        lambda done, total: calls.append((done, total)),
    )
    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_scan_gives_empty_text(scanned_pdf: bytes) -> None:
    assert convert_pdf(scanned_pdf) == ""


def test_broken_pdf_raises() -> None:
    with pytest.raises(Exception):
        convert_pdf(b"%PDF-1.4 kaputt")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("|1|Product Introduction <sup>.......... </sup>2|", "|1|Product Introduction – 2|"),
        ("||1.1 Overview <sup>..........</sup>2|", "||1.1 Overview – 2|"),
        ("Einleitung ................ 3", "Einleitung – 3"),
        ("Einleitung . . . . . . . . 3", "Einleitung – 3"),
        ("Einleitung …………… 3", "Einleitung – 3"),
        ("Anhang ..........", "Anhang"),
        ("|Anhang <sup>.......</sup>|", "|Anhang|"),
    ],
)
def test_dot_leaders_are_replaced(raw: str, expected: str) -> None:
    assert strip_dot_leaders(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Und dann... kam nichts mehr.",
        "x<sup>2</sup> + y<sup>2</sup>",
        "```\nfor i in ....:\n```",
        "Version 1.2.3.4",
    ],
)
def test_other_dots_stay(text: str) -> None:
    assert strip_dot_leaders(text) == text


def test_leader_in_converted_pdf() -> None:
    pdf = build_pdf(["Inhalt", "Einleitung .................................... 3"])
    markdown = convert_pdf(pdf)
    assert "Einleitung – 3" in markdown
    assert "...." not in markdown


def test_checked_conversion_returns_markdown_and_coverage(digital_pdf: bytes) -> None:
    result = convert_pdf_checked(digital_pdf)
    assert isinstance(result, PdfResult)
    assert result.markdown == convert_pdf(digital_pdf)
    assert result.coverage is not None
    assert result.coverage.percent >= 90
    assert result.coverage.weak_pages == []


def test_checked_conversion_reports_progress() -> None:
    calls: list[tuple[int, int]] = []
    convert_pdf_checked(build_pdf([text_page(1), text_page(2)]), lambda d, t: calls.append((d, t)))
    assert calls == [(1, 2), (2, 2)]


def test_checked_conversion_of_scan_has_no_coverage(scanned_pdf: bytes) -> None:
    result = convert_pdf_checked(scanned_pdf)
    assert result.markdown == ""
    assert result.coverage is None


def test_coverage_failure_does_not_break_conversion(
    digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(reference: str, markdown: str) -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(convert_pdf_module, "page_coverage", boom)
    with caplog.at_level(logging.WARNING, logger="markitdown-web"):
        result = convert_pdf_checked(digital_pdf)
    assert "Testseite" in result.markdown
    assert result.coverage is None
    assert "coverage check failed" in caplog.text
