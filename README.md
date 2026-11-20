# markitdown-web

Upload a PDF, Word, PowerPoint, Excel or CSV file, get Markdown back. A small
FastAPI app that I run on a Raspberry Pi 5 at home. The public instance is at
https://markdown.ki-kuenstler.com (German, English version under `/en`).

Why: pasting a 70-page PDF into ChatGPT or Claude costs a lot of tokens, and
much of that is layout. Markdown keeps headings and tables and drops the rest.

## What it does

- Digital PDFs go through [PyMuPDF4LLM](https://github.com/pymupdf/pymupdf4llm),
  page by page, with running headers and footers removed.
- DOCX, PPTX and XLSX go through [MarkItDown](https://github.com/microsoft/markitdown).
- CSV has its own importer (`app/convert_csv.py`). Bank and Excel exports are
  often Windows-1252 with semicolons, which generic readers turn into one
  column. Encoding and delimiter are detected, empty columns are dropped.
- Scanned PDFs (fewer than half of the pages have text) are OCRed first, either
  locally with [OCRmyPDF](https://ocrmypdf.readthedocs.io/)/Tesseract or with
  Claude, depending on `OCR_ENGINE`. Without either, scans are rejected with a
  clear message.
- After converting a digital PDF the app compares the words and numbers of each
  page's text layer with the Markdown and reports the share that made it
  ("text coverage"), plus the pages below 90 %. That makes a lost table visible
  without reading the whole output.
- Files are processed in memory and discarded. Nothing is stored, the log has
  size, method, page count and duration, no file names and no content.

The page shows a preview and the raw Markdown, with copy and download buttons,
and a few facts about the file (format, size, pages, PDF metadata).

## Running it

With Docker (the build runs the test suite first and fails if a test fails):

```sh
docker build -t markitdown-web .
docker run --rm --network host -e OCR_ENGINE=tesseract markitdown-web
```

The container binds to `127.0.0.1:8090`. Put a reverse proxy or a tunnel in
front of it for HTTPS. `deploy/compose.yaml` is the setup I use on the Pi
(host network, read-only root, no capabilities, memory limit).
`deploy/public/` is the public instance behind a Cloudflare Tunnel, with
an internal Docker network and an iptables script that keeps the tunnel
containers away from the LAN.

`deploy/opencloud/` is not part of markitdown-web: it is the OpenCloud
instance (private file storage) that shares the Pi and the Cloudflare Tunnel.
It lives there because the tunnel container and `isolate.sh` have to know
about its network. The folder holds the compose file, a nightly restic backup
script and its systemd timer. The admin password goes into an `.env` next to
the compose file before the first start.

Without Docker, for development:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
uvicorn app.main:app --port 8090 --reload
```

Local OCR needs `ocrmypdf` and the Tesseract language packs on the system
(`apt install ocrmypdf tesseract-ocr-deu tesseract-ocr-eng` on Debian).

## Settings

All optional, read from the environment at startup. An invalid value stops
the app.

| Variable | Default | Meaning |
|---|---|---|
| `OCR_ENGINE` | `claude` | `claude` (needs `ANTHROPIC_API_KEY`) or `tesseract` |
| `ANTHROPIC_API_KEY` | unset | Without a key and with `OCR_ENGINE=claude`, scans are rejected |
| `MAX_UPLOAD_MB` | 30 | Upload limit, at most 30 |
| `MAX_PDF_PAGES` | 500 | Page limit for digital PDFs |
| `MAX_OCR_PAGES` | 100 | Page limit for scans |
| `OCR_JOBS` | 2 | Parallel Tesseract processes |
| `MAX_PARALLEL` | 2 | Conversions at the same time; more get a 503 with `Retry-After` |
| `MAX_UNZIPPED_MB` | 300 | Cap for the unpacked size of Office files (zip bombs) |
| `COUNTER_FILE` | unset | File that keeps the count of successful conversions across restarts |
| `COFFEES` | 0 | Number shown next to the donation link |

## API

`POST /convert` with a multipart form: `file`, optionally `force_ocr=true`.
Add `?lang=en` for English error messages.

With `Accept: application/x-ndjson` the answer is a stream of JSON lines:
a `start` event with the method and page count, `progress` events per page
(per block of 20 pages with Claude), then `result` or `error`. A blank line
every 15 seconds keeps proxies from closing the connection during OCR.
Without that header you get a single JSON document.

```json
{"markdown": "...", "method": "pymupdf4llm", "pages": 12, "filename": "report.md",
 "info": {"format": "PDF", "size": 184233, "pages": 12, "scanned": false,
          "coverage": 97, "weak_pages": [4]}}
```

`GET /health` returns the OCR engine in use, the upload limit and the counter.

## Security notes

The app expects untrusted input. Office files are checked for zip bombs before
MarkItDown opens them (entry count, central directory size, declared sizes).
PDFs are opened with pypdf first, so broken or encrypted files are rejected
with a message instead of a traceback. OCRmyPDF runs as a separate process with
a timeout and a minimal environment, so a crash or hang there cannot take the
app down and the API key never reaches it. Error messages never quote document
content. The preview runs Markdown through DOMPurify. Responses carry a strict
CSP, HSTS and `no-store` on `/convert`. A POST from another origin is rejected
by `Sec-Fetch-Site`, so nobody can trigger OCR on your account from a foreign
page.

## Things that belong to my instance

`app/static/impressum.html` and `datenschutz.html` are the legal pages German
law requires for my site. `index.html` and `en.html` carry my domain, the
Open Graph image and a PayPal link. If you run your own instance, replace
those. `scripts/make_og_image.py` renders the preview image.

## License

AGPL-3.0, see `LICENSE`. PyMuPDF and PyMuPDF4LLM are AGPL-licensed, which is
why this code is published. Vendored front-end libraries: marked (MIT) and
DOMPurify (Apache-2.0 or MPL-2.0), see `app/static/vendor/`.
