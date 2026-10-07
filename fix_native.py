import re
with open('hydra_cli/native_tools.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('},\\n            {', '},\n            {')

with open('hydra_cli/native_tools.py', 'w', encoding='utf-8') as f:
    f.write(text)
