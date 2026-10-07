import re

with open("hydra_cli/agent.py", "r", encoding="utf-8") as f:
    text = f.read()

t_input = """        try:
            sys.stdout.write(prompt_label)
            sys.stdout.flush()
            try:
                first_line = input('')
            except (EOFError, StopIteration):
                break
            lines = [first_line]
            
            if first_line.strip():
                while True:
                    try:
                        next_line = input("... ")
                    except (EOFError, StopIteration):
                        break
                    if not next_line.strip():
                        active_steer_mode = True
                        sys.stdout.write(f"\\n{c_bright}[Steering directive engaged: step-by-step confirmation active]{c_reset}\\n")
                        sys.stdout.flush()
                        break
                    lines.append(next_line)
                    
            line = "\\n".join(lines).strip()
            last_interrupt_time = 0.0"""
r_input = """        try:
            sys.stdout.write(prompt_label)
            sys.stdout.flush()
            try:
                line = input('')
            except (EOFError, StopIteration):
                break
                
            if not line.strip():
                try:
                    second = input("... ")
                    if not second.strip():
                        active_steer_mode = True
                        sys.stdout.write(f"\\n{c_bright}[Steering directive engaged: step-by-step confirmation active]{c_reset}\\n")
                        sys.stdout.flush()
                except (EOFError, StopIteration):
                    break
                continue
                
            last_interrupt_time = 0.0"""
text = text.replace(t_input, r_input)

with open("hydra_cli/agent.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Patch9 applied.")
