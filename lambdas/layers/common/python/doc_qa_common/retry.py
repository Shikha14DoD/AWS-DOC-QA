"""tiny retry-with-backoff helper, no deps"""

import os
import random
import time
import urllib.error

# Default ceiling on how long we'll wait because a server asked us to. Sized
# for ingest (5 min timeout). API-facing Lambdas set MAX_RETRY_DELAY much
# lower: the query Lambda has 30 s total, and a 20 s sleep there ran it into
# its timeout before it could fall back to Groq (live 503s, 2026-11-02).
MAX_RETRY_DELAY = 20.0


def max_retry_delay() -> float:
    return float(os.environ.get("MAX_RETRY_DELAY", MAX_RETRY_DELAY))


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return True
    return False


def retry_delay_hint(exc: Exception) -> float | None:
    """Seconds the server asked us to wait, if it said so.

    gemini.py sets `retry_delay` on an HTTPError it raises, parsed from
    either the Retry-After header or the RetryInfo detail Gemini's 429
    body carries - either way, the server's own number beats a guess.
    """
    return getattr(exc, "retry_delay", None)


def call_with_retry(fn, *args, retries=3, base_delay=0.5, **kwargs):
    """call fn(*args, **kwargs), retrying on transient errors with backoff + jitter.

    Honors a server-supplied retry hint (Retry-After / RetryInfo) when one is
    attached to the exception; falls back to exponential backoff otherwise.
    If the server wants a longer wait than this Lambda can afford, re-raise
    immediately instead of sleeping: retrying sooner than asked just gets
    another 429, and failing fast lets the caller's fallback (Groq, or the
    async retry + DLQ) take over while there's still time.
    """
    max_delay = max_retry_delay()
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            attempt += 1
            if attempt > retries or not is_transient(exc):
                raise
            hint = retry_delay_hint(exc)
            if hint is not None:
                if hint > max_delay:
                    raise
                delay = hint + random.uniform(0, 0.25)
            else:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            time.sleep(delay)
