"""OCR scanned PDFs locally: OCRmyPDF (Tesseract) adds a text layer, PyMuPDF4LLM converts.

Nothing leaves the server. OCRmyPDF runs as a separate process (Debian package with
its own Python), so a crash or hang in it cannot take the app down.
"""
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from app.convert_claude import OcrError
from app.convert_pdf import Progress, convert_pdf

LANGUAGES = "deu+eng"
PLUGIN = Path(__file__).with_name("ocrmypdf_progress.py")
# About 5 s per page is normal on a Pi 5 with two cores; this is the hard stop.
TIMEOUT_PER_PAGE = 30
TIMEOUT_BASE = 30.0
_PROGRESS = re.compile(r"^PROGRESS OCR ([0-9.]+) ([0-9]+)$")
# Only what OCRmyPDF and Tesseract need, no API keys from the app's environment.
_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


def _command(source: Path, target: Path, force: bool, jobs: int) -> list[str]:
    return [
        "ocrmypdf",
        "--plugin", str(PLUGIN),
        "-l", LANGUAGES,
        "--jobs", str(jobs),
        # Forced OCR re-renders pages that already have text; otherwise those are skipped.
        "--force-ocr" if force else "--skip-text",
        "--output-type", "pdf",
        "--optimize", "0",
        str(source),
        str(target),
    ]


def _environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    # Tesseract's own threads on top of --jobs would oversubscribe the CPUs.
    env["OMP_THREAD_LIMIT"] = "1"
    return env


def _run_ocrmypdf(
    command: list[str], timeout: float, pages: int, on_progress: Progress | None
) -> None:
    """Run OCRmyPDF, forward its per-page progress, raise OcrError on failure."""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=_environment(),
        )
    except OSError as exc:
        raise OcrError("Texterkennung ist nicht installiert") from exc
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        process.kill()

    timer = threading.Timer(timeout, kill)
    timer.daemon = True
    timer.start()
    try:
        reported = 0
        assert process.stderr is not None
        for line in process.stderr:
            match = _PROGRESS.match(line.strip())
            if match and on_progress:
                done = min(int(float(match.group(1))), pages)
                if done > reported:
                    reported = done
                    on_progress(done, pages)
        returncode = process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()
    if timed_out.is_set():
        raise OcrError("Texterkennung hat zu lange gedauert")
    if returncode != 0:
        # OCRmyPDF's messages may quote document content, so only the code is shown.
        raise OcrError(f"Texterkennung fehlgeschlagen (Code {returncode})")


def ocr_with_tesseract(
    data: bytes, pages: int, force: bool, jobs: int, on_progress: Progress | None = None
) -> str:
    """Add a text layer with OCRmyPDF, then convert like a digital PDF."""
    with tempfile.TemporaryDirectory(prefix="ocr-") as workdir:
        source, target = Path(workdir, "in.pdf"), Path(workdir, "out.pdf")
        source.write_bytes(data)
        command = _command(source, target, force, jobs)
        _run_ocrmypdf(command, TIMEOUT_PER_PAGE * pages + TIMEOUT_BASE, pages, on_progress)
        if not target.is_file():
            raise OcrError("Texterkennung hat kein Ergebnis geliefert")
        ocr_pdf = target.read_bytes()
    return convert_pdf(ocr_pdf)
