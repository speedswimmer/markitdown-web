from collections.abc import Callable
from io import BytesIO

import pypdf
import pytest

import app.detect as detect
from app.detect import PdfInfo, PdfOpenError, analyze_pdf, open_pdf
from tests.pdf_factory import PageSpec, build_pdf, text_page

MakePdf = Callable[[list[PageSpec]], bytes]


def test_digital_pdf_is_not_scanned(digital_pdf: bytes) -> None:
    assert analyze_pdf(open_pdf(digital_pdf)) == PdfInfo(pages=2, scanned=False)


def test_graphics_only_pdf_is_scanned(scanned_pdf: bytes) -> None:
    assert analyze_pdf(open_pdf(scanned_pdf)) == PdfInfo(pages=3, scanned=True)


def test_less_than_half_text_pages_counts_as_scan(make_pdf: MakePdf) -> None:
    reader = open_pdf(make_pdf([text_page(1), None, None]))
    assert analyze_pdf(reader).scanned is True


def test_half_or_more_text_pages_is_digital(make_pdf: MakePdf) -> None:
    reader = open_pdf(make_pdf([text_page(1), text_page(2), None]))
    assert analyze_pdf(reader).scanned is False


def test_exactly_half_text_pages_is_digital(make_pdf: MakePdf) -> None:
    reader = open_pdf(make_pdf([None, text_page(2), None, text_page(4)]))
    assert analyze_pdf(reader).scanned is False


def test_short_text_does_not_count(make_pdf: MakePdf) -> None:
    assert analyze_pdf(open_pdf(make_pdf(["Seite 1", "Seite 2"]))).scanned is True


def test_extract_text_failure_counts_as_no_text(
    digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: pypdf.PageObject, *args: object, **kwargs: object) -> str:
        raise ValueError("malformed")

    monkeypatch.setattr(pypdf.PageObject, "extract_text", boom)
    assert analyze_pdf(open_pdf(digital_pdf)).scanned is True


def test_stops_early_once_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    original = pypdf.PageObject.extract_text
    calls: list[int] = []

    def counting(self: pypdf.PageObject, *args: object, **kwargs: object) -> str:
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pypdf.PageObject, "extract_text", counting)
    reader = open_pdf(build_pdf([text_page(n) for n in range(10)]))
    assert analyze_pdf(reader).scanned is False
    assert len(calls) <= 5


def test_encrypted_pdf_raises(encrypted_pdf: bytes) -> None:
    with pytest.raises(PdfOpenError, match="passwortgeschützt"):
        open_pdf(encrypted_pdf)


def test_owner_password_only_pdf_is_accepted(owner_only_pdf: bytes) -> None:
    assert analyze_pdf(open_pdf(owner_only_pdf)).pages == 2


def test_garbage_raises() -> None:
    with pytest.raises(PdfOpenError, match="nicht öffnen"):
        open_pdf(b"das ist kein pdf")


def test_too_many_pages_raises(make_pdf: MakePdf) -> None:
    with pytest.raises(PdfOpenError, match=f"höchstens {detect.MAX_PAGES}"):
        open_pdf(make_pdf([None] * (detect.MAX_PAGES + 1)))


def test_pdf_without_pages_raises() -> None:
    buf = BytesIO()
    pypdf.PdfWriter().write(buf)
    with pytest.raises(PdfOpenError, match="keine Seiten"):
        open_pdf(buf.getvalue())
