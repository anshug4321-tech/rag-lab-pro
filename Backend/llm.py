"""Gemini wrapper: retry with backoff + jitter, timeouts, token and cost tracking.
Uses the official `google-genai` SDK (package: google-genai, import: from google import genai)."""
import random
import time
from typing import Iterator

from backend import config
from backend.utils import get_logger

log = get_logger("llm")
_client = None


class LLMError(Exception):
    """Friendly error that is safe to show in the UI."""


def get_client():
    """Create the Gemini client once. Key comes only from .env."""
    global _client
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is missing. Add it to your .env file and restart.")
    if _client is None:
        from google import genai
        from google.genai import types
        _client = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=config.LLM_TIMEOUT_MS))
    return _client


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """Estimated USD cost. Prices in config are approximate: verify them."""
    return tokens_in / 1e6 * config.PRICE_IN_PER_M + tokens_out / 1e6 * config.PRICE_OUT_PER_M


def _is_retryable(exc: Exception) -> bool:
    return getattr(exc, "code", None) in (429, 500, 503, 504) or isinstance(exc, TimeoutError)


def _friendly(exc: Exception) -> LLMError:
    code = getattr(exc, "code", None)
    if code in (401, 403):
        return LLMError("The API key was rejected. Check GEMINI_API_KEY in .env.")
    if code == 429:
        return LLMError("Free-tier rate limit reached. Wait a minute and try again.")
    return LLMError("The AI service had a problem. Please try again.")


def _call_with_retry(fn):
    """Run fn(); retry rate-limit/server errors with exponential backoff and jitter."""
    from google.genai import errors
    for attempt in range(config.LLM_RETRIES + 1):
        try:
            return fn()
        except (errors.APIError, TimeoutError) as exc:
            if attempt == config.LLM_RETRIES or not _is_retryable(exc):
                raise _friendly(exc) from exc
            wait = min(30, 2 ** attempt) + random.random()
            log.info("LLM retry %d in %.1fs", attempt + 1, wait)
            time.sleep(wait)


def _cfg(system: str | None, json_mode: bool):
    from google.genai import types
    kw = {"system_instruction": system} if system else {}
    if json_mode:
        kw["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(temperature=0.2, **kw)


def generate(prompt: str, system: str | None = None, model: str | None = None,
             json_mode: bool = False) -> tuple[str, int, int]:
    """One-shot call. Returns (text, tokens_in, tokens_out)."""
    client = get_client()
    res = _call_with_retry(lambda: client.models.generate_content(
        model=model or config.CHAT_MODEL, contents=prompt, config=_cfg(system, json_mode)))
    u = res.usage_metadata
    return (res.text or ""), int(getattr(u, "prompt_token_count", 0) or 0), int(getattr(u, "candidates_token_count", 0) or 0)


def stream(prompt: str, usage: dict, system: str | None = None, model: str | None = None) -> Iterator[str]:
    """Yield text pieces. Fills `usage` with tokens_in/tokens_out when finished.
    Retries only before the first piece arrives."""
    client = get_client()
    gen = _call_with_retry(lambda: client.models.generate_content_stream(
        model=model or config.CHAT_MODEL, contents=prompt, config=_cfg(system, False)))
    from google.genai import errors
    try:
        for part in gen:
            if part.text:
                yield part.text
            u = part.usage_metadata
            if u:
                usage["tokens_in"] = int(getattr(u, "prompt_token_count", 0) or 0)
                usage["tokens_out"] = int(getattr(u, "candidates_token_count", 0) or 0)
    except (errors.APIError, TimeoutError) as exc:
        raise _friendly(exc) from exc
