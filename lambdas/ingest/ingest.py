"""Ingest Lambda: S3 upload -> chunk -> embed -> DynamoDB.

Triggered by S3 ObjectCreated and ObjectRemoved events on the documents
bucket (both wired to the same function - see infrastructure_stack.py).

On ObjectCreated:
  1. reads the object from S3
  2. splits the text into overlapping chunks on paragraph boundaries
  3. embeds each chunk with the Gemini gemini-embedding-001 model
  4. writes one item per chunk to the ChunksTable

On ObjectRemoved: deletes every chunk for that document_id from the
ChunksTable. Without this, removing a file from S3 left its chunks fully
searchable and citable forever - the one place the system could keep citing
a document that no longer exists. Found this gap, not told about it; see
INTERVIEW_NOTES.md.

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
from doc_qa_common.chunks_table import delete_chunks as _delete_chunks
from doc_qa_common.document_id import document_id_from_key as _document_id_from_key

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

        # One batchEmbedContents call (or a few, past gemini.MAX_BATCH_SIZE)
        # instead of one embedContents call per chunk - fewer HTTP round
        # trips, and fewer independent shots at a free-tier rate limit.
        vectors = gemini.embed_batch([ch["text"] for ch in chunks])

        with _table.batch_writer() as batch:
            for i, (ch, vector) in enumerate(zip(chunks, vectors)):
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


def _delete_document(key: str) -> int:
    """Remove every chunk for the document an S3 key maps to.

    Delegates the actual query-then-delete to doc_qa_common.chunks_table,
    shared with the reconcile Lambda - see that module's docstring.
    """
    started = time.time()
    document_id = _document_id_from_key(key)
    try:
        deleted = _delete_chunks(_table, document_id)
    except Exception as exc:
        log_delete(key, 0, started, ok=False, error=str(exc))
        raise

    log_delete(key, deleted, started, ok=True)
    return deleted


def log_ingest(key, chunk_count, started, ok, skipped=False, error=None, **extra):
    _log("ingest", key, started, ok, chunks_written=chunk_count, skipped=skipped,
         error=error, **extra)


def log_delete(key, chunk_count, started, ok, error=None):
    _log("delete", key, started, ok, chunks_deleted=chunk_count, error=error)


def _log(event_name, key, started, ok, error=None, **fields):
    entry = {
        "event": event_name,
        "key": key,
        "ok": ok,
        "latency_ms": round((time.time() - started) * 1000),
        **fields,
    }
    if error:
        entry["error"] = error
    print(json.dumps(entry))


def handler(event, context):
    written = deleted = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        event_name = record.get("eventName", "")
        if event_name.startswith("ObjectRemoved"):
            deleted += _delete_document(key)
        else:
            written += _process_object(bucket, key)
    return {"chunks_written": written, "chunks_deleted": deleted}
