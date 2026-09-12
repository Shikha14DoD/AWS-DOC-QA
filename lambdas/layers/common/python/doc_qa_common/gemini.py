"""Minimal Gemini client - embeddings and text generation over raw HTTPS.

No SDK on purpose: the handlers stay a plain zip with no Lambda layer build
step for third-party packages, and cold starts stay low. Only the standard
library is used here.

The API key is read once per cold start from SSM Parameter Store (SecureString)
and cached for the life of the execution environment.
"""

import json
import os
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post(model: str, method: str, payload: dict, timeout: int) -> dict:
    # a couple retries with backoff - gemini free tier rate limits (429) a lot
    return call_with_retry(_do_post, model, method, payload, timeout)


def embed(text: str, model: str | None = None, timeout: int = 20) -> list[float]:
    """Return the embedding vector for a single string."""
    model = model or os.environ.get("EMBED_MODEL", "text-embedding-004")
    body = _post(
        model,
        "embedContent",
        {"model": f"models/{model}", "content": {"parts": [{"text": text}]}},
        timeout,
    )
    return body["embedding"]["values"]


def generate(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: int = 30,
) -> str:
    """Return generated text for a prompt."""
    model = model or os.environ.get("CHAT_MODEL", "gemini-2.0-flash")
    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    body = _post(model, "generateContent", payload, timeout)
    return body["candidates"][0]["content"]["parts"][0]["text"]
