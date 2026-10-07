with open('hydra_cli/agent.py', 'r', encoding='utf-8') as f:
    text = f.read()
text = text.replace('(f"You are Hydra', '(f"""You are Hydra')
text = text.replace('all tests pass."),)', 'all tests pass."""),)')
with open('hydra_cli/agent.py', 'w', encoding='utf-8') as f:
    f.write(text)
