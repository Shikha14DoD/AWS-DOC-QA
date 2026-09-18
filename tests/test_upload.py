"""Offline tests for the upload Lambda's validation logic.

No AWS or network: S3 and the classifier are stubbed before calling into the
handler.
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DOCUMENTS_BUCKET_NAME", "test-bucket")
os.environ.setdefault("GEMINI_API_KEY_PARAM", "/test/key")
os.environ.setdefault("MAX_UPLOAD_BYTES", "100")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "layers" / "common" / "python"))
sys.path.insert(0, str(ROOT / "lambdas" / "upload"))

import upload  # noqa: E402
from doc_qa_common import gemini, pdf_extract  # noqa: E402
import base64  # noqa: E402


def _put(monkeypatch):
    calls = []
    monkeypatch.setattr(upload._s3, "put_object", lambda **kw: calls.append(kw))
    return calls


def test_sanitize_filename_strips_path_and_odd_chars():
    assert upload._sanitize_filename("../../etc/passwd.md") == "passwd.md"
    assert upload._sanitize_filename("my notes!!.md") == "my_notes_.md"


def test_rejects_missing_fields():
    resp = upload.handler({"body": json.dumps({"filename": "a.md"})}, None)
    assert resp["statusCode"] == 400


def test_rejects_bad_extension(monkeypatch):
    resp = upload.handler(
        {"body": json.dumps({"filename": "a.exe", "content": "hi"})}, None
    )
    assert resp["statusCode"] == 400


def test_rejects_oversized_content(monkeypatch):
    big = "x" * 200  # over the 100-byte test cap
    resp = upload.handler(
        {"body": json.dumps({"filename": "a.md", "content": big})}, None
    )
    assert resp["statusCode"] == 413


def test_rejects_off_topic_content(monkeypatch):
    monkeypatch.setattr(gemini, "generate", lambda prompt, **kw: "NO")
    resp = upload.handler(
        {"body": json.dumps({"filename": "recipe.md", "content": "how to bake bread"})}, None
    )
    assert resp["statusCode"] == 422


def test_accepts_aws_content(monkeypatch):
    calls = _put(monkeypatch)
    monkeypatch.setattr(gemini, "generate", lambda prompt, **kw: "YES")
    resp = upload.handler(
        {"body": json.dumps({"filename": "notes.md", "content": "AWS Lambda facts"})}, None
    )
    assert resp["statusCode"] == 202
    assert len(calls) == 1
    assert calls[0]["Key"] == "notes.md"


def test_pdf_requires_content_base64():
    resp = upload.handler({"body": json.dumps({"filename": "a.pdf"})}, None)
    assert resp["statusCode"] == 400


def test_pdf_rejects_invalid_base64():
    resp = upload.handler(
        {"body": json.dumps({"filename": "a.pdf", "content_base64": "not-base64!!"})}, None
    )
    assert resp["statusCode"] == 400


def test_pdf_rejects_when_no_text_extracted(monkeypatch):
    monkeypatch.setattr(pdf_extract, "extract_text", lambda data: "")
    content_b64 = base64.b64encode(b"%PDF-fake-bytes").decode()
    resp = upload.handler(
        {"body": json.dumps({"filename": "scan.pdf", "content_base64": content_b64})}, None
    )
    assert resp["statusCode"] == 422


def test_pdf_accepts_when_aws_related(monkeypatch):
    calls = _put(monkeypatch)
    monkeypatch.setattr(pdf_extract, "extract_text", lambda data: "AWS EC2 instance facts")
    monkeypatch.setattr(gemini, "generate", lambda prompt, **kw: "YES")
    content_b64 = base64.b64encode(b"%PDF-fake-bytes").decode()
    resp = upload.handler(
        {"body": json.dumps({"filename": "ec2.pdf", "content_base64": content_b64})}, None
    )
    assert resp["statusCode"] == 202
    assert calls[0]["Key"] == "ec2.pdf"
    assert calls[0]["Body"] == b"%PDF-fake-bytes"
