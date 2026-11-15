"""Tests for the HTTP layer."""
import importlib
import json
import re
import struct
import time
import zipfile
from collections.abc import Iterator
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app import main
from app.convert_claude import OcrError, Transcriber
from app.convert_pdf import PdfResult
from tests.pdf_factory import text_page


def fake_transcriber(chunk: bytes) -> str:
    return "# Gescannt"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.create_app(fake_transcriber))


def make_zip(entries: dict[str, bytes] | None = None) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (entries or {"word/document.xml": b"<x/>"}).items():
            archive.writestr(name, content)
    return buf.getvalue()


def scanned_info_version(data: bytes) -> str:
    return data[:8].decode("ascii").removeprefix("%PDF-")


def upload(client: TestClient, name: str, data: bytes, **form: str) -> httpx.Response:
    return client.post("/convert", files={"file": (name, data)}, data=form)


def test_index_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_docs_are_disabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize(
    "transcriber, local_ocr, engine",
    [(fake_transcriber, False, "claude"), (None, True, "tesseract"), (None, False, None)],
)
def test_health(transcriber: Transcriber | None, local_ocr: bool, engine: str | None) -> None:
    response = TestClient(main.create_app(transcriber, local_ocr)).get("/health")
    assert response.json() == {
        "status": "ok",
        "ocr": engine is not None,
        "ocr_engine": engine,
        "max_upload_mb": 30,
        "converted": 0,
        "coffees": 0,
    }


def test_health_reports_coffees() -> None:
    response = TestClient(main.create_app(None, coffees=3)).get("/health")
    assert response.json()["coffees"] == 3


def test_coffees_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_ENGINE", "tesseract")
    monkeypatch.setenv("COFFEES", "2")
    assert TestClient(main._default_app()).get("/health").json()["coffees"] == 2
    monkeypatch.delenv("COFFEES")
    assert TestClient(main._default_app()).get("/health").json()["coffees"] == 0
    monkeypatch.setenv("COFFEES", "many")
    with pytest.raises(RuntimeError, match="COFFEES"):
        main._default_app()


def test_health_reports_upload_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_BYTES", 10 * 1024 * 1024)
    response = TestClient(main.create_app(None)).get("/health")
    assert response.json()["max_upload_mb"] == 10


def test_env_int_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_TEST", raising=False)
    assert main._env_int("X_TEST", 7, 1, 9) == 7


def test_env_int_empty_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_TEST", " ")
    assert main._env_int("X_TEST", 7, 1, 9) == 7


@pytest.mark.parametrize("raw, expected", [("1", 1), ("9", 9), (" 4 ", 4)])
def test_env_int_valid(monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("X_TEST", raw)
    assert main._env_int("X_TEST", 7, 1, 9) == expected


@pytest.mark.parametrize("raw", ["0", "10", "-1", "abc", "3.5", "²"])
def test_env_int_invalid_stops_startup(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("X_TEST", raw)
    with pytest.raises(RuntimeError, match="X_TEST"):
        main._env_int("X_TEST", 7, 1, 9)


def test_unknown_extension_is_rejected(client: TestClient) -> None:
    response = upload(client, "a.txt", b"hello")
    assert response.status_code == 415
    assert "Nur PDF" in response.json()["error"]


def test_too_large_in_handler(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Content-Length stays within the middleware's slack, so the handler check fires.
    monkeypatch.setattr(main, "MAX_BYTES", len(digital_pdf) - 1)
    response = upload(client, "a.pdf", digital_pdf)
    assert response.status_code == 413
    assert response.json() == {"error": "Datei zu groß, höchstens 30 MB."}


def test_too_large_by_content_length(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_BYTES", 100)
    monkeypatch.setattr(main, "MULTIPART_OVERHEAD", 100)
    response = upload(client, "a.pdf", b"x" * 1000)
    assert response.status_code == 413
    assert response.json() == {"error": "Datei zu groß, höchstens 30 MB."}


def test_limits_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "10")
    monkeypatch.setenv("MAX_PARALLEL", "1")
    monkeypatch.setenv("MAX_UNZIPPED_MB", "50")
    monkeypatch.setenv("MAX_PDF_PAGES", "100")
    monkeypatch.setenv("MAX_OCR_PAGES", "20")
    try:
        importlib.reload(main)
        assert main.MAX_BYTES == 10 * 2**20
        assert "10 MB" in main.MSG_TOO_LARGE
        assert main.MAX_PARALLEL == 1
        assert main.MAX_UNZIPPED_BYTES == 50 * 2**20
        assert main.MAX_PDF_PAGES == 100
        assert main.MAX_OCR_PAGES == 20
        response = TestClient(main.create_app(None)).post(
            "/convert",
            content=b"x",
            headers={
                "content-type": "multipart/form-data; boundary=b",
                "content-length": str(11 * 2**20 + 1),
            },
        )
        assert response.status_code == 413
        assert response.json() == {"error": main.MSG_TOO_LARGE}
    finally:
        monkeypatch.delenv("MAX_UPLOAD_MB")
        monkeypatch.delenv("MAX_PARALLEL")
        monkeypatch.delenv("MAX_UNZIPPED_MB")
        monkeypatch.delenv("MAX_PDF_PAGES")
        monkeypatch.delenv("MAX_OCR_PAGES")
        importlib.reload(main)


def test_missing_content_length_is_rejected(client: TestClient) -> None:
    def body() -> Iterator[bytes]:  # chunked body: no Content-Length header
        yield b"x"

    response = client.post(
        "/convert", content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert response.status_code == 411


def test_digital_pdf(client: TestClient, digital_pdf: bytes) -> None:
    response = upload(client, "Brief.pdf", digital_pdf)
    body = response.json()
    assert response.status_code == 200
    assert body["method"] == "pymupdf4llm"
    assert body["pages"] == 2
    assert body["filename"] == "Brief.md"
    assert "Testseite" in body["markdown"]
    assert "warning" not in body


def test_scanned_pdf(client: TestClient, scanned_pdf: bytes) -> None:
    response = upload(client, "Scan.pdf", scanned_pdf)
    assert response.status_code == 200
    body = response.json()
    info = body.pop("info")
    assert body == {
        "markdown": "# Gescannt",
        "method": "claude",
        "pages": 3,
        "filename": "Scan.md",
    }
    assert info["scanned"] is True
    assert info["pages"] == 3


def test_force_ocr_on_digital_pdf(client: TestClient, digital_pdf: bytes) -> None:
    response = upload(client, "Brief.pdf", digital_pdf, force_ocr="true")
    assert response.status_code == 200
    assert response.json()["method"] == "claude"


def test_scan_without_ocr(scanned_pdf: bytes) -> None:
    client = TestClient(main.create_app(None))
    response = upload(client, "Scan.pdf", scanned_pdf)
    assert response.status_code == 422
    assert response.json() == {"error": main.MSG_NO_OCR}
    assert "Key" not in main.MSG_NO_OCR


def test_forced_ocr_without_ocr(digital_pdf: bytes) -> None:
    client = TestClient(main.create_app(None))
    response = upload(client, "Brief.pdf", digital_pdf, force_ocr="true")
    assert response.status_code == 422
    assert response.json() == {"error": main.MSG_OCR_UNAVAILABLE}


def test_scan_over_ocr_page_cap(
    client: TestClient, scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_OCR_PAGES", 2)
    response = upload(client, "Scan.pdf", scanned_pdf)
    assert response.status_code == 413
    assert response.json()["error"] == "Für die Texterkennung höchstens 2 Seiten."


def test_encrypted_pdf(client: TestClient, encrypted_pdf: bytes) -> None:
    response = upload(client, "a.pdf", encrypted_pdf)
    assert response.status_code == 422
    assert "passwortgeschützt" in response.json()["error"]


def test_broken_pdf(client: TestClient) -> None:
    assert upload(client, "a.pdf", b"not a pdf").status_code == 422


def test_ocr_error(scanned_pdf: bytes) -> None:
    def failing(chunk: bytes) -> str:
        raise OcrError("API-Key ungültig")

    response = upload(TestClient(main.create_app(failing)), "a.pdf", scanned_pdf)
    assert response.status_code == 502
    assert response.json()["error"].startswith(
        "Fehler bei der Texterkennung: API-Key ungültig"
    )


def test_empty_markdown_adds_warning(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "convert_pdf_checked", lambda data, progress: PdfResult("", None))
    body = upload(client, "a.pdf", digital_pdf).json()
    assert body["markdown"] == ""
    assert "Texterkennung erzwingen" in body["warning"]



def test_empty_markdown_without_ocr_does_not_suggest_ocr(
    digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "convert_pdf_checked", lambda data, progress: PdfResult("", None))
    body = upload(TestClient(main.create_app(None)), "a.pdf", digital_pdf).json()
    assert body["warning"] == main.MSG_EMPTY_NO_OCR
    assert "Texterkennung" not in body["warning"]


def test_office_file(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: f"# {extension}")
    response = upload(client, "Bericht.docx", make_zip())
    assert response.status_code == 200
    assert response.json() == {
        "markdown": "# .docx",
        "method": "markitdown",
        "pages": None,
        "filename": "Bericht.md",
        "info": {"format": "DOCX", "size": len(make_zip())},
    }


def test_office_file_that_is_not_a_zip(client: TestClient) -> None:
    response = upload(client, "a.docx", b"plain text")
    assert response.status_code == 422
    assert response.json()["error"] == "Die Datei konnte nicht umgewandelt werden."


def test_zip_bomb_by_size(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_UNZIPPED_BYTES", 1000)
    response = upload(client, "a.xlsx", make_zip({"a": b"0" * 5000}))
    assert response.status_code == 413
    assert response.json()["error"] == "Datei ist entpackt zu groß."


def test_zip_bomb_by_entry_count(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_ZIP_ENTRIES", 2)
    response = upload(client, "a.pptx", make_zip({"a": b"1", "b": b"2", "c": b"3"}))
    assert response.status_code == 413


def test_converter_crash_is_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(data: bytes, extension: str) -> str:
        raise KeyError("boom")

    monkeypatch.setattr(main, "convert_local", crash)
    response = upload(client, "a.docx", make_zip())
    assert response.status_code == 422
    assert response.json()["error"] == "Die Datei konnte nicht umgewandelt werden."


def test_filename_path_components_are_dropped(client: TestClient, digital_pdf: bytes) -> None:
    response = upload(client, "../../x/Brief.pdf", digital_pdf)
    assert response.json()["filename"] == "Brief.md"


def test_cross_site_post_is_rejected(client: TestClient, digital_pdf: bytes) -> None:
    response = client.post(
        "/convert",
        files={"file": ("a.pdf", digital_pdf)},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "Anfrage von fremder Seite abgelehnt."}


def test_same_origin_post_is_allowed(client: TestClient, digital_pdf: bytes) -> None:
    response = client.post(
        "/convert",
        files={"file": ("a.pdf", digital_pdf)},
        headers={"sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 200


def test_invalid_multipart_body_uses_error_format(client: TestClient) -> None:
    response = client.post(
        "/convert",
        content=b"garbage",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Ungültige Anfrage."}


def test_missing_file_field_uses_error_format(client: TestClient) -> None:
    response = client.post("/convert", data={"force_ocr": "false"})
    assert response.status_code == 422
    assert response.json() == {"error": "Ungültige Anfrage."}


def test_unknown_route_uses_error_format(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"error": "Ungültige Anfrage."}


def test_unexpected_exception_is_a_plain_500(scanned_pdf: bytes) -> None:
    def broken(chunk: bytes) -> str:
        raise RuntimeError("boom")

    # Default TestClient re-raises unhandled errors: passing proves the crash is contained.
    response = upload(TestClient(main.create_app(broken)), "a.pdf", scanned_pdf)
    assert response.status_code == 500
    assert response.json() == {"error": "Interner Fehler."}
    assert response.headers["x-content-type-options"] == "nosniff"


def test_busy_server_answers_503(digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_PARALLEL", 0)
    response = upload(TestClient(main.create_app(None)), "a.pdf", digital_pdf)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_slot_is_released_after_each_request(
    digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_PARALLEL", 1)
    client = TestClient(main.create_app(None))
    assert upload(client, "a.txt", b"x").status_code == 415  # error path releases too
    assert upload(client, "a.pdf", digital_pdf).status_code == 200


def test_zip_entry_count_is_read_from_eocd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_ZIP_ENTRIES", 2)
    data = make_zip({"a": b"1", "b": b"2", "c": b"3"})

    def no_central_directory(*args: object, **kwargs: object) -> None:
        raise AssertionError("central directory must not be built")

    monkeypatch.setattr(main.zipfile, "ZipFile", no_central_directory)
    with pytest.raises(main.ApiError) as info:
        main._check_office_zip(data)
    assert info.value.status == 413


def test_zip_with_spoofed_entry_count_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_ZIP_ENTRIES", 2)
    data = bytearray(make_zip({"a": b"1", "b": b"2", "c": b"3"}))
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, eocd + 8, 1, 1)  # claim a single entry
    response = upload(client, "a.docx", bytes(data))
    assert response.status_code == 413


def test_zip_with_large_central_directory_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_CENTRAL_DIR_BYTES", 10)
    response = upload(client, "a.docx", make_zip())
    assert response.status_code == 413


def test_zip64_is_rejected_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    original = make_zip({"a": b"1", "b": b"2", "c": b"3"})
    e = original.rfind(b"PK\x05\x06")
    count, size, offset = struct.unpack_from("<HII", original, e + 10)
    record = b"PK\x06\x06" + struct.pack(
        "<QHHIIQQQQ", 44, 45, 45, 0, 0, count, count, size, offset
    )
    locator = b"PK\x06\x07" + struct.pack("<IQI", 0, e, 1)
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, 46, offset, 0)
    data = original[:e] + record + locator + eocd

    def no_parsing(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZIP64 archive must not be parsed")

    monkeypatch.setattr(main.zipfile, "ZipFile", no_parsing)
    with pytest.raises(main.ApiError) as info:
        main._check_office_zip(data)
    assert info.value.status == 413


def test_zip_without_eocd_is_rejected(client: TestClient) -> None:
    response = upload(client, "a.docx", b"PK\x03\x04" + b"\x00" * 100)
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/", "/health", "/static/index.html"])
def test_security_headers(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_convert_response_headers(client: TestClient, digital_pdf: bytes) -> None:
    for response in (upload(client, "a.pdf", digital_pdf), upload(client, "a.txt", b"x")):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/", "/static/style.css", "/health"])
def test_other_responses_must_be_revalidated(client: TestClient, path: str) -> None:
    # Cloudflare and browsers would otherwise keep an old style.css next to a new page.
    assert client.get(path).headers["cache-control"] == "no-cache"


def test_static_files_are_mounted(client: TestClient) -> None:
    assert client.get("/static/index.html").status_code == 200


# --- file facts ---------------------------------------------------------------------


def info_of(response: httpx.Response) -> dict[str, object]:
    assert response.status_code == 200
    return response.json()["info"]


def with_metadata(data: bytes, metadata: dict[str, str]) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    writer.add_metadata(metadata)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_digital_pdf_info(client: TestClient, digital_pdf: bytes) -> None:
    info = info_of(upload(client, "a.pdf", digital_pdf))
    assert info["format"] == "PDF"
    assert info["size"] == len(digital_pdf)
    assert info["pages"] == 2
    assert info["scanned"] is False
    assert info["encrypted"] is False
    assert info["version"] == scanned_info_version(digital_pdf)


def test_forced_ocr_keeps_analysis_result(client: TestClient, digital_pdf: bytes) -> None:
    info = info_of(upload(client, "a.pdf", digital_pdf, force_ocr="true"))
    assert info["scanned"] is False


def test_owner_only_pdf_is_encrypted(client: TestClient, owner_only_pdf: bytes) -> None:
    assert info_of(upload(client, "a.pdf", owner_only_pdf))["encrypted"] is True


def test_pdf_metadata_is_cleaned(client: TestClient, digital_pdf: bytes) -> None:
    data = with_metadata(
        digital_pdf, {"/Title": "  Angebot\x07 2026 ", "/Author": "x" * 300}
    )
    info = info_of(upload(client, "a.pdf", data))
    assert info["title"] == "Angebot 2026"
    assert info["author"] == "x" * 200


def test_pdf_creation_date(client: TestClient, digital_pdf: bytes) -> None:
    data = with_metadata(digital_pdf, {"/CreationDate": "D:20240315123000Z"})
    assert info_of(upload(client, "a.pdf", data))["created"] == "2024-03-15"


def test_pdf_without_metadata_omits_fields(client: TestClient) -> None:
    writer = PdfWriter()  # unlike reportlab output, this carries no metadata at all
    writer.add_blank_page(200, 200)
    buf = BytesIO()
    writer.write(buf)
    info = info_of(upload(client, "a.pdf", buf.getvalue()))
    assert not {"title", "author", "created"} & info.keys()
    assert None not in info.values()


def test_blank_title_is_omitted(client: TestClient, digital_pdf: bytes) -> None:
    data = with_metadata(digital_pdf, {"/Title": " \x07 "})
    assert "title" not in info_of(upload(client, "a.pdf", data))


def test_failing_metadata_does_not_break_conversion(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: PdfReader) -> None:
        raise ValueError("broken")

    monkeypatch.setattr(PdfReader, "metadata", property(boom))
    response = upload(client, "a.pdf", with_metadata(digital_pdf, {"/Title": "T"}))
    info = info_of(response)
    assert "title" not in info
    assert info["pages"] == 2


def test_pptx_slide_count(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: "# x")
    data = make_zip({
        "ppt/slides/slide1.xml": b"<x/>",
        "ppt/slides/slide2.xml": b"<x/>",
        "ppt/slides/_rels/slide1.xml.rels": b"<x/>",
        "ppt/slideLayouts/slideLayout1.xml": b"<x/>",
    })
    assert info_of(upload(client, "a.pptx", data)) == {
        "format": "PPTX", "size": len(data), "slides": 2
    }


def test_xlsx_sheet_count(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: "# x")
    data = make_zip({
        "xl/worksheets/sheet1.xml": b"<x/>",
        "xl/worksheets/sheet2.xml": b"<x/>",
        "xl/worksheets/sheet3.xml": b"<x/>",
        "xl/worksheets/_rels/sheet1.xml.rels": b"<x/>",
    })
    info = info_of(upload(client, "a.xlsx", data))
    assert info == {"format": "XLSX", "size": len(data), "sheets": 3}


@pytest.mark.parametrize(
    "app_xml, expected",
    [
        (b"<Properties><Pages>3</Pages></Properties>", 3),
        (b"<Properties><ep:Pages>12</ep:Pages></Properties>", 12),
        (b"<Properties><Words>3</Words></Properties>", None),
        (b"<Properties><Pages>abc</Pages></Properties>", None),
        (b"<Properties><Pages>3</Pages>" + b" " * 70_000 + b"</Properties>", None),
    ],
)
def test_docx_page_count(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    app_xml: bytes,
    expected: int | None,
) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: "# x")
    data = make_zip({"word/document.xml": b"<x/>", "docProps/app.xml": app_xml})
    info = info_of(upload(client, "a.docx", data))
    assert info.get("pages") == expected
    assert info["format"] == "DOCX"


def test_docx_without_app_xml_has_no_pages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: "# x")
    assert "pages" not in info_of(upload(client, "a.docx", make_zip()))


def test_facts_are_not_logged(
    client: TestClient, digital_pdf: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    data = with_metadata(digital_pdf, {"/Title": "Geheimtitel"})
    with caplog.at_level("DEBUG", logger="markitdown-web"):
        upload(client, "a.pdf", data)
    assert "converted size=" in caplog.text
    assert "Geheimtitel" not in caplog.text


def test_broken_pdf_header_does_not_break_conversion(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: object) -> str:
        raise ValueError("broken")

    monkeypatch.setattr("pypdf.PdfReader.pdf_header", property(boom))
    response = upload(client, "a.pdf", digital_pdf)
    assert response.status_code == 200
    assert "version" not in response.json()["info"]


def test_pdf_over_page_cap(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_PDF_PAGES", 1)
    response = upload(client, "a.pdf", digital_pdf)
    assert response.status_code == 413
    assert response.json() == {"error": "PDF hat zu viele Seiten, höchstens 1."}


def test_pdf_converter_crash_is_422(
    client: TestClient, digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(data: bytes, progress: object) -> PdfResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "convert_pdf_checked", crash)
    response = upload(client, "a.pdf", digital_pdf)
    assert response.status_code == 422
    assert response.json() == {"error": main.MSG_CONVERT_FAILED}


# --- Streaming (Accept: application/x-ndjson) ---


def stream_events(client: TestClient, name: str, data: bytes, **form: str) -> list[dict]:
    response = client.post(
        "/convert",
        files={"file": (name, data)},
        data=form,
        headers={"accept": main.NDJSON},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(main.NDJSON)
    assert response.headers["cache-control"] == "no-store"
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_stream_reports_progress_per_page(client: TestClient, make_pdf) -> None:
    pdf = make_pdf([text_page(1), text_page(2), text_page(3)])
    events = stream_events(client, "Brief.pdf", pdf)
    assert events[0] == {"type": "start", "method": "pymupdf4llm", "pages": 3}
    progress = [(e["done"], e["total"]) for e in events if e["type"] == "progress"]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    result = events[-1]
    assert result["type"] == "result"
    assert result["filename"] == "Brief.md"
    assert "Testseite" in result["markdown"]


def test_stream_reports_ocr_progress_per_chunk(client: TestClient, scanned_pdf: bytes) -> None:
    events = stream_events(client, "Scan.pdf", scanned_pdf)
    assert events[0] == {"type": "start", "method": "claude", "pages": 3}
    progress = [(e["done"], e["total"]) for e in events if e["type"] == "progress"]
    assert progress == [(3, 3)]  # one chunk of up to 20 pages
    assert events[-1]["markdown"] == "# Gescannt"


def test_stream_office_file_has_no_page_progress(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: "# Bericht")
    events = stream_events(client, "Bericht.docx", make_zip())
    assert [e["type"] for e in events] == ["start", "result"]
    assert events[0] == {"type": "start", "method": "markitdown", "pages": None}


def test_stream_error_after_start_is_an_event(scanned_pdf: bytes) -> None:
    def failing(chunk: bytes) -> str:
        raise OcrError("Rate Limit erreicht")

    events = stream_events(TestClient(main.create_app(failing)), "a.pdf", scanned_pdf)
    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 502
    assert events[-1]["error"].startswith("Fehler bei der Texterkennung: Rate Limit")


def test_stream_crash_is_a_plain_error_event(scanned_pdf: bytes) -> None:
    def broken(chunk: bytes) -> str:
        raise RuntimeError("geheimer Inhalt")

    events = stream_events(TestClient(main.create_app(broken)), "a.pdf", scanned_pdf)
    assert events[-1] == {"type": "error", "status": 500, "error": "Interner Fehler."}


def test_stream_early_errors_keep_their_status(client: TestClient) -> None:
    response = client.post(
        "/convert",
        files={"file": ("a.txt", b"x")},
        headers={"accept": main.NDJSON},
    )
    assert response.status_code == 415
    assert response.json() == {"error": main.MSG_UNSUPPORTED}


def test_stream_sends_heartbeats_while_idle(
    digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow(data: bytes, progress: object) -> PdfResult:
        time.sleep(0.25)
        return PdfResult("# Fertig", None)

    monkeypatch.setattr(main, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(main, "convert_pdf_checked", slow)
    response = TestClient(main.create_app(None)).post(
        "/convert", files={"file": ("a.pdf", digital_pdf)}, headers={"accept": main.NDJSON}
    )
    assert "\n\n" in response.text  # at least one blank heartbeat line
    assert json.loads(response.text.splitlines()[-1])["markdown"] == "# Fertig"


def test_stream_releases_slot(digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_PARALLEL", 1)
    client = TestClient(main.create_app(None))
    for _ in range(3):
        assert stream_events(client, "a.pdf", digital_pdf)[-1]["type"] == "result"
    assert upload(client, "a.pdf", digital_pdf).status_code == 200


# --- Local OCR with Tesseract (public instance) ---


def fake_tesseract(data: bytes, pages: int, force: bool, jobs: int, progress) -> str:
    for page in range(1, pages + 1):
        progress(page, pages)
    return f"# Lokal erkannt (force={force})"


def test_scan_with_local_ocr(scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "ocr_with_tesseract", fake_tesseract)
    client = TestClient(main.create_app(None, local_ocr=True))
    events = stream_events(client, "Scan.pdf", scanned_pdf)
    assert events[0] == {"type": "start", "method": "tesseract", "pages": 3}
    progress = [(e["done"], e["total"]) for e in events if e["type"] == "progress"]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert events[-1]["markdown"] == "# Lokal erkannt (force=False)"


def test_forced_local_ocr_on_digital_pdf(
    digital_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "ocr_with_tesseract", fake_tesseract)
    client = TestClient(main.create_app(None, local_ocr=True))
    body = upload(client, "a.pdf", digital_pdf, force_ocr="true").json()
    assert body["method"] == "tesseract"
    assert body["markdown"] == "# Lokal erkannt (force=True)"


def test_local_ocr_error_is_502(scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: object) -> str:
        raise OcrError("Texterkennung hat zu lange gedauert")

    monkeypatch.setattr(main, "ocr_with_tesseract", failing)
    response = upload(TestClient(main.create_app(None, local_ocr=True)), "a.pdf", scanned_pdf)
    assert response.status_code == 502
    assert response.json()["error"] == "Fehler bei der Texterkennung: Texterkennung hat zu lange gedauert"


def test_local_ocr_respects_page_cap(scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_OCR_PAGES", 2)
    response = upload(TestClient(main.create_app(None, local_ocr=True)), "a.pdf", scanned_pdf)
    assert response.status_code == 413


@pytest.mark.parametrize(("value", "engine"), [("tesseract", "tesseract"), (" Tesseract ", "tesseract")])
def test_ocr_engine_from_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, engine: str
) -> None:
    monkeypatch.setenv("OCR_ENGINE", value)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = main._default_app()
    assert TestClient(app).get("/health").json()["ocr_engine"] == engine


def test_unknown_ocr_engine_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_ENGINE", "gpt")
    with pytest.raises(RuntimeError, match="OCR_ENGINE"):
        main._default_app()


def test_digital_pdf_reports_coverage(client: TestClient, digital_pdf: bytes) -> None:
    info = info_of(upload(client, "a.pdf", digital_pdf))
    assert isinstance(info["coverage"], int) and 90 <= info["coverage"] <= 100
    assert info["weak_pages"] == []


def test_office_and_csv_have_no_coverage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "convert_local", lambda data, extension: "# x")
    assert "coverage" not in info_of(upload(client, "a.docx", make_zip()))
    assert "coverage" not in info_of(upload(client, "a.csv", b"a;b\n1;2\n"))


def test_coverage_is_logged_without_pages(
    client: TestClient, digital_pdf: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="markitdown-web"):
        upload(client, "a.pdf", digital_pdf)
    assert re.search(r"converted .*coverage=\d+ weak=0", caplog.text)
