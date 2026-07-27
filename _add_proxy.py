with open("mandi_rdd/api/main.py", "r", newline="", encoding="utf-8") as f:
    content = f.read()

# Add urllib.parse import
content = content.replace(
    "import urllib.request\n",
    "import urllib.request\nimport urllib.parse\n",
    1,
)

# Proxy endpoint
proxy_endpoint = '''
@app.get('/proxy/github/{path:path}', tags=['Proxy'])
async def proxy_github(path: str, request: Request):
    """Proxy requests to GitHub API to avoid CORS issues from browser."""
    query = request.url.query
    github_token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'MandiIQ-API/1.0',
    }
    if github_token:
        headers['Authorization'] = f'Bearer {github_token}'
    url = f'https://api.github.com/{path}'
    if query:
        url += '?' + query
    try:
        req = urllib.request.Request(url, headers=headers, method='GET')
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8')
            return JSONResponse(content=json.loads(body))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode('utf-8'))
        except Exception:
            err_body = {'error': e.reason}
        return JSONResponse(status_code=e.code, content=err_body)
    except Exception as e:
        return JSONResponse(status_code=502, content={'error': str(e)})
'''

# Insert before the __main__ block
idx = content.rfind('if __name__ == "__main__":')
if idx > 0:
    content = content[:idx] + proxy_endpoint + content[idx:]
    with open("mandi_rdd/api/main.py", "w", newline="") as f:
        f.write(content)
    print("OK - proxy endpoint added")
else:
    print("FAIL: __main__ not found")
