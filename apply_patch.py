import re

with open("hydra_cli/agent.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Imports
if "clean_pasted_text" not in text:
    text = text.replace("from hydra_cli.ui import render_prompt_box, wrap_text", "from hydra_cli.ui import clean_pasted_text, render_prompt_box, wrap_text")

# 2. DEFAULT_AGENT_SYSTEM_PROMPT
t_prompt_start = 'DEFAULT_AGENT_SYSTEM_PROMPT = os.environ.get('
t_prompt_end = '    ),\n)'
prompt_match = re.search(r'DEFAULT_AGENT_SYSTEM_PROMPT = os\.environ\.get\([^)]+\),\n\)', text, re.DOTALL)
if prompt_match:
    new_prompt = '''DEFAULT_AGENT_SYSTEM_PROMPT = os.environ.get(
    "HYDRA_AGENT_SYSTEM_PROMPT",
    (
        f"You are Hydra v{__version__}, an elite autonomous software engineering agent operating with the autonomy, precision, and multi-turn execution rigor of Cursor and Antigravity.\\n"
        "Your mission is to solve coding tasks, refactorings, bug fixes, and implementations end-to-end with verifiable empirical proof.\\n\\n"
        "OPERATIONAL WORKFLOW (Autonomous ReAct Loop):\\n"
        "1. Inspect: Systematically explore the workspace before making changes. Use grep_search, find_files, list_dir, and read_file to inspect files, locate symbols, and understand existing patterns.\\n"
        "2. Plan: Formulate a minimal, deterministic plan. Identify invariants, dependencies, and potential side effects.\\n"
        "3. Implement: Apply surgical changes using edit_file or write_file. Prefer minimal, clean diffs that preserve existing code conventions and formatting.\\n"
        "4. Verify: Always run tests, type checks, or build commands using run_command to verify changes before declaring completion. Exit code 0 is passing; unverified assertions carry zero truth value.\\n\\n"
        "CONTEXT LEDGER & STEERING:\\n"
        "- The system maintains a rigorous context ledger of your execution history. Commands like /compact and /retrieve manipulate this state.\\n"
        "- The user may interrupt your execution or provide in-flight steering directives. You must immediately realign your plan to these directives.\\n\\n"
        "TOOL USAGE INSTRUCTIONS:\\n"
        "- Proactively invoke tools at every turn to inspect and modify state: read_file, edit_file, write_file, run_command, grep_search, find_files, list_dir.\\n"
        "- Never guess or hallucinate file contents or test results. Always read files before modifying them.\\n"
        "- Use edit_file for precise, scoped search-and-replace modifications with unique old_text.\\n"
        "- Use write_file for creating new files or complete rewrites.\\n"
        "- Use run_command to execute test suites, linters, and verification scripts.\\n"
        "- Drive tasks to verifiable completion. Do not halt prematurely, do not leave placeholders, stubs, or TODO comments.\\n"
        "- If an execution step fails, analyze the error output and iteratively fix the issue until all tests pass."
    ),
)'''
    text = text[:prompt_match.start()] + new_prompt + text[prompt_match.end():]

# 3. wrap_text for thought and preamble
t_thought = '''        if thought_content and str(thought_content).strip():
            sys.stderr.write(f"\\n{c_mid}[Thought]{c_reset} {str(thought_content).strip()}\\n")
            sys.stderr.flush()

        msg_content = message.get("content") or ""
        if msg_content:
            preamble = re.sub(r"<tool_call>.*?</tool_call>", "", msg_content, flags=re.DOTALL | re.IGNORECASE).strip()
            preamble = re.sub(r"```(?:tool_call|tool).*?```", "", preamble, flags=re.DOTALL | re.IGNORECASE).strip()
            if preamble:
                sys.stderr.write(f"\\n{c_mid}{preamble}{c_reset}\\n")
                sys.stderr.flush()'''

r_thought = '''        if thought_content and str(thought_content).strip():
            wrapped = wrap_text(str(thought_content).strip())
            sys.stderr.write(f"\\n{c_mid}[Thought]{c_reset}\\n{c_mid}{wrapped}{c_reset}\\n")
            sys.stderr.flush()

        msg_content = message.get("content") or ""
        if msg_content:
            preamble = re.sub(r"<tool_call>.*?</tool_call>", "", msg_content, flags=re.DOTALL | re.IGNORECASE).strip()
            preamble = re.sub(r"```(?:tool_call|tool).*?```", "", preamble, flags=re.DOTALL | re.IGNORECASE).strip()
            if preamble:
                wrapped_pre = wrap_text(preamble)
                sys.stderr.write(f"\\n{c_mid}{wrapped_pre}{c_reset}\\n")
                sys.stderr.flush()'''
text = text.replace(t_thought, r_thought)

# 4. run_interactive_agent loop
t_loop = '''    while True:
        prompt_label = f"{c_bright}hydra-agent [{active_alias}:{active_tier or 'frontier'}]{c_reset}> "
        try:
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
                
            last_interrupt_time = 0.0'''

r_loop = '''    while True:
        if sys.stdin.isatty():
            sys_len = len(active_system_prompt)
            rules_len = len(rules_msg or "")
            turns_chars = sum(len(p) + len(a) for p, a in turns_history)
            cp = SessionCheckpointer.load(active_session_id)
            scratch_chars = len(cp.scratchpad.format_for_context()) if cp else 0
            total_chars = sys_len + rules_len + turns_chars + scratch_chars
            est_tokens = total_chars // 4
            max_budget = get_context_window(active_alias)
            box = render_prompt_box(model=active_alias, version=__version__, est_tokens=est_tokens, max_budget=max_budget, turns=len(turns_history))
            sys.stdout.write(f"\\n{box}\\n")
            sys.stdout.flush()

        prompt_label = f"{c_bright}hydra-agent [{active_alias}:{active_tier or 'frontier'}]{c_reset}> "
        try:
            sys.stdout.write(prompt_label)
            sys.stdout.flush()
            try:
                line = input('')
            except (EOFError, StopIteration):
                break
                
            line = clean_pasted_text(line).strip()
            
            if not line:
                try:
                    second = input("... ")
                    if not second.strip():
                        active_steer_mode = not active_steer_mode
                        state_str = "active" if active_steer_mode else "disabled"
                        sys.stdout.write(f"\\n{c_bright}[Steering directive mode toggled: step-by-step confirmation {state_str}]{c_reset}\\n")
                        sys.stdout.flush()
                except (EOFError, StopIteration):
                    break
                continue
                
            last_interrupt_time = 0.0'''
text = text.replace(t_loop, r_loop)

with open("hydra_cli/agent.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Patch implemented.")
