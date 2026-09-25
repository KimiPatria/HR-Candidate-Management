"""Plain-text extraction from uploaded documents."""

import io
import logging

log = logging.getLogger(__name__)

# .csv is here for Google Sheets: Drive exports a native spreadsheet as CSV and nothing
# else that this pipeline can read, and a requirements matrix kept in a sheet is a real
# thing HR hands over.
SUPPORTED = {".txt", ".md", ".csv", ".pdf", ".docx"}
MAX_BYTES = 20 * 1024 * 1024


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return _pdf(data)
    if name.endswith(".docx"):
        return _docx(data)
    if name.endswith((".txt", ".md", ".csv")):
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type: {filename}. Supported: {sorted(SUPPORTED)}")


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text.strip():
        raise ValueError(
            "No selectable text found in this PDF. It is probably a scan - "
            "run OCR first or paste the text instead."
        )
    return text


def _docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)
