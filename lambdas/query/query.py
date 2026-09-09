"""Query Lambda: question -> retrieve chunks -> grounded answer + citations.

Invoked by API Gateway (HTTP API, payload format 2.0) on `POST /query` with a
JSON body: {"question": "...", "top_k": 5}.

Retrieval has no vector index: we read every chunk from DynamoDB, rank by
cosine similarity against the question embedding in memory, and pass the top
matches to the LLM as context. The full chunk set is cached in the execution
environment for CHUNK_CACHE_TTL seconds so warm invocations skip the scan.
"""

import json
import math
import os
import time

import boto3

from doc_qa_common import gemini

TABLE_NAME = os.environ["CHUNKS_TABLE_NAME"]
DEFAULT_TOP_K = int(os.environ.get("TOP_K", "5"))
CHUNK_CACHE_TTL = int(os.environ.get("CHUNK_CACHE_TTL", "300"))

_table = boto3.resource("dynamodb").Table(TABLE_NAME)

# (loaded_at, chunks) cached across warm invocations.
_cache: tuple[float, list[dict]] | None = None

SYSTEM_PROMPT = (
    "You are a precise assistant. Answer the question using ONLY the numbered "
    "context passages provided. If the answer is not in the context, say you "
    "don't know. Cite the passages you used by their number in square brackets, "
    "e.g. [1]. Keep the answer concise."
)


def _load_chunks(force: bool = False) -> list[dict]:
    global _cache
    now = time.time()
    if not force and _cache and now - _cache[0] < CHUNK_CACHE_TTL:
        return _cache[1]

    items: list[dict] = []
    kwargs = {
        "ProjectionExpression": "document_id, chunk_id, #t, embedding, source_key, char_start, char_end",
        "ExpressionAttributeNames": {"#t": "text"},
    }
    while True:
        resp = _table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    for it in items:
        it["_vec"] = json.loads(it["embedding"])
    _cache = (now, items)
    return items


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve(question: str, top_k: int) -> list[dict]:
    q_vec = gemini.embed(question)
    scored = [
        {**{k: v for k, v in c.items() if k != "_vec"}, "score": _cosine(q_vec, c["_vec"])}
        for c in _load_chunks()
    ]
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_k]


def _build_prompt(question: str, hits: list[dict]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(f"[{i}] (source: {h['source_key']})\n{h['text']}")
    context = "\n\n".join(blocks)
    return f"Context passages:\n\n{context}\n\nQuestion: {question}"


def answer(question: str, top_k: int) -> dict:
    hits = retrieve(question, top_k)
    if not hits:
        return {"answer": "No documents have been ingested yet.", "citations": []}

    text = gemini.generate(_build_prompt(question, hits), system=SYSTEM_PROMPT)
    citations = [
        {
            "marker": i,
            "document_id": h["document_id"],
            "chunk_id": h["chunk_id"],
            "source_key": h["source_key"],
            "char_start": int(h["char_start"]),
            "char_end": int(h["char_end"]),
            "score": round(float(h["score"]), 4),
            "snippet": h["text"][:240],
        }
        for i, h in enumerate(hits, 1)
    ]
    return {"answer": text, "citations": citations}


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    try:
        raw = event.get("body") or "{}"
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return _response(400, {"error": "body must be valid JSON"})

    question = (payload.get("question") or "").strip()
    if not question:
        return _response(400, {"error": "missing 'question'"})

    top_k = int(payload.get("top_k") or DEFAULT_TOP_K)
    top_k = max(1, min(top_k, 20))

    try:
        result = answer(question, top_k)
    except Exception as exc:  # noqa: BLE001 - surface a clean error, log the detail
        print(f"query failed: {type(exc).__name__}: {exc}")
        return _response(502, {"error": "upstream failure answering the question"})

    return _response(200, result)
