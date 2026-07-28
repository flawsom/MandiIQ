"""Find the fetch_varietywise_recent function boundaries."""
c = open("C:\\Users\\sibap\\Downloads\\MandiIQ\\mandi_rdd\\ingestion\\archive_scanner.py", encoding="utf-8").readlines()
start = None
for i, line in enumerate(c):
    if "def fetch_varietywise_recent" in line:
        start = i
        break
print(f"Function starts at line {start} (0-indexed)")
# Find end: next def or class at column 0
next_fn = len(c)
for i in range(start+1, len(c)):
    stripped = c[i].lstrip()
    if stripped.startswith("def ") or stripped.startswith("class "):
        next_fn = i
        break
print(f"Next definition at line {next_fn}")
print(f"Function body is {next_fn - start - 1} lines")
# Show last few lines of function
for i in range(max(start, next_fn-10), next_fn):
    print(f"  {i}: {c[i].rstrip()}")
