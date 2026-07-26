"""
MandiRDD — Stale-offset quarantine, circuit breaker, adaptive probe,
and 80M-row variety-wise archive scanner.

Extracted from fetch_prices.py to keep each module focused on one
responsibility.  Dependencies point *into* fetch_prices (one-way), so
there is no circular import.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os as _os_mod
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from mandi_rdd.ingestion.fetch_prices import (
    _get_api_key,
    fetch_page_for_resource,
    normalize_price_record,
)

logger = logging.getLogger(__name__)

VARIETYWISE_RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"

_STALE_OFFSETS_PATH = _os_mod.path.join(
    _os_mod.path.dirname(__file__), "..", "data", "stale_offsets.json"
)
_STALE_OFFSET_TTL_HOURS = 168


def _load_stale_offsets() -> dict:
    path = _os_mod.path.abspath(_STALE_OFFSETS_PATH)
    try:
        if _os_mod.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load stale offsets: {e}")
    return {}


def _save_stale_offsets(data: dict) -> None:
    path = _os_mod.path.abspath(_STALE_OFFSETS_PATH)
    try:
        _os_mod.makedirs(_os_mod.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except OSError as e:
        logger.warning(f"Failed to save stale offsets: {e}")


def _get_cached_stale_max(resource_id: str) -> int | None:
    data = _load_stale_offsets()
    entry = data.get(resource_id)
    if not entry:
        return None
    max_stale = entry.get("max_stale_offset", 0)
    confirmed_at = entry.get("confirmed_at", "")
    if not confirmed_at or max_stale <= 0:
        return None
    try:
        confirmed_dt = datetime.datetime.strptime(confirmed_at[:19], "%Y-%m-%dT%H:%M:%S")
        elapsed_hours = (datetime.datetime.utcnow() - confirmed_dt).total_seconds() / 3600
        if elapsed_hours > _STALE_OFFSET_TTL_HOURS:
            logger.info(
                "Stale-offset cache for %s expired (%.0fh > %dh TTL). Re-probing from offset 0.",
                resource_id[:20], elapsed_hours, _STALE_OFFSET_TTL_HOURS,
            )
            data.pop(resource_id, None)
            _save_stale_offsets(data)
            return None
    except (ValueError, TypeError):
        return None
    logger.info(
        "Using cached stale-offset for %s: skipping offsets 0-%d (%dh old)",
        resource_id[:20], max_stale, round(elapsed_hours),
    )
    return max_stale


def _quarantine_stale_range(resource_id: str, max_stale_offset: int) -> None:
    if max_stale_offset <= 0:
        return
    data = _load_stale_offsets()
    data[resource_id] = {
        "max_stale_offset": max_stale_offset,
        "confirmed_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _save_stale_offsets(data)
    logger.info(
        "Quarantined offsets 0-%d for %s (TTL %dh)",
        max_stale_offset, resource_id[:20], _STALE_OFFSET_TTL_HOURS,
    )


_CIRCUIT_BREAKER_COOLDOWN_HOURS = 24
_CIRCUIT_BREAKER_THRESHOLD = 3
_CB_KEY = "_circuit_breaker"


def _record_stale_detection(resource_id: str) -> None:
    data = _load_stale_offsets()
    cb = data.setdefault(_CB_KEY, {})
    entry = cb.setdefault(resource_id, {"consecutive": 0})
    cooldown_until = entry.get("cooldown_until", "")
    if cooldown_until:
        try:
            cd = datetime.datetime.strptime(str(cooldown_until)[:19], "%Y-%m-%dT%H:%M:%S")
            if datetime.datetime.utcnow() < cd:
                logger.info(
                    "Circuit breaker for %s already open (cooldown until %s).",
                    resource_id[:20], cooldown_until,
                )
                return
            entry["consecutive"] = 0
            entry.pop("cooldown_until", None)
        except (ValueError, TypeError):
            pass
    entry["consecutive"] = entry.get("consecutive", 0) + 1
    count = entry["consecutive"]
    if count >= _CIRCUIT_BREAKER_THRESHOLD:
        cooldown_until = (
            datetime.datetime.utcnow()
            + datetime.timedelta(hours=_CIRCUIT_BREAKER_COOLDOWN_HOURS)
        ).isoformat() + "Z"
        entry["cooldown_until"] = cooldown_until
        logger.warning(
            "Circuit breaker TRIPPED for %s after %d consecutive stale runs. "
            "Skipping archive for %dh (until %s).",
            resource_id[:20], count, _CIRCUIT_BREAKER_COOLDOWN_HOURS, cooldown_until,
        )
    else:
        logger.info(
            "Circuit breaker for %s: %d/%d consecutive stale runs.",
            resource_id[:20], count, _CIRCUIT_BREAKER_THRESHOLD,
        )
    cb[resource_id] = entry
    _save_stale_offsets(data)


def _is_circuit_open(resource_id: str) -> bool:
    data = _load_stale_offsets()
    cb = data.get(_CB_KEY, {})
    entry = cb.get(resource_id)
    if not entry:
        return False
    cooldown_until = entry.get("cooldown_until", "")
    if not cooldown_until:
        return False
    try:
        cd = datetime.datetime.strptime(str(cooldown_until)[:19], "%Y-%m-%dT%H:%M:%S")
        if datetime.datetime.utcnow() < cd:
            remaining = round((cd - datetime.datetime.utcnow()).total_seconds() / 60)
            logger.info(
                "Circuit breaker open for %s: %dmin remaining until %s.",
                resource_id[:20], remaining, cooldown_until,
            )
            return True
        logger.info(
            "Circuit breaker for %s: cooldown expired. Auto-retrying.",
            resource_id[:20],
        )
        entry["consecutive"] = 0
        entry.pop("cooldown_until", None)
        cb[resource_id] = entry
        _save_stale_offsets(data)
    except (ValueError, TypeError):
        pass
    return False


def _reset_circuit(resource_id: str) -> None:
    data = _load_stale_offsets()
    cb = data.setdefault(_CB_KEY, {})
    entry = cb.get(resource_id)
    if entry and entry.get("consecutive", 0) > 0:
        logger.info(
            "Circuit breaker for %s: probe successful, resetting consecutive count.",
            resource_id[:20],
        )
    cb[resource_id] = {"consecutive": 0}
    _save_stale_offsets(data)



def _adaptive_probe_count(total: int, page_size: int = 1000) -> int:
    ratio = max(total / page_size, 1.0)
    probes = math.ceil(math.log10(ratio) * 2)
    return max(3, min(10, probes))


def _iso_boundary_days_ago(days: int) -> str:
    return (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _probe_varietywise_pages(
    resource_id: str,
    since_date: datetime.date,
    total: int,
    page_size: int = 1000,
    extra_params: list = None,
    n_probes: int | None = None,
) -> tuple[list[dict], int, bool, set[int]]:
    if total <= 0:
        return [], 0, False, set()
    if n_probes is None:
        n_probes = _adaptive_probe_count(total, page_size)
    last_safe_offset = max(page_size, total // 2)
    step = max(page_size, (last_safe_offset - page_size) // max(n_probes - 1, 1))
    probe_offsets = [page_size + i * step for i in range(n_probes)]
    probe_offsets = [min(o, total - page_size) for o in probe_offsets]
    probe_offsets = sorted(set(o for o in probe_offsets if o > 0))[:n_probes]
    if not probe_offsets:
        return [], page_size, False, set()

    collected: list[dict] = []
    stale_at_offset = 0
    probe_results: list[tuple[int, bool]] = []
    probed_offsets: set[int] = set()

    def _probe_one(offset: int) -> tuple[int, list[dict], bool]:
        try:
            data = fetch_page_for_resource(
                resource_id, offset=offset, limit=page_size, extra_params=extra_params
            )
        except Exception as e:
            logger.warning(f"Variety-wise probe failed at offset {offset}: {e}")
            return offset, [], False
        records = data.get("records", [])
        page_fresh = False
        page_out = []
        for r in records:
            rec = normalize_price_record(r)
            rec["_source"] = {
                "source_type": "varietywise",
                "source_name": "data.gov.in variety-wise prices archive",
                "resource_id": VARIETYWISE_RESOURCE_ID,
            }
            ad = rec.get("arrival_date")
            if ad:
                try:
                    d = datetime.datetime.strptime(str(ad)[:10], "%Y-%m-%d").date()
                except ValueError:
                    d = None
                if d is not None and d >= since_date:
                    page_fresh = True
                    page_out.append(rec)
        return offset, page_out, page_fresh

    with ThreadPoolExecutor(max_workers=n_probes) as ex:
        futs = {ex.submit(_probe_one, o): o for o in probe_offsets}
        for fut in as_completed(futs):
            offset, records, was_fresh = fut.result()
            probed_offsets.add(offset)
            probe_results.append((offset, was_fresh))
            if was_fresh:
                collected.extend(records)
            else:
                if offset > stale_at_offset:
                    stale_at_offset = offset

    fresh_count = sum(1 for _, f in probe_results if f)
    majority_fresh = fresh_count >= len(probe_results) / 2
    stable_offset = max(stale_at_offset + page_size, page_size)
    if majority_fresh:
        logger.info(
            "Variety-wise probe: %d/%d pages fresh. Stable offset=%d (after highest stale=%d)",
            fresh_count, len(probe_results), stable_offset, stale_at_offset,
        )
    else:
        logger.info(
            "Variety-wise probe: %d/%d pages stale. "
            "Stable offset=%d (quarantining full probed range 0-%d).",
            len(probe_results) - fresh_count, len(probe_results),
            stable_offset, stale_at_offset,
        )
    return collected, stable_offset, majority_fresh, probed_offsets



def fetch_varietywise_recent(days: int = 60, max_records: int = 20000) -> list:
    try:
        _get_api_key()
    except RuntimeError:
        logger.info("DATA_GOV_IN_API_KEY not set. Skipping variety-wise supplement.")
        return []

    since = _iso_boundary_days_ago(days)
    since_date = datetime.datetime.strptime(since, "%Y-%m-%d").date()
    extra = ["filters[Arrival_Date][>=]=" + urllib.parse.quote(since)]
    page_size = 1000

    if _is_circuit_open(VARIETYWISE_RESOURCE_ID):
        logger.warning(
            "Variety-wise archive SKIPPED: circuit breaker is open for %s.",
            VARIETYWISE_RESOURCE_ID[:20],
        )
        return []

    cached_stale_max = _get_cached_stale_max(VARIETYWISE_RESOURCE_ID)
    probe_from_offset = 0
    if cached_stale_max is not None:
        probe_from_offset = cached_stale_max + page_size
        logger.info(
            "Variety-wise: using cached quarantine, skipping offsets 0-%d (starting at %d)",
            cached_stale_max, probe_from_offset,
        )

    try:
        first_page = fetch_page_for_resource(
            VARIETYWISE_RESOURCE_ID, offset=probe_from_offset, limit=page_size,
            extra_params=extra,
        )
    except Exception as e:
        logger.warning(f"Variety-wise initial fetch failed: {e}")
        return []

    total = first_page.get("total", 0)
    first_page_count = first_page.get("count", len(first_page.get("records", [])))
    logger.info(
        "Variety-wise archive: total=%d records, first_page_count=%d (offset=%d)",
        total, first_page_count, probe_from_offset,
    )
    effective_total = max(total - probe_from_offset, 0)
    scan_boundary = effective_total
    if first_page_count < page_size:
        scan_boundary = min(effective_total, first_page_count + page_size * 2)

    out = []
    for r in first_page.get("records", []):
        rec = normalize_price_record(r)
        rec["_source"] = {
            "source_type": "varietywise",
            "source_name": "data.gov.in variety-wise prices archive",
            "resource_id": VARIETYWISE_RESOURCE_ID,
        }
        ad = rec.get("arrival_date")
        if ad:
            try:
                d = datetime.datetime.strptime(str(ad)[:10], "%Y-%m-%d").date()
            except ValueError:
                d = None
            if d is not None and d >= since_date:
                out.append(rec)

    if scan_boundary > page_size and len(out) < max_records:
        probe_records, stable_offset, majority_fresh, probed_offsets = _probe_varietywise_pages(
            VARIETYWISE_RESOURCE_ID, since_date, effective_total, page_size, extra,
        )
        out.extend(probe_records)

        if not majority_fresh:
            _record_stale_detection(VARIETYWISE_RESOURCE_ID)
            _quarantine_stale_range(
                VARIETYWISE_RESOURCE_ID,
                max_stale_offset=probe_from_offset + stable_offset,
            )
            return out

        _reset_circuit(VARIETYWISE_RESOURCE_ID)

        offset = stable_offset
        while len(out) < max_records:
            if offset >= scan_boundary:
                break
            if offset in probed_offsets:
                offset += page_size
                continue
            try:
                data = fetch_page_for_resource(
                    VARIETYWISE_RESOURCE_ID, offset=probe_from_offset + offset,
                    limit=page_size, extra_params=extra,
                )
            except Exception as e:
                logger.warning(f"Variety-wise sequential fetch failed at offset {offset}: {e}")
                break
            records = data.get("records", [])
            if not records:
                break
            for r in records:
                rec = normalize_price_record(r)
                rec["_source"] = {
                    "source_type": "varietywise",
                    "source_name": "data.gov.in variety-wise prices archive",
                    "resource_id": VARIETYWISE_RESOURCE_ID,
                }
                ad = rec.get("arrival_date")
                if ad:
                    try:
                        d = datetime.datetime.strptime(str(ad)[:10], "%Y-%m-%d").date()
                    except ValueError:
                        d = None
                    if d is not None and d >= since_date:
                        out.append(rec)
                else:
                    out.append(rec)
                if len(out) >= max_records:
                    break
            offset += page_size
            time.sleep(0.3)

    logger.info(
        "Variety-wise supplement: collected %d recent rows (since %s) from %d total.",
        len(out), since, total,
    )
    return out
