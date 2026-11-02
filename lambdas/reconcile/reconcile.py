"""Reconcile Lambda: catch chunks the ObjectRemoved handler missed.

Runs on a schedule (see infrastructure_stack.py), not on an event. The
ObjectRemoved handler in ingest.py covers the normal delete path, but S3
notifications aren't the only way an object disappears - a lifecycle
expiration rule, for instance, doesn't fire ObjectRemoved the same way a
console/API delete does. If a chunk's document_id has no matching key left
in the bucket, nothing in the normal flow will ever notice.

This Lambda is the safety net: list every key actually in the bucket, scan
every document_id actually in the table, and delete whatever's in the table
but not in the bucket. Same document_id derivation and delete logic as
ingest.py - both pull from doc_qa_common so the two can't drift.
"""

import json
import os
import time

import boto3

from doc_qa_common.chunks_table import delete_chunks as _delete_chunks
from doc_qa_common.document_id import document_id_from_key as _document_id_from_key

BUCKET_NAME = os.environ["DOCUMENTS_BUCKET_NAME"]
TABLE_NAME = os.environ["CHUNKS_TABLE_NAME"]

_s3 = boto3.client("s3")
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _live_document_ids() -> set[str]:
    """document_ids for every object currently in the bucket."""
    ids: set[str] = set()
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME):
        for obj in page.get("Contents", []):
            ids.add(_document_id_from_key(obj["Key"]))
    return ids


def _stored_document_ids() -> set[str]:
    """document_ids for every chunk currently in the table.

    A scan, same tradeoff as the query Lambda's retrieval scan - fine at
    this corpus size, and this only runs on a schedule, not per-request.
    """
    ids: set[str] = set()
    kwargs = {"ProjectionExpression": "document_id"}
    while True:
        resp = _table.scan(**kwargs)
        ids.update(item["document_id"] for item in resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return ids


def handler(event, context):
    started = time.time()
    live = _live_document_ids()
    stored = _stored_document_ids()
    orphans = sorted(stored - live)

    deleted_chunks = 0
    for document_id in orphans:
        deleted_chunks += _delete_chunks(_table, document_id)

    result = {
        "documents_in_bucket": len(live),
        "documents_in_table": len(stored),
        "orphan_documents_deleted": len(orphans),
        "chunks_deleted": deleted_chunks,
        "latency_ms": round((time.time() - started) * 1000),
    }
    print(json.dumps(result))
    return result
