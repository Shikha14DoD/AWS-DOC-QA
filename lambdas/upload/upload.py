"""Upload Lambda: POST /upload -> validate -> write to the documents bucket.

Invoked by API Gateway (HTTP API, payload format 2.0) with a JSON body:
{"filename": "notes.md", "content": "..."}.

This is the one public-write surface in the whole project (the query API only
reads), so it validates harder than anything else here:
  1. filename extension must be one the ingest Lambda actually processes
  2. content size is capped well below what a demo doc needs
  3. content must plausibly be about AWS, checked with a cheap LLM
     classification call (Gemini, falling back to Groq) - a keyword filter
     is easy to fool and easy to have false negatives; a small classification
     prompt is a better fit for "is this topically about AWS" than a regex.

On acceptance it writes straight to the documents bucket, which the existing
S3 -> ingest Lambda trigger picks up exactly like a CLI-uploaded document -
no separate ingestion path to maintain.
"""

import json
import os
import re

import boto3

from doc_qa_common import gemini, groq

BUCKET_NAME = os.environ["DOCUMENTS_BUCKET_NAME"]
MAX_CONTENT_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "20000"))  # 20 KB
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".rst")

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


def _is_about_aws(content: str) -> bool:
    prompt = CLASSIFY_PROMPT.format(content=content[:4000])
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
    content = payload.get("content") or ""

    if not filename or not content.strip():
        return _response(400, {"error": "missing 'filename' or 'content'"})

    ext = re.search(r"\.[^.]+$", filename)
    if not ext or ext.group(0).lower() not in TEXT_EXTENSIONS:
        return _response(400, {"error": f"filename must end in one of {TEXT_EXTENSIONS}"})

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        return _response(
            413, {"error": f"content too large - max {MAX_CONTENT_BYTES} bytes for this demo"}
        )

    try:
        about_aws = _is_about_aws(content)
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
    _s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=content_bytes)

    return _response(202, {
        "message": "accepted - it's being chunked, embedded, and indexed now",
        "key": key,
    })
