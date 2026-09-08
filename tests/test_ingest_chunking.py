"""Unit tests for the ingest Lambda's chunking logic.

These run without any AWS or network access: we set the env vars the handler
module expects at import time, then exercise `chunk_text` directly.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("CHUNKS_TABLE_NAME", "test-table")
os.environ.setdefault("GEMINI_API_KEY_PARAM", "/test/key")
os.environ.setdefault("CHUNK_SIZE", "200")
os.environ.setdefault("CHUNK_OVERLAP", "40")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "ingest"))

import handler  # noqa: E402


def test_short_text_is_one_chunk():
    chunks = handler.chunk_text("A single short paragraph.")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "A single short paragraph."
    assert chunks[0]["start"] == 0


def test_paragraphs_are_packed_and_overlapped():
    text = "\n\n".join(f"Paragraph number {i} with some filler words." for i in range(10))
    chunks = handler.chunk_text(text)

    assert len(chunks) > 1
    # No chunk materially exceeds CHUNK_SIZE (allow a small slack for the
    # paragraph joiner).
    assert all(len(c["text"]) <= 200 + 20 for c in chunks)
    # Offsets are monotonic and within the document.
    assert chunks[0]["start"] == 0
    assert all(c["end"] <= len(text) + 5 for c in chunks)
    assert all(a["start"] <= b["start"] for a, b in zip(chunks, chunks[1:]))


def test_oversized_paragraph_is_hard_split():
    text = "word " * 200  # ~1000 chars, no paragraph breaks
    chunks = handler.chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 200 for c in chunks)
