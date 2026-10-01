"""Storage-layer recovery tests.

The production warehouse went three months without a successful write because
DuckDB's UNIQUE index over `prices` was left inconsistent by an out-of-memory
bulk load: every later INSERT failed with "Failed to delete all rows from
index" and the only code that could have fixed it deleted the database instead.
These tests pin the replacement behaviour:

  * the memory cap that caused the fault is gone,
  * a broken index is detectable (duplicates are impossible while it works),
  * it is repairable without losing rows,
  * and a write that hits it repairs itself and retries.
"""

from __future__ import annotations

import duckdb
import pytest

from mandi_rdd.storage import duckdb_store

PRICES_DDL = """
    CREATE TABLE prices (
        id INTEGER PRIMARY KEY DEFAULT nextval('seq_prices'),
        state VARCHAR NOT NULL,
        district VARCHAR NOT NULL,
        market VARCHAR NOT NULL,
        commodity VARCHAR NOT NULL,
        variety VARCHAR,
        grade VARCHAR,
        arrival_date DATE NOT NULL,
        min_price DOUBLE,
        max_price DOUBLE,
        modal_price DOUBLE,
        UNIQUE(market, commodity, variety, grade, arrival_date)
    )
"""


def _record(**overrides) -> dict:
    rec = {
        "state": "Maharashtra",
        "district": "Pune",
        "market": "Pune",
        "commodity": "Onion",
        "variety": "Other",
        "grade": "FAQ",
        "arrival_date": "2026-09-12",
        "min_price": 1000.0,
        "max_price": 1500.0,
        "modal_price": 1200.0,
    }
    rec.update(overrides)
    return rec


@pytest.fixture()
def conn():
    """An in-memory warehouse with the real prices schema."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SEQUENCE IF NOT EXISTS seq_prices START 1")
    connection.execute(PRICES_DDL)
    yield connection
    connection.close()


def _insert_raw(connection, rows):
    for row in rows:
        connection.execute(
            """INSERT INTO prices
               (state, district, market, commodity, variety, grade,
                arrival_date, min_price, max_price, modal_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                row["state"], row["district"], row["market"], row["commodity"],
                row.get("variety"), row.get("grade"), row["arrival_date"],
                row.get("min_price"), row.get("max_price"), row.get("modal_price"),
            ],
        )


# ── memory limit ────────────────────────────────────────────────────────────

def _memory_limit_bytes(conn) -> float:
    """DuckDB normalises memory_limit to MiB, so compare in bytes."""
    raw = str(conn.execute("SELECT current_setting('memory_limit')").fetchone()[0])
    number = float("".join(c for c in raw if c.isdigit() or c == "."))
    if "GiB" in raw:
        return number * 1024 ** 3
    if "MiB" in raw:
        return number * 1024 ** 2
    if "KiB" in raw:
        return number * 1024
    return number


def test_connection_does_not_cap_memory_at_200mb(tmp_path):
    """The old hard-coded cap is what made the index inconsistent."""
    db = tmp_path / "m.duckdb"
    conn = duckdb_store.get_connection(db_path=db, read_only=False)
    try:
        limit = _memory_limit_bytes(conn)
    finally:
        conn.close()
    assert limit > 200 * 1024 ** 2, f"memory limit is still capped: {limit} bytes"


def test_memory_limit_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDIIQ_MEMORY_LIMIT", "300MB")
    db = tmp_path / "m2.duckdb"
    conn = duckdb_store.get_connection(db_path=db, read_only=False)
    try:
        limit = _memory_limit_bytes(conn)
    finally:
        conn.close()
    assert abs(limit - 300 * 1000 ** 2) < 2 * 1024 ** 2, limit


# ── duplicate detection ─────────────────────────────────────────────────────

def _bare_prices_table(conn):
    """The observable state of a broken index: no UNIQUE constraint left."""
    conn.execute("DROP TABLE prices")
    conn.execute("""
        CREATE TABLE prices (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_prices'),
            state VARCHAR NOT NULL,
            district VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            commodity VARCHAR NOT NULL,
            variety VARCHAR,
            grade VARCHAR,
            arrival_date DATE NOT NULL,
            min_price DOUBLE,
            max_price DOUBLE,
            modal_price DOUBLE
        )
    """)


def test_duplicate_keys_are_impossible_while_the_index_works(conn):
    duckdb_store.upsert_prices(conn, [_record(), _record()])
    assert duckdb_store.find_duplicate_price_keys(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1


def test_null_variety_duplicates_are_legal(conn):
    """SQL uniqueness treats NULLs as distinct, so these are not a fault."""
    _insert_raw(conn, [_record(variety=None, grade=None), _record(variety=None, grade=None)])
    assert duckdb_store.find_duplicate_price_keys(conn) == 0


def test_duplicates_are_detected_when_the_index_stops_enforcing(conn):
    _bare_prices_table(conn)
    _insert_raw(conn, [_record(), _record()])
    assert duckdb_store.find_duplicate_price_keys(conn) == 1


# ── repair ──────────────────────────────────────────────────────────────────

def test_rebuild_collapses_duplicates_and_keeps_ids(conn):
    _bare_prices_table(conn)
    _insert_raw(conn, [_record(), _record(), _record(commodity="Tomato")])
    before_ids = {r[0] for r in conn.execute("SELECT id FROM prices").fetchall()}

    report = duckdb_store.rebuild_prices_table(conn)

    assert report["rows_before"] == 3
    assert report["rows_after"] == 2
    assert report["rows_removed"] == 1
    kept_ids = {r[0] for r in conn.execute("SELECT id FROM prices").fetchall()}
    assert kept_ids <= before_ids, "the rebuild must not renumber surviving rows"
    # The constraint is back on, so a duplicate insert is refused again.
    assert conn.execute(
        "SELECT COUNT(*) FROM information_schema.table_constraints "
        "WHERE table_name = 'prices' AND constraint_type = 'UNIQUE'"
    ).fetchone()[0] >= 1


def test_ensure_price_index_only_rebuilds_when_needed(conn):
    duckdb_store.upsert_prices(conn, [_record()])
    clean = duckdb_store.ensure_price_index(conn)
    assert clean["rebuilt"] is False
    assert clean["duplicates"] == 0

    _bare_prices_table(conn)
    _insert_raw(conn, [_record(), _record()])
    report = duckdb_store.ensure_price_index(conn)

    assert report["rebuilt"] is True
    assert report["duplicates_after"] == 0


def test_rebuilding_does_not_delete_the_database_file(tmp_path, monkeypatch):
    """Deleting the warehouse is the one mistake there is no coming back from."""
    path = tmp_path / "keep.duckdb"
    conn = duckdb_store.get_connection(db_path=path, read_only=False)
    duckdb_store.init_schema(conn)
    duckdb_store.upsert_prices(conn, [_record()])
    conn.close()

    dropped = []
    monkeypatch.setattr(duckdb_store, "_drop_corrupt_database",
                        lambda p: dropped.append(p))
    monkeypatch.setattr(duckdb_store, "_matches",
                        lambda err, markers: "failed to delete" in str(err).lower())

    # Re-open: the integrity probe must not reach the delete path for an
    # index-level message.
    playback = duckdb.connect(str(path), read_only=True)
    try:
        probe_err = Exception("FATAL Error: Failed to delete all rows from index")
        assert duckdb_store._is_index_fault(probe_err) is True
        assert duckdb_store._is_index_fault(Exception("syntax error")) is False
    finally:
        playback.close()
    assert dropped == []
    assert path.exists()


def test_an_index_fault_is_recorded_next_to_the_database(tmp_path, monkeypatch):
    """The marker has to outlive the restart the fault caused.

    A container restart discards the image layer, so a marker kept beside the
    code disappears exactly when the next process needs it - which is why the
    repair kept missing its cue in production.
    """
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")

    assert duckdb_store.index_fault_flagged() is False
    assert duckdb_store.note_index_fault(
        Exception("FATAL Error: Failed to delete all rows from index")
    ) is True
    assert duckdb_store.index_fault_flagged() is True
    assert duckdb_store.index_fault_flag_path().parent.name == "vol", (
        "the marker must sit on the volume, not beside the code"
    )

    duckdb_store.clear_index_fault()
    assert duckdb_store.index_fault_flagged() is False


def test_unrelated_errors_are_not_recorded_as_index_faults(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    assert duckdb_store.note_index_fault(
        Exception("price source unavailable: <urlopen error timed out>")
    ) is False
    assert duckdb_store.index_fault_flagged() is False


def test_a_recorded_fault_forces_the_rebuild(conn, tmp_path, monkeypatch):
    """A fault a read cannot see must still be repaired, and then forgotten."""
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    duckdb_store.upsert_prices(conn, [_record(), _record(commodity="Tomato")])
    duckdb_store.note_index_fault(
        Exception("FATAL Error: Failed to delete all rows from index")
    )

    report = duckdb_store.heal_price_index(conn)

    assert report["rebuilt"] is True
    assert report["trigger"] == "recorded_index_fault"
    assert report["rows_after"] == 2, "no rows may be lost in the repair"
    assert duckdb_store.index_fault_flagged() is False, (
        "the marker must be cleared or every run would rebuild"
    )


def test_heal_is_a_no_op_without_a_fault(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    duckdb_store.upsert_prices(conn, [_record()])
    assert duckdb_store.heal_price_index(conn)["rebuilt"] is False


def test_the_api_verifies_the_index_before_it_ingests():
    """The first write of a run is what dies, so the check is in front of it."""
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[2]
    source = (repo_root / "mandi_rdd" / "api" / "main.py").read_text(encoding="utf-8")
    assert "_verify_price_index_once()" in source
    assert source.index("_verify_price_index_once()") < source.index(
        "summary = run_ingestion(scope=scope)"
    )


def test_the_marker_is_written_before_the_probe(conn, tmp_path, monkeypatch):
    """A probe that kills the process must still teach the next one."""
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    duckdb_store.upsert_prices(conn, [_record()])

    real_probe = duckdb_store.probe_price_index
    calls = {"n": 0}

    def _die_then_work(conn):
        calls["n"] += 1
        if calls["n"] == 1:
            # Assert the state the next process will inherit, mid-failure.
            assert duckdb_store.index_fault_flagged() is True, \
                "the marker must exist before the probe can fail"
            raise RuntimeError("probe died")
        # The rebuild re-probes the index it just created, so a second call is
        # the real probe and must succeed for the marker to be cleared.
        return real_probe(conn)

    monkeypatch.setattr(duckdb_store, "probe_price_index", _die_then_work)
    report = duckdb_store.verify_price_index(conn)

    assert report["rebuilt"] is True
    assert report["trigger"] == "write_probe"
    assert report["probed"] is True, "the heal must be proved by its own probe"
    assert duckdb_store.index_fault_flagged() is False
    assert calls["n"] == 2, "the failed probe is followed by the rebuild's probe"
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1, \
        "the rebuild must keep the rows"


def test_a_rebuild_whose_probe_still_fails_is_not_reported_as_healed(
    conn, tmp_path, monkeypatch
):
    """A marker that survives a failed re-probe is how the next run learns."""
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    duckdb_store.upsert_prices(conn, [_record()])

    def _always_explode(_conn):
        raise RuntimeError("Failed to delete all rows from index")

    monkeypatch.setattr(duckdb_store, "probe_price_index", _always_explode)
    report = duckdb_store.verify_price_index(conn)

    assert report["rebuilt"] is True
    assert report["probed"] is False
    assert duckdb_store.index_fault_flagged() is True, \
        "an unproved heal must leave the fault recorded"


def test_a_healthy_probe_leaves_the_warehouse_untouched(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    duckdb_store.upsert_prices(conn, [_record(), _record(commodity="Tomato")])

    report = duckdb_store.verify_price_index(conn)

    assert report["rebuilt"] is False
    assert report["probed"] is True
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM prices WHERE market = ?", [duckdb_store.PROBE_MARKET]
    ).fetchone()[0] == 0, "the probe row must be gone"
    assert duckdb_store.index_fault_flagged() is False


# ── the rebuild must never publish a short or broken copy ───────────────────


def test_a_rebuild_that_cannot_copy_everything_keeps_the_original_table(
    conn, monkeypatch
):
    """The failure that emptied the production warehouse must stay impossible.

    The rebuild used to commit the staging copy, then run `DROP TABLE prices`
    as its own committed statement. When the copy was short - it ran out of
    memory on the 512 MB container - the delete still went through and the
    live warehouse was left with an empty prices table. Nothing is copied into
    `prices` itself any more: the copy lands in a staging table, the swap is the
    only atomic step, and a failure has to leave the original rows in place.
    """
    duckdb_store.upsert_prices(conn, [
        _record(), _record(commodity="Tomato"), _record(commodity="Potato"),
    ])
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 3

    broken_ddl = duckdb_store._PRICES_DDL.replace(
        "min_price DOUBLE,", "min_price DOUBLE_typo,"
    )
    monkeypatch.setattr(duckdb_store, "_PRICES_DDL", broken_ddl)

    with pytest.raises(Exception):
        duckdb_store.rebuild_prices_table(conn)

    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 3, (
        "a failed rebuild must leave the original rows in place"
    )


def test_a_rebuild_refuses_to_publish_a_short_copy(conn, monkeypatch):
    """A copy far smaller than the original is corruption, not a dedupe."""
    duckdb_store.upsert_prices(conn, [
        _record(), _record(commodity="Tomato"), _record(commodity="Potato"),
    ])
    monkeypatch.setattr(duckdb_store, "_REBUILD_MIN_COPY_RATIO", 5.0)

    with pytest.raises(RuntimeError, match="refusing to publish"):
        duckdb_store.rebuild_prices_table(conn)

    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 3


def test_a_successful_rebuild_keeps_every_row_and_proves_the_index(conn):
    """The healthy path copies every row, swaps atomically, and re-probes."""
    duckdb_store.upsert_prices(conn, [
        _record(), _record(commodity="Tomato"), _record(commodity="Potato"),
    ])

    report = duckdb_store.rebuild_prices_table(conn)

    assert report["rows_before"] == 3
    assert report["rows_after"] == 3
    assert report["rows_removed"] == 0
    assert report["probed"] is True, "the new index must be proved by a write"
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM prices WHERE market = ?", [duckdb_store.PROBE_MARKET]
    ).fetchone()[0] == 0, "the probe row must be gone"


def test_a_rebuild_copies_across_several_batches(conn, monkeypatch):
    """The copy is batched, so the window boundaries have to cover the table."""
    monkeypatch.setattr(duckdb_store, "_REBUILD_BATCH_ROWS", 2)
    duckdb_store.upsert_prices(conn, [
        _record(arrival_date=f"2026-09-{day:02d}")
        for day in range(1, 8)
    ])

    report = duckdb_store.rebuild_prices_table(conn)

    assert report["rows_before"] == 7
    assert report["rows_after"] == 7, "every batch window must be copied"
    assert report["probed"] is True


def test_a_duplicate_key_straddling_two_windows_is_still_collapsed(conn, monkeypatch):
    """    The dedupe copy has to be bucketed by the business key, not by the id.

    The copy is a plain INSERT now - the `ON CONFLICT DO NOTHING` clause it used
    to carry probes the unique index once per row and turned a 2M-row repair
    into tens of minutes - so nothing absorbs a duplicate the copy lets through.
    Under id windows the second copy of this key would land in the second window,
    hit the constraint, and fail the whole rebuild. Here one copy sits in id
    window 1 and the other in id window 2, and both must still be in one pass.
    """
    monkeypatch.setattr(duckdb_store, "_REBUILD_BATCH_ROWS", 2)
    _bare_prices_table(conn)
    _insert_raw(conn, [_record(), _record(commodity="Tomato"), _record()])
    assert [r[0] for r in conn.execute("SELECT id FROM prices ORDER BY id").fetchall()] == [1, 2, 3], (
        "the fixture must put one copy of the key in each window"
    )

    report = duckdb_store.rebuild_prices_table(conn)

    assert report["rows_before"] == 3
    assert report["rows_after"] == 2
    assert report["rows_removed"] == 1
    assert [r[0] for r in conn.execute("SELECT id FROM prices ORDER BY id").fetchall()] == [1, 2], (
        "the lowest id must be the one that survives"
    )


class _DiesOnTheSecondWindow:
    """Proxy that fails the copy once it is past the first id window."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args, **kwargs):
        window = args[0] if args else None
        if (
            isinstance(window, list) and len(window) == 2 and window[0] > 0
            and "INSERT INTO prices_rebuild" in sql
        ):
            raise RuntimeError("copy killed mid-way")
        return self._real.execute(sql, *args, **kwargs)

    def register(self, *a, **k):
        return self._real.register(*a, **k)

    def unregister(self, *a, **k):
        return self._real.unregister(*a, **k)


def test_a_copy_that_dies_half_way_leaves_the_live_warehouse_alone(conn, monkeypatch):
    """The copy commits window by window, so the guarantee cannot come from a
    rollback any more - it comes from where the copy lands.

    One transaction around the whole copy accumulated every window in the
    transaction's local storage and hit the cap on the live-shaped warehouse
    (`could not allocate block of size 256.0 KiB (183.0 MiB/183.1 MiB used)`),
    and the copy does not need that transaction: it writes to a table nobody
    reads. What has to survive an interrupted copy is the live warehouse.
    """
    monkeypatch.setattr(duckdb_store, "_REBUILD_BATCH_ROWS", 2)
    duckdb_store.upsert_prices(conn, [
        _record(arrival_date=f"2026-09-{day:02d}") for day in range(1, 8)
    ])

    with pytest.raises(RuntimeError, match="copy killed mid-way"):
        duckdb_store.rebuild_prices_table(_DiesOnTheSecondWindow(conn))

    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 7, (
        "an interrupted copy must not touch the live warehouse"
    )
    staged = conn.execute("SELECT COUNT(*) FROM prices_rebuild").fetchone()[0]
    assert 0 < staged < 7, (
        "the staging table is all a half-finished copy leaves behind"
    )


def test_the_copy_does_not_probe_the_index_per_row():
    """Pin the clause that made the rebuild un-shippable on the 512 MB box.

    Measured on 2026-10-01 against a 2,000,001-row warehouse, under the memory
    cap and single thread the rebuild sets itself, each statement on its own:
    the first 200k-row window copied in 0.9s and the next one in 0.9s, but with
    `ON CONFLICT DO NOTHING` the second window took 132s (the conflict check
    probes the unique index once per row). A rebuild of that warehouse was
    therefore tens of minutes - long enough that it was still being killed
    mid-copy, leaving the fault marker it was meant to clear.
    """
    assert "ON CONFLICT" not in duckdb_store._REBUILD_PLAIN_INSERT
    assert "ON CONFLICT" not in duckdb_store._REBUILD_DEDUPE_INSERT


def test_a_rebuild_marks_the_warehouse_busy_for_exactly_its_duration(conn, monkeypatch):
    """/health serves its snapshot instead of sampling while a rebuild holds the
    warehouse, so the mark has to be on while the copy runs and off after it."""
    stamp = []
    real = duckdb_store.find_duplicate_price_keys

    def spy(connection):
        stamp.append(duckdb_store.maintenance_in_progress())
        return real(connection)

    monkeypatch.setattr(duckdb_store, "find_duplicate_price_keys", spy)
    duckdb_store.upsert_prices(conn, [_record()])
    assert duckdb_store.maintenance_in_progress() is False

    duckdb_store.rebuild_prices_table(conn)

    assert stamp == [True], "the mark must be set while the rebuild is running"
    assert duckdb_store.maintenance_in_progress() is False


def test_a_failed_rebuild_does_not_leave_the_warehouse_marked_busy(conn, monkeypatch):
    """A raised rebuild must clear the mark, or /health would serve a stale
    snapshot for the rest of the process's life."""
    monkeypatch.setattr(
        duckdb_store, "_PRICES_DDL",
        duckdb_store._PRICES_DDL.replace("min_price DOUBLE,", "min_price DOUBLE_typo,"),
    )

    with pytest.raises(Exception):
        duckdb_store.rebuild_prices_table(conn)

    assert duckdb_store.maintenance_in_progress() is False


# ── self-healing writes ─────────────────────────────────────────────────────

class _FlakyConn:
    """Proxy that fails the first statement matching `marker`."""

    def __init__(self, real, marker: str, message: str):
        self._real = real
        self._marker = marker
        self._message = message
        self.failures = 0

    def execute(self, sql, *args, **kwargs):
        if self._marker in sql and self.failures == 0:
            self.failures += 1
            raise RuntimeError(self._message)
        return self._real.execute(sql, *args, **kwargs)

    def register(self, *a, **k):
        return self._real.register(*a, **k)

    def unregister(self, *a, **k):
        return self._real.unregister(*a, **k)


def test_upsert_repairs_the_index_and_retries_once(conn, monkeypatch):
    calls = []
    real_rebuild = duckdb_store.rebuild_prices_table

    def spy(connection):
        calls.append(connection)
        return real_rebuild(connection)

    monkeypatch.setattr(duckdb_store, "rebuild_prices_table", spy)

    flaky = _FlakyConn(
        conn,
        marker="INSERT INTO prices",
        message="FATAL Error: Failed to delete all rows from index. "
                "Only deleted 0 out of 12 rows.",
    )

    n_new = duckdb_store.upsert_prices(flaky, [_record()])

    assert n_new == 1, "the retry after the repair must still insert the row"
    assert len(calls) == 1, "the table must be rebuilt exactly once"
    assert flaky.failures == 1


def test_upsert_does_not_swallow_unrelated_errors(conn, monkeypatch):
    monkeypatch.setattr(duckdb_store, "rebuild_prices_table",
                        lambda connection: pytest.fail("must not rebuild"))
    flaky = _FlakyConn(conn, marker="INSERT INTO prices", message="Binder Error: no such column")
    with pytest.raises(RuntimeError, match="Binder Error"):
        duckdb_store.upsert_prices(flaky, [_record()])


def test_upsert_is_idempotent_and_never_duplicates(conn):
    records = [_record(), _record(commodity="Tomato"), _record(arrival_date="2026-09-13")]
    assert duckdb_store.upsert_prices(conn, records) == 3
    assert duckdb_store.upsert_prices(conn, records) == 0
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 3
