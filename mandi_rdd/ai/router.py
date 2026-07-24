"""
MandiIQ — Multi-Provider LLM Router with Circuit Breaker (FREE-FIRST).

Design:
- Provider selection is automatic and free-first:
    1. GEMINI_API_KEY set → Google Gemini direct (FREE tier, no middleman)
    2. OPENROUTER_API_KEY set → OpenRouter (free-tier models)
    3. Neither set → structured-data fallback (no narrative, no cost)
- Ranked list of free models for the active provider (from models.yaml)
- Tries models in order; on 429 (rate limit) or 5xx, marks model "cooling down"
  for N minutes and falls through to the next in chain
- If ALL models exhausted, returns structured data fallback (no narrative)
- Logs which model served each call
- Health check: pings each model once before nightly narrative generation

This keeps the app 100% free and open source — Gemini's free tier needs no
paid subscription and no OpenRouter middleman.
"""

import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import openai SDK (used for BOTH Gemini OpenAI-compatible + OpenRouter)
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("openai SDK not installed. Install with: pip install openai")

# ── Provider endpoints ──
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ── Circuit breaker state ──
_cool_down: dict[str, float] = {}  # model_id -> timestamp until which it's cooling down
_lock = threading.Lock()

# ── Global timeout & fallback counter ──
GLOBAL_TIMEOUT_SECONDS = 60     # hard ceiling for the entire call_llm() chain
_fallback_count: int = 0
_fallback_lock = threading.Lock()


def get_llm_fallback_count() -> int:
    """Return how many times call_llm() exhausted all models."""
    with _fallback_lock:
        return _fallback_count


def reset_llm_fallback_count():
    """Reset the fallback counter to 0."""
    global _fallback_count
    with _fallback_lock:
        _fallback_count = 0


def timeout_config() -> dict:
    """Return current timeout configuration for observability."""
    return {
        "global_timeout_seconds": GLOBAL_TIMEOUT_SECONDS,
        "cool_down_models": list(_cool_down.keys()),
        "fallback_count": get_llm_fallback_count(),
    }


def _detect_provider() -> tuple[Optional[str], Optional[str]]:
    """
    Detect which LLM provider to use. Free-first ordering.

    Returns (provider, api_key):
        ("gemini", key)     — Google Gemini direct (free tier)
        ("openrouter", key) — OpenRouter (free-tier models)
        (None, None)       — no provider configured
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        return "gemini", gemini_key
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if or_key:
        return "openrouter", or_key
    return None, None


def _base_url_for(provider: Optional[str]) -> str:
    if provider == "nvidia":
        return NVIDIA_BASE_URL
    if provider == "gemini":
        return GEMINI_BASE_URL
    return OPENROUTER_BASE_URL


def _default_headers_for(provider: Optional[str]) -> dict:
    if provider == "nvidia" or provider == "gemini":
        return {}  # NVIDIA and Gemini don't need attribution headers
    return {
        "HTTP-Referer": "https://github.com/flawsom/MandiIQ",
        "X-Title": "MandiIQ",
    }


def _default_models(provider: Optional[str]) -> list[dict]:
    """Fallback model list if models.yaml is missing or unreadable."""
    if provider == "nvidia":
        return [
            {"id": "nvidia/nemotron-3-ultra-550b-a55b", "name": "Nemotron 3 Ultra 550B",
             "max_retries": 1, "timeout_seconds": 60, "cool_down_minutes": 3},
            {"id": "deepseek-ai/deepseek-v4-pro", "name": "DeepSeek V4 Pro",
             "max_retries": 1, "timeout_seconds": 60, "cool_down_minutes": 3},
            {"id": "moonshotai/kimi-k2.6", "name": "Kimi K2.6",
             "max_retries": 1, "timeout_seconds": 60, "cool_down_minutes": 3},
            {"id": "z-ai/glm-5.2", "name": "GLM-5.2",
             "max_retries": 1, "timeout_seconds": 60, "cool_down_minutes": 3},
        ]
    if provider == "gemini":
        return [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash (free)",
             "max_retries": 1, "timeout_seconds": 30, "cool_down_minutes": 3},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash (free)",
             "max_retries": 1, "timeout_seconds": 30, "cool_down_minutes": 3},
            {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite (free)",
             "max_retries": 1, "timeout_seconds": 30, "cool_down_minutes": 3},
        ]
    # OpenRouter (or unknown)
    return [
        {"id": "openrouter/free", "name": "OpenRouter Free Auto-Router",
         "max_retries": 1, "timeout_seconds": 30, "cool_down_minutes": 5},
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B Instruct",
         "max_retries": 1, "timeout_seconds": 45, "cool_down_minutes": 5},
        {"id": "google/gemma-4-31b-it:free", "name": "Gemma 4 31B IT",
         "max_retries": 1, "timeout_seconds": 45, "cool_down_minutes": 5},
    ]


def _load_models(provider: Optional[str]) -> list[dict]:
    """Load model configuration for the active provider from models.yaml."""
    try:
        import yaml as _yaml
    except ImportError:
        logger.warning("pyyaml not installed — using default model list")
        return _default_models(provider)

    models_path = Path(__file__).resolve().parent / "models.yaml"
    if not models_path.exists():
        logger.warning(f"models.yaml not found at {models_path}")
        return _default_models(provider)
    try:
        with open(models_path) as f:
            config = _yaml.safe_load(f)
        if provider and provider in config and config[provider].get("models"):
            return config[provider]["models"]
        # Legacy schema: top-level "models" key (treat as openrouter)
        if config.get("models"):
            return config["models"]
        return _default_models(provider)
    except Exception as e:
        logger.warning(f"Failed to load models.yaml: {e}")
        return _default_models(provider)


def _is_cooling_down(model_id: str) -> bool:
    """Check if a model is currently cooling down (rate-limited)."""
    with _lock:
        until = _cool_down.get(model_id, 0)
        if time.time() < until:
            return True
        if until > 0:
            del _cool_down[model_id]
        return False


def _mark_cooling_down(model_id: str, minutes: int = 5):
    """Mark a model as cooling down for N minutes."""
    with _lock:
        _cool_down[model_id] = time.time() + (minutes * 60)
    logger.info(f"Model {model_id} cooling down for {minutes} min")


def get_api_key() -> Optional[str]:
    """Get the active provider's API key from environment (free-first)."""
    _provider, key = _detect_provider()
    if not key:
        logger.warning("No LLM provider configured. Set NVIDIA_API_KEY, "
                       "GEMINI_API_KEY (free), or OPENROUTER_API_KEY")
    return key


def get_provider() -> Optional[str]:
    """Return the active provider name ('gemini' | 'openrouter' | None)."""
    provider, _key = _detect_provider()
    return provider




def call_llm(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.3,
) -> dict:
    """
    Call the LLM through the fallback chain for the active provider.

    Tries each model in ranked order. On failure (rate limit, error, timeout),
    marks the model cooling down and tries the next.

    Returns:
        dict with:
          - "content": str  (the response text)
          - "model": str    (which model served it)
          - "error": str | None
          - "endpoints_cited": list[str] (which internal endpoints were invoked)
    """
    _call_llm_deadline = time.time() + GLOBAL_TIMEOUT_SECONDS

    provider, api_key = _detect_provider()
    if not provider or not api_key:
        return {"content": "", "model": "", "error":
                "No LLM provider configured. Set NVIDIA_API_KEY, "
                "GEMINI_API_KEY (free), or OPENROUTER_API_KEY", "endpoints_cited": []}

    if not OPENAI_AVAILABLE:
        return {"content": "", "model": "", "error": "openai SDK not installed",
                "endpoints_cited": []}

    models = _load_models(provider)
    if not models:
        return {"content": "", "model": "", "error":
                "No models configured in models.yaml", "endpoints_cited": []}

    base_url = _base_url_for(provider)
    default_headers = _default_headers_for(provider)

    last_error = None

    for model_cfg in models:
        model_id = model_cfg["id"]
        model_name = model_cfg.get("name", model_id)
        timeout = model_cfg.get("timeout_seconds", 30)
        cool_min = model_cfg.get("cool_down_minutes", 5)
        max_retries = model_cfg.get("max_retries", 1)

        if _is_cooling_down(model_id):
            logger.debug(f"Skipping {model_id} — cooling down")
            continue

        # Global deadline check
        if time.time() > _call_llm_deadline:
            logger.warning("Global timeout reached — stopping model chain")
            break

        logger.info(f"Trying model: {model_id} ({model_name}) via {provider}")

        for attempt in range(max_retries + 1):
            try:
                client = openai.OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                    default_headers=default_headers,
                )

                response = client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=temperature,
                    max_tokens=1024,
                )

                content = response.choices[0].message.content.strip()

                logger.info(f"[OK] Model {model_id} served response "
                           f"({len(content)} chars, finish={response.choices[0].finish_reason})")

                return {
                    "content": content,
                    "model": model_id,
                    "error": None,
                    "endpoints_cited": _extract_endpoints(content),
                }

            except openai.RateLimitError as e:
                last_error = f"Rate limited on {model_id}: {e}"
                logger.warning(last_error)
                _mark_cooling_down(model_id, cool_min)
                break  # Don't retry — move to next model

            except openai.APIStatusError as e:
                if e.status_code >= 500:
                    last_error = f"Server error on {model_id}: {e}"
                    logger.warning(last_error)
                    _mark_cooling_down(model_id, cool_min)
                    break
                elif e.status_code == 429:
                    last_error = f"Rate limited on {model_id}: {e}"
                    logger.warning(last_error)
                    _mark_cooling_down(model_id, cool_min)
                    break
                else:
                    last_error = f"API error on {model_id} (attempt {attempt + 1}): {e}"
                    logger.warning(last_error)

            except openai.APITimeoutError as e:
                last_error = f"Timeout on {model_id}: {e}"
                logger.warning(last_error)
                if attempt < max_retries:
                    time.sleep(2)

            except openai.APIConnectionError as e:
                last_error = f"Connection error on {model_id}: {e}"
                logger.warning(last_error)
                _mark_cooling_down(model_id, cool_min)
                break

            except Exception as e:
                last_error = f"Unexpected error on {model_id}: {e}"
                logger.warning(last_error)

    logger.error(f"All models exhausted. Last error: {last_error}")
    global _fallback_count
    with _fallback_lock:
        _fallback_count += 1
    return {
        "content": "",
        "model": "",
        "error": f"All models exhausted. {last_error or ''}",
        "endpoints_cited": [],
    }


def health_check_all() -> dict[str, bool]:
    """
    Ping each model in the chain once to check availability.
    Returns a dict of model_id -> is_alive.
    """
    provider, api_key = _detect_provider()
    if not provider or not api_key or not OPENAI_AVAILABLE:
        return {}

    models = _load_models(provider)
    base_url = _base_url_for(provider)
    default_headers = _default_headers_for(provider)
    results = {}

    for model_cfg in models:
        model_id = model_cfg["id"]
        try:
            client = openai.OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=5,
                default_headers=default_headers,
            )
            client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            results[model_id] = True
        except Exception:
            results[model_id] = False
            _mark_cooling_down(model_id, model_cfg.get("cool_down_minutes", 5))

    return results


def clear_cool_down(model_id: Optional[str] = None):
    """Manually clear cool-down for a model (or all models)."""
    with _lock:
        if model_id:
            _cool_down.pop(model_id, None)
        else:
            _cool_down.clear()


def _extract_endpoints(content: str) -> list[str]:
    """Naively extract endpoint names from the LLM response."""
    known_endpoints = [
        "rdd-result", "rdd_plot", "robustness", "forecast",
        "risk_score", "recommendation", "prices", "health",
    ]
    cited = []
    for ep in known_endpoints:
        if ep in content:
            cited.append(ep)
    return cited
