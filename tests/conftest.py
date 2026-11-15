"""Shared fixtures."""
from collections.abc import Callable
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from tests.pdf_factory import PageSpec, build_pdf, text_page


@pytest.fixture
def make_pdf() -> Callable[[list[PageSpec]], bytes]:
    return build_pdf


@pytest.fixture
def digital_pdf() -> bytes:
    return build_pdf([text_page(1), text_page(2)])


@pytest.fixture
def scanned_pdf() -> bytes:
    return build_pdf([None, None, None])


def _encrypt(data: bytes, user_password: str, owner_password: str | None = None) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    writer.encrypt(
        user_password=user_password, owner_password=owner_password, algorithm="RC4-128"
    )
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def encrypted_pdf(digital_pdf: bytes) -> bytes:
    return _encrypt(digital_pdf, "geheim")


@pytest.fixture
def owner_only_pdf(digital_pdf: bytes) -> bytes:
    """Empty user password, so anyone can open it (only editing is restricted)."""
    return _encrypt(digital_pdf, "", "x")
