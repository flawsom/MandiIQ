import subprocess, json, base64

result = subprocess.run(
    ["gh", "api", "repos/flawsom/MandiIQ/contents/.github/workflows/deploy-pages.yml", "--jq", ".content"],
    capture_output=True, text=True
)
b64 = result.stdout.strip()
decoded = base64.b64decode(b64).decode("utf-8")

new = decoded.replace("""    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
""", "")

new = """name: Deploy SEO to GitHub Pages

on:
  push:
    branches: [master]
    paths:
      - "docs/**"
      - ".github/workflows/deploy-pages.yml"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Pages
        uses: actions/configure-pages@v5
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: docs
      - id: deployment
        name: Deploy
        uses: actions/deploy-pages@v4
"""

with open(".github/workflows/deploy-pages.yml", "w", newline="") as f:
    f.write(new)
print("Written")
