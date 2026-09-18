"""PDF text extraction - the one third-party dependency in this layer.

Everything else here is stdlib on purpose (see gemini.py/groq.py), but there
is no reasonable stdlib way to parse a PDF. pypdf is pure Python (no C
extension to cross-compile for Lambda's runtime), so it's vendored straight
into this layer's python/ directory rather than needing a Docker-bundled
build step.
"""

import io

from pypdf import PdfReader

MAX_PAGES = 50  # a runaway PDF shouldn't burn unbounded Lambda time/memory


def extract_text(data: bytes) -> str:
    """Return the concatenated text of every page in a PDF's raw bytes."""
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:MAX_PAGES]
    return "\n\n".join(page.extract_text() or "" for page in pages).strip()
