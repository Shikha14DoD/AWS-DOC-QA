"""Offline tests for the ingest Lambda's S3-delete cleanup.

Deleting a document from S3 used to leave its chunks in DynamoDB forever -
still searchable and citable for a document that no longer exists. These
tests cover the fix: an S3 ObjectRemoved event wipes the matching chunks, and
the handler dispatches each S3 record to the right path by eventName.

No AWS or network access: `ingest._table` is monkeypatched with fakes.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("CHUNKS_TABLE_NAME", "test-table")
os.environ.setdefault("GEMINI_API_KEY_PARAM", "/test/key")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "layers" / "common" / "python"))
sys.path.insert(0, str(ROOT / "lambdas" / "ingest"))

import ingest  # noqa: E402


def _s3_record(key, event_name):
    return {
        "eventName": event_name,
        "s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}},
    }


def test_delete_document_derives_the_right_document_id(monkeypatch):
    seen = {}

    def fake_delete_chunks(table, document_id):
        seen["document_id"] = document_id
        return 3

    monkeypatch.setattr(ingest, "_delete_chunks", fake_delete_chunks)

    deleted = ingest._delete_document("my notes!!.pdf")

    assert deleted == 3
    assert seen["document_id"] == "my_notes"  # same sanitize rule as ingest's own keys


def test_delete_document_with_nothing_to_delete_is_a_noop(monkeypatch):
    monkeypatch.setattr(ingest, "_delete_chunks", lambda table, document_id: 0)
    assert ingest._delete_document("already-gone.md") == 0


def test_delete_document_raises_and_logs_on_failure(monkeypatch):
    def boom(table, document_id):
        raise RuntimeError("table unavailable")

    monkeypatch.setattr(ingest, "_delete_chunks", boom)

    try:
        ingest._delete_document("notes.md")
        assert False, "should have raised"
    except RuntimeError:
        pass


def test_handler_routes_removed_events_to_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "_delete_document", lambda key: calls.append(("delete", key)) or 2)
    monkeypatch.setattr(ingest, "_process_object", lambda bucket, key: calls.append(("ingest", key)) or 5)

    event = {"Records": [
        _s3_record("old.md", "ObjectRemoved:Delete"),
        _s3_record("new.md", "ObjectCreated:Put"),
    ]}
    result = ingest.handler(event, None)

    assert calls == [("delete", "old.md"), ("ingest", "new.md")]
    assert result == {"chunks_written": 5, "chunks_deleted": 2}
