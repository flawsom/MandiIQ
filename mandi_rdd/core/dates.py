"""Arrival-date parsing and integrity guardrails for mandi price data.

data.gov.in publishes ``arrival_date`` as ``DD/MM/YYYY``. pandas/dateutil guess
month-first whenever both components are <= 12, so "12/09/2026" (12 September)
is stored as 2026-12-09 and "08/09/2026" becomes 2026-08-09. That files records
months away from the day they were actually quoted, and every downstream number
(freshness, RDD, forecasts, nowcasts, dashboards) inherits the error.

On top of the ambiguity, some warehouse rows carry a date that simply cannot be
true: an arrival date in the future. Those rows are rejected at ingest, counted
by :func:`date_quality`, and corrected by :func:`repair_future_dates`, so the
numbers the API and dashboards show always match the day a price was quoted.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections import Counter
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# A mandi price can only be quoted today or earlier. One day of slack absorbs
# timezone offsets (IST is UTC+5:30) and feeds that publish a day ahead.
MAX_FUTURE_DAYS = 1

# Nothing in the agmarknet feed predates 2001; anything earlier is a parse
# artefact rather than a record.
EARLIEST_PLAUSIBLE = _dt.date(1990, 1, 1)

# Explicit formats, checked in order. ISO first (unambiguous), then the Indian
# day-first spellings the public resources use.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d/%m/%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%b %d, %Y",
)

_MISSING_TOKENS = {"", "nan", "nat", "none", "null", "-"}


def _as_text(value) -> Optional[str]:
    """Return a stripped string for scalar inputs, else None."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (_dt.date, _dt.datetime)):
        return None  # handled as a real date object
    if value is None:
        return None
    # numpy/pandas scalars stringify cleanly; floats like 20260912.0 do not.
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _as_date(value) -> Optional[_dt.date]:
    """Coerce real date objects / epoch numbers to a ``date``."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds (the API has shipped both units at different times).
        ts = float(value)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        if 3.0e8 <= ts <= 4.5e9:
            try:
                return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).date()
            except (OverflowError, OSError, ValueError):
                return None
    return None


def _parse_text(text: str) -> Optional[_dt.date]:
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # ISO-ish prefix: "2026-09-12 00:00:00+05:30", "2026-09-12T00:00:00Z"
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def classify_date(
    value,
    today: Optional[_dt.date] = None,
    max_future_days: int = MAX_FUTURE_DAYS,
) -> tuple[Optional[str], str]:
    """Classify one raw arrival date.

    Returns ``(iso_date, status)`` where status is one of ``ok``, ``missing``,
    ``unparseable``, ``future`` or ``impossible``. ``iso_date`` is only set for
    ``ok``: callers must never store a date this function declined.
    """
    today = today or _dt.date.today()

    if value is None:
        return None, "missing"

    probed = _as_date(value)
    if probed is None:
        text = _as_text(value)
        if text is None:
            return None, "unparseable"
        if text.lower() in _MISSING_TOKENS:
            return None, "missing"
        probed = _parse_text(text)
        if probed is None:
            return None, "unparseable"

    if probed > today + _dt.timedelta(days=max_future_days):
        return None, "future"
    if probed < EARLIEST_PLAUSIBLE:
        return None, "impossible"
    return probed.isoformat(), "ok"


def parse_arrival_date(
    value,
    today: Optional[_dt.date] = None,
    max_future_days: int = MAX_FUTURE_DAYS,
) -> Optional[str]:
    """Return an ISO arrival date, or None when the value cannot be trusted."""
    return classify_date(value, today=today, max_future_days=max_future_days)[0]


def swap_month_day(value) -> Optional[_dt.date]:
    """Swap the month and day components of a date, when that stays valid."""
    probed = _as_date(value)
    if probed is None:
        text = _as_text(value)
        probed = _parse_text(text) if text else None
    if probed is None:
        return None
    try:
        return _dt.date(probed.year, probed.day, probed.month)
    except ValueError:
        return None


def summarize_dates(
    values: Iterable,
    today: Optional[_dt.date] = None,
) -> dict:
    """Count parse outcomes for a batch of raw dates (ingest logging)."""
    today = today or _dt.date.today()
    statuses: Counter = Counter()
    ambiguous = 0
    for value in values:
        iso, status = classify_date(value, today=today)
        statuses[status] += 1
        if status == "ok":
            month, day = int(iso[5:7]), int(iso[8:10])
            if month <= 12 and day <= 12 and month != day:
                ambiguous += 1
    return {
        "ok": statuses["ok"],
        "missing": statuses["missing"],
        "unparseable": statuses["unparseable"],
        "future": statuses["future"],
        "impossible": statuses["impossible"],
        "rejected": statuses["missing"] + statuses["unparseable"]
        + statuses["future"] + statuses["impossible"],
        "ambiguous_day_month": ambiguous,
    }


def date_quality(conn, today: Optional[_dt.date] = None) -> dict:
    """Warehouse-level date integrity report (used by /data-quality)."""
    today = today or _dt.date.today()
    cutoff = (today + _dt.timedelta(days=MAX_FUTURE_DAYS)).isoformat()
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS n_rows,
            MIN(arrival_date) AS min_date,
            MAX(arrival_date) AS max_date,
            SUM(CASE WHEN arrival_date IS NULL THEN 1 ELSE 0 END) AS n_null_dates,
            SUM(CASE WHEN arrival_date > ? THEN 1 ELSE 0 END) AS n_future_dates
        FROM prices
        """,
        [cutoff],
    ).fetchone()
    n_rows, min_date, max_date, n_nulls, n_future = (list(row) + [0] * 5)[:5]
    max_date = str(max_date) if max_date is not None else None
    days_behind = None
    if max_date:
        try:
            days_behind = (today - _dt.date.fromisoformat(max_date[:10])).days
        except ValueError:
            days_behind = None
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": today.isoformat(),
        "future_cutoff": cutoff,
        "n_rows": int(n_rows or 0),
        "min_date": str(min_date)[:10] if min_date is not None else None,
        "max_date": max_date[:10] if max_date else None,
        "days_behind": days_behind,
        "n_null_dates": int(n_nulls or 0),
        "n_future_dates": int(n_future or 0),
        "status": "ok" if not n_future else "repair_needed",
    }


def repair_future_dates(
    conn,
    today: Optional[_dt.date] = None,
    max_future_days: int = MAX_FUTURE_DAYS,
    dry_run: bool = False,
) -> dict:
    """Correct (or drop) rows whose arrival date cannot be true.

    A stored future date is the inverse of the month-first mis-parse, so
    swapping the month and day components lands on the day the price was
    actually quoted (2026-12-09 -> 2026-09-12). Rows that stay impossible after
    the swap are unusable: they are removed so they cannot pollute freshness,
    RDD or forecast outputs.
    """
    today = today or _dt.date.today()
    cutoff = (today + _dt.timedelta(days=max_future_days)).isoformat()

    report = {
        "cutoff": cutoff,
        "as_of_date": today.isoformat(),
        "n_future": 0,
        "repaired": 0,
        "dropped": 0,
        "dry_run": bool(dry_run),
    }
    try:
        report["n_future"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM prices WHERE arrival_date > ?", [cutoff]
            ).fetchone()[0]
            or 0
        )
    except Exception as exc:  # warehouse unavailable / empty
        logger.warning("Date repair skipped: %s", exc)
        return report

    if dry_run or report["n_future"] == 0:
        return report

    repair_sql = """
        UPDATE prices
        SET arrival_date = TRY_CAST(strftime(arrival_date, '%Y-%d-%m') AS DATE)
        WHERE arrival_date > ?
          AND TRY_CAST(strftime(arrival_date, '%Y-%d-%m') AS DATE) IS NOT NULL
          -- Skip symmetric dates (05/05) and anything the swap cannot fix;
          -- those are dropped below instead of being counted as repaired.
          AND strftime(arrival_date, '%Y-%d-%m') <> strftime(arrival_date, '%Y-%m-%d')
    """
    try:
        report["repaired"] = int(conn.execute(repair_sql, [cutoff]).fetchone()[0] or 0)
    except Exception as exc:
        # e.g. a uniqueness conflict on the swapped key: drop, do not guess.
        logger.warning("Could not swap month/day for future rows (%s); dropping them", exc)

    try:
        report["dropped"] = int(
            conn.execute("DELETE FROM prices WHERE arrival_date > ?", [cutoff]).fetchone()[0]
            or 0
        )
    except Exception as exc:
        logger.error("Could not drop impossible arrival dates: %s", exc)

    if report["repaired"] or report["dropped"]:
        logger.warning(
            "Date integrity repair: %d rows rewritten, %d impossible rows dropped "
            "(cutoff %s)",
            report["repaired"], report["dropped"], cutoff,
        )
    return report
