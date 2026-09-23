"""PDF text extraction - the one third-party dependency in this layer.

Everything else here is stdlib on purpose (see gemini.py/groq.py), but there
is no reasonable stdlib way to parse a PDF. pypdf is pure Python (no C
extension to cross-compile for Lambda's runtime), so it's vendored straight
into this layer's python/ directory rather than needing a Docker-bundled
build step.
"""

import io
import os

from pypdf import PdfReader

# A runaway PDF shouldn't burn unbounded Lambda time or embedding quota, so
# only the first N pages are indexed. This used to be a silent hard-coded 50,
# which quietly dropped pages 51-62 of a real 62-page upload - now it's
# configurable and callers can ask how many pages there were (page_count) to
# tell the uploader what got left out.
MAX_PAGES = int(os.environ.get("PDF_MAX_PAGES", "50"))


def page_count(data: bytes) -> int:
    """Total pages in the PDF (not capped)."""
    return len(PdfReader(io.BytesIO(data)).pages)


def extract_text(data: bytes) -> str:
    """Return the concatenated text of the first MAX_PAGES pages."""
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:MAX_PAGES]
    return "\n\n".join(page.extract_text() or "" for page in pages).strip()
