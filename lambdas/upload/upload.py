"""Upload Lambda: POST /upload -> validate -> write to the documents bucket.

Invoked by API Gateway (HTTP API, payload format 2.0) with a JSON body:
  - text files:  {"filename": "notes.md", "content": "..."}
  - PDF files:   {"filename": "notes.pdf", "content_base64": "..."}
(PDF is binary, JSON isn't, so it travels as base64 - a browser can't read a
PDF's bytes as text the way it can a .md file.)

This is the one public-write surface in the whole project (the query API only
reads), so it validates harder than anything else here:
  1. filename extension must be one the ingest Lambda actually processes
  2. content size is capped well below what a demo doc needs (text and PDF
     get different caps - PDF overhead means 20 KB is nothing for a real PDF)
  3. content must plausibly be about AWS, checked with a cheap LLM
     classification call (Gemini, falling back to Groq) - a keyword filter
     is easy to fool and easy to have false negatives; a small classification
     prompt is a better fit for "is this topically about AWS" than a regex.
     For a PDF, the text is extracted first (same pdf_extract helper the
     ingest Lambda uses) purely to have something to classify - the
     original PDF bytes, not the extracted text, are what gets stored.

On acceptance it writes straight to the documents bucket, which the existing
S3 -> ingest Lambda trigger picks up exactly like a CLI-uploaded document -
no separate ingestion path to maintain.
"""

import base64
import json
import os
import re

import boto3

from doc_qa_common import gemini, groq, pdf_extract

BUCKET_NAME = os.environ["DOCUMENTS_BUCKET_NAME"]
MAX_TEXT_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "20000"))  # 20 KB
MAX_PDF_BYTES = int(os.environ.get("MAX_UPLOAD_PDF_BYTES", "2000000"))  # 2 MB
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".rst")
PDF_EXTENSIONS = (".pdf",)

_s3 = boto3.client("s3")

CLASSIFY_PROMPT = (
    "You are a strict content filter for a demo that only answers questions "
    "about Amazon Web Services (AWS). Read the document below and decide "
    "whether it is substantially about AWS - its services (e.g. Lambda, S3, "
    "DynamoDB, EC2, API Gateway...), cloud architecture on AWS, or AWS "
    "documentation/quotas/pricing. Reply with exactly one word, YES or NO, "
    "and nothing else.\n\nDocument:\n{content}"
)


def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name)  # strip any path components
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    return name.strip("_") or "document.md"


def _is_about_aws(text: str) -> bool:
    prompt = CLASSIFY_PROMPT.format(content=text[:4000])
    try:
        verdict = gemini.generate(prompt, temperature=0.0, timeout=15)
    except Exception as exc:  # noqa: BLE001 - fall back like the query path does
        print(f"gemini classify failed, falling back to groq: {type(exc).__name__}: {exc}")
        verdict = groq.generate(prompt, temperature=0.0, timeout=15)
    return verdict.strip().upper().startswith("YES")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    try:
        payload = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return _response(400, {"error": "body must be valid JSON"})

    filename = (payload.get("filename") or "").strip()
    ext_match = re.search(r"\.[^.]+$", filename)
    ext = ext_match.group(0).lower() if ext_match else ""

    if not filename:
        return _response(400, {"error": "missing 'filename'"})
    if ext not in TEXT_EXTENSIONS + PDF_EXTENSIONS:
        return _response(
            400, {"error": f"filename must end in one of {TEXT_EXTENSIONS + PDF_EXTENSIONS}"}
        )

    truncation_note = ""
    if ext in PDF_EXTENSIONS:
        content_b64 = payload.get("content_base64") or ""
        if not content_b64.strip():
            return _response(400, {"error": "missing 'content_base64' for a PDF upload"})
        try:
            raw_bytes = base64.b64decode(content_b64, validate=True)
        except (ValueError, base64.binascii.Error):
            return _response(400, {"error": "content_base64 is not valid base64"})
        if len(raw_bytes) > MAX_PDF_BYTES:
            return _response(
                413, {"error": f"PDF too large - max {MAX_PDF_BYTES} bytes for this demo"}
            )
        try:
            text_for_classify = pdf_extract.extract_text(raw_bytes)
        except Exception as exc:  # noqa: BLE001 - not a valid/parseable PDF
            print(f"pdf extract failed: {type(exc).__name__}: {exc}")
            return _response(400, {"error": "couldn't read that as a PDF"})
        if not text_for_classify.strip():
            return _response(
                422, {"error": "couldn't find any text in that PDF - scanned/image-only "
                               "PDFs aren't supported, there's no OCR step here"}
            )
        # only the first MAX_PAGES get indexed - say so instead of silently
        # dropping the rest (this used to be invisible)
        try:
            total_pages = pdf_extract.page_count(raw_bytes)
        except Exception:  # noqa: BLE001 - the note is best-effort, never blocks an upload
            total_pages = 0
        if total_pages > pdf_extract.MAX_PAGES:
            truncation_note = (
                f" Note: only the first {pdf_extract.MAX_PAGES} of {total_pages} "
                "pages will be indexed."
            )
    else:
        content = payload.get("content") or ""
        if not content.strip():
            return _response(400, {"error": "missing 'content'"})
        raw_bytes = content.encode("utf-8")
        if len(raw_bytes) > MAX_TEXT_BYTES:
            return _response(
                413, {"error": f"content too large - max {MAX_TEXT_BYTES} bytes for this demo"}
            )
        text_for_classify = content

    try:
        about_aws = _is_about_aws(text_for_classify)
    except Exception as exc:  # noqa: BLE001 - classifier itself is down
        print(f"upload classify failed: {type(exc).__name__}: {exc}")
        return _response(502, {"error": "couldn't verify the document topic, try again"})

    if not about_aws:
        return _response(
            422,
            {"error": "this demo only accepts documents about AWS - "
                      "the uploaded content doesn't look AWS-related"},
        )

    key = _sanitize_filename(filename)
    _s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=raw_bytes)

    return _response(202, {
        "message": "accepted - it's being chunked, embedded, and indexed now."
                   + truncation_note,
        "key": key,
    })
