import re

with open('hydra_cli/agent.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add ui imports
if 'from hydra_cli.ui import' not in text:
    text = text.replace('from hydra_cli.tool_adapter import adapt_messages_for_prompt_tools, is_tool_unsupported_error', 
                        'from hydra_cli.tool_adapter import adapt_messages_for_prompt_tools, is_tool_unsupported_error\nfrom hydra_cli.ui import render_prompt_box, wrap_text\nfrom hydra_cli.context import SessionContextLedger')

# 1. run_agent_loop modifications
# Inject ledger creation
if 'ledger = SessionContextLedger(' not in text:
    text = text.replace('checkpointer.transition("PLANNING")', 'checkpointer.transition("PLANNING")\n    ledger = SessionContextLedger(clean_id)\n    if hasattr(registry, "ledger"):\n        registry.ledger = ledger')

# Inject token check and compact
# "If context tokens exceed 75% of context window, auto-compact via ledger.compact_session()."
compaction_code = '''
        # Auto-compact if > 75%
        sys_len = len(active_sys_prompt)
        turns_chars = sum(len(t.get("content", "")) for t in ledger.turns)
        est_tokens = (sys_len + turns_chars) // 4
        max_budget = get_context_window(requested_model)
        if est_tokens > max_budget * 0.75:
            ledger.compact_session()
'''
# We need to insert this inside complete_turn() or before it. Let's put it at the beginning of the while loop.
text = text.replace('while turn_idx < max_turns:\n        try:', 'while turn_idx < max_turns:\n' + compaction_code + '\n        try:')

# Record turns
record_turn_code = '''
        # Record turn
        tool_results_list = [msg for msg in tool_executions] if tool_executions else None
        ledger.append_turn(role="assistant", content=message.get("content", ""), thought=None, tool_calls=tool_calls, tool_results=tool_results_list)
'''
# We'll put it right before 	urn_idx += 1
text = text.replace('turn_idx += 1\n        continue', record_turn_code + '\n        turn_idx += 1\n        continue')
# Also at the end of tool execution loop
text = text.replace('turn_idx += 1\n\n    return', record_turn_code + '\n    turn_idx += 1\n\n    return')

# Wrap thought/preamble prints with wrap_text
text = text.replace('sys.stdout.write(f"\\n{c_bright}[THOUGHT]{c_reset}\\n{thought}\\n")', 'sys.stdout.write(f"\\n{c_bright}[THOUGHT]{c_reset}\\n{wrap_text(thought)}\\n")')

# 2. run_interactive_agent modifications
# Use render_prompt_box
# Wrap model response outputs using wrap_text

repl = '''
        sys_len = len(active_system_prompt)
        rules_len = len(rules_msg or "")
        turns_chars = sum(len(p) + len(a) for p, a in turns_history)
        total_chars = sys_len + rules_len + turns_chars
        est_tokens = total_chars // 4
        max_budget = get_context_window(active_alias)
        
        prompt_box = render_prompt_box(
            model=active_alias,
            version=__version__,
            est_tokens=est_tokens,
            max_budget=max_budget,
            turns=len(turns_history)
        )
        sys.stdout.write(prompt_box + "\\n")
        try:
            line = input("> ").strip()
'''
text = re.sub(r'prompt_label = f"\{c_bright\}hydra-agent.*?try:\n            line = input\(prompt_label\)\.strip\(\)', repl, text, flags=re.DOTALL)

# Add /compact and /retrieve commands
cmd_code = '''
            elif cmd == "/compact":
                if hasattr(registry, "ledger") and registry.ledger:
                    res = registry.ledger.compact_session()
                    sys.stdout.write(f"{c_mid}[Session compacted: {res['summary']}]{c_reset}\\n")
                else:
                    sys.stdout.write(f"{c_mid}[No active ledger]{c_reset}\\n")
                sys.stdout.flush()

            elif cmd == "/retrieve":
                if hasattr(registry, "ledger") and registry.ledger:
                    res = registry.ledger.retrieve_verbatim([arg])
                    sys.stdout.write(f"{c_mid}[Retrieved {len(res)} turns]{c_reset}\\n")
                else:
                    sys.stdout.write(f"{c_mid}[No active ledger]{c_reset}\\n")
                sys.stdout.flush()
'''
text = text.replace('elif cmd in ("/help", "/h", "?"):', cmd_code + '\n            elif cmd in ("/help", "/h", "?"):')

# Wrap model response
text = text.replace('sys.stdout.write(f"\\n{answer}\\n\\n")', 'sys.stdout.write(f"\\n{wrap_text(answer)}\\n\\n")')

with open('hydra_cli/agent.py', 'w', encoding='utf-8') as f:
    f.write(text)
