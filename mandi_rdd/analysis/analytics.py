"""
MandiIQ - Composite analytics report.

One call assembles the full analyst work-packet for a commodity:

- conformal: distribution-free prediction intervals around the forecast
- drift: PSI / KS / Page-Hinkley / EWMA plus a 0-100 data-quality score
- tail_risk: historical VaR/CVaR, EVT tail fits, max drawdown
- dml: cross-fitted debiased rainfall-price sensitivity
- nowcast: Kalman month-end projection for incomplete reporting

Each section degrades independently, so a missing rainfall table or short
history never takes down the whole report.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ANALYTICS_VERSION = "2.0"


def commodity_analytics(conn, commodity: str) -> dict:
    """Run every analytics module for one commodity and assemble the report."""
    from mandi_rdd.analysis import conformal, dml, drift, nowcast, tail_risk

    sections: dict[str, dict] = {}
    runners = {
        "conformal": conformal.conformal_report,
        "drift": drift.drift_report,
        "tail_risk": tail_risk.tail_risk_report,
        "dml": dml.dml_report,
        "nowcast": nowcast.nowcast_report,
    }

    for name, runner in runners.items():
        try:
            sections[name] = runner(conn, commodity)
        except Exception as exc:  # noqa: BLE001 - a section must never kill the report
            logger.exception("Analytics section %s failed for %s", name, commodity)
            sections[name] = {"error": str(exc)}

    available = [name for name, payload in sections.items() if "error" not in payload]
    return {
        "commodity": commodity,
        "version": ANALYTICS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sections_available": available,
        "sections_failed": [name for name in sections if name not in available],
        "headline": _headline(sections),
        "sections": sections,
    }


def _headline(sections: dict) -> str:
    """One-line analyst summary built only from available section outputs."""
    notes: list[str] = []

    quality = (sections.get("drift") or {}).get("data_quality") or {}
    if quality.get("score") is not None:
        notes.append(f"data quality {quality['score']}/100")
    psi = (sections.get("drift") or {}).get("psi") or {}
    if psi.get("verdict"):
        notes.append(f"price distribution {psi['verdict'].replace('_', ' ')}")

    evt = (sections.get("tail_risk") or {}).get("evt_99") or {}
    if evt.get("var") is not None:
        notes.append(f"99% daily VaR {evt['var'] * 100:.1f}%")

    dml_result = sections.get("dml") or {}
    if dml_result.get("p_value") is not None:
        notes.append(f"rainfall sensitivity p={dml_result['p_value']:.3f}")

    nowcast = sections.get("nowcast") or {}
    if nowcast.get("nowcast_price") is not None:
        notes.append(
            f"month-end nowcast {nowcast['nowcast_price']:.0f} "
            f"({nowcast['completion_pct']:.0f}% of month reported)"
        )

    if not notes:
        return "No analytics section produced output for this commodity yet."
    return "; ".join(notes) + "."
