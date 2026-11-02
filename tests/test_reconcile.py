"""Offline tests for the reconcile Lambda.

No AWS access: `reconcile._s3` and `reconcile._table` are monkeypatched
with fakes, same approach as test_ingest_delete.py.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("CHUNKS_TABLE_NAME", "test-table")
os.environ.setdefault("DOCUMENTS_BUCKET_NAME", "test-bucket")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "layers" / "common" / "python"))
sys.path.insert(0, str(ROOT / "lambdas" / "reconcile"))

import reconcile  # noqa: E402


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeS3:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self._pages)


class _FakeTable:
    """Fake DynamoDB table. query() doesn't inspect the KeyConditionExpression
    (extracting the document_id back out of a boto3 ConditionBase isn't worth
    it here) - instead each query() call pops the next queued response, which
    works because delete_chunks makes exactly one query per orphan document_id,
    in the sorted order the caller queues responses in.
    """

    def __init__(self, scan_pages, query_responses):
        self._scan_pages = scan_pages
        self._query_responses = list(query_responses)
        self.deleted = []

    def scan(self, **kwargs):
        return self._scan_pages.pop(0)

    def query(self, **kwargs):
        return {"Items": self._query_responses.pop(0)}

    def batch_writer(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def delete_item(self, Key):
        self.deleted.append(Key)


def test_handler_deletes_only_orphaned_documents(monkeypatch):
    # bucket still has doc-a.md; doc-b.md was removed without an S3 event
    # ever reaching the ingest Lambda (e.g. a lifecycle expiration).
    monkeypatch.setattr(
        reconcile, "_s3",
        _FakeS3([{"Contents": [{"Key": "doc-a.md"}]}]),
    )
    table = _FakeTable(
        scan_pages=[{"Items": [
            {"document_id": "doc-a"},
            {"document_id": "doc-b"},
        ]}],
        query_responses=[
            [
                {"document_id": "doc-b", "chunk_id": "0000"},
                {"document_id": "doc-b", "chunk_id": "0001"},
            ],
        ],
    )
    monkeypatch.setattr(reconcile, "_table", table)

    result = reconcile.handler({}, None)

    assert result["documents_in_bucket"] == 1
    assert result["documents_in_table"] == 2
    assert result["orphan_documents_deleted"] == 1
    assert result["chunks_deleted"] == 2
    assert table.deleted == [
        {"document_id": "doc-b", "chunk_id": "0000"},
        {"document_id": "doc-b", "chunk_id": "0001"},
    ]


def test_handler_is_a_noop_when_bucket_and_table_agree(monkeypatch):
    monkeypatch.setattr(
        reconcile, "_s3",
        _FakeS3([{"Contents": [{"Key": "doc-a.md"}]}]),
    )
    table = _FakeTable(
        scan_pages=[{"Items": [{"document_id": "doc-a"}]}],
        query_responses=[],
    )
    monkeypatch.setattr(reconcile, "_table", table)

    result = reconcile.handler({}, None)

    assert result["orphan_documents_deleted"] == 0
    assert result["chunks_deleted"] == 0
    assert table.deleted == []


def test_live_document_ids_paginates_across_pages(monkeypatch):
    monkeypatch.setattr(
        reconcile, "_s3",
        _FakeS3([
            {"Contents": [{"Key": "a.md"}]},
            {"Contents": [{"Key": "b.pdf"}]},
        ]),
    )

    assert reconcile._live_document_ids() == {"a", "b"}


def test_stored_document_ids_paginates_on_last_evaluated_key(monkeypatch):
    table = _FakeTable(
        scan_pages=[
            {"Items": [{"document_id": "a"}], "LastEvaluatedKey": {"document_id": "a"}},
            {"Items": [{"document_id": "b"}]},
        ],
        query_responses=[],
    )
    monkeypatch.setattr(reconcile, "_table", table)

    assert reconcile._stored_document_ids() == {"a", "b"}
