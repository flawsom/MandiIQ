"""Check run_auto function from ingest_historical_csv."""
c = open("C:\\Users\\sibap\\Downloads\\MandiIQ\\mandi_rdd\\ingestion\\ingest_historical_csv.py", encoding="utf-8").readlines()
for i, line in enumerate(c):
    if "def run_auto" in line:
        print(f"Line {i}: {line.rstrip()}")
        for j in range(i+1, min(i+60, len(c))):
            ll = c[j].rstrip()
            print(f"  {j}: {ll}")
            if ll and ll.startswith("def ") and "run_auto" not in ll:
                break
        break
