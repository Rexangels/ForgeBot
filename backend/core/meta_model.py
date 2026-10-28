"""Small, server-side client for the Meta Model API's OpenAI-compatible endpoint."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class MetaModelError(RuntimeError):
    """A safe-to-display model configuration or request error."""


def _model_timeout_s() -> float:
    try:
        return max(5.0, float(getattr(settings, "FORGEBOT_MODEL_TIMEOUT_S", 120)))
    except (TypeError, ValueError):
        return 120.0


def _model_max_attempts() -> int:
    try:
        return max(1, int(getattr(settings, "FORGEBOT_MODEL_MAX_ATTEMPTS", 3)))
    except (TypeError, ValueError):
        return 3


def _request_json_once(path: str, payload: dict | None = None):
    """One HTTP attempt. Returns parsed JSON or raises a retryable/fatal error."""
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
        with urlopen(request, timeout=_model_timeout_s()) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Never surface the raw provider response: it may echo request data.
        if exc.code == 401:
            raise MetaModelError("Meta rejected the API key. Check that backend/.env contains a valid Model API key.") from None
        if exc.code == 429:
            raise _RetryableModelError("Meta Model API rate limit reached. Try again later.") from None
        if exc.code >= 500:
            raise _RetryableModelError("Meta Model API is temporarily unavailable. Try again later.") from None
        raise MetaModelError(f"Meta Model API rejected the request (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise _RetryableModelError("Could not reach Meta Model API. Check the network and try again.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MetaModelError("Meta Model API returned an unreadable response.") from None


class _RetryableModelError(MetaModelError):
    """A transient provider or network failure worth one more attempt."""


def _request_json(path: str, payload: dict | None = None) -> dict:
    """Call the provider with bounded retries and exponential backoff.

    Retries only transient failures (rate limits, 5xx, network errors) up to
    FORGEBOT_MODEL_MAX_ATTEMPTS with a 1s/2s/4s backoff. Auth errors, other
    4xx responses, and unreadable bodies fail immediately.
    """
    import time

    attempts = _model_max_attempts()
    last_error: MetaModelError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _request_json_once(path, payload)
        except _RetryableModelError as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2.0 ** (attempt - 1))
    assert last_error is not None
    raise last_error


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


def generate_multi_image_structured_json(
    *,
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[str, str]],
    schema_name: str,
    schema: dict,
) -> dict:
    """Send several labeled images in one request and get schema-constrained JSON.

    `images` holds (label, data-url) pairs; each image is preceded by a short
    text part naming its viewpoint so the model can tell views apart.
    """
    content: list[dict] = [{"type": "text", "text": user_prompt}]
    for label, data_url in images:
        content.append({"type": "text", "text": f"Reference view ({label}):"})
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    response = _request_json(
        "/chat/completions",
        {
            "model": settings.MODEL_API_NAME,
            "messages": [
                {"role": "developer", "content": system_prompt},
                {"role": "user", "content": content},
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
