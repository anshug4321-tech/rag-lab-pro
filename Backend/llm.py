"""Gemini wrapper: retry with backoff + jitter, timeouts, token and cost tracking, and model fallback.
Uses the official `google-genai` SDK (package: google-genai, import: from google import genai)."""
import random
import time
from typing import Iterator

from backend import config
from backend.utils import get_logger

log = get_logger("llm")
_client = None
_WORKING: dict[str, str] = {}  # remembers which model name worked, so we do not retry dead names


class LLMError(Exception):
    """Friendly error that is safe to show in the UI. `code` is the HTTP status when known."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def get_client():
    """Create the Gemini client once. Key comes only from the environment."""
    global _client
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is missing. Add it in Render > Environment and redeploy.")
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
    log.error("Gemini error: code=%s type=%s", code, type(exc).__name__)
    if code in (401, 403):
        return LLMError("The API key was rejected. Check GEMINI_API_KEY.", code)
    if code == 404:
        return LLMError("The AI model name is not available any more. Set CHAT_MODEL in Render > Environment.", 404)
    if code == 429:
        return LLMError("Free-tier rate limit reached. Wait a minute and try again.", 429)
    return LLMError("The AI service had a problem. Please try again.", code)


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


def _chain(model: str | None) -> list[str]:
    """Model names to try, best first: the one that worked before, the requested one, then fallbacks."""
    want = model or config.CHAT_MODEL
    return list(dict.fromkeys([_WORKING.get(want, want), want, *config.MODEL_FALLBACKS]))


def generate(prompt: str, system: str | None = None, model: str | None = None,
             json_mode: bool = False) -> tuple[str, int, int]:
    """One-shot call. Returns (text, tokens_in, tokens_out). Tries fallback models on 404."""
    client, last = get_client(), None
    for name in _chain(model):
        try:
            res = _call_with_retry(lambda n=name: client.models.generate_content(
                model=n, contents=prompt, config=_cfg(system, json_mode)))
        except LLMError as exc:
            if exc.code == 404:
                log.info("Model %s not found, trying the next one", name)
                last = exc
                continue
            raise
        _WORKING[model or config.CHAT_MODEL] = name
        u = res.usage_metadata
        return (res.text or ""), int(getattr(u, "prompt_token_count", 0) or 0), int(getattr(u, "candidates_token_count", 0) or 0)
    raise last or LLMError("No AI model is available.")


def stream(prompt: str, usage: dict, system: str | None = None, model: str | None = None) -> Iterator[str]:
    """Yield text pieces. Fills `usage` with tokens_in/tokens_out when finished.
    Errors can appear on the first piece, so we handle retries and model fallback around the loop."""
    from google.genai import errors
    client, last = get_client(), None
    for name in _chain(model):
        for attempt in range(3):
            started = False
            try:
                for part in client.models.generate_content_stream(model=name, contents=prompt, config=_cfg(system, False)):
                    started = True
                    if part.text:
                        yield part.text
                    u = part.usage_metadata
                    if u:
                        usage["tokens_in"] = int(getattr(u, "prompt_token_count", 0) or 0)
                        usage["tokens_out"] = int(getattr(u, "candidates_token_count", 0) or 0)
                _WORKING[model or config.CHAT_MODEL] = name
                return
            except (errors.APIError, TimeoutError) as exc:
                code = getattr(exc, "code", None)
                if not started and code == 404:
                    log.info("Model %s not found, trying the next one", name)
                    last = _friendly(exc)
                    break
                if not started and _is_retryable(exc) and attempt < 2:
                    time.sleep(2 ** (attempt + 1) + random.random())
                    continue
                raise _friendly(exc) from exc
    raise last or LLMError("No AI model is available.")
