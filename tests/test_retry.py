import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "layers" / "common" / "python"))

from doc_qa_common.retry import call_with_retry, is_transient  # noqa: E402


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
