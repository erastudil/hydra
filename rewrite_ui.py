import os

ui_path = r'C:\Users\jpm05\Documents\hydra\hydra_cli\ui.py'
with open(ui_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_art = """HYDRA_7_HEADS_DETAILED = r\"\"\"
             _.-'-._       _.-'-._       _.-'-._       _.-'-._       _.-'-._       _.-'-._       _.-'-._
            /  _ _  \\     /  _ _  \\     /  _ _  \\     /  _ _  \\     /  _ _  \\     /  _ _  \\     /  _ _  \\
           |  (o|o)  |   |  (o|o)  |   |  (o|o)  |   |  (o|o)  |   |  (o|o)  |   |  (o|o)  |   |  (o|o)  |
           {   >v<   }   {   >v<   }   {   >v<   }   {   >v<   }   {   >v<   }   {   >v<   }   {   >v<   }
            \\ '^^^' /     \\ '^^^' /     \\ '^^^' /     \\ '^^^' /     \\ '^^^' /     \\ '^^^' /     \\ '^^^' /
             )     (       )     (       )     (       )     (       )     (       )     (       )     (
            /       \\     /       \\     /       \\     /       \\     /       \\     /       \\     /       \\
           |   | |   |   |   | |   |   |   | |   |   |   | |   |   |   | |   |   |   | |   |   |   | |   |
           |   | |   |   |   | |   |   |   | |   |   |   | |   |   |   | |   |   |   | |   |   |   | |   |
           \\   \\ /   /   \\   \\ /   /   \\   \\ /   /   \\   \\ /   /   \\   \\ /   /   \\   \\ /   /   \\   \\ /   /
            \\   V   /     \\   V   /     \\   V   /     \\   V   /     \\   V   /     \\   V   /     \\   V   /
             \\     /       \\     /       \\     /       \\     /       \\     /       \\     /       \\     /
              \\   /         \\   /         \\   /         \\   /         \\   /         \\   /         \\   /
               | |           | |           | |           | |           | |           | |           | |
  ~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~^~~
   ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~
\"\"\"\n\nHYDRA_7_HEADS_MONSTER = HYDRA_7_HEADS_DETAILED\n\n"""

# Lines 30 to 71 in 1-based indexing means indices 29 to 71 in 0-based.
# Wait, the prompt says "Lines 30 to 71 contain the two old art blocks".
# Let's find exactly where HYDRA_7_HEADS_DETAILED starts and HYDRA_7_HEADS_MONSTER ends.
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if line.startswith("HYDRA_7_HEADS_DETAILED = r\"\"\""):
        start_idx = i
    if line.startswith("HYDRA_7_HEADS_MONSTER = r\"\"\""):
        # find the next \"\"\"
        for j in range(i+1, len(lines)):
            if lines[j].startswith("\"\"\""):
                end_idx = j + 1
                break
        if end_idx != -1:
            break

if start_idx != -1 and end_idx != -1:
    new_lines = lines[:start_idx] + [new_art] + lines[end_idx:]
else:
    # fallback to 29:71
    new_lines = lines[:29] + [new_art] + lines[71:]

new_funcs = """
import shutil

def wrap_text(text: str, width: Optional[int] = None, indent: str = \"\", subsequent_indent: str = \"\") -> str:
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns

    def ansi_len(s: str) -> int:
        import re
        return len(re.sub(r'\\x1b\\[[0-9;]*m', '', s))

    lines = text.split(\"\\n\")
    wrapped_lines = []
    in_code_block = False
    
    for line in lines:
        if line.strip().startswith(\"```\"):
            in_code_block = not in_code_block
        if in_code_block or line.strip().startswith(\"```\") or line.strip().startswith(\"|\"):
            wrapped_lines.append(line)
            continue
            
        if not line.strip():
            wrapped_lines.append(line)
            continue

        current_line = []
        current_len = 0
        words = line.split(\" \")
        is_first_line = True
        
        for word in words:
            word_len = ansi_len(word)
            current_indent = indent if is_first_line else subsequent_indent
            indent_len = ansi_len(current_indent)
            
            if current_len + word_len + (1 if current_line else 0) > width - indent_len:
                if current_line:
                    wrapped_lines.append(current_indent + \" \".join(current_line))
                    current_line = [word]
                    current_len = word_len
                    is_first_line = False
                else:
                    wrapped_lines.append(current_indent + word)
                    current_line = []
                    current_len = 0
            else:
                current_line.append(word)
                current_len += word_len + (1 if current_line else 0)
                
        if current_line:
            current_indent = indent if is_first_line else subsequent_indent
            wrapped_lines.append(current_indent + \" \".join(current_line))
            
    return \"\\n\".join(wrapped_lines)

def print_wrapped(text: str, width: Optional[int] = None, indent: str = \"\", stream: Optional[Any] = None) -> None:
    target_stream = stream if stream is not None else sys.stdout
    target_stream.write(wrap_text(text, width=width, indent=indent, subsequent_indent=indent) + \"\\n\")
    target_stream.flush()

def render_prompt_box(model: str, version: str, est_tokens: int, max_budget: int, turns: int, queued_steer: Optional[str] = None, width: Optional[int] = None) -> str:
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns

    box_width = width - 2
    if box_width < 10: box_width = 10
    header = f\" {model} v{version} | {est_tokens}/{max_budget} tok | {turns} turns \"
    
    top_border = \"╭\" + header.center(box_width, \"─\") + \"╮\"
    bottom_border = \"╰\" + \"─\" * box_width + \"╯\"
    
    lines = [top_border]
    if queued_steer:
        steer_lines = wrap_text(f\"Steer: {queued_steer}\", width=box_width).split(\"\\n\")
        for line in steer_lines:
            lines.append(\"│ \" + line.ljust(box_width - 1) + \"│\")
        lines.append(\"├\" + \"─\" * box_width + \"┤\")
    
    return \"\\n\".join(lines) + \"\\n\" + bottom_border

def clean_pasted_text(text: str) -> str:
    import re
    text = re.sub(r'\\x1b\\[200~', '', text)
    text = re.sub(r'\\x1b\\[201~', '', text)
    text = text.replace('\\r\\n', '\\n').replace('\\r', '\\n')
    return text
"""

final_text = "".join(new_lines)
if 'def wrap_text' not in final_text:
    final_text += new_funcs

with open(ui_path, 'w', encoding='utf-8') as f:
    f.write(final_text)

