"""
Verification script for Hydra desktop launcher and enhanced coding agent telemetry.
Standard library only. Progen syntax verification output.
"""
import os
import subprocess
import sys

def verify():
    hydra_dir = r"C:\Users\jpm05\Documents\hydra"
    docs_dir = r"C:\Users\jpm05\Documents"
    bat_file = r"C:\Users\jpm05\Documents\bin\hydra-terminal.bat"

    print("--- 1. Testing bin/hydra-terminal.bat invariants ---")
    assert os.path.isfile(bat_file), f"Missing {bat_file}"
    with open(bat_file, "r", encoding="utf-8") as f:
        bat_content = f.read()

    assert r'set "PYTHONPATH=C:\Users\jpm05\Documents\hydra;%PYTHONPATH%"' in bat_content
    assert r'cd /d "C:\Users\jpm05\Documents"' in bat_content
    assert 'python -m hydra_cli agent --model "glm 5.3 flash"' in bat_content
    assert 'cmd /k' in bat_content
    print("launcher invariant verification : exit 0.")

    print("\n--- 2. Testing hydra_cli import and DEFAULT_AGENT_SYSTEM_PROMPT ---")
    sys.path.insert(0, hydra_dir)
    from hydra_cli.agent import DEFAULT_AGENT_SYSTEM_PROMPT, run_interactive_agent
    assert "Cursor and Antigravity" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "read_file" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "edit_file" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "run_command" in DEFAULT_AGENT_SYSTEM_PROMPT
    print("agent system prompt verification : exit 0.")

    print("\n--- 3. Testing interactive agent REPL launch with GLM 5.3 Flash ---")
    env = os.environ.copy()
    env["PYTHONPATH"] = hydra_dir + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "hydra_cli", "agent", "-i", "--model", "glm 5.3 flash"],
        input="/exit\n",
        capture_output=True,
        text=True,
        cwd=docs_dir,
        env=env,
        timeout=15,
    )
    assert proc.returncode == 0, f"Process failed with exit {proc.returncode}, stderr: {proc.stderr}"
    assert "HYDRA CODING AGENT" in proc.stdout
    assert "glm 5.3 flash" in proc.stdout
    assert "Detected (AGENTS.md / .cursorrules / CLAUDE.md)" in proc.stdout
    assert "Exiting Hydra agent session" in proc.stdout
    print("interactive agent REPL execution : exit 0.")

    print("\n--- 4. Verification complete: all gates passed ---")
    return 0

if __name__ == "__main__":
    sys.exit(verify())
