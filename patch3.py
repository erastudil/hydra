import re, os
with open('hydra_cli/agent.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Imports and docstring
t1 = '"""\nAutonomous ReAct agent execution loop with MCP tool calling, native coding tools, and multi-turn state.\nZero external dependencies.\n"""'
r1 = '"""\nAutonomous ReAct agent execution loop with MCP tool calling, native coding tools, and multi-turn state.\nZero external dependencies.\n"""\nfrom hydra_cli.context import SessionContextLedger\nfrom hydra_cli.ui import render_prompt_box, wrap_text\nfrom hydra_cli._version import __version__'
if t1 in text:
    text = text.replace(t1, r1)
elif 'from hydra_cli.context import SessionContextLedger' not in text:
    text = r1 + "\n" + text.split('"""', 2)[-1]

# 2. System prompt
# We will use re.sub to replace the DEFAULT_AGENT_SYSTEM_PROMPT completely.
new_prompt = 'DEFAULT_AGENT_SYSTEM_PROMPT = os.environ.get(\n    "HYDRA_AGENT_SYSTEM_PROMPT",\n    (\n        f"You are Hydra, an elite autonomous software engineering agent operating with the autonomy, precision, and multi-turn execution rigor of Cursor and Antigravity (v{__version__}).\\n"\n        "Your mission is to solve coding tasks, refactorings, bug fixes, and implementations end-to-end with verifiable empirical proof.\\n\\n"\n        "OPERATIONAL WORKFLOW (Autonomous ReAct Loop):\\n"\n        "1. Inspect: Systematically explore the workspace before making changes. Use grep_search, find_files, list_dir, and read_file to inspect files, locate symbols, and understand existing patterns.\\n"\n        "2. Plan: Formulate a minimal, deterministic plan. Identify invariants, dependencies, and potential side effects.\\n"\n        "3. Implement: Apply surgical changes using edit_file or write_file. Prefer minimal, clean diffs that preserve existing code conventions and formatting.\\n"\n        "4. Verify: Always run tests, type checks, or build commands using run_command to verify changes before declaring completion. Exit code 0 is passing; unverified assertions carry zero truth value.\\n\\n"\n        "CONTEXT LEDGER & STEERING:\\n"\n        "- The system maintains a rigorous context ledger of your execution history. Commands like /compact and /retrieve manipulate this state.\\n"\n        "- The user may interrupt your execution or provide in-flight steering directives. You must immediately realign your plan to these directives.\\n\\n"\n        "TOOL USAGE INSTRUCTIONS:\\n"\n        "- Proactively invoke tools at every turn to inspect and modify state: read_file, edit_file, write_file, run_command, grep_search, find_files, list_dir.\\n"\n        "- Never guess or hallucinate file contents or test results. Always read files before modifying them.\\n"\n        "- Use edit_file for precise, scoped search-and-replace modifications with unique old_text.\\n"\n        "- Use write_file for creating new files or complete rewrites.\\n"\n        "- Use run_command to execute test suites, linters, and verification scripts.\\n"\n        "- Drive tasks to verifiable completion. Do not halt prematurely, do not leave placeholders, stubs, or TODO comments.\\n"\n        "- If an execution step fails, analyze the error output and iteratively fix the issue until all tests pass."\n    ),\n)'
text = re.sub(r'DEFAULT_AGENT_SYSTEM_PROMPT = os\.environ\.get\([^)]+\),\n\)', new_prompt, text, flags=re.DOTALL)

# 3. Commands
text = text.replace('"/tokens",\n        "/undo",', '"/tokens",\n        "/undo",\n        "/compact",\n        "/retrieve",')

# 4. Native reg
t4 = '''            native_reg = NativeToolRegistry(
                cwd=target_cwd,
                subagent_depth=subagent_depth,
                tier=resolved_tier or None,
                alias=actual_alias,
            )'''
r4 = '''            native_reg = NativeToolRegistry(
                cwd=target_cwd,
                subagent_depth=subagent_depth,
                tier=resolved_tier or None,
                alias=actual_alias,
            )
            if hasattr(native_reg, "ledger"):
                native_reg.ledger = ledger'''
text = text.replace(t4, r4)

t5 = '''    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": prompt},
    ]'''
r5 = '''    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": prompt},
    ]
    ledger.append_turn(role="user", content=prompt)'''
text = text.replace(t5, r5)

t6 = '''        if not tool_calls:
            # Model emitted final answer -> statechart transition to COMPLETED'''
r6 = '''        if tool_calls:
            ledger.append_turn(
                role="assistant",
                content=message.get("content") or "",
                thought=message.get("reasoning_content") or message.get("thought"),
                tool_calls=tool_calls
            )

        if not tool_calls:
            # Model emitted final answer -> statechart transition to COMPLETED'''
text = text.replace(t6, r6)

t7 = '''        if steering_directive:
            directive_msg = {
                "role": "user",
                "content": f"[IN-FLIGHT STEERING DIRECTIVE]: {steering_directive}",
            }
            messages.append(directive_msg)
            current_turn_msgs.append(directive_msg)

        turn_groups.append(current_turn_msgs)'''
r7 = '''        if steering_directive:
            directive_msg = {
                "role": "user",
                "content": f"[IN-FLIGHT STEERING DIRECTIVE]: {steering_directive}",
            }
            messages.append(directive_msg)
            current_turn_msgs.append(directive_msg)

        if tool_executions:
            ledger.append_turn(
                role="tool",
                content="",
                tool_results=tool_executions
            )
        if steering_directive:
            ledger.append_turn(
                role="user",
                content=f"[IN-FLIGHT STEERING DIRECTIVE]: {steering_directive}"
            )

        turn_groups.append(current_turn_msgs)'''
text = text.replace(t7, r7)

t8 = '''        if not tool_calls:
            # Model emitted final answer -> statechart transition to COMPLETED
            final_content = message.get("content", "") or ""
            checkpointer.final_response = final_content
            checkpointer.transition("COMPLETED")
            return final_content'''
r8 = '''        if not tool_calls:
            # Model emitted final answer -> statechart transition to COMPLETED
            final_content = message.get("content", "") or ""
            checkpointer.final_response = final_content
            checkpointer.transition("COMPLETED")
            ledger.append_turn(
                role="assistant",
                content=final_content,
                thought=message.get("reasoning_content") or message.get("thought")
            )
            return final_content'''
text = text.replace(t8, r8)

t9 = '''    checkpointer.transition("MAX_TURNS")
    last_content = messages[-1].get("content", "Agent loop reached maximum turns without termination.")
    return str(last_content)'''
r9 = '''    checkpointer.transition("MAX_TURNS")
    last_content = messages[-1].get("content", "Agent loop reached maximum turns without termination.")
    ledger.append_turn(role="assistant", content=str(last_content))
    return str(last_content)'''
text = text.replace(t9, r9)

t10 = '''        try:
            line = input("> ").strip()

            last_interrupt_time = 0.0'''
r10 = '''        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            first_line = input()
            lines = [first_line]
            
            if first_line.strip():
                while True:
                    next_line = input("... ")
                    if not next_line.strip():
                        active_steer_mode = True
                        sys.stdout.write(f"\\n{c_bright}[Steering directive engaged: step-by-step confirmation active]{c_reset}\\n")
                        sys.stdout.flush()
                        break
                    lines.append(next_line)
                    
            line = "\\n".join(lines).strip()
            last_interrupt_time = 0.0'''
text = text.replace(t10, r10)

with open('hydra_cli/agent.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("Patch applied.")
