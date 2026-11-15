import base64
from collections.abc import Callable
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pypdf import PdfReader, PdfWriter

from app.convert_claude import (
    MODEL,
    OcrError,
    convert_with_claude,
    make_transcriber,
    split_pdf,
)
from app.detect import open_pdf
from tests.pdf_factory import PageSpec

MakePdf = Callable[[list[PageSpec]], bytes]


def _pages_bytes(pdf: bytes) -> bytes:
    """The PDF as written by split_pdf (same writer, so sizes are comparable)."""
    return next(iter(split_pdf(open_pdf(pdf)))).data


def page_count(pdf: bytes) -> int:
    return len(PdfReader(BytesIO(pdf)).pages)


def test_split_into_chunks_of_20(make_pdf: MakePdf) -> None:
    chunks = list(split_pdf(open_pdf(make_pdf([None] * 45))))
    assert [page_count(c.data) for c in chunks] == [20, 20, 5]
    assert [(c.first_page, c.last_page) for c in chunks] == [(1, 20), (21, 40), (41, 45)]


def test_small_pdf_is_one_chunk(scanned_pdf: bytes) -> None:
    assert [page_count(c.data) for c in split_pdf(open_pdf(scanned_pdf))] == [3]


def test_results_joined_in_page_order(make_pdf: MakePdf) -> None:
    seen: list[int] = []

    def fake_transcribe(chunk: bytes) -> str:
        seen.append(page_count(chunk))
        return f"Teil {len(seen)}\n"

    markdown = convert_with_claude(open_pdf(make_pdf([None] * 41)), fake_transcribe)
    assert seen == [20, 20, 1]
    assert markdown == "Teil 1\n\nTeil 2\n\nTeil 3"


def test_progress_after_each_chunk(make_pdf: MakePdf) -> None:
    calls: list[tuple[int, int]] = []
    convert_with_claude(
        open_pdf(make_pdf([None] * 41)),
        lambda chunk: "x",
        lambda done, total: calls.append((done, total)),
    )
    assert calls == [(20, 41), (40, 41), (41, 41)]


def test_failing_chunk_fails_whole_conversion(make_pdf: MakePdf) -> None:
    calls: list[int] = []

    def fake_transcribe(chunk: bytes) -> str:
        calls.append(1)
        if len(calls) == 2:
            raise OcrError("Claude nicht erreichbar")
        return "ok"

    with pytest.raises(OcrError, match=r"nicht erreichbar \(Seiten 21–40\)"):
        convert_with_claude(open_pdf(make_pdf([None] * 45)), fake_transcribe)
    # The third chunk is never built or sent.
    assert len(calls) == 2


def test_split_failure_becomes_ocr_error(
    scanned_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_write(self: PdfWriter, *args: object, **kwargs: object) -> None:
        raise ValueError("kaputt")

    monkeypatch.setattr(PdfWriter, "write", broken_write)
    with pytest.raises(OcrError, match="aufgeteilt"):
        list(split_pdf(open_pdf(scanned_pdf)))


def test_oversized_chunk_is_halved_in_page_order(make_pdf: MakePdf) -> None:
    reader = open_pdf(make_pdf([None] * 5))
    four_pages = len(_pages_bytes(make_pdf([None] * 4)))
    chunks = list(split_pdf(reader, max_bytes=four_pages))
    assert [(c.first_page, c.last_page) for c in chunks] == [(1, 2), (3, 5)]
    assert [page_count(c.data) for c in chunks] == [2, 3]


def test_single_oversized_page_is_ocr_error(scanned_pdf: bytes) -> None:
    with pytest.raises(OcrError, match="Seite 1 ist zu groß"):
        list(split_pdf(open_pdf(scanned_pdf), max_bytes=1))


def test_single_page_chunk_error_names_the_page(make_pdf: MakePdf) -> None:
    def failing(chunk: bytes) -> str:
        raise OcrError("Fehler")

    with pytest.raises(OcrError, match=r"Fehler \(Seite 1\)"):
        convert_with_claude(open_pdf(make_pdf([None])), failing)


class FakeStream:
    def __init__(self, message: SimpleNamespace | None) -> None:
        self.message = message

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get_final_message(self) -> SimpleNamespace | None:
        return self.message


def fake_client(
    calls: list[dict[str, Any]],
    message: SimpleNamespace | None = None,
    error: Exception | None = None,
) -> Any:
    def stream(**kwargs: Any) -> FakeStream:
        calls.append(kwargs)
        if error:
            raise error
        return FakeStream(message)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def reply(stop_reason: str, *texts: str) -> SimpleNamespace:
    blocks = [SimpleNamespace(type="thinking", thinking="")]
    blocks += [SimpleNamespace(type="text", text=t) for t in texts]
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


def test_transcriber_sends_pdf_and_joins_text() -> None:
    calls: list[dict[str, Any]] = []
    transcribe = make_transcriber(fake_client(calls, reply("end_turn", "# Titel\n", "Text")))
    assert transcribe(b"%PDF-fake") == "# Titel\nText"

    request = calls[0]
    assert request["model"] == MODEL
    assert request["max_tokens"] == 128000
    assert request["output_config"] == {"effort": "low"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    document = request["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert base64.b64decode(document["source"]["data"]) == b"%PDF-fake"


def test_refusal_becomes_ocr_error() -> None:
    transcribe = make_transcriber(fake_client([], reply("refusal")))
    with pytest.raises(OcrError, match="abgelehnt"):
        transcribe(b"%PDF-fake")


def test_max_tokens_becomes_ocr_error() -> None:
    transcribe = make_transcriber(fake_client([], reply("max_tokens", "abgeschnitten")))
    with pytest.raises(OcrError, match="zu lang"):
        transcribe(b"%PDF-fake")


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(
    cls: type[anthropic.APIStatusError], code: int, body: object
) -> anthropic.APIStatusError:
    return cls("raw", response=httpx2.Response(code, request=REQUEST), body=body)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(anthropic.AuthenticationError, 401, None), "API-Key ungültig"),
        (status_error(anthropic.RateLimitError, 429, None), "Rate Limit"),
        (
            status_error(
                anthropic.BadRequestError, 400, {"error": {"message": "Guthaben leer"}}
            ),
            "Anfrage abgelehnt: Guthaben leer",
        ),
        (
            status_error(anthropic.BadRequestError, 400, "kein dict"),
            "Anfrage abgelehnt: unbekannter Fehler",
        ),
        (
            status_error(
                anthropic.APIStatusError, 200, {"error": {"message": "Overloaded"}}
            ),
            "Claude-Fehler 200: Overloaded",
        ),
        (anthropic.APIConnectionError(request=REQUEST), "Claude nicht erreichbar"),
        (anthropic.APITimeoutError(request=REQUEST), "Claude nicht erreichbar"),
        (httpx2.ReadTimeout("timeout"), "abgebrochen"),
        (anthropic.AnthropicError("kein Key"), "Claude-Fehler"),
    ],
)
def test_api_errors_become_ocr_error(error: Exception, expected: str) -> None:
    transcribe = make_transcriber(fake_client([], error=error))
    with pytest.raises(OcrError, match=expected):
        transcribe(b"%PDF-fake")
