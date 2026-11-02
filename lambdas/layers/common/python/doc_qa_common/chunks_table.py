"""Shared ChunksTable delete logic - used by the ingest Lambda (one document,
triggered by an S3 ObjectRemoved event) and the reconcile Lambda (any number
of orphaned documents, found on a schedule). One implementation instead of
two that could quietly diverge.
"""

from boto3.dynamodb.conditions import Key


def existing_chunk_keys(table, document_id: str) -> list[dict]:
    """All {document_id, chunk_id} keys currently stored for a document."""
    keys: list[dict] = []
    kwargs = {
        "KeyConditionExpression": Key("document_id").eq(document_id),
        "ProjectionExpression": "document_id, chunk_id",
    }
    while True:
        resp = table.query(**kwargs)
        keys.extend(
            {"document_id": i["document_id"], "chunk_id": i["chunk_id"]}
            for i in resp.get("Items", [])
        )
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return keys


def delete_chunks(table, document_id: str) -> int:
    """Delete every chunk for a document_id. Returns how many were removed.

    Queries for whatever's actually there rather than assuming a chunk
    count, so it's correct even if a prior ingest only partially completed
    (a rate-limited embed call can leave some chunks written and some not).
    """
    keys = existing_chunk_keys(table, document_id)
    with table.batch_writer() as batch:
        for k in keys:
            batch.delete_item(Key=k)
    return len(keys)
