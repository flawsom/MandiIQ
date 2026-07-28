import sys
sys.path.insert(0, '.')
from mandi_rdd.storage.duckdb_store import get_connection
conn = get_connection()

# Check if state column is NULL or empty string
result = conn.execute('SELECT state, district FROM prices WHERE state IS NULL OR length(TRIM(state)) = 0 LIMIT 10').fetchall()
print('Empty state records:')
for r in result:
    print(f'  State: {repr(r[0])} | District: {repr(r[1])}')

# Check if there are any records with state populated
result2 = conn.execute('SELECT state FROM prices WHERE state IS NOT NULL AND length(TRIM(state)) > 0 LIMIT 5').fetchall()
print('\nSample of populated state values:')
for r in result2:
    print(f'  {repr(r[0])}')

# Check district counts
districts = conn.execute('SELECT district, COUNT(*) as cnt FROM prices GROUP BY district ORDER BY cnt DESC LIMIT 10').fetchall()
print('\nTop districts by price count:')
for d, cnt in districts:
    print(f'  {repr(d)}: {cnt} records')

# Query the API records directly to see what the 'district' field looks like
from mandi_rdd.ingestion.daily_fetcher import fetch_and_store_all_daily_prices
conn2 = get_connection()
print('\nTrying to fetch new prices to see what we get:')
try:
    conn2.begin()
    # Just fetch first page to inspect schema
    from mandi_rdd.ingestion.daily_fetcher import fetch_page_for_resource
    data = fetch_page_for_resource('9ef84268-d588-465a-a308-a864a43d0070', limit=10)
    if 'records' in data:
        print('\nSample API records (first 3):')
        for i, record in enumerate(data['records'][:3]):
            print(f'\nRecord {i+1}:')
            print(f'  District: {record.get("District", "N/A")}')
            print(f'  Market: {record.get("Market", "N/A")}')
            print(f'  State: {record.get("State", "N/A")}')
            print(f'  Delicious market/district string: {record.get("Market", "") + " - " + record.get("District", "")}')
except Exception as e:
    print(f'Error fetching API {e}')