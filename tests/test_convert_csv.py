"""Tests for the CSV import."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.convert_csv import CsvError, decode, detect_delimiter, parse_csv, to_markdown

BANK_EXPORT = (
    "Buchungstag;Empfänger;Verwendungszweck;Gläubiger ID;Betrag\r\n"
    "01.10.2026;Stadtwerke;Abschlag Oktober;;-1.234,56\r\n"
    "02.10.2026;Bäckerei Müller;Kaffee & Kuchen;;-12,50\r\n"
    "05.10.2026;Arbeitgeber;Gehalt;;3.000,00 €\r\n"
)


def test_german_bank_export() -> None:
    rows = parse_csv(BANK_EXPORT.encode("cp1252"))
    assert rows[0] == ["Buchungstag", "Empfänger", "Verwendungszweck", "Gläubiger ID", "Betrag"]
    assert rows[3][4] == "3.000,00 €"
    markdown = to_markdown(rows)
    # The empty "Gläubiger ID" column is left out.
    assert markdown.splitlines() == [
        "| Buchungstag | Empfänger | Verwendungszweck | Betrag |",
        "| --- | --- | --- | --- |",
        "| 01.10.2026 | Stadtwerke | Abschlag Oktober | -1.234,56 |",
        "| 02.10.2026 | Bäckerei Müller | Kaffee & Kuchen | -12,50 |",
        "| 05.10.2026 | Arbeitgeber | Gehalt | 3.000,00 € |",
    ]


@pytest.mark.parametrize(
    ("text", "delimiter"),
    [
        ("a;b;c\n1,5;2,5;3,5\n", ";"),
        ("a,b,c\n1,2,3\n", ","),
        ("a\tb\n1\t2\n", "\t"),
        ("a|b\n1|2\n", "|"),
        ('name,note\n"Müller, Hans","a;b"\n', ","),
        ("single\nvalue\n", ","),
    ],
)
def test_detect_delimiter(text: str, delimiter: str) -> None:
    assert detect_delimiter(text) == delimiter


@pytest.mark.parametrize(
    "data",
    [
        "Größe;Preis\nM;5 €\n".encode("utf-8"),
        "﻿Größe;Preis\nM;5 €\n".encode("utf-8"),
        "Größe;Preis\nM;5 €\n".encode("cp1252"),
        "﻿Größe\tPreis\nM\t5 €\n".encode("utf-16-le"),
    ],
)
def test_encodings(data: bytes) -> None:
    rows = parse_csv(data)
    assert rows[0][0] == "Größe"
    assert rows[1][1] == "5 €"


def test_binary_file_is_rejected() -> None:
    with pytest.raises(CsvError, match="keine Textdatei"):
        decode(b"PK\x03\x04\x00\x00binary")


def test_cells_stay_in_one_line_and_pipes_are_escaped() -> None:
    rows = parse_csv(b'a,b\n"line 1\nline 2","x|y"\n')
    assert to_markdown(rows).splitlines()[-1] == "| line 1<br>line 2 | x\\|y |"


def test_ragged_rows_and_empty_lines() -> None:
    rows = parse_csv(b"a;b;c\n\n1;2\n;;\n4;5;6;7\n")
    assert rows == [["a", "b", "c"], ["1", "2"], ["4", "5", "6", "7"]]
    assert to_markdown(rows).splitlines() == [
        "| a | b | c |  |",
        "| --- | --- | --- | --- |",
        "| 1 | 2 |  |  |",
        "| 4 | 5 | 6 | 7 |",
    ]


def test_header_only_keeps_all_columns() -> None:
    assert to_markdown(parse_csv(b"a;b\n")) == "| a | b |\n| --- | --- |"


def test_empty_file() -> None:
    assert to_markdown(parse_csv(b"")) == ""


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.create_app(None))


def test_upload_csv(client: TestClient) -> None:
    response = client.post(
        "/convert", files={"file": ("umsatz.csv", BANK_EXPORT.encode("cp1252"))}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "csv"
    assert body["filename"] == "umsatz.md"
    assert body["markdown"].startswith("| Buchungstag | Empfänger |")
    assert body["info"] == {"format": "CSV", "size": len(BANK_EXPORT.encode("cp1252")), "rows": 3, "columns": 5}


def test_upload_csv_streams(client: TestClient) -> None:
    response = client.post(
        "/convert", files={"file": ("a.csv", b"a;b\n1;2\n")}, headers={"accept": main.NDJSON}
    )
    lines = [line for line in response.text.splitlines() if line.strip()]
    assert '"type": "start", "method": "csv"' in lines[0]
    assert '"type": "result"' in lines[-1]


@pytest.mark.parametrize("lang, message", [("de", "Die Datei ist keine Textdatei."), ("en", "The file is not a text file.")])
def test_binary_csv_upload_is_rejected(client: TestClient, lang: str, message: str) -> None:
    response = client.post(f"/convert?lang={lang}", files={"file": ("a.csv", b"\x00\x01\x02")})
    assert response.status_code == 422
    assert response.json() == {"error": message}


def test_oversized_field_is_reported_as_malformed(client: TestClient) -> None:
    response = client.post("/convert", files={"file": ("a.csv", b"a\n" + b"x" * 200_000)})
    assert response.status_code == 422
    assert response.json() == {"error": "Die CSV-Datei ist fehlerhaft."}
