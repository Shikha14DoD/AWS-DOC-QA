"""Ingest Lambda: S3 upload -> chunk -> embed -> DynamoDB.

Triggered by an S3 ObjectCreated event on the documents bucket. For each
uploaded document it:

  1. reads the object from S3
  2. splits the text into overlapping chunks on paragraph boundaries
  3. embeds each chunk with the Gemini gemini-embedding-001 model
  4. writes one item per chunk to the ChunksTable

Gemini access and the SSM-backed API key live in the shared `doc_qa_common`
layer.
"""

import json
import os
import re
import time
import urllib.parse

import boto3

from doc_qa_common import gemini, pdf_extract

TABLE_NAME = os.environ["CHUNKS_TABLE_NAME"]

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "150"))

# Plain-text extensions read as UTF-8 directly; PDF goes through pdf_extract
# first. Same allowlist the upload Lambda validates against.
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".rst")
PDF_EXTENSIONS = (".pdf",)
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS + PDF_EXTENSIONS

_s3 = boto3.client("s3")
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _document_id_from_key(key: str) -> str:
    """Turn an S3 key into a stable, readable document id."""
    stem = re.sub(r"\.[^.]+$", "", key)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or "document"


def chunk_text(text: str) -> list[dict]:
    """Split text into overlapping chunks, preferring paragraph boundaries.

    Returns a list of {"text", "start", "end"} dicts. `start`/`end` are
    character offsets into the original document, kept for citations.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[dict] = []
    buf = ""
    buf_start = 0
    cursor = 0

    for para in paragraphs:
        # Locate this paragraph in the original text so offsets stay honest.
        para_pos = text.find(para, cursor)
        if para_pos == -1:
            para_pos = cursor
        cursor = para_pos + len(para)

        if not buf:
            buf, buf_start = para, para_pos
        elif len(buf) + 2 + len(para) <= CHUNK_SIZE:
            buf = f"{buf}\n\n{para}"
        else:
            chunks.append({"text": buf, "start": buf_start, "end": buf_start + len(buf)})
            # Start the next buffer with an overlap tail of the previous one.
            tail = buf[-CHUNK_OVERLAP:] if CHUNK_OVERLAP else ""
            buf = f"{tail}\n\n{para}" if tail else para
            buf_start = para_pos - len(tail) if tail else para_pos

        # A single oversized paragraph: hard-split it.
        while len(buf) > CHUNK_SIZE:
            chunks.append(
                {"text": buf[:CHUNK_SIZE], "start": buf_start, "end": buf_start + CHUNK_SIZE}
            )
            step = CHUNK_SIZE - CHUNK_OVERLAP
            buf = buf[step:]
            buf_start += step

    if buf.strip():
        chunks.append({"text": buf, "start": buf_start, "end": buf_start + len(buf)})
    return chunks


def _process_object(bucket: str, key: str) -> int:
    started = time.time()
    ext_match = re.search(r"\.[^.]+$", key)
    ext = ext_match.group(0).lower() if ext_match else ""
    if ext not in SUPPORTED_EXTENSIONS:
        log_ingest(key, 0, started, ok=True, skipped=True)
        return 0

    document_id = _document_id_from_key(key)
    try:
        obj = _s3.get_object(Bucket=bucket, Key=key)
        raw = obj["Body"].read()
        extra = {}
        if ext in PDF_EXTENSIONS:
            text = pdf_extract.extract_text(raw)
            total_pages = pdf_extract.page_count(raw)
            extra = {
                "pages_total": total_pages,
                "pages_indexed": min(total_pages, pdf_extract.MAX_PAGES),
            }
        else:
            text = raw.decode("utf-8", errors="replace")
        chunks = chunk_text(text)

        with _table.batch_writer() as batch:
            for i, ch in enumerate(chunks):
                vector = gemini.embed(ch["text"])
                batch.put_item(
                    Item={
                        "document_id": document_id,
                        "chunk_id": f"{i:04d}",
                        "text": ch["text"],
                        "embedding": json.dumps(vector),
                        "source_key": key,
                        "char_start": ch["start"],
                        "char_end": ch["end"],
                    }
                )
    except Exception as exc:
        # let it raise after logging - lambda will retry the async invoke,
        # and it eventually lands in the dlq if it keeps failing
        log_ingest(key, 0, started, ok=False, error=str(exc))
        raise

    log_ingest(key, len(chunks), started, ok=True, **extra)
    return len(chunks)


def log_ingest(key, chunk_count, started, ok, skipped=False, error=None, **extra):
    entry = {
        "event": "ingest",
        "key": key,
        "ok": ok,
        "skipped": skipped,
        "chunks_written": chunk_count,
        "latency_ms": round((time.time() - started) * 1000),
        **extra,
    }
    if error:
        entry["error"] = error
    print(json.dumps(entry))


def handler(event, context):
    total = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        total += _process_object(bucket, key)
    return {"chunks_written": total}
