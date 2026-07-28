"""Find and fix the state filter bug."""
c = open("C:\\Users\\sibap\\Downloads\\MandiIQ\\mandi_rdd\\analysis\\rdd_engine.py", encoding="utf-8").readlines()

# Find lines with state filtering
for i, line in enumerate(c):
    if 'price_df["state"] == state' in line or "price_df['state'] == state" in line:
        print(f"Line {i}: {line.rstrip()}")
        # Show context: the block around line 393
        for j in range(max(0,i-5), min(len(c), i+10)):
            print(f"  {j}: {c[j].rstrip()}")
