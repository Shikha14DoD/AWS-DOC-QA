"""tiny retry-with-backoff helper, no deps"""

import random
import time
import urllib.error

# Capped: a server-supplied hint can be minutes (free-tier rate limits have
# said this), and a Lambda has a hard timeout - sleeping past it just burns
# the invocation instead of letting it fail and retry fresh.
MAX_RETRY_DELAY = 20.0


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
    """
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
                delay = min(hint, MAX_RETRY_DELAY) + random.uniform(0, 0.25)
            else:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            time.sleep(delay)
