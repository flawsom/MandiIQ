with open('.github/workflows/deploy-pages.yml', 'r', newline='') as f:
    content = f.read()

old = ('on:\n'
       '  push:\n'
       '    branches: [master]\n'
       '    paths:\n'
       '      - "docs/**"\n'
       '      - "seo/**"\n'
       '      - "package.json"\n'
       '      - "package-lock.json"')
new = old + '\n  workflow_dispatch:'

if old in content:
    content = content.replace(old, new, 1)
    with open('.github/workflows/deploy-pages.yml', 'w', newline='') as f:
        f.write(content)
    print('OK - added workflow_dispatch')
else:
    print('FAIL - old text not found')
    # Show what's around the trigger section
    import re
    m = re.search(r'off:|on:[^\n]+', content)
    print(repr(content[:500]) if not m else repr(m.group()))
