"""
MandiIQ — AI Orchestration Layer (Phase 11)

Multi-model router on OpenRouter free tier with circuit-breaker fallback,
tool-calling to every internal endpoint, and no-hallucination grounding.

Components:
- router.py:   OpenRouter API client with per-model circuit breakers
- orchestrator.py: Tool definitions + compose grounded answers
- models.yaml: Config-driven ranked model list for the fallback chain
"""
