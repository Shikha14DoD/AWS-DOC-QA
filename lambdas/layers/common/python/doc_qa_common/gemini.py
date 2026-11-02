"""Minimal Gemini client - embeddings and text generation over raw HTTPS.

No SDK on purpose: the handlers stay a plain zip with no Lambda layer build
step for third-party packages, and cold starts stay low. Only the standard
library is used here.

The API key is read once per cold start from SSM Parameter Store (SecureString)
and cached for the life of the execution environment.
"""

import json
import os
import re
import urllib.error
import urllib.request

import boto3

from .retry import call_with_retry

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_ssm = boto3.client("ssm")
_api_key_cache: dict[str, str] = {}


def get_api_key(param_name: str | None = None) -> str:
    param_name = param_name or os.environ["GEMINI_API_KEY_PARAM"]
    if param_name not in _api_key_cache:
        resp = _ssm.get_parameter(Name=param_name, WithDecryption=True)
        _api_key_cache[param_name] = resp["Parameter"]["Value"]
    return _api_key_cache[param_name]


def _do_post(model: str, method: str, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{_BASE}/{model}:{method}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": get_api_key(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # log the body google sent back (no key in it) - "404 Not Found" alone
        # isn't enough to tell a bad model name from a disabled API
        body = exc.read().decode("utf-8", errors="replace")
        print(f"gemini http error {exc.code} calling {model}:{method}: {body[:500]}")
        exc.retry_delay = _retry_delay_from_response(exc, body)
        raise


def _retry_delay_from_response(exc: urllib.error.HTTPError, body: str) -> float | None:
    """Pull a server-suggested retry delay off a 429, if it gave one.

    Checked in order: the Retry-After header (seconds), then Gemini's own
    RetryInfo error detail (a string like "13s" in the JSON body) - whichever
    is present first. Neither is guaranteed; retry.py falls back to its own
    backoff when this returns None.
    """
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
    if match:
        return float(match.group(1))
    return None


def _post(model: str, method: str, payload: dict, timeout: int) -> dict:
    # a couple retries with backoff - gemini free tier rate limits (429) a lot
    return call_with_retry(_do_post, model, method, payload, timeout)


def embed(text: str, model: str | None = None, timeout: int = 20) -> list[float]:
    """Return the embedding vector for a single string."""
    model = model or os.environ.get("EMBED_MODEL", "gemini-embedding-001")
    body = _post(
        model,
        "embedContent",
        {"model": f"models/{model}", "content": {"parts": [{"text": text}]}},
        timeout,
    )
    return body["embedding"]["values"]


# Gemini's own cap on batchEmbedContents - not a tuning knob, it's the most
# the API accepts in one call.
MAX_BATCH_SIZE = 100


def embed_batch(
    texts: list[str], model: str | None = None, timeout: int = 60
) -> list[list[float]]:
    """Embed many strings in as few HTTP round trips as the API allows.

    One chunk at a time (the original approach) meant one Gemini call - and
    one shot at hitting the free-tier rate limit - per chunk of a document,
    even though they're all going to the same model in the same request
    anyway. batchEmbedContents takes up to MAX_BATCH_SIZE at once; texts
    beyond that are split into further batches, each still one HTTP call.
    Order of the returned vectors matches the order of `texts`.
    """
    model = model or os.environ.get("EMBED_MODEL", "gemini-embedding-001")
    vectors: list[list[float]] = []
    for i in range(0, len(texts), MAX_BATCH_SIZE):
        group = texts[i : i + MAX_BATCH_SIZE]
        body = _post(
            model,
            "batchEmbedContents",
            {
                "requests": [
                    {
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": t}]},
                    }
                    for t in group
                ]
            },
            timeout,
        )
        vectors.extend(e["values"] for e in body["embeddings"])
    return vectors


def generate(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: int = 30,
) -> str:
    """Return generated text for a prompt."""
    model = model or os.environ.get("CHAT_MODEL", "gemini-3.6-flash")
    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    body = _post(model, "generateContent", payload, timeout)
    return body["candidates"][0]["content"]["parts"][0]["text"]
