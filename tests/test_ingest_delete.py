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
from unittest.mock import MagicMock

os.environ.setdefault("CHUNKS_TABLE_NAME", "test-table")
os.environ.setdefault("GEMINI_API_KEY_PARAM", "/test/key")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "layers" / "common" / "python"))
sys.path.insert(0, str(ROOT / "lambdas" / "ingest"))

import ingest  # noqa: E402


class _FakeBatchWriter:
    """Mimics boto3's `table.batch_writer()` context manager just enough to
    record what gets deleted, without a real DynamoDB table behind it."""

    def __init__(self):
        self.deleted = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def delete_item(self, Key):
        self.deleted.append(Key)


def _s3_record(key, event_name):
    return {
        "eventName": event_name,
        "s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}},
    }


def test_existing_chunk_keys_paginates(monkeypatch):
    pages = [
        {
            "Items": [{"document_id": "doc", "chunk_id": "0000"}],
            "LastEvaluatedKey": {"document_id": "doc", "chunk_id": "0000"},
        },
        {"Items": [{"document_id": "doc", "chunk_id": "0001"}]},
    ]
    query = MagicMock(side_effect=pages)
    monkeypatch.setattr(ingest._table, "query", query)

    keys = ingest._existing_chunk_keys("doc")

    assert keys == [
        {"document_id": "doc", "chunk_id": "0000"},
        {"document_id": "doc", "chunk_id": "0001"},
    ]
    assert query.call_count == 2
    assert "ExclusiveStartKey" in query.call_args_list[1].kwargs


def test_delete_document_removes_every_chunk(monkeypatch):
    monkeypatch.setattr(
        ingest, "_existing_chunk_keys",
        lambda doc_id: [{"document_id": doc_id, "chunk_id": f"{i:04d}"} for i in range(3)],
    )
    writer = _FakeBatchWriter()
    monkeypatch.setattr(ingest._table, "batch_writer", lambda: writer)

    deleted = ingest._delete_document("notes.md")

    assert deleted == 3
    assert writer.deleted == [
        {"document_id": "notes", "chunk_id": "0000"},
        {"document_id": "notes", "chunk_id": "0001"},
        {"document_id": "notes", "chunk_id": "0002"},
    ]


def test_delete_document_with_nothing_to_delete_is_a_noop(monkeypatch):
    monkeypatch.setattr(ingest, "_existing_chunk_keys", lambda doc_id: [])
    writer = _FakeBatchWriter()
    monkeypatch.setattr(ingest._table, "batch_writer", lambda: writer)

    assert ingest._delete_document("already-gone.md") == 0
    assert writer.deleted == []


def test_delete_document_raises_and_logs_on_failure(monkeypatch):
    def boom(doc_id):
        raise RuntimeError("table unavailable")

    monkeypatch.setattr(ingest, "_existing_chunk_keys", boom)

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
