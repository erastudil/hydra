import re

with open("hydra_cli/agent.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update run_agent_loop signature and default ledger
t_sig = """    steer_mode: bool = False,
    skip_next_tool: bool = False,
) -> str:"""
r_sig = """    steer_mode: bool = False,
    skip_next_tool: bool = False,
    ledger: Optional[SessionContextLedger] = None,
) -> str:"""
if t_sig in text: text = text.replace(t_sig, r_sig)

t_ledger_init = """    if not checkpointer:
        checkpointer = SessionCheckpointer("""
r_ledger_init = """    if ledger is None:
        ledger = SessionContextLedger()

    if not checkpointer:
        checkpointer = SessionCheckpointer("""
if t_ledger_init in text: text = text.replace(t_ledger_init, r_ledger_init)

# 2. Update run_interactive_agent state variables
t_state = """    active_system_prompt = (
        DEFAULT_AGENT_SYSTEM_PROMPT
        if (not system_prompt or system_prompt == DEFAULT_SYSTEM_PROMPT)
        else system_prompt
    )
    skip_next_tool = False
    turns_history: List[Tuple[str, str]] = []"""
r_state = """    active_system_prompt = (
        DEFAULT_AGENT_SYSTEM_PROMPT
        if (not system_prompt or system_prompt == DEFAULT_SYSTEM_PROMPT)
        else system_prompt
    )
    skip_next_tool = False
    turns_history: List[Tuple[str, str]] = []
    active_ledger = SessionContextLedger()"""
if t_state in text: text = text.replace(t_state, r_state)

# 3. Update the retry run_agent_loop call
t_retry = """                    answer = run_agent_loop(
                        alias=active_alias,
                        prompt=retry_p,
                        system_prompt=active_system_prompt,
                        registry=registry,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        session_id=active_session_id,
                        resume_session=True,
                        tier=active_tier,
                        cwd=target_cwd,
                        steer_mode=active_steer_mode,
                        skip_next_tool=skip_next_tool,
                    )"""
r_retry = """                    answer = run_agent_loop(
                        alias=active_alias,
                        prompt=retry_p,
                        system_prompt=active_system_prompt,
                        registry=registry,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        session_id=active_session_id,
                        resume_session=True,
                        tier=active_tier,
                        cwd=target_cwd,
                        steer_mode=active_steer_mode,
                        skip_next_tool=skip_next_tool,
                        ledger=active_ledger,
                    )"""
if t_retry in text: text = text.replace(t_retry, r_retry)

# 4. Update the main run_agent_loop call
t_main = """            answer = run_agent_loop(
                alias=active_alias,
                prompt=line,
                system_prompt=active_system_prompt,
                registry=registry,
                temperature=temperature,
                max_tokens=max_tokens,
                session_id=active_session_id,
                resume_session=True,
                tier=active_tier,
                cwd=target_cwd,
                steer_mode=active_steer_mode,
                skip_next_tool=skip_next_tool,
            )"""
r_main = """            answer = run_agent_loop(
                alias=active_alias,
                prompt=line,
                system_prompt=active_system_prompt,
                registry=registry,
                temperature=temperature,
                max_tokens=max_tokens,
                session_id=active_session_id,
                resume_session=True,
                tier=active_tier,
                cwd=target_cwd,
                steer_mode=active_steer_mode,
                skip_next_tool=skip_next_tool,
                ledger=active_ledger,
            )"""
if t_main in text: text = text.replace(t_main, r_main)

# 5. Fix multiline input
t_input = """        try:
            line = input(prompt_label).strip()
            last_interrupt_time = 0.0"""
r_input = """        try:
            sys.stdout.write(prompt_label)
            sys.stdout.flush()
            first_line = input()
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
if t_input in text: text = text.replace(t_input, r_input)

with open("hydra_cli/agent.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Patch4 applied.")
