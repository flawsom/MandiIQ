"""Regression tests for the /ask orchestrator.

Covers the two failure modes fixed for the public launch:
1. Commodity detection matched substrings, so "price-spike" detected Rice.
2. When the selected tool errored (no trained model), /ask returned an empty
   reply instead of falling back to the always-available analysis tools.
"""

from __future__ import annotations

import pytest

from mandi_rdd.ai import orchestrator


class _FakeTool:
    """Callable test double that records how it was invoked."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _install_tools(monkeypatch, tools: dict[str, dict]) -> None:
    monkeypatch.setattr(orchestrator, "TOOLS", tools)


def _no_db(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "_load_db_commodities", lambda: [])


def _no_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator,
        "call_llm",
        lambda **kwargs: {"content": "", "model": "", "error": "no provider configured"},
    )


def test_price_word_does_not_detect_rice():
    # Regression: "price-spike" contains the substring "rice", and the old
    # keyword loop returned Rice for onion questions.
    assert orchestrator._detect_commodity("What is the price-spike risk for onion?") == "Onion"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("price of rice", "Paddy(Common)"),
        ("onion mandi rate", "Onion"),
        ("green chilli arrivals", "Green Chilli"),
        ("tomato forecast", "Tomato"),
        ("potato", "Potato"),
        ("no commodity in this sentence", "Onion"),
    ],
)
def test_detects_canonical_commodity_names(query, expected):
    assert orchestrator._detect_commodity(query) == expected


def test_known_commodity_list_wins_on_exact_name():
    known = ["Onion", "Onion Green", "Tomato"]
    assert orchestrator._detect_commodity("onion green rate", known) == "Onion Green"
    assert orchestrator._detect_commodity("onion rate", known) == "Onion"


def test_district_detection_is_word_bounded():
    assert orchestrator._detect_district("onion in Nashik") == "Nashik"
    assert orchestrator._detect_district("no district mentioned here") is None


def test_tool_selection_routes_by_intent():
    assert orchestrator._select_tools("price-spike risk for onion") == ["get_risk_score"]
    tools = orchestrator._select_tools("should i buy onion now")
    assert "get_recommendation" in tools


def test_structured_fallback_uses_tool_data(monkeypatch):
    _no_db(monkeypatch)
    _no_llm(monkeypatch)
    tool = _FakeTool({"commodity": "Onion", "effect": 350.0, "p_value": 0.003})
    _install_tools(monkeypatch, {"get_rdd_result": {"func": tool, "params": ["commodity"]}})

    result = orchestrator.answer_question("what is the causal effect for onion")

    assert result["commodity"] == "Onion"
    assert "350" in result["answer"]
    assert result["model_used"] is None
    assert "get_rdd_result" in result["endpoints_used"]


def test_fallback_tools_rescue_when_primary_tool_fails(monkeypatch):
    _no_db(monkeypatch)
    _no_llm(monkeypatch)
    _install_tools(
        monkeypatch,
        {
            "get_risk_score": {
                "func": _FakeTool({"error": "No trained model for Onion"}),
                "params": ["commodity", "district"],
            },
            "get_rdd_result": {
                "func": _FakeTool({"commodity": "Onion", "effect": 350.0, "p_value": 0.01}),
                "params": ["commodity"],
            },
            "get_forecast": {
                "func": _FakeTool({"error": "Prophet not installed"}),
                "params": ["commodity"],
            },
        },
    )

    result = orchestrator.answer_question("price-spike risk for onion")

    assert any("fallback" in endpoint for endpoint in result["endpoints_used"])
    assert "350" in result["answer"]


def test_empty_result_names_the_commodity_and_tools(monkeypatch):
    _no_db(monkeypatch)
    _install_tools(
        monkeypatch,
        {
            "get_risk_score": {
                "func": _FakeTool({"error": "No trained model for Sesamum"}),
                "params": ["commodity", "district"],
            },
            "get_rdd_result": {"func": _FakeTool({"error": "no data"}), "params": ["commodity"]},
            "get_forecast": {"func": _FakeTool({"error": "no data"}), "params": ["commodity"]},
        },
    )

    result = orchestrator.answer_question("sesame price-spike risk")

    assert result["error"] == "No tool results available"
    assert "Sesamum(Sesame,Gingelly,Til)" in result["answer"]
    assert "Tools tried" in result["answer"]
