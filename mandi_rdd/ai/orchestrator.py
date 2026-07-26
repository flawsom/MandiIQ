"""
MandiIQ — AI Orchestrator with Tool-Calling.

Turns five separate outputs (RDD, robustness, forecast, risk score, NDVI)
into one coherent, grounded answer.

Design principles:
1. Tool results are provided as context to the LLM; the system prompt forbids
   stating a number not returned by a tool call this turn.
2. The _build_structured_answer() fallback enforces code-level grounding
   when the entire model chain is exhausted — constructs an answer directly
   from tool outputs without any LLM involvement.
3. Every answer shows which endpoints were used and which model served it.
4. If the fallback chain is exhausted, returns structured data without narrative.
"""

import json
import logging
from typing import Optional

import numpy as np

from mandi_rdd.ai.router import call_llm, get_api_key

logger = logging.getLogger(__name__)

# ── System prompt ──

SYSTEM_PROMPT = """You are MandiIQ's AI procurement assistant. You analyze agricultural commodity price data from Indian mandis to answer procurement questions.

CORE RULES (never violate these):
1. You have access to the following tools. You MUST use them before answering.
2. NEVER state a number, statistic, or price that was NOT returned by a tool call this turn. If you didn't retrieve it, don't say it.
3. If a tool returns an error or no data, say so honestly — don't make up a number.
4. Keep answers concise (3-5 sentences). The user is a commodity buyer who wants a fast, actionable answer.
5. Always specify the commodity and district you're referring to.
6. If you don't have enough data to answer confidently, say so rather than guessing.

TOOLS (call these via the orchestrator — never fabricate their outputs):
- get_rdd_result(commodity): Causal effect of crossing the -19% rainfall threshold
- get_forecast(commodity, district): Prophet price forecast for next 3-6 months
- get_risk_score(commodity, district): XGBoost price-spike risk probability (0-100)
- get_recommendation(commodity, district): Combined prescriptive recommendation
- get_robustness(commodity): Full robustness bundle (bandwidth sensitivity, placebo, density)

NIGHTLY NARRATIVE MODE:
When asked to summarize "what changed" or provide a "nightly update", structure your response as:
1. Headline finding (1 sentence — the most important change)
2. Price movement summary (1-2 sentences)
3. Risk outlook (1 sentence)
4. Recommendation (1 sentence)
"""


# ── Tool definitions ──

def tool_get_rdd_result(commodity: str, state: Optional[str] = None) -> dict:
    """Get the causal RDD estimate for a commodity at the -19% rainfall cutoff."""
    try:
        from mandi_rdd.analysis.rdd_engine import run_rdd
        from mandi_rdd.storage.duckdb_store import get_connection, init_schema
        conn = get_connection()
        init_schema(conn)
        result = run_rdd(conn, commodity=commodity, state=state)
        conn.close()
        return _sanitize(result)
    except Exception as e:
        logger.error(f"tool_get_rdd_result failed: {e}")
        return {"error": str(e)}


def tool_get_forecast(commodity: str, district: Optional[str] = None) -> dict:
    """Get a Prophet price forecast for a commodity."""
    try:
        from mandi_rdd.analysis.forecast import get_forecast_summary
        from mandi_rdd.storage.duckdb_store import get_connection, init_schema
        conn = get_connection()
        init_schema(conn)
        result = get_forecast_summary(conn, commodity=commodity)
        conn.close()
        return _sanitize(result)
    except Exception as e:
        logger.error(f"tool_get_forecast failed: {e}")
        return {"error": str(e)}


def tool_get_risk_score(commodity: str, district: Optional[str] = None) -> dict:
    """Get the XGBoost price-spike risk probability (0-100) for a commodity."""
    try:
        from mandi_rdd.analysis.classifier import predict_spike_risk
        from mandi_rdd.storage.duckdb_store import get_connection, init_schema
        conn = get_connection()
        init_schema(conn)
        result = predict_spike_risk(conn, commodity=commodity, district=district)
        conn.close()
        return _sanitize(result)
    except Exception as e:
        logger.error(f"tool_get_risk_score failed: {e}")
        return {"error": str(e)}


def tool_get_recommendation(commodity: str, district: Optional[str] = None) -> dict:
    """Get a combined procurement recommendation (RDD + risk + forecast)."""
    try:
        from mandi_rdd.analysis.prescriptive import compute_recommendation
        from mandi_rdd.storage.duckdb_store import get_connection, init_schema
        conn = get_connection()
        init_schema(conn)
        result = compute_recommendation(conn, commodity=commodity, district=district)
        conn.close()
        return _sanitize(result)
    except Exception as e:
        logger.error(f"tool_get_recommendation failed: {e}")
        return {"error": str(e)}


def tool_get_robustness(commodity: str) -> dict:
    """Get the full robustness check bundle (bandwidth sensitivity, placebo, density)."""
    try:
        from mandi_rdd.analysis.rdd_engine import run_rdd
        from mandi_rdd.storage.duckdb_store import get_connection, init_schema
        conn = get_connection()
        init_schema(conn)
        result = run_rdd(conn, commodity=commodity)
        conn.close()
        return _sanitize({
            "commodity": commodity,
            "bandwidth_sensitivity": result.get("bandwidth_sensitivity", []),
            "placebo_tests": result.get("placebo_tests", []),
            "density_test": result.get("density_test", {}),
            "covariate_balance": result.get("covariate_balance", {}),
        })
    except Exception as e:
        logger.error(f"tool_get_robustness failed: {e}")
        return {"error": str(e)}


# ── Tool registry ──

TOOLS = {
    "get_rdd_result": {
        "func": tool_get_rdd_result,
        "description": "Get causal RDD estimate for a commodity",
        "params": ["commodity"],
    },
    "get_forecast": {
        "func": tool_get_forecast,
        "description": "Get Prophet price forecast",
        "params": ["commodity"],
    },
    "get_risk_score": {
        "func": tool_get_risk_score,
        "description": "Get price-spike risk probability",
        "params": ["commodity"],
    },
    "get_recommendation": {
        "func": tool_get_recommendation,
        "description": "Get combined procurement recommendation",
        "params": ["commodity"],
    },
    "get_robustness": {
        "func": tool_get_robustness,
        "description": "Get full robustness check bundle",
        "params": ["commodity"],
    },
}


# ── Orchestrator ──

def answer_question(
    query: str,
    commodity: Optional[str] = None,
    district: Optional[str] = None,
    temperature: float = 0.3,
) -> dict:
    """
    Answer a free-text procurement question using the AI orchestrator.

    1. Parse the query for commodity/district hints if not provided
    2. Determine which tools to call
    3. Call the internal analysis functions
    4. Compose the LLM prompt with only the tool results that were returned
    5. Route through OpenRouter fallback chain
    6. Return the answer + metadata (which endpoints, which model)

    Args:
        query: Free-text question from the user
        commodity: Optional commodity override
        district: Optional district override

    Returns:
        dict with answer, model_used, endpoints_used, error, tool_results
    """
    # 1. Detect commodity from query if not provided
    detected_commodity = commodity or _detect_commodity(query)
    detected_district = district or _detect_district(query)

    # 2. Determine which tools to call based on query content
    tool_names = _select_tools(query)

    # 3. Execute tool calls (only the ones that apply)
    tool_results = {}
    endpoints_used = []

    for name in tool_names:
        tool = TOOLS.get(name)
        if not tool:
            continue
        try:
            if "district" in tool["params"] and detected_district:
                result = tool["func"](commodity=detected_commodity, district=detected_district)
            elif "state" in tool["params"]:
                result = tool["func"](commodity=detected_commodity)
            else:
                result = tool["func"](commodity=detected_commodity)

            # Only include results that have actual data (not errors)
            if result and "error" not in result:
                tool_results[name] = result
                endpoints_used.append(name)
            elif result and "error" in result:
                tool_results[name] = {"note": result["error"]}
                endpoints_used.append(f"{name} (error: {result['error']})")
        except Exception as e:
            tool_results[name] = {"note": f"Error: {e}"}
            endpoints_used.append(f"{name} (error)")

    # 4. Build the context string from tool results (GROUNDING — this is the
    #    only data the LLM sees; it cannot interpolate anything else)
    context_parts = []
    for name, result in tool_results.items():
        if result:
            context_parts.append(f"--- {name} ---")
            try:
                context_parts.append(json.dumps(result, indent=2, default=str))
            except (TypeError, ValueError):
                context_parts.append(str(result))

    # If no tools produced useful results, return structured fallback
    if not any(r for r in tool_results.values() if "note" not in r):
        # Build a minimal structured response from whatever we have
        fallback = {
            "query": query,
            "commodity": detected_commodity,
            "district": detected_district or "All",
            "answer": "I don't have enough data to answer that question yet. "
                       "The pipeline needs to run first to populate the database. "
                       "Try running the scheduler: python -m mandi_rdd.ingestion.scheduler",
            "model_used": None,
            "endpoints_used": endpoints_used or ["No data available"],
            "error": "No tool results available",
        }
        return fallback

    context = "\n\n".join(context_parts)

    # 5. Compose the user message
    user_message = (
        f"Query: {query}\n\n"
        f"Commodity: {detected_commodity}\n"
        f"District: {detected_district or 'All'}\n\n"
        f"Tool results (only use these numbers — never make up data):\n{context}"
    )

    # 6. Call the LLM through the OpenRouter fallback chain
    llm_response = call_llm(
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message,
        temperature=temperature,
    )

    # 7. If LLM failed, return structured fallback
    if llm_response.get("error") or not llm_response.get("content"):
        # Build answer from tool results directly (no LLM narrative)
        structured_answer = _build_structured_answer(tool_results, detected_commodity, detected_district)
        return {
            "query": query,
            "commodity": detected_commodity,
            "district": detected_district or "All",
            "answer": structured_answer,
            "model_used": None,
            "endpoints_used": endpoints_used,
            "error": llm_response.get("error"),
        }

    # 8. Return the grounded answer
    return {
        "query": query,
        "commodity": detected_commodity,
        "district": detected_district or "All",
        "answer": llm_response["content"],
        "model_used": llm_response.get("model"),
        "endpoints_used": endpoints_used,
        "error": None,
    }


def generate_nightly_narrative(
    commodity: str = "Onion",
) -> dict:
    """
    Generate a cached nightly plain-English narrative for a commodity.

    Called after the pipeline finishes (in scheduler.py). The narrative is
    3-4 sentences summarizing what changed vs. last week.

    Returns:
        dict with narrative text and metadata
    """
    result = answer_question(
        query="Summarize what changed this week for this commodity. "
              "Focus on: price movement, rainfall deficiency, risk level, "
              "and a procurement recommendation.",
        commodity=commodity,
        temperature=0.2,
    )
    return result


# ── Helpers ──

def _detect_commodity(query: str) -> str:
    """Detect commodity from query text. Falls back to 'Onion'."""
    # Comprehensive list of commodities from actual market data
    known = [
        "paddy", "wheat", "rice", "maize", "bajra", "jowar", "ragi",
        "onion", "tomato", "potato", "cabbage", "cauliflower",
        "brinjal", "ladyfinger", "chilli", "garlic", "ginger",
        "turmeric", "coriander", "cumin", "mustard", "pepper",
        "chana", "arhar", "moong", "urad", "masoor", "gram",
        "groundnut", "sesame", "sunflower", "soybean", "coconut",
        "cotton", "sugarcane", "banana", "mango", "apple", "orange",
        "grapes", "guava", "papaya", "lemon", "pomegranate",
        "almond", "cashewnut", "walnut", "raisin",
        "pea", "beans", "carrot", "radish", "beetroot", "spinach",
        "milk", "egg", "fish", "mutton", "chicken",
    ]
    q_lower = query.lower()
    for k in known:
        if k in q_lower:
            return k.upper() if k in ["arhar", "urad"] else k.capitalize()
    return "Onion"


def _detect_district(query: str) -> Optional[str]:
    """Detect district from query text."""
    # Common mandi districts in Maharashtra, Karnataka, etc.
    known = ["nashik", "pune", "ahmednagar", "solapur", "mumbai",
             "bangalore", "belgaum", "bagalkot", "bijapur", "dharwad",
             "jaipur", "ajmer", "kota", "udaipur", "delhi",
             "lucknow", "kanpur", "varanasi", "agra", "indore"]
    q_lower = query.lower()
    for k in known:
        if k in q_lower:
            return k.capitalize()
    return None


def _select_tools(query: str) -> list[str]:
    """
    Select which tools to call based on the query.

    Rules:
    - "risk", "spike", "probability" → risk_score
    - "forecast", "predict", "future" → forecast
    - "recommend", "procurement", "buy", "should I", "lock" → recommendation
    - "robust", "bandwidth", "placebo", "check" → robustness
    - "effect", "causal", "rdd", "discontinuity", "jump" → rdd_result
    - Default: all tools
    """
    q = query.lower()

    # If asking about specific things, only call relevant tools
    tools = []

    if any(w in q for w in ["risk", "spike", "probability", "chance", "how likely"]):
        tools.append("get_risk_score")
    if any(w in q for w in ["forecast", "predict", "future", "trend", "next month", "price path"]):
        tools.append("get_forecast")
    if any(w in q for w in ["recommend", "procurement", "buy", "should i", "lock", "advise", "advice"]):
        tools.append("get_recommendation")
    if any(w in q for w in ["robust", "bandwidth", "placebo", "check", "reliable", "sensitivity"]):
        tools.append("get_robustness")
    if any(w in q for w in ["effect", "causal", "rdd", "discontinuity", "jump", "cutoff", "deficiency"]):
        tools.append("get_rdd_result")

    # If nothing specific matched, call the most useful ones
    if not tools:
        tools = ["get_rdd_result", "get_forecast", "get_risk_score"]

    return tools


def _build_structured_answer(
    tool_results: dict,
    commodity: str,
    district: Optional[str],
) -> str:
    """
    Build a plain-text answer from tool results directly (no LLM).

    This is the graceul degradation path when all models are exhausted.
    """
    parts = [f"📊 {commodity} — Procurement Intelligence Report"]

    if district:
        parts.append(f"📍 District: {district}")

    # RDD result
    rdd = tool_results.get("get_rdd_result", {})
    if rdd and rdd.get("effect") is not None:
        parts.append(
            f"• Causal effect at -19% rainfall cutoff: ₹{rdd['effect']:.0f} "
            f"(p={rdd.get('p_value', 'N/A')})"
        )

    # Risk score
    risk = tool_results.get("get_risk_score", {})
    if risk and risk.get("overall_risk") is not None:
        parts.append(
            f"• Price-spike risk score: {risk['overall_risk']:.0f}/100 "
            f"(max: {risk.get('max_risk', 'N/A')})"
        )

    # Forecast
    forecast = tool_results.get("get_forecast", {})
    if forecast and forecast.get("forecast"):
        preds = forecast["forecast"]
        if len(preds) >= 2:
            current = preds[0]["forecast"]
            future = preds[-1]["forecast"]
            trend = ((future - current) / current * 100) if current > 0 else 0
            parts.append(
                f"• Price forecast: ₹{current:.0f} → ₹{future:.0f} "
                f"({trend:+.1f}% over {len(preds)} months)"
            )

    # Recommendation
    rec = tool_results.get("get_recommendation", {})
    if rec and rec.get("recommendation"):
        parts.append(f"• Recommendation: {rec['recommendation']}")

    if len(parts) == 1:
        parts.append("No data available yet. Run the scheduler first.")

    return "\n\n".join(parts)


def _sanitize(result: dict) -> dict:
    """Remove non-serializable objects from a result dict.

    Handles numpy types (int64, float64, bool_, ndarray) and other objects
    that aren't JSON-serializable by default.
    """
    sanitized = {}
    for k, v in result.items():
        if isinstance(v, (str, int, float, bool, list, dict)):
            sanitized[k] = v
        elif v is None:
            sanitized[k] = None
        elif isinstance(v, np.integer):
            sanitized[k] = int(v)
        elif isinstance(v, np.floating):
            sanitized[k] = float(v)
        elif isinstance(v, np.bool_):
            sanitized[k] = bool(v)
        elif isinstance(v, np.ndarray):
            sanitized[k] = v.tolist()
        else:
            sanitized[k] = str(v)
    return sanitized
