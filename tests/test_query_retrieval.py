"""Offline tests for the query Lambda's retrieval + response shaping.

No AWS or network: we stub `_load_chunks` (the DynamoDB scan) and
`gemini.embed` / `gemini.generate` before calling into the handler.
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("CHUNKS_TABLE_NAME", "test-table")
os.environ.setdefault("GEMINI_API_KEY_PARAM", "/test/key")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "layers" / "common" / "python"))
sys.path.insert(0, str(ROOT / "lambdas" / "query"))

import query as handler  # noqa: E402
from doc_qa_common import gemini, groq  # noqa: E402


def _chunk(doc, cid, vec, text="passage"):
    return {
        "document_id": doc,
        "chunk_id": cid,
        "text": text,
        "source_key": f"{doc}.md",
        "char_start": 0,
        "char_end": len(text),
        "_vec": vec,
    }


CHUNKS = [
    _chunk("a", "0000", [1.0, 0.0, 0.0], "about lambda timeout"),
    _chunk("a", "0001", [0.0, 1.0, 0.0], "about s3 buckets"),
    _chunk("b", "0000", [0.0, 0.0, 1.0], "about dynamodb keys"),
]


def _setup(monkeypatch, q_vec):
    monkeypatch.setattr(handler, "_load_chunks", lambda force=False: CHUNKS)
    monkeypatch.setattr(gemini, "embed", lambda text, **kw: q_vec)


def test_cosine_ranks_the_aligned_vector_first():
    assert handler._cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert handler._cosine([1, 0, 0], [0, 1, 0]) == 0.0


def test_retrieve_returns_top_k_in_score_order(monkeypatch):
    _setup(monkeypatch, [0.9, 0.1, 0.0])
    hits = handler.retrieve("lambda timeout?", top_k=2)
    assert [h["chunk_id"] for h in hits] == ["0000", "0001"]
    assert hits[0]["score"] > hits[1]["score"]
    assert "_vec" not in hits[0]


def test_answer_builds_numbered_citations(monkeypatch):
    _setup(monkeypatch, [1.0, 0.0, 0.0])
    monkeypatch.setattr(gemini, "generate", lambda prompt, **kw: "It is 900 seconds [1].")
    out = handler.answer("max lambda timeout?", top_k=1)
    assert out["answer"] == "It is 900 seconds [1]."
    assert len(out["citations"]) == 1
    assert out["citations"][0]["marker"] == 1
    assert out["citations"][0]["document_id"] == "a"


def test_handler_rejects_missing_question(monkeypatch):
    resp = handler.handler({"body": json.dumps({"top_k": 3})}, None)
    assert resp["statusCode"] == 400


def test_falls_back_to_groq_when_gemini_fails(monkeypatch):
    _setup(monkeypatch, [1.0, 0.0, 0.0])

    def _boom(*a, **kw):
        raise RuntimeError("gemini is down")

    monkeypatch.setattr(gemini, "generate", _boom)
    monkeypatch.setattr(groq, "generate", lambda prompt, **kw: "groq says hi [1]")

    out = handler.answer("timeout?", top_k=1)
    assert out["answer"] == "groq says hi [1]"
    assert out["provider"] == "groq"


def test_handler_happy_path(monkeypatch):
    _setup(monkeypatch, [1.0, 0.0, 0.0])
    monkeypatch.setattr(gemini, "generate", lambda prompt, **kw: "answer [1]")
    resp = handler.handler({"body": json.dumps({"question": "timeout?"})}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["answer"] == "answer [1]"
    assert body["citations"]
