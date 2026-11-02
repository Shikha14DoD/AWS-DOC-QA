import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "layers" / "common" / "python"))

from doc_qa_common.retry import MAX_RETRY_DELAY, call_with_retry, is_transient  # noqa: E402


def test_is_transient_on_429_and_5xx():
    assert is_transient(urllib.error.HTTPError("u", 429, "rate limited", {}, None))
    assert is_transient(urllib.error.HTTPError("u", 503, "down", {}, None))
    assert not is_transient(urllib.error.HTTPError("u", 400, "bad request", {}, None))


def test_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("u", 503, "down", {}, None)
        return "ok"

    result = call_with_retry(flaky, retries=3, base_delay=0)
    assert result == "ok"
    assert calls["n"] == 3


def test_gives_up_on_non_transient_error():
    def bad():
        raise urllib.error.HTTPError("u", 400, "bad request", {}, None)

    try:
        call_with_retry(bad, retries=3, base_delay=0)
        assert False, "should have raised"
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_honors_a_server_supplied_retry_delay(monkeypatch):
    # gemini.py attaches retry_delay to the exception it raises - stand that
    # in directly rather than round-tripping through urllib here.
    sleeps = []
    monkeypatch.setattr("doc_qa_common.retry.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("doc_qa_common.retry.random.uniform", lambda a, b: 0)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            exc = urllib.error.HTTPError("u", 429, "rate limited", {}, None)
            exc.retry_delay = 7.0
            raise exc
        return "ok"

    assert call_with_retry(flaky, retries=3, base_delay=0) == "ok"
    assert sleeps == [7.0]


def test_caps_a_retry_delay_that_exceeds_the_max(monkeypatch):
    sleeps = []
    monkeypatch.setattr("doc_qa_common.retry.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("doc_qa_common.retry.random.uniform", lambda a, b: 0)

    def flaky():
        exc = urllib.error.HTTPError("u", 429, "rate limited", {}, None)
        exc.retry_delay = MAX_RETRY_DELAY * 10
        raise exc

    try:
        call_with_retry(flaky, retries=1, base_delay=0)
    except urllib.error.HTTPError:
        pass
    assert sleeps == [MAX_RETRY_DELAY]
