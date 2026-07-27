import re

with open(".github/workflows/mandi_rdd_ci.yml", "r", newline="", encoding="utf-8") as f:
    content = f.read()

# 1. Add lfs: true to the test job checkout
old = "    steps:\n      - uses: actions/checkout@v4\n\n      - name: Set up Python"
new = "    steps:\n      - uses: actions/checkout@v4\n        with:\n          lfs: true\n\n      - name: Set up Python"
content = content.replace(old, new, 1)

# 2. Add lfs: true to the daily-ingest checkout
old = "      - uses: actions/checkout@v4\n        with:\n          token: \${{ secrets.GITHUB_TOKEN }}"
new = "      - uses: actions/checkout@v4\n        with:\n          lfs: true\n          token: \${{ secrets.GITHUB_TOKEN }}"
content = content.replace(old, new, 1)

# 3. Update test file references
old = "          python -m pytest mandi_rdd/tests/test_ingestion.py mandi_rdd/tests/test_rdd_engine.py mandi_rdd/tests/test_no_mock_data.py -v --tb=short"
new = "          python -m pytest mandi_rdd/tests/test_verification.py -v --tb=short"
content = content.replace(old, new, 1)

with open(".github/workflows/mandi_rdd_ci.yml", "w", newline="", encoding="utf-8") as f:
    f.write(content)
print("mandi_rdd_ci.yml updated")
