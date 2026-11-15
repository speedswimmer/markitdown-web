(() => {
  "use strict";
  const EXTENSIONS = [".pdf", ".docx", ".pptx", ".xlsx", ".csv"];
  // UI texts; the page's <html lang> picks the language, the server answers in the same one.
  const LANG = document.documentElement.lang === "en" ? "en" : "de";
  const TEXT = {
    de: {
      limits: (mb) => `PDF · DOCX · PPTX · XLSX · CSV · bis ${mb} MB`,
      decimal: ",",
      methods: {
        claude: "Claude (Texterkennung)", tesseract: "Tesseract (Texterkennung)",
        pymupdf4llm: "PyMuPDF4LLM", markitdown: "MarkItDown", csv: "CSV-Import",
      },
      formats: { DOCX: "Word (DOCX)", PPTX: "PowerPoint (PPTX)", XLSX: "Excel (XLSX)", CSV: "CSV" },
      date: (y, m, d) => `${d}.${m}.${y}`,
      facts: {
        format: "Format", size: "Größe", pages: "Seiten", wordPages: "Seiten (laut Word)",
        slides: "Folien", sheets: "Tabellenblätter", rows: "Zeilen", columns: "Spalten", kind: "Art", scan: "Scan (Bild)", text: "Text",
        locked: "Geschützt", lockedValue: "Ja, Bearbeitung eingeschränkt",
        title: "Titel", author: "Autor", created: "Erstellt",
        coverage: "Textabdeckung", weakPages: "Seiten mit Verlust",
        more: (n) => ` und ${n} weitere`,
      },
      oneFile: "Bitte nur eine Datei auf einmal.",
      uploading: (pct) => `Hochladen … ${pct} %`,
      uploadStart: "Hochladen …",
      processing: "Wird verarbeitet …",
      claudeHint: "Scans brauchen etwa 2–4 Sekunden pro Seite.",
      claudeRunning: "Texterkennung mit Claude …",
      tesseractHint: "Scans brauchen etwa 3–5 Sekunden pro Seite.",
      tesseractRunning: "Texterkennung läuft …",
      page: (done, total) => `Seite ${done} von ${total}`,
      ocrPages: (done, total) => `Texterkennung: ${done} von ${total} Seiten`,
      building: "Markdown wird erstellt …",
      converting: "Wird umgewandelt …",
      failed: "Die Umwandlung ist fehlgeschlagen.",
      noFile: "Bitte zuerst eine Datei auswählen.",
      wrongType: "Nur PDF, DOCX, PPTX, XLSX und CSV werden unterstützt.",
      tooLarge: (mb) => `Datei zu groß, höchstens ${mb} MB.`,
      interrupted: "Verbindung abgebrochen, bitte noch einmal versuchen.",
      unexpected: (status) => `Unerwartete Antwort (${status})`,
      network: "Verbindung fehlgeschlagen.",
      convert: "Umwandeln",
      pageCount: (n) => `${n} ${n === 1 ? "Seite" : "Seiten"}`,
      copied: "Kopiert ✓",
      copy: "Kopieren",
      copyFailed: "Kopieren nicht möglich. Bitte in der Markdown-Ansicht manuell markieren.",
      counter: (n, count) => n === 1
        ? ["Bereits ", count, " Dokument umgewandelt"]
        : ["Bereits ", count, " Dokumente umgewandelt"],
      coffees: (n, count) => [" · Bisher ", count, " Kaffee spendiert, danke!"],
    },
    en: {
      limits: (mb) => `PDF · DOCX · PPTX · XLSX · CSV · up to ${mb} MB`,
      decimal: ".",
      methods: {
        claude: "Claude (OCR)", tesseract: "Tesseract (OCR)",
        pymupdf4llm: "PyMuPDF4LLM", markitdown: "MarkItDown", csv: "CSV import",
      },
      formats: { DOCX: "Word (DOCX)", PPTX: "PowerPoint (PPTX)", XLSX: "Excel (XLSX)", CSV: "CSV" },
      date: (y, m, d) => `${y}-${m}-${d}`,
      facts: {
        format: "Format", size: "Size", pages: "Pages", wordPages: "Pages (according to Word)",
        slides: "Slides", sheets: "Sheets", rows: "Rows", columns: "Columns", kind: "Type", scan: "Scan (image)", text: "Text",
        locked: "Protected", lockedValue: "Yes, editing restricted",
        title: "Title", author: "Author", created: "Created",
        coverage: "Text coverage", weakPages: "Pages with loss",
        more: (n) => ` and ${n} more`,
      },
      oneFile: "Please drop only one file at a time.",
      uploading: (pct) => `Uploading … ${pct} %`,
      uploadStart: "Uploading …",
      processing: "Processing …",
      claudeHint: "Scans take about 2–4 seconds per page.",
      claudeRunning: "Text recognition with Claude …",
      tesseractHint: "Scans take about 3–5 seconds per page.",
      tesseractRunning: "Text recognition running …",
      page: (done, total) => `Page ${done} of ${total}`,
      ocrPages: (done, total) => `Text recognition: ${done} of ${total} pages`,
      building: "Creating Markdown …",
      converting: "Converting …",
      failed: "The conversion failed.",
      noFile: "Please choose a file first.",
      wrongType: "Only PDF, DOCX, PPTX, XLSX and CSV are supported.",
      tooLarge: (mb) => `File too large, at most ${mb} MB.`,
      interrupted: "Connection lost, please try again.",
      unexpected: (status) => `Unexpected response (${status})`,
      network: "Connection failed.",
      convert: "Convert",
      pageCount: (n) => `${n} ${n === 1 ? "page" : "pages"}`,
      copied: "Copied ✓",
      copy: "Copy",
      copyFailed: "Copying is not possible. Please select the text in the Markdown view.",
      counter: (n, count) => [count, n === 1 ? " document converted so far" : " documents converted so far"],
      coffees: (n, count) => [" · ", count, n === 1 ? " coffee so far, thank you!" : " coffees so far, thank you!"],
    },
  }[LANG];
  // No OCR until /health confirms it; the server enforces its own size limit anyway.
  let maxUploadMb = 30;
  let ocrAvailable = false;
  const $ = (id) => document.getElementById(id);
  const form = $("form"), fileInput = $("file"), ocr = $("ocr"), submit = $("submit");
  const drop = $("drop"), picked = $("picked"), limits = $("limits");
  const status = $("status"), result = $("result"), meta = $("meta");
  const preview = $("preview"), raw = $("raw"), facts = $("facts");
  const viewPreview = $("view-preview"), viewRaw = $("view-raw");
  const copyBtn = $("copy"), downloadBtn = $("download");
  const ocrOption = $("ocr-option"), noteOcr = $("note-ocr");
  const progress = $("progress"), progressBar = $("progress-bar"), progressLabel = $("progress-label");
  const counter = $("counter"), coffees = $("coffees");
  const pickedDefault = Array.from(picked.childNodes, (node) => node.cloneNode(true));
  let markdown = "", filename = "", uploadName = "";
  let converted = null;  // conversions so far, from /health; null hides the counter

  // Preview links must not navigate this page away or leak the referrer.
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("rel", "noopener noreferrer");
      node.setAttribute("target", "_blank");
    }
  });

  fetch("/health")
    .then((response) => (response.ok ? response.json() : null))
    .then((health) => {
      if (!health || typeof health !== "object") return;
      if (Number.isInteger(health.max_upload_mb) && health.max_upload_mb > 0) {
        maxUploadMb = health.max_upload_mb;
        limits.textContent = TEXT.limits(maxUploadMb);
      }
      if (isCount(health.converted)) {
        converted = health.converted;
        showCounter();
      }
      if (isCount(health.coffees) && health.coffees > 0) showCoffees(health.coffees);
      ocrAvailable = health.ocr === true;
      ocrOption.hidden = !ocrAvailable;
      if (!ocrAvailable) ocr.checked = false;
      // Only Claude sends pages off the server; local OCR (Tesseract) needs no note.
      noteOcr.hidden = health.ocr_engine !== "claude";
    })
    .catch(() => { /* keep defaults; the server enforces its own limits */ });

  // "Bereits 1.234 Dokumente umgewandelt", the number in bold; hidden until there is one.
  function showCounter() {
    counter.hidden = !converted;
    if (!converted) return;
    const number = document.createElement("strong");
    number.textContent = converted.toLocaleString(LANG === "en" ? "en-US" : "de-DE");
    counter.replaceChildren(...TEXT.counter(converted, number));
  }

  // "· Bisher 1 Kaffee spendiert, danke!" after the coffee link, the number in bold.
  function showCoffees(n) {
    const number = document.createElement("strong");
    number.textContent = n.toLocaleString(LANG === "en" ? "en-US" : "de-DE");
    coffees.replaceChildren(...TEXT.coffees(n, number));
    coffees.hidden = false;
  }

  function setStatus(text, isError = false) {
    status.textContent = text;
    status.classList.toggle("error", isError);
  }

  function formatSize(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1).replace(".", TEXT.decimal)} MB`;
  }

  const isCount = (value) => typeof value === "number" && Number.isInteger(value) && value >= 0;
  const isText = (value) => typeof value === "string" && value !== "";

  // "2024-03-15" -> "15.03.2024" (German); split instead of Date to avoid timezone shifts.
  function formatDate(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    return match ? TEXT.date(match[1], match[2], match[3]) : null;
  }

  // Label/value pairs for the facts list; unknown or malformed values are skipped.
  function factRows(info) {
    const rows = [];
    if (!info || typeof info !== "object") return rows;
    const add = (label, value) => { if (value !== null) rows.push([label, value]); };
    const f = TEXT.facts;
    if (info.format === "PDF") {
      add(f.format, isText(info.version) ? `PDF ${info.version}` : "PDF");
    } else if (typeof info.format === "string" && Object.hasOwn(TEXT.formats, info.format)) {
      add(f.format, TEXT.formats[info.format]);
    }
    add(f.size, isCount(info.size) ? formatSize(info.size) : null);
    add(info.format === "DOCX" ? f.wordPages : f.pages, isCount(info.pages) ? String(info.pages) : null);
    add(f.slides, isCount(info.slides) ? String(info.slides) : null);
    add(f.sheets, isCount(info.sheets) ? String(info.sheets) : null);
    add(f.rows, isCount(info.rows) ? String(info.rows) : null);
    add(f.columns, isCount(info.columns) ? String(info.columns) : null);
    if (typeof info.scanned === "boolean") add(f.kind, info.scanned ? f.scan : f.text);
    if (info.encrypted === true) add(f.locked, f.lockedValue);
    add(f.title, isText(info.title) ? info.title : null);
    add(f.author, isText(info.author) ? info.author : null);
    add(f.created, isText(info.created) ? formatDate(info.created) : null);
    if (isCount(info.coverage) && info.coverage <= 100) {
      add(f.coverage, `${info.coverage} %`);
      if (Array.isArray(info.weak_pages) && info.weak_pages.length > 0 && info.weak_pages.every(isCount)) {
        const shown = info.weak_pages.slice(0, 10).join(", ");
        const rest = info.weak_pages.length - 10;
        add(f.weakPages, rest > 0 ? shown + f.more(rest) : shown);
      }
    }
    return rows;
  }

  function showFacts(info) {
    const items = factRows(info).map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "fact";
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      item.append(term, detail);
      return item;
    });
    facts.replaceChildren(...items);
    facts.hidden = items.length === 0;
  }

  function showPicked() {
    const file = fileInput.files[0];
    drop.classList.toggle("picked", Boolean(file));
    if (file) {
      picked.textContent = `${file.name} · ${formatSize(file.size)}`;
    } else {
      picked.replaceChildren(...pickedDefault.map((node) => node.cloneNode(true)));
    }
    setStatus("");
  }

  fileInput.addEventListener("change", showPicked);

  // A file dropped outside the zone would otherwise replace the page.
  for (const type of ["dragover", "drop"]) {
    window.addEventListener(type, (event) => event.preventDefault());
  }
  drop.addEventListener("dragenter", () => drop.classList.add("over"));
  drop.addEventListener("dragover", () => drop.classList.add("over"));
  drop.addEventListener("dragleave", (event) => {
    if (!drop.contains(event.relatedTarget)) drop.classList.remove("over");
  });
  drop.addEventListener("drop", (event) => {
    drop.classList.remove("over");
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || files.length === 0) return;
    if (files.length > 1) {
      setStatus(TEXT.oneFile, true);
      return;
    }
    fileInput.files = files;
    showPicked();
  });

  // fraction null shows a running bar without a percentage.
  function showProgress(label, fraction) {
    progress.hidden = false;
    progressLabel.textContent = label;
    if (fraction === null) {
      progressBar.removeAttribute("value");
    } else {
      progressBar.value = Math.round(Math.min(1, Math.max(0, fraction)) * 100);
    }
  }

  function hideProgress() {
    progress.hidden = true;
    progressBar.value = 0;
    progressLabel.textContent = "";
  }

  // POST /convert as a stream: XHR because fetch cannot report upload progress.
  // Resolves with {stream: true} once all events are handled, or with
  // {stream: false, status, text} for a plain JSON answer (errors before the start).
  function sendConvert(body, onEvent) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let parsed = 0;
      const isStream = () => xhr.status === 200
        && (xhr.getResponseHeader("content-type") || "").startsWith("application/x-ndjson");
      const drain = () => {
        const text = xhr.responseText;
        let end;
        while ((end = text.indexOf("\n", parsed)) !== -1) {
          const line = text.slice(parsed, end).trim();
          parsed = end + 1;
          if (!line) continue;  // heartbeat
          let data = null;
          try { data = JSON.parse(line); } catch { /* skip malformed lines */ }
          if (data && typeof data === "object") onEvent(data);
        }
      };
      xhr.open("POST", LANG === "en" ? "/convert?lang=en" : "/convert");
      xhr.setRequestHeader("Accept", "application/x-ndjson");
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const fraction = event.loaded / event.total;
          showProgress(TEXT.uploading(Math.round(fraction * 100)), fraction);
        }
      });
      xhr.upload.addEventListener("load", () => showProgress(TEXT.processing, null));
      xhr.addEventListener("progress", () => { if (isStream()) drain(); });
      xhr.addEventListener("load", () => {
        if (isStream()) {
          drain();
          resolve({ stream: true });
        } else {
          resolve({ stream: false, status: xhr.status, text: xhr.responseText });
        }
      });
      xhr.addEventListener("error", () => reject(new Error("network")));
      xhr.addEventListener("abort", () => reject(new Error("abort")));
      xhr.send(body);
    });
  }

  function handleEvent(data, state) {
    if (data.type === "start") {
      state.method = data.method;
      if (data.method === "claude") {
        setStatus(TEXT.claudeHint);
        // Claude reports per block of up to 20 pages: a running bar until the first block is done.
        showProgress(TEXT.claudeRunning, null);
      } else if (data.method === "tesseract") {
        setStatus(TEXT.tesseractHint);
        showProgress(TEXT.tesseractRunning, null);
      } else if (isCount(data.pages) && data.pages > 0) {
        showProgress(TEXT.page(0, data.pages), 0);
      } else {
        showProgress(TEXT.converting, null);
      }
    } else if (data.type === "progress" && isCount(data.done) && isCount(data.total) && data.total > 0) {
      const isOcr = state.method === "claude" || state.method === "tesseract";
      if (state.method === "tesseract" && data.done === data.total) {
        // The text layer is done; turning it into Markdown takes a few more seconds.
        showProgress(TEXT.building, null);
      } else {
        const label = isOcr
          ? TEXT.ocrPages(data.done, data.total)
          : TEXT.page(data.done, data.total);
        showProgress(label, data.done / data.total);
      }
    } else if (data.type === "result" && typeof data.markdown === "string") {
      state.result = data;
    } else if (data.type === "error") {
      state.error = isText(data.error) ? data.error : TEXT.failed;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) {
      setStatus(TEXT.noFile, true);
      return;
    }
    const name = file.name.toLowerCase();
    if (!EXTENSIONS.some((ext) => name.endsWith(ext))) {
      setStatus(TEXT.wrongType, true);
      return;
    }
    // Mirrors the server limit; a rejected upload can surface as a network error behind the proxy.
    if (file.size > maxUploadMb * 1024 * 1024) {
      setStatus(TEXT.tooLarge(maxUploadMb), true);
      return;
    }
    uploadName = file.name;
    submit.disabled = true;
    submit.classList.add("busy");
    submit.textContent = TEXT.converting;
    result.hidden = true;
    setStatus("");
    showProgress(TEXT.uploadStart, 0);
    const body = new FormData();
    body.append("file", file);
    body.append("force_ocr", ocr.checked ? "true" : "false");
    const state = { method: null, result: null, error: null };
    try {
      const answer = await sendConvert(body, (data) => handleEvent(data, state));
      if (answer.stream) {
        if (state.error) {
          setStatus(state.error, true);
        } else if (!state.result) {
          setStatus(TEXT.interrupted, true);
        }
      } else {
        let data = null;
        try { data = JSON.parse(answer.text); } catch { /* handled below */ }
        const message = data && isText(data.error) ? data.error : null;
        setStatus(message || TEXT.unexpected(answer.status), true);
      }
    } catch {
      setStatus(TEXT.network, true);
    } finally {
      hideProgress();
      submit.disabled = false;
      submit.classList.remove("busy");
      submit.textContent = TEXT.convert;
    }
    if (state.result && !state.error) {
      show(state.result);
      // The server counts non-empty results; mirror that instead of asking again.
      if (converted !== null && state.result.markdown) {
        converted += 1;
        showCounter();
      }
    }
  });

  function setView(showRaw) {
    raw.hidden = !showRaw;
    preview.hidden = showRaw;
    viewRaw.setAttribute("aria-pressed", String(showRaw));
    viewPreview.setAttribute("aria-pressed", String(!showRaw));
  }

  function show(data) {
    markdown = data.markdown;
    filename = data.filename;
    const parts = [uploadName, (Object.hasOwn(TEXT.methods, data.method) && TEXT.methods[data.method]) || "MarkItDown"];
    if (data.pages) parts.push(TEXT.pageCount(data.pages));
    meta.textContent = parts.join(" · ");
    preview.replaceChildren(
      DOMPurify.sanitize(marked.parse(markdown), { RETURN_DOM_FRAGMENT: true })
    );
    raw.textContent = markdown;
    showFacts(data.info);
    setView(false);
    result.hidden = false;
    setStatus(data.warning || "");
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  viewPreview.addEventListener("click", () => setView(false));
  viewRaw.addEventListener("click", () => setView(true));

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      copyBtn.textContent = TEXT.copied;
      setTimeout(() => { copyBtn.textContent = TEXT.copy; }, 1500);
    } catch {
      setStatus(TEXT.copyFailed, true);
    }
  });

  downloadBtn.addEventListener("click", () => {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = filename;
    link.click();
    // Revoking synchronously aborts the download in WebKit.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
