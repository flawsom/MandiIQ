"""Add API fallback to executive_overview.py cached data loaders."""

file = 'mandi_rdd/dashboard/pages/executive_overview.py'
with open(file, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace _cached_rdd
old_rdd = '''@st.cache_data(ttl=300, show_spinner=False)
def _cached_rdd(commodity: str):
    try:
        conn = get_connection(read_only=True)
        try:
            return get_latest_rdd(conn, commodity)
        finally:
            conn.close()
    except Exception:
        return None'''

new_rdd = '''@st.cache_data(ttl=300, show_spinner=False)
def _cached_rdd(commodity: str):
    # Try local DuckDB first
    try:
        conn = get_connection(read_only=True)
        try:
            result = get_latest_rdd(conn, commodity)
            if result and result.get("effect") is not None:
                return result
        finally:
            conn.close()
    except Exception:
        pass
    # Fallback to API if local data is empty/missing
    try:
        import requests
        api_base = get_api_base()
        resp = requests.get(f"{api_base}/rdd-result/{commodity}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("effect") is not None:
                return {
                    "commodity": commodity,
                    "effect": data["effect"],
                    "p_value": data.get("p_value"),
                    "std_error": data.get("std_error"),
                    "n_left": data.get("n_left"),
                    "n_right": data.get("n_right"),
                    "bandwidth": 20,
                    "cutoff": -19,
                    "interpretation": data.get("interpretation", ""),
                    "fe_effect": "N/A",
                }
    except Exception:
        pass
    return None'''

content = content.replace(old_rdd, new_rdd)

with open(file, 'w', encoding='utf-8') as f:
    f.write(content)

print("Added API fallback to _cached_rdd")
