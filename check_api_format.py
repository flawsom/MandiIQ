import sys
import os
sys.path.insert(0, '.')

# Load environment variables
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()

from mandi_rdd.ingestion.fetch_prices import fetch_page_for_resource

api_key = os.getenv('DATA_GOV_IN_API_KEY') or os.getenv('DATA_GOV_API_KEY')
if api_key:
    print(f'API Key loaded from env: {api_key[:10]}...')
else:
    print('API key not set! Exiting.')
    sys.exit(1)

data = fetch_page_for_resource('9ef84268-d588-465a-a308-a864a43d0070', limit=10)
if 'records' in data:
    print('\nSample API records:')
    for i, record in enumerate(data['records'][:5]):
        print(f'\nRecord {i+1}:')
        state = record.get('State', 'N/A')
        district = record.get('District', 'N/A')
        print(f'  State: {state}')
        print(f'  District: {district}')
        if state and district:
            tasty_format = f'{state} - {district}'
            print(f'  State-District format: {tasty_format}')
else:
    print('No records found or API error')