"""tiny retry-with-backoff helper, no deps"""

import random
import time
import urllib.error


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return True
    return False


def call_with_retry(fn, *args, retries=3, base_delay=0.5, **kwargs):
    """call fn(*args, **kwargs), retrying on transient errors with backoff + jitter"""
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            attempt += 1
            if attempt > retries or not is_transient(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            time.sleep(delay)
