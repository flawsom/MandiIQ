"""Check how run_ingestion gets its connection."""
c = open("C:\\Users\\sibap\\Downloads\\MandiIQ\\mandi_rdd\\ingestion\\scheduler.py", encoding="utf-8").readlines()
for i, line in enumerate(c):
    if "get_connection" in line:
        print(f"Line {i}: {line.rstrip()}")
