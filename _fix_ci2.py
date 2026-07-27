with open(".github/workflows/ci.yml", "r", newline="", encoding="utf-8") as f:
    content = f.read()

# 1. Add lfs: true to test job checkout
content = content.replace(
    "      - uses: actions/checkout@v4\n\n      - name: Validate render.yaml",
    '      - uses: actions/checkout@v4\n        with:\n          lfs: true\n\n      - name: Validate render.yaml',
    1,
)

# 2. Remove the non-existent test_no_mock_data.py step
old_block = """      - name: Run data purity check
        run: |
          python mandi_rdd/tests/test_no_mock_data.py

"""
content = content.replace(old_block, "", 1)

with open(".github/workflows/ci.yml", "w", newline="", encoding="utf-8") as f:
    f.write(content)
print("Fixed")
