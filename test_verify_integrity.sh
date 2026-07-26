#!/usr/bin/env bash
# ============================================================
# Behavioral verification of the R2 backup SHA256 integrity check
# ============================================================
# This script simulates the logic from:
#   - nightly-ingest.yml (backup step: sha256sum computation + metrics JSON)
#   - .github/actions/verify-r2-backup/action.yml (full verification pipeline)
#
# It does NOT hit the actual R2 bucket — it simulates the file operations
# locally to validate all edge cases and failure modes.

set -euo pipefail

PASS=0
FAIL=0
TMPDIR="./tmp_verify_test_$$"
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

DB_PATH="$TMPDIR/mandi_iq.duckdb"
METRICS_FILE="$TMPDIR/r2_backup_metrics.json"
DL_FILE="$TMPDIR/verify_mandi_iq.duckdb.gz"
DECOMPRESSED_FILE="$TMPDIR/verify_mandi_iq.duckdb"

# ── Helper: create a realistic DuckDB-like file (10MB) ──
create_db() {
  python3 -c "import os; f=os.open('$DB_PATH', os.O_CREAT|os.O_WRONLY); os.write(f, os.urandom(10*1024*1024)); os.close(f)"
  sha256sum "$DB_PATH" | cut -d' ' -f1
}

# ── Helper: write metrics JSON ──
write_metrics() {
  local sha=$1 epoch=$2
  cat > "$METRICS_FILE" <<EOF
{
  "raw_bytes": 77594624,
  "compressed_bytes": 22900000,
  "compression_pct": 70,
  "sha256": "$sha",
  "timestamp_utc": "2026-07-26T12:00:00Z",
  "timestamp_epoch": $epoch
}
EOF
}

# ── Helper: run the verification logic ──
run_verify() {
  local expected_sha="$1"
  local now_epoch="$2"
  local metrics_epoch="$3"

  # Simulate the action's logic step by step
  local result="ok"
  local msg=""

  # Step 0: metrics file exists check
  if [ ! -f "$METRICS_FILE" ]; then
    echo "RESULT=skip_no_metrics"
    return
  fi

  # Step 1: Read SHA256 from metrics
  local EXPECTED_SHA
  EXPECTED_SHA=$(python3 -c "import json; print(json.load(open('${METRICS_FILE}')).get('sha256', ''))" 2>/dev/null || true)
  if [ -z "$EXPECTED_SHA" ]; then
    echo "RESULT=skip_no_sha256"
    return
  fi

  # Step 2: Freshness check
  local TS_EPOCH
  TS_EPOCH=$(python3 -c "import json; print(json.load(open('${METRICS_FILE}')).get('timestamp_epoch', 0))" 2>/dev/null || true)
  local STALE_CUTOFF=$((now_epoch - 7200))
  if [ "$TS_EPOCH" -lt "$STALE_CUTOFF" ] 2>/dev/null; then
    echo "RESULT=skip_stale"
    return
  fi

  # Step 3: Download check (simulated — file must exist)
  if [ ! -f "$DL_FILE" ]; then
    echo "RESULT=fail_download"
    return
  fi

  # Step 4: Decompress
  if ! gunzip -c "$DL_FILE" > "$DECOMPRESSED_FILE" 2>/dev/null; then
    echo "RESULT=fail_decompress"
    return
  fi

  # Step 5: Size sanity
  local SIZE
  SIZE=$(stat -c%s "$DECOMPRESSED_FILE" 2>/dev/null || echo 0)
  if [ "$SIZE" -lt 1000 ]; then
    echo "RESULT=fail_size"
    return
  fi

  # Step 6: SHA256 comparison
  local ACTUAL_SHA
  ACTUAL_SHA=$(sha256sum "$DECOMPRESSED_FILE" | cut -d' ' -f1)
  if [ "$ACTUAL_SHA" = "$EXPECTED_SHA" ]; then
    echo "RESULT=pass"
  else
    echo "RESULT=fail_sha_mismatch"
  fi
}

# ============================================================
# TEST 1: Happy path — everything matches
# ============================================================
echo "=== TEST 1: Happy path (SHA256 match) ==="
cleanup() { rm -f "$DL_FILE" "$DECOMPRESSED_FILE"; }
cleanup
SHA=$(create_db)
write_metrics "$SHA" "$(date +%s)"
gzip -c "$DB_PATH" > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=pass"; then
  echo "  ✅ PASS"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected pass)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# TEST 2: SHA256 mismatch (simulates silent corruption)
# ============================================================
echo "=== TEST 2: SHA256 mismatch (silent corruption) ==="
cleanup
SHA=$(create_db)
write_metrics "$SHA" "$(date +%s)"
# Create a DIFFERENT file for the "download" (simulates corruption)
gzip -c /dev/urandom > "$DL_FILE" 2>/dev/null || dd if=/dev/zero bs=1024 count=1 2>/dev/null | gzip > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=fail_sha_mismatch"; then
  echo "  ✅ PASS (detected mismatch)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected sha_mismatch, got $RESULT)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# TEST 3: Gzip corruption (gunzip fails)
# ============================================================
echo "=== TEST 3: Gzip corruption ==="
cleanup
SHA=$(create_db)
write_metrics "$SHA" "$(date +%s)"
# Write a corrupt gzip file
echo "not a gzip file" > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=fail_decompress"; then
  echo "  ✅ PASS (detected gzip corruption)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected fail_decompress, got $RESULT)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# TEST 4: Download failure (missing file)
# ============================================================
echo "=== TEST 4: Download failure ==="
cleanup
SHA=$(create_db)
write_metrics "$SHA" "$(date +%s)"
# Don't create DL_FILE — simulates download failure
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=fail_download"; then
  echo "  ✅ PASS (detected download failure)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected fail_download, got $RESULT)"
  FAIL=$((FAIL + 1))
fi

# ============================================================
# TEST 5: Stale metrics file (old timestamp)
# ============================================================
echo "=== TEST 5: Stale metrics file (>2h old) ==="
cleanup
SHA=$(create_db)
write_metrics "$SHA" $(( $(date +%s) - 10800 ))  # 3 hours ago
gzip -c "$DB_PATH" > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "0")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=skip_stale"; then
  echo "  ✅ PASS (detected stale metrics)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected skip_stale, got $RESULT)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# TEST 6: Missing SHA256 field (old format)
# ============================================================
echo "=== TEST 6: Missing SHA256 field ==="
cleanup
SHA=$(create_db)
# Write metrics WITHOUT sha256
cat > "$METRICS_FILE" <<EOF
{
  "raw_bytes": 77594624,
  "compressed_bytes": 22900000,
  "compression_pct": 70,
  "timestamp_utc": "2026-07-26T12:00:00Z",
  "timestamp_epoch": $(date +%s)
}
EOF
gzip -c "$DB_PATH" > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=skip_no_sha256"; then
  echo "  ✅ PASS (gracefully skipped)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected skip_no_sha256, got $RESULT)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# TEST 7: Missing metrics file
# ============================================================
echo "=== TEST 7: Missing metrics file ==="
cleanup
rm -f "$METRICS_FILE"
SHA=$(create_db)
gzip -c "$DB_PATH" > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=skip_no_metrics"; then
  echo "  ✅ PASS (gracefully skipped)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected skip_no_metrics, got $RESULT)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# TEST 8: Partial upload (small file)
# ============================================================
echo "=== TEST 8: Partial upload (tiny gzip < 1000 bytes decompressed) ==="
cleanup
SHA=$(create_db)
write_metrics "$SHA" "$(date +%s)"
# Create a valid gzip file that decompresses to very few bytes
echo "tiny" | gzip > "$DL_FILE"
RESULT=$(run_verify "$SHA" "$(date +%s)" "$(date +%s)")
echo "  Result: $RESULT"
if echo "$RESULT" | grep -q "RESULT=fail_size"; then
  echo "  ✅ PASS (detected partial upload by size)"
  PASS=$((PASS + 1))
else
  echo "  ❌ FAIL (expected fail_size, got $RESULT)"
  FAIL=$((FAIL + 1))
fi
cleanup

# ============================================================
# SUMMARY
# ============================================================
echo ""
echo "========================================"
echo "  RESULTS: $PASS passed / $FAIL failed"
echo "========================================"
if [ "$FAIL" -eq 0 ]; then
  echo "  ✅ ALL TESTS PASSED"
else
  echo "  ❌ SOME TESTS FAILED"
fi

rm -rf "$TMPDIR"
exit $FAIL
