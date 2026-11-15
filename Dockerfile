FROM python:3.13-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp
WORKDIR /srv
# Local OCR (OCR_ENGINE=tesseract): Debian's OCRmyPDF runs on the system Python,
# separate from the app's packages, with Tesseract and German/English language data.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ocrmypdf tesseract-ocr-deu tesseract-ocr-eng \
  && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app

# `docker build --target test .` runs the test suite against the real dependencies.
FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
COPY tests ./tests
# The marker file lets the runtime stage depend on green tests.
RUN pip install -r requirements-dev.txt && python -m pytest -q && touch /srv/.tests-passed

# Last stage, so a plain `docker build` produces the runtime image.
# Copying the marker from the test stage makes every runtime build run the tests first
# (outside /tmp, which is a tmpfs at runtime).
FROM base AS runtime
COPY --from=test /srv/.tests-passed /srv/.tests-passed
# /data holds the conversion counter (a volume; new volumes copy this owner).
RUN useradd --system --uid 10001 --no-create-home web && install -d -o web -g web /data
USER web
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8090", "--no-server-header"]
