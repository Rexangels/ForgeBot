"""Small, server-side client for the Meta Model API's OpenAI-compatible endpoint."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class MetaModelError(RuntimeError):
    """A safe-to-display model configuration or request error."""


def _request_json(path: str, payload: dict | None = None) -> dict:
    api_key = settings.MODEL_API_KEY
    if not api_key:
        raise MetaModelError(
            "Meta Model API is not configured. Set META_API or "
            "FORGEBOT_MODEL_API_KEY in backend/.env."
        )
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{settings.MODEL_API_BASE_URL}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Never surface the raw provider response: it may echo request data.
        if exc.code == 401:
            reason = "Meta rejected the API key. Check that backend/.env contains a valid Model API key."
        elif exc.code == 429:
            reason = "Meta Model API rate limit reached. Try again later."
        elif exc.code >= 500:
            reason = "Meta Model API is temporarily unavailable. Try again later."
        else:
            reason = f"Meta Model API rejected the request (HTTP {exc.code})."
        raise MetaModelError(reason) from None
    except (URLError, TimeoutError, OSError):
        raise MetaModelError(
            "Could not reach Meta Model API. Check the network and try again."
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MetaModelError("Meta Model API returned an unreadable response.") from None


def generate_structured_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: dict,
) -> dict:
    """Request schema-constrained JSON and parse the provider response."""
    response = _request_json(
        "/chat/completions",
        {
            "model": settings.MODEL_API_NAME,
            "messages": [
                {"role": "developer", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                },
            },
        },
    )
    try:
        content = response["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise MetaModelError(
            "Meta returned a response that could not be read as the requested design."
        ) from None
    if not isinstance(result, dict):
        raise MetaModelError("Meta returned a design with the wrong top-level shape.")
    return result


def generate_image_structured_json(
    *,
    system_prompt: str,
    user_prompt: str,
    image_data_url: str,
    schema_name: str,
    schema: dict,
) -> dict:
    """Send a user image and request a schema-constrained model observation."""
    response = _request_json(
        "/chat/completions",
        {
            "model": settings.MODEL_API_NAME,
            "messages": [
                {"role": "developer", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema},
            },
        },
    )
    try:
        result = json.loads(response["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise MetaModelError("Meta returned a response that could not be read as the requested analysis.") from None
    if not isinstance(result, dict):
        raise MetaModelError("Meta returned an analysis with the wrong top-level shape.")
    return result


def check_connection() -> None:
    """Make a minimal completion request without printing its content or key."""
    response = _request_json(
        "/chat/completions",
        {
            "model": settings.MODEL_API_NAME,
            "messages": [
                {"role": "user", "content": "Reply with the single word: ready"}
            ],
        },
    )
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise MetaModelError("Meta returned an unexpected connectivity-check response.") from None
    if not isinstance(content, str) or not content.strip():
        raise MetaModelError("Meta returned an empty connectivity-check response.")
