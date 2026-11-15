from io import BytesIO

from openpyxl import Workbook

from app.convert_local import convert_local


def make_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Preise"
    sheet.append(["Artikel", "Preis"])
    sheet.append(["Kaffee", 3])
    buf = BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def test_xlsx_to_markdown_table() -> None:
    markdown = convert_local(make_xlsx(), ".xlsx")
    assert "## Preise" in markdown
    assert "| Artikel | Preis |" in markdown
    assert "Kaffee" in markdown
