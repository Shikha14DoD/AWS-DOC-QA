"""Documents Lambda: GET /documents -> what's currently in the corpus.

No LLM call, no shared layer needed - just a cheap DynamoDB scan (projected
to two attributes) so the demo page can show visitors what's actually been
ingested before they ask anything, instead of them guessing.
"""

import json
import os

import boto3

TABLE_NAME = os.environ["CHUNKS_TABLE_NAME"]

_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def list_documents() -> list[dict]:
    items: list[dict] = []
    kwargs = {"ProjectionExpression": "document_id, source_key"}
    while True:
        resp = _table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    docs: dict[str, dict] = {}
    for it in items:
        doc_id = it["document_id"]
        entry = docs.setdefault(doc_id, {
            "document_id": doc_id,
            "source_key": it.get("source_key", doc_id),
            "chunk_count": 0,
        })
        entry["chunk_count"] += 1

    return sorted(docs.values(), key=lambda d: d["source_key"])


def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"documents": list_documents()}),
    }
