import re

with open('hydra_cli/agent.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('sys.stdout.write(prompt_box + "\n")\n        try:\n            line = input("> ").strip()\n', 'sys.stdout.write(prompt_box + "\\n")\n        try:\n            line = input("> ").strip()\n')
text = text.replace('sys.stdout.write(f"{c_mid}[Session compacted: {res[\'summary\']}]{c_reset}\n")\n', 'sys.stdout.write(f"{c_mid}[Session compacted: {res[\'summary\']}]{c_reset}\\n")\n')
text = text.replace('sys.stdout.write(f"{c_mid}[No active ledger]{c_reset}\n")\n', 'sys.stdout.write(f"{c_mid}[No active ledger]{c_reset}\\n")\n')
text = text.replace('sys.stdout.write(f"{c_mid}[Retrieved {len(res)} turns]{c_reset}\n")\n', 'sys.stdout.write(f"{c_mid}[Retrieved {len(res)} turns]{c_reset}\\n")\n')
text = text.replace('sys.stdout.write(f"\n{wrap_text(answer)}\n\n")\n', 'sys.stdout.write(f"\\n{wrap_text(answer)}\\n\\n")\n')

with open('hydra_cli/agent.py', 'w', encoding='utf-8') as f:
    f.write(text)
