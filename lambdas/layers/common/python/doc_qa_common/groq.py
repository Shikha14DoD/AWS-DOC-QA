"""groq client, for fallback when gemini is down. openai-compatible chat api."""

import json
import os
import urllib.error
import urllib.request

import boto3

from .retry import call_with_retry

_URL = "https://api.groq.com/openai/v1/chat/completions"

_ssm = boto3.client("ssm")
_api_key_cache: dict[str, str] = {}


def get_api_key(param_name: str | None = None) -> str:
    param_name = param_name or os.environ["GROQ_API_KEY_PARAM"]
    if param_name not in _api_key_cache:
        resp = _ssm.get_parameter(Name=param_name, WithDecryption=True)
        _api_key_cache[param_name] = resp["Parameter"]["Value"]
    return _api_key_cache[param_name]


def _do_post(payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        _URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {get_api_key()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        print(f"groq http error {exc.code}: {detail}")
        raise


def generate(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: int = 30,
) -> str:
    model = model or os.environ.get("GROQ_CHAT_MODEL", "llama-3.1-8b-instant")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {"model": model, "messages": messages, "temperature": temperature}
    body = call_with_retry(_do_post, payload, timeout)
    return body["choices"][0]["message"]["content"]
