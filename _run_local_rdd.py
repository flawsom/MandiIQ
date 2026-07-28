"""Run RDD locally using existing rainfall data."""
import sys
sys.path.insert(0, '.')

from mandi_rdd.storage.duckdb_store import get_connection, save_rdd_result
from mandi_rdd.analysis.rdd_engine import run_rdd

conn = get_connection()
commodities = ['Onion', 'Tomato', 'Potato', 'Cabbage', 'Cauliflower']

for c in commodities:
    try:
        result = run_rdd(conn, commodity=c)
        if result and result.get('effect') is not None:
            save_rdd_result(conn, result)
            print(f"RDD {c}: effect={result['effect']:.2f} p={result.get('p_value')}")
        else:
            print(f"RDD {c}: no result")
    except Exception as e:
        print(f"RDD {c}: ERROR {e}")

n = conn.execute('SELECT COUNT(*) FROM rdd_results').fetchone()[0]
print(f"Total RDD results: {n}")
conn.close()
