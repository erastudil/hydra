import re

with open("hydra_cli/agent.py", "r", encoding="utf-8") as f:
    text = f.read()

t_input = """        try:
            sys.stdout.write(prompt_label)
            sys.stdout.flush()
            first_line = input('')"""
r_input = """        try:
            sys.stdout.write(prompt_label)
            sys.stdout.flush()
            try:
                first_line = input('')
            except (EOFError, StopIteration):
                break"""
text = text.replace(t_input, r_input)

with open("hydra_cli/agent.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Patch8 applied.")
