"""Transcribe scanned PDFs to Markdown with Claude, in chunks of pages."""
import base64
from collections.abc import Callable, Iterator
from io import BytesIO
from typing import NamedTuple

import anthropic
import httpx2
from pypdf import PdfReader, PdfWriter

CHUNK_PAGES = 20
# Base64 adds 4/3, so 20 MB becomes ~27 MB: headroom under the API's 32 MB request limit.
MAX_CHUNK_BYTES = 20 * 1024 * 1024
MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 128000
PROMPT = (
    "Transcribe this document into Markdown. Reproduce the content completely and "
    "faithfully in its original language. Keep headings, lists and tables (as Markdown "
    "tables). Do not summarize, translate, correct or comment. Mark illegible passages "
    "as [unleserlich] instead of guessing. Output only the Markdown, without a code fence."
)

# Takes a PDF (one chunk) and returns its Markdown.
Transcriber = Callable[[bytes], str]


class OcrError(Exception):
    """Transcription with Claude failed; the message is shown to the user."""


class Chunk(NamedTuple):
    """A part of the PDF; page numbers are 1-based and inclusive."""

    first_page: int
    last_page: int
    data: bytes


def _write_pages(reader: PdfReader, start: int, end: int) -> bytes:
    """Write pages start..end-1 (0-based) as a new PDF."""
    try:
        writer = PdfWriter()
        for index in range(start, end):
            writer.add_page(reader.pages[index])
        buf = BytesIO()
        writer.write(buf)
    except Exception as exc:  # pypdf raises many types on malformed input
        raise OcrError("PDF konnte nicht aufgeteilt werden") from exc
    return buf.getvalue()


def split_pdf(
    reader: PdfReader,
    chunk_pages: int = CHUNK_PAGES,
    max_bytes: int = MAX_CHUNK_BYTES,
) -> Iterator[Chunk]:
    """Yield chunks in page order, one in memory at a time.

    A range is halved until it fits into max_bytes.
    """
    total = len(reader.pages)
    # Stack of (start, end) ranges, 0-based; the next range to emit is on top.
    pending = [
        (start, min(start + chunk_pages, total))
        for start in range(0, total, chunk_pages)
    ][::-1]
    while pending:
        start, end = pending.pop()
        data = _write_pages(reader, start, end)
        if len(data) > max_bytes:
            if end - start == 1:
                raise OcrError(f"Seite {start + 1} ist zu groß für die Umwandlung")
            middle = (start + end) // 2
            pending.extend([(middle, end), (start, middle)])
            continue
        yield Chunk(start + 1, end, data)


def convert_with_claude(
    reader: PdfReader,
    transcribe: Transcriber,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Transcribe all chunks in page order and join the results.

    on_progress gets (pages done, pages total) after each chunk.
    """
    total = len(reader.pages)
    parts: list[str] = []
    for chunk in split_pdf(reader):
        try:
            parts.append(transcribe(chunk.data).strip())
        except OcrError as exc:
            if chunk.first_page == chunk.last_page:
                pages = f"Seite {chunk.first_page}"
            else:
                pages = f"Seiten {chunk.first_page}–{chunk.last_page}"
            raise OcrError(f"{exc} ({pages})") from exc
        if on_progress:
            on_progress(chunk.last_page, total)
    return "\n\n".join(part for part in parts if part)


def _api_detail(exc: anthropic.APIStatusError) -> str:
    """Extract the error message from the API response body, defensively."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return "unbekannter Fehler"


def make_transcriber(client: anthropic.Anthropic) -> Transcriber:
    """Build a Transcriber that sends one PDF chunk to Claude."""

    def transcribe(pdf: bytes) -> str:
        document = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode("ascii"),
            },
        }
        try:
            # Streaming: 20 pages of output can outlast a plain request.
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low"},
                messages=[
                    {"role": "user", "content": [document, {"type": "text", "text": PROMPT}]}
                ],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise OcrError("API-Key ungültig") from exc
        except anthropic.RateLimitError as exc:
            raise OcrError("Rate Limit erreicht, bitte gleich noch einmal versuchen") from exc
        except anthropic.BadRequestError as exc:
            # Also covers an empty credit balance.
            raise OcrError(f"Anfrage abgelehnt: {_api_detail(exc)}") from exc
        except anthropic.APIStatusError as exc:
            # Also covers errors sent mid-stream (e.g. overloaded_error with status 200).
            raise OcrError(f"Claude-Fehler {exc.status_code}: {_api_detail(exc)}") from exc
        except anthropic.APIConnectionError as exc:
            # Includes APITimeoutError.
            raise OcrError("Claude nicht erreichbar") from exc
        except httpx2.TransportError as exc:
            # Raised unwrapped while the SSE stream is being read.
            raise OcrError("Verbindung zu Claude abgebrochen") from exc
        except anthropic.AnthropicError as exc:
            # E.g. missing credentials.
            raise OcrError("Claude-Fehler") from exc

        if message.stop_reason == "refusal":
            raise OcrError("Claude hat die Umwandlung abgelehnt")
        if message.stop_reason == "max_tokens":
            raise OcrError("Ausgabe zu lang")
        return "".join(block.text for block in message.content if block.type == "text")

    return transcribe
