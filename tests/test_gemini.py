"""Offline tests for doc_qa_common.gemini - the retry-delay parsing (the
server telling us how long to back off on a 429) and batch embedding
(embed_batch split into groups of MAX_BATCH_SIZE). No network access:
gemini._post is monkeypatched for embed_batch; _retry_delay_from_response is
exercised directly against fake HTTPError-like objects for the parsing.
"""

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "layers" / "common" / "python"))

from doc_qa_common import gemini  # noqa: E402


def test_retry_delay_from_retry_after_header():
    exc = urllib.error.HTTPError("u", 429, "rate limited", {"Retry-After": "12"}, None)
    assert gemini._retry_delay_from_response(exc, body="{}") == 12.0


def test_retry_delay_from_retry_info_body():
    exc = urllib.error.HTTPError("u", 429, "rate limited", {}, None)
    body = (
        '{"error": {"code": 429, "details": [{"@type": '
        '"type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13.5s"}]}}'
    )
    assert gemini._retry_delay_from_response(exc, body) == 13.5


def test_retry_delay_is_none_when_neither_is_present():
    exc = urllib.error.HTTPError("u", 429, "rate limited", {}, None)
    assert gemini._retry_delay_from_response(exc, body='{"error": {"code": 429}}') is None


def test_embed_batch_preserves_order_within_one_call(monkeypatch):
    calls = []

    def fake_post(model, method, payload, timeout):
        calls.append((method, len(payload["requests"])))
        return {"embeddings": [{"values": [float(i)]} for i in range(len(payload["requests"]))]}

    monkeypatch.setattr(gemini, "_post", fake_post)

    vectors = gemini.embed_batch(["a", "b", "c"])

    assert vectors == [[0.0], [1.0], [2.0]]
    assert calls == [("batchEmbedContents", 3)]


def test_embed_batch_splits_into_groups_past_the_api_cap(monkeypatch):
    monkeypatch.setattr(gemini, "MAX_BATCH_SIZE", 2)
    calls = []

    def fake_post(model, method, payload, timeout):
        n = len(payload["requests"])
        calls.append(n)
        return {"embeddings": [{"values": [1.0]} for _ in range(n)]}

    monkeypatch.setattr(gemini, "_post", fake_post)

    vectors = gemini.embed_batch(["a", "b", "c", "d", "e"])

    assert calls == [2, 2, 1]
    assert len(vectors) == 5


def test_embed_batch_on_empty_input_makes_no_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini, "_post", lambda *a, **kw: calls.append(1))

    assert gemini.embed_batch([]) == []
    assert calls == []
