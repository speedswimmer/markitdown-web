"""OCRmyPDF wrapper: tested with a fake ocrmypdf; the real one runs in the Docker image."""
import shutil
import sys
from pathlib import Path

import pytest

from app import convert_tesseract
from app.convert_claude import OcrError
from app.convert_tesseract import ocr_with_tesseract
from tests.pdf_factory import build_pdf, text_page

FAKE = """
import shutil, sys, time
source, target, mode = sys.argv[1], sys.argv[2], sys.argv[3]
if mode == "fail":
    sys.exit(7)
if mode == "slow":
    time.sleep(10)
for done in ("0.5", "1.0", "1.5", "2.0"):
    print(f"PROGRESS OCR {done} 2", file=sys.stderr, flush=True)
print("PROGRESS Linearizing 50 100", file=sys.stderr, flush=True)
shutil.copy(source, target)
"""


def use_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str) -> None:
    script = tmp_path / "fake_ocrmypdf.py"
    script.write_text(FAKE, encoding="utf-8")

    def command(source: Path, target: Path, force: bool, jobs: int) -> list[str]:
        return [sys.executable, str(script), str(source), str(target), mode]

    monkeypatch.setattr(convert_tesseract, "_command", command)


def test_progress_and_conversion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, "ok")
    calls: list[tuple[int, int]] = []
    pdf = build_pdf([text_page(1), text_page(2)])  # the fake "OCR" just copies it
    markdown = ocr_with_tesseract(pdf, 2, False, 2, lambda d, t: calls.append((d, t)))
    assert calls == [(1, 2), (2, 2)]  # whole pages only, other stages ignored
    assert "Testseite" in markdown


def test_failure_hides_details(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, "fail")
    with pytest.raises(OcrError, match=r"^Texterkennung fehlgeschlagen \(Code 7\)$"):
        ocr_with_tesseract(build_pdf([None]), 1, False, 1)


def test_timeout_kills_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, "slow")
    monkeypatch.setattr(convert_tesseract, "TIMEOUT_PER_PAGE", 0)
    monkeypatch.setattr(convert_tesseract, "TIMEOUT_BASE", 0.5)
    with pytest.raises(OcrError, match="zu lange"):
        ocr_with_tesseract(build_pdf([None]), 1, False, 1)


def test_missing_ocrmypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        convert_tesseract, "_command", lambda *args: ["ocrmypdf-gibt-es-nicht-xyz"]
    )
    with pytest.raises(OcrError, match="nicht installiert"):
        ocr_with_tesseract(build_pdf([None]), 1, False, 1)


def test_environment_does_not_leak_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-geheim")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:1")
    env = convert_tesseract._environment()
    assert "ANTHROPIC_API_KEY" not in env
    assert "HTTPS_PROXY" not in env
    assert env["OMP_THREAD_LIMIT"] == "1"


def test_command_uses_skip_or_force(tmp_path: Path) -> None:
    normal = convert_tesseract._command(tmp_path / "a", tmp_path / "b", False, 2)
    forced = convert_tesseract._command(tmp_path / "a", tmp_path / "b", True, 2)
    assert "--skip-text" in normal and "--force-ocr" not in normal
    assert "--force-ocr" in forced and "--skip-text" not in forced
    assert normal[normal.index("-l") + 1] == "deu+eng"


@pytest.mark.skipif(shutil.which("ocrmypdf") is None, reason="ocrmypdf not installed")
def test_real_ocr_on_rendered_scan() -> None:
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (1700, 2200), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    draw.text((150, 200), "Rechnung Nummer 4711", font=font, fill=0)
    draw.text((150, 320), "Schrauben und Muttern", font=font, fill=0)
    buf = BytesIO()
    image.save(buf, format="PDF", resolution=200)
    markdown = ocr_with_tesseract(buf.getvalue(), 1, False, 1)
    assert "4711" in markdown
    assert "Schrauben" in markdown
