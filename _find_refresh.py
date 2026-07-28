"""Find the refresh endpoint in main.py."""
c = open("C:\\Users\\sibap\\Downloads\\MandiIQ\\mandi_rdd\\api\\main.py", encoding="utf-8").readlines()
found = False
for i, line in enumerate(c):
    stripped = line.strip()
    if stripped.startswith("@app.post") and "refresh" in stripped:
        found = True
        print(f"Line {i}: {stripped}")
        for j in range(i+1, min(i+80, len(c))):
            ll = c[j].rstrip()
            print(f"  {j}: {ll}")
            if ll and ll.startswith("@app.") and j > i+1:
                break
            if ll and "async def" in ll:
                # continue showing the function body
                pass
            if ll and ("return" in ll and j > i+3):
                # likely end of function
                pass
        break
if not found:
    print("Refresh endpoint NOT FOUND")
