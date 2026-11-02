"""Offline tests for doc_qa_common.chunks_table and .document_id - the
query-then-delete logic shared by the ingest Lambda (one document, on an S3
event) and the reconcile Lambda (any number, on a schedule). Fully offline:
a fake table object stands in for DynamoDB.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "layers" / "common" / "python"))

from doc_qa_common import chunks_table  # noqa: E402
from doc_qa_common.document_id import document_id_from_key  # noqa: E402


class _FakeBatchWriter:
    def __init__(self):
        self.deleted = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def delete_item(self, Key):
        self.deleted.append(Key)


def test_document_id_from_key_strips_extension_and_sanitizes():
    assert document_id_from_key("lambda-limits.md") == "lambda-limits"
    assert document_id_from_key("my notes!!.pdf") == "my_notes"
    assert document_id_from_key("nested/path/doc.txt") == "nested_path_doc"


def test_document_id_from_key_never_returns_empty():
    # dots survive sanitization (they're an allowed character), so a run of
    # them doesn't hit the empty-stem fallback - something with no allowed
    # characters at all does.
    assert document_id_from_key("!!!.md") == "document"


def test_existing_chunk_keys_paginates_and_projects():
    pages = [
        {
            "Items": [{"document_id": "doc", "chunk_id": "0000", "text": "ignored"}],
            "LastEvaluatedKey": {"document_id": "doc", "chunk_id": "0000"},
        },
        {"Items": [{"document_id": "doc", "chunk_id": "0001", "text": "ignored"}]},
    ]
    table = MagicMock()
    table.query.side_effect = pages

    keys = chunks_table.existing_chunk_keys(table, "doc")

    assert keys == [
        {"document_id": "doc", "chunk_id": "0000"},
        {"document_id": "doc", "chunk_id": "0001"},
    ]
    assert table.query.call_count == 2
    assert "ExclusiveStartKey" in table.query.call_args_list[1].kwargs


def test_delete_chunks_removes_everything_found():
    table = MagicMock()
    table.query.return_value = {"Items": [
        {"document_id": "doc", "chunk_id": f"{i:04d}"} for i in range(3)
    ]}
    writer = _FakeBatchWriter()
    table.batch_writer.return_value = writer

    deleted = chunks_table.delete_chunks(table, "doc")

    assert deleted == 3
    assert writer.deleted == [
        {"document_id": "doc", "chunk_id": "0000"},
        {"document_id": "doc", "chunk_id": "0001"},
        {"document_id": "doc", "chunk_id": "0002"},
    ]


def test_delete_chunks_is_a_noop_for_a_document_with_nothing_stored():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    writer = _FakeBatchWriter()
    table.batch_writer.return_value = writer

    assert chunks_table.delete_chunks(table, "never-existed") == 0
    assert writer.deleted == []
