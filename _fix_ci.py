import re

with open(".github/workflows/ci.yml", "r", newline="", encoding="utf-8") as f:
    content = f.read()

# 1. Add lfs: true to the validate-all job checkout
old = "    steps:\n      - uses: actions/checkout@v4\n\n      - name: Validate render.yaml + DuckDB"
new = "    steps:\n      - uses: actions/checkout@v4\n        with:\n          lfs: true\n\n      - name: Validate render.yaml + DuckDB"
content = content.replace(old, new, 1)

# 2. Update test commands to only run existing tests
old = "          python -m pytest mandi_rdd/tests/test_ingestion.py mandi_rdd/tests/test_rdd_engine.py mandi_rdd/tests/test_no_mock_data.py -v --tb=short"
new = "          python -m pytest mandi_rdd/tests/ -v --tb=short  # run all existing tests"
content = content.replace(old, new, 1)

# 3. Also the duplicate test run in the daily-ingest section
old = "          python -m pytest mandi_rdd/tests/test_no_mock_data.py -v --tb=short || echo \"No mock-data gate — skipped\""
new = "          python -m pytest mandi_rdd/tests/test_verification.py -v --tb=short || echo \"Verification test — skipped\""
content = content.replace(old, new, 1)

with open(".github/workflows/ci.yml", "w", newline="", encoding="utf-8") as f:
    f.write(content)
print("ci.yml updated")
