"""HTTP layer: upload a document, get Markdown back."""
import json
import logging
import os
import queue
import struct
import threading
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import TypeVar

import anthropic
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.convert_claude import OcrError, Transcriber, convert_with_claude, make_transcriber
from app.convert_csv import CsvError, parse_csv, to_markdown
from app.convert_local import convert_local
from app.convert_pdf import PdfResult, Progress, convert_pdf_checked
from app.convert_tesseract import ocr_with_tesseract
from app.counter import Counter
from app.detect import PdfOpenError, analyze_pdf, open_pdf
from app.facts import CONTROL_CHARS, FORMAT_NAMES, office_facts, pdf_facts
from app.i18n import translate

T = TypeVar("T")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# pypdf logs a warning for many malformed files.
logging.getLogger("pypdf").setLevel(logging.ERROR)
log = logging.getLogger("markitdown-web")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    """Integer setting from the environment; an invalid value stops the app at startup."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if not (raw.isascii() and raw.isdigit()) or not low <= int(raw) <= high:
        raise RuntimeError(f"{name} must be an integer from {low} to {high}, got {raw!r}")
    return int(raw)


STATIC_DIR = Path(__file__).parent / "static"
SITE = "https://markdown.ki-kuenstler.com"
# Public pages with their source file, for the sitemap.
SITEMAP_PAGES = (("/", "index.html"), ("/en", "en.html"))


def _sitemap_xml() -> str:
    """Sitemap with lastmod taken from each page file, so it never goes stale."""
    alternates = "".join(
        f'\n    <xhtml:link rel="alternate" hreflang="{lang}" href="{SITE}{path}"/>'
        for lang, path in (("de", "/"), ("en", "/en"))
    )
    entries = []
    for path, filename in SITEMAP_PAGES:
        modified = time.strftime("%Y-%m-%d", time.gmtime((STATIC_DIR / filename).stat().st_mtime))
        entries.append(
            f"  <url>\n    <loc>{SITE}{path}</loc>\n    <lastmod>{modified}</lastmod>{alternates}\n  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(entries) + "\n</urlset>\n"
    )


MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 30, 1, 30)
MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# Slack on top of MAX_BYTES for the multipart envelope (boundaries, headers, form fields).
MULTIPART_OVERHEAD = 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".csv"}
# OCR runs synchronously inside the request, so its duration and cost need a bound.
MAX_OCR_PAGES = _env_int("MAX_OCR_PAGES", 100, 1, 100)
# Local PDF conversion takes about 0.5-1.5 s per page on a Pi 5, depending on the CPU share.
MAX_PDF_PAGES = _env_int("MAX_PDF_PAGES", 500, 1, 500)
# Parallel Tesseract processes for local OCR (one per CPU the container may use).
OCR_JOBS = _env_int("OCR_JOBS", 2, 1, 4)
# A streamed response sends a blank line after this many idle seconds, so proxies
# (Cloudflare cuts after 100 s) keep the connection open during a long OCR chunk.
HEARTBEAT_SECONDS = 15.0
NDJSON = "application/x-ndjson"
# Office files are ZIP containers: bound what they may expand to (zip bombs).
MAX_UNZIPPED_MB = _env_int("MAX_UNZIPPED_MB", 300, 1, 300)
MAX_UNZIPPED_BYTES = MAX_UNZIPPED_MB * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
# Real Office files use ~100 B of central directory per entry. zipfile's parse loop is
# bounded by this size, not by the entry count in the EOCD record.
MAX_CENTRAL_DIR_BYTES = 2 * 1024 * 1024
# Conversions in flight at the same time (a Pi 5 has limited memory and CPU).
MAX_PARALLEL = _env_int("MAX_PARALLEL", 2, 1, 4)
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR = b"PK\x06\x07"
_EOCD_SEARCH_BYTES = 22 + 0xFFFF  # fixed record size plus the longest possible comment

# Six months; no preload, so it can still be withdrawn.
_HSTS = "max-age=15552000; includeSubDomains"

_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

MSG_TOO_LARGE = f"Datei zu groß, höchstens {MAX_BYTES // 2**20} MB."
MSG_UNZIPPED_TOO_LARGE = "Datei ist entpackt zu groß."
MSG_INVALID = "Ungültige Anfrage."
MSG_UNSUPPORTED = "Nur PDF, DOCX, PPTX, XLSX und CSV werden unterstützt."
MSG_INTERNAL = "Interner Fehler."
MSG_OCR_UNAVAILABLE = "Texterkennung ist hier nicht verfügbar."
MSG_NO_OCR = "Gescannte PDFs werden hier nicht unterstützt, nur Dateien mit echtem Text."
MSG_CONVERT_FAILED = "Die Datei konnte nicht umgewandelt werden."
MSG_EMPTY = "Das Ergebnis ist leer. Mit „Texterkennung erzwingen“ noch einmal versuchen?"
MSG_EMPTY_NO_OCR = "Das Ergebnis ist leer. Enthält die Datei vielleicht nur Bilder?"


class ApiError(Exception):
    """A request problem that is reported to the client as {"error": message}."""

    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers


def _lang(request: Request) -> str:
    """UI language of the caller: the English page sends ?lang=en."""
    return "en" if request.query_params.get("lang") == "en" else "de"


def _error_response(
    status: int, message: str, lang: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": translate(message, lang)}, status_code=status, headers=headers)


def _localized(result: dict[str, object], lang: str) -> dict[str, object]:
    """Translate the optional warning of a conversion result."""
    warning = result.get("warning")
    if isinstance(warning, str):
        return {**result, "warning": translate(warning, lang)}
    return result


def default_transcriber() -> Transcriber | None:
    """Claude transcriber, or None when no API key is configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    # Streaming: the read timeout is the idle time between bytes, not the total duration.
    # Retries only happen before the stream has started.
    client = anthropic.Anthropic(
        timeout=anthropic.Timeout(120.0, connect=10.0), max_retries=2
    )
    return make_transcriber(client)


def _safe_name(raw: str | None) -> str:
    """Basename without control characters; never echo path components."""
    name = Path((raw or "").replace("\\", "/")).name
    return CONTROL_CHARS.sub("", name)


def _check_office_zip(data: bytes) -> list[zipfile.ZipInfo]:
    """Reject non-ZIP input and zip bombs before MarkItDown opens the container.

    Returns the entries of the archive, so callers need no second parse.

    zipfile never inflates an entry beyond its declared file_size, so the declared
    sizes are a reliable upper bound for what the converters can read.
    """
    # Read the entry count from the End Of Central Directory record first: building
    # the central directory for hundreds of thousands of entries costs a lot of memory.
    eocd = data.rfind(_EOCD_SIGNATURE, max(0, len(data) - _EOCD_SEARCH_BYTES))
    if eocd < 0 or eocd + 22 > len(data):
        raise ApiError(422, MSG_CONVERT_FAILED)
    # zipfile trusts a ZIP64 locator right before the EOCD and then ignores the 32-bit
    # fields checked below. Office files under the size limit never need ZIP64.
    if eocd >= 20 and data[eocd - 20 : eocd - 16] == _ZIP64_LOCATOR:
        raise ApiError(413, MSG_UNZIPPED_TOO_LARGE)
    entry_count, directory_size = struct.unpack_from("<HI", data, eocd + 10)
    # 0xFFFF marks ZIP64; Office files never need it, and it exceeds the cap anyway.
    if entry_count > MAX_ZIP_ENTRIES or directory_size > MAX_CENTRAL_DIR_BYTES:
        raise ApiError(413, MSG_UNZIPPED_TOO_LARGE)
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            declared = sum(entry.file_size for entry in entries)
    except Exception as exc:  # untrusted input
        raise ApiError(422, MSG_CONVERT_FAILED) from exc
    # The EOCD count is only a hint and can be spoofed; check the parsed entries too.
    if len(entries) > MAX_ZIP_ENTRIES or declared > MAX_UNZIPPED_BYTES:
        raise ApiError(413, MSG_UNZIPPED_TOO_LARGE)
    return entries


@dataclass(frozen=True)
class Plan:
    """A checked upload: run() does the slow part and may report progress."""

    method: str
    pages: int | None
    run: Callable[[Progress], str]
    facts: dict[str, object] = field(default_factory=dict)


def _plan_pdf(
    data: bytes, force_ocr: bool, transcriber: Transcriber | None, local_ocr: bool
) -> Plan:
    """Check a PDF and pick the converter; the facts include the scan analysis and metadata."""
    try:
        reader = open_pdf(data)
        info = analyze_pdf(reader)
        facts = {"pages": info.pages, "scanned": info.scanned, **pdf_facts(reader)}
    except PdfOpenError as exc:
        raise ApiError(422, f"{exc}.") from exc
    if not (info.scanned or force_ocr):
        if info.pages > MAX_PDF_PAGES:
            raise ApiError(413, f"PDF hat zu viele Seiten, höchstens {MAX_PDF_PAGES}.")

        def run_digital(progress: Progress) -> str:
            # Plan.run only returns the Markdown; the coverage goes into the shared
            # facts dict, which finish() reads after run() has returned.
            result: PdfResult = _run_local(lambda: convert_pdf_checked(data, progress))
            if result.coverage is not None:
                facts["coverage"] = result.coverage.percent
                facts["weak_pages"] = result.coverage.weak_pages
            return result.markdown

        return Plan("pymupdf4llm", info.pages, run_digital, facts)
    if transcriber is None and not local_ocr:
        # Scans get the neutral message; forced OCR on a digital PDF just isn't offered.
        raise ApiError(422, MSG_NO_OCR if info.scanned else MSG_OCR_UNAVAILABLE)
    if info.pages > MAX_OCR_PAGES:
        raise ApiError(413, f"Für die Texterkennung höchstens {MAX_OCR_PAGES} Seiten.")

    def run(progress: Progress) -> str:
        try:
            if transcriber is not None:
                return convert_with_claude(reader, transcriber, progress)
            return ocr_with_tesseract(data, info.pages, force_ocr, OCR_JOBS, progress)
        except OcrError as exc:
            raise ApiError(502, f"Fehler bei der Texterkennung: {exc}") from exc

    method = "claude" if transcriber is not None else "tesseract"
    return Plan(method, info.pages, run, facts)


def _plan_csv(data: bytes) -> Plan:
    """Parse right away (fast even for large files), so broken files fail before the stream."""
    try:
        rows = parse_csv(data)
    except CsvError as exc:
        raise ApiError(422, str(exc)) from exc
    except Exception as exc:  # e.g. a field over the csv module's size limit
        log.warning("csv parse failed: %s", type(exc).__name__)
        raise ApiError(422, MSG_CONVERT_FAILED) from exc
    facts = {"rows": max(len(rows) - 1, 0), "columns": max((len(row) for row in rows), default=0)}
    return Plan("csv", None, lambda progress: to_markdown(rows), facts)


def _run_local(convert: Callable[[], T]) -> T:
    try:
        return convert()
    except Exception as exc:  # parser libraries raise many types on bad input
        log.warning("conversion failed: %s", type(exc).__name__)
        raise ApiError(422, MSG_CONVERT_FAILED) from exc


def _contained(func: Callable[[], T]) -> T:
    """Turn unexpected errors into a plain 500; parser tracebacks may quote content."""
    try:
        return func()
    except ApiError:
        raise
    except Exception as exc:
        log.error("conversion crashed: %s", type(exc).__name__)
        raise ApiError(500, MSG_INTERNAL) from None


def _ignore_progress(done: int, total: int) -> None:
    """Progress sink for plain JSON responses."""


def _event(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _start_stream(
    plan: Plan,
    finish: Callable[[Progress], dict[str, object]],
    release: Callable[[], None],
    lang: str,
) -> Iterator[bytes]:
    """Run the conversion in a worker thread and stream its events as NDJSON.

    The worker starts right away and releases the slot itself, so a client that
    disconnects early cannot leak it.
    """
    events: queue.Queue[dict[str, object] | None] = queue.Queue()

    def progress(done: int, total: int) -> None:
        events.put({"type": "progress", "done": done, "total": total})

    def work() -> None:
        try:
            events.put({"type": "result", **_localized(_contained(lambda: finish(progress)), lang)})
        except ApiError as exc:
            events.put(
                {"type": "error", "status": exc.status, "error": translate(exc.message, lang)}
            )
        finally:
            release()
            events.put(None)

    threading.Thread(target=work, name="convert", daemon=True).start()

    def stream() -> Iterator[bytes]:
        yield _event({"type": "start", "method": plan.method, "pages": plan.pages})
        while True:
            try:
                event = events.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                yield b"\n"
                continue
            if event is None:
                return
            yield _event(event)

    return stream()


# Pages that also answer with a trailing slash ("/en/" -> "/en").
_SLASH_PAGES = frozenset({"/en", "/impressum", "/datenschutz"})


def _slash_redirect(request: Request) -> Response | None:
    """Permanent redirect for known pages with a trailing slash.

    Starlette's own redirect builds an absolute URL from the plain-HTTP hop behind
    the tunnel (http://...); a relative target keeps the browser on HTTPS. Only
    fixed paths are redirected, so the target can never point to another site.
    """
    path = request.url.path
    if request.method not in {"GET", "HEAD"} or not path.endswith("/"):
        return None
    target = path.rstrip("/")
    if target not in _SLASH_PAGES:
        return None
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=301)


def _guard_convert(request: Request) -> Response | None:
    """Early rejections for POST /convert; None lets the request through."""
    # Browsers send Sec-Fetch-Site; a POST from another site could burn OCR credits.
    # A missing header (curl, tests) is allowed.
    lang = _lang(request)
    if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
        return _error_response(403, "Anfrage von fremder Seite abgelehnt.", lang)
    # Starlette spools the whole multipart body before the handler runs, so the size
    # has to be checked from the header first. uvicorn enforces the declared length,
    # so a client cannot send more than it announces.
    length = request.headers.get("content-length", "")
    if not (length.isascii() and length.isdigit()):
        return _error_response(411, "Länge fehlt.", lang)
    if int(length) > MAX_BYTES + MULTIPART_OVERHEAD:
        return _error_response(413, MSG_TOO_LARGE, lang)
    return None


def create_app(
    transcriber: Transcriber | None,
    local_ocr: bool = False,
    counter: Counter | None = None,
    coffees: int = 0,
) -> FastAPI:
    """OCR for scans: Claude when a transcriber is given, else Tesseract if local_ocr.

    The counter tallies successful conversions; without one it counts in memory.
    coffees is the number of donations received so far, shown next to the coffee link.
    """
    converted = counter or Counter(None)
    ocr_engine = "claude" if transcriber is not None else "tesseract" if local_ocr else None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    slots = threading.BoundedSemaphore(MAX_PARALLEL)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status, exc.message, _lang(request), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(422, MSG_INVALID, _lang(request))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = MSG_INVALID if exc.status_code < 500 else MSG_INTERNAL
        return _error_response(exc.status_code, message, _lang(request))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unexpected error: %s", type(exc).__name__)
        return _error_response(500, MSG_INTERNAL, _lang(request))

    @app.middleware("http")
    async def guard_and_harden(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        is_convert = request.url.path == "/convert"
        rejection = _guard_convert(request) if is_convert and request.method == "POST" else None
        response = rejection or _slash_redirect(request) or await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Everything else may be cached but must be revalidated (ETag), so a proxy such as
        # Cloudflare never serves an old style.css or app.js next to a new page.
        response.headers["Cache-Control"] = "no-store" if is_convert else "no-cache"
        response.headers["Content-Security-Policy"] = _CSP
        # Both instances are only reached over HTTPS (Cloudflare, Tailscale Serve);
        # browsers ignore this header on plain HTTP, so local curl tests are unaffected.
        response.headers["Strict-Transport-Security"] = _HSTS
        return response

    @app.api_route("/", methods=["GET", "HEAD"])
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.api_route("/en", methods=["GET", "HEAD"])
    def index_en() -> FileResponse:
        return FileResponse(STATIC_DIR / "en.html")

    @app.api_route("/impressum", methods=["GET", "HEAD"])
    def imprint() -> FileResponse:
        return FileResponse(STATIC_DIR / "impressum.html")

    @app.api_route("/datenschutz", methods=["GET", "HEAD"])
    def privacy() -> FileResponse:
        return FileResponse(STATIC_DIR / "datenschutz.html")

    # Crawlers expect these at the site root.
    @app.api_route("/robots.txt", methods=["GET", "HEAD"])
    def robots() -> FileResponse:
        return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain; charset=utf-8")

    @app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
    def sitemap() -> Response:
        return Response(_sitemap_xml(), media_type="application/xml")

    # Browsers and search engines request these at the root regardless of <link> tags.
    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")

    @app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"])
    def apple_touch_icon() -> FileResponse:
        return FileResponse(STATIC_DIR / "apple-touch-icon.png", media_type="image/png")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "ocr": ocr_engine is not None,
            "ocr_engine": ocr_engine,
            "max_upload_mb": MAX_BYTES // 2**20,
            "converted": converted.value,
            "coffees": coffees,
        }

    def prepare(
        file: UploadFile, force_ocr: bool
    ) -> tuple[Plan, Callable[[Progress], dict[str, object]]]:
        """Fast checks; returns the plan and the function that does the slow part."""
        started = time.monotonic()
        name = _safe_name(file.filename)
        extension = Path(name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ApiError(415, MSG_UNSUPPORTED)
        data = file.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ApiError(413, MSG_TOO_LARGE)

        if extension == ".pdf":
            plan = _plan_pdf(data, force_ocr, transcriber, local_ocr)
        elif extension == ".csv":
            plan = _plan_csv(data)
        else:
            entries = _check_office_zip(data)
            plan = Plan(
                "markitdown",
                None,
                lambda progress: _run_local(lambda: convert_local(data, extension)),
                office_facts(data, extension, entries),
            )

        def finish(progress: Progress) -> dict[str, object]:
            markdown = plan.run(progress)
            weak = plan.facts.get("weak_pages")
            log.info(
                "converted size=%d method=%s pages=%s duration=%.1fs coverage=%s weak=%s",
                len(data), plan.method, plan.pages, time.monotonic() - started,
                plan.facts.get("coverage", "-"), len(weak) if isinstance(weak, list) else "-",
            )
            result: dict[str, object] = {
                "markdown": markdown,
                "method": plan.method,
                "pages": plan.pages,
                "filename": (Path(name).stem or "dokument") + ".md",
                "info": {"format": FORMAT_NAMES[extension], "size": len(data), **plan.facts},
            }
            if markdown:
                converted.increment()
            else:
                result["warning"] = MSG_EMPTY if ocr_engine else MSG_EMPTY_NO_OCR
            return result

        return plan, finish

    @app.post("/convert")
    def convert(
        request: Request, file: UploadFile = File(...), force_ocr: bool = Form(False)
    ) -> Response:
        if not slots.acquire(blocking=False):
            raise ApiError(
                503,
                "Gerade ausgelastet, bitte gleich noch einmal versuchen.",
                {"Retry-After": "5"},
            )
        # The stream's worker thread releases the slot itself once it has started.
        handed_over = False
        lang = _lang(request)
        try:
            plan, finish = _contained(lambda: prepare(file, force_ocr))
            if NDJSON not in request.headers.get("accept", ""):
                return JSONResponse(_localized(_contained(lambda: finish(_ignore_progress)), lang))
            # Errors from here on arrive as an "error" event: the status line is already sent.
            stream = _start_stream(plan, finish, slots.release, lang)
            handed_over = True
            return StreamingResponse(stream, media_type=NDJSON)
        finally:
            if not handed_over:
                slots.release()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _default_app() -> FastAPI:
    """OCR_ENGINE picks the OCR for scans: "claude" (default, needs a key) or "tesseract".

    COUNTER_FILE (e.g. /data/converted.txt on a volume) keeps the conversion count.
    COFFEES is the number of coffee donations received so far (raise it by hand).
    """
    counter_file = os.environ.get("COUNTER_FILE", "").strip()
    counter = Counter(Path(counter_file) if counter_file else None)
    coffees = _env_int("COFFEES", 0, 0, 1_000_000)
    engine = os.environ.get("OCR_ENGINE", "claude").strip().lower()
    if engine == "claude":
        return create_app(default_transcriber(), counter=counter, coffees=coffees)
    if engine == "tesseract":
        return create_app(None, local_ocr=True, counter=counter, coffees=coffees)
    raise RuntimeError(f"OCR_ENGINE must be claude or tesseract, got {engine!r}")


app = _default_app()
