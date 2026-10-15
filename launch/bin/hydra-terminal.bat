@echo off
color 0A
title Hydra Sovereign Terminal - Multi-Headed AI Shell

cd /d "C:\Users\jpm05\Documents"

set "PYTHONPATH=C:\Users\jpm05\Documents\hydra;%PYTHONPATH%"
set "PATH=%PATH%;C:\Users\jpm05\Documents\bin;C:\Users\jpm05\AppData\Local\hermes\bin;C:\Users\jpm05\AppData\Local\Programs\cursor\resources\app\bin"

set "HYDRA_ORCHESTRATOR_MODEL=mimo 2.6 flash"
set "HYDRA_DEFAULT_MODEL=mimo 2.6 flash"
set "HYDRA_DEFAULT_ALIAS=mimo 2.6 flash"
set "HYDRA_AGENT_MODEL=mimo 2.6 flash"

doskey hydra=python -m hydra_cli $*
doskey hermes=C:\Users\jpm05\AppData\Local\hermes\bin\hermes.exe --toolsets all $*
doskey pi=python -m hydra_cli pi $*
doskey cursor=C:\Users\jpm05\AppData\Local\Programs\cursor\resources\app\bin\cursor.cmd $*
doskey free=python -m hydra_cli free $*
doskey local=python -m hydra_cli local $*
doskey swarm=python -m hydra_cli swarm $*
doskey agent=python -m hydra_cli agent --model "mimo 2.6 flash" $*
doskey orchestrator=python -m hydra_cli agent --model "mimo 2.6 flash" $*
doskey mimo=python -m hydra_cli "mimo 2.6 pro" $*
doskey flash=python -m hydra_cli "mimo 2.6 flash" $*
doskey glm=python -m hydra_cli "glm 5.3 flash" $*
doskey tui=C:\Users\jpm05\Documents\hydra\hydra-tui-rs\target\release\hydra-tui.exe $*
doskey serve=python -m hydra_cli serve $*
doskey cls=cls $T python -m hydra_cli banner
doskey clear=cls $T python -m hydra_cli banner

rem The agent TUI prints the splash once. A banner call here prints the art a second time.
cls

echo ===============================================================================
echo  SOVEREIGN HYDRA INTERACTIVE SHELL  [PHOSPHOR GREEN 0A . PROGEN IRON CORE]
echo ===============================================================================
echo  Default Orchestrator: Xiaomi MiMo 2.6 Flash (Host: CheaperInference ^| Fallback: GLM 5.3 Flash)
echo ===============================================================================
echo  Commands available:
echo    hydra ^<alias^> "^<prompt^>"   Frontier reasoning (mimo 2.6 flash, mimo 2.6 pro, glm 5.3 flash, opus 5.5, sonnet 5.5, sol 6.1)
echo    orchestrator "^<task^>"        Sovereign orchestrator loop (mimo 2.6 flash via cheaperinference)
echo    agent "^<task^>"               Autonomous ReAct loop with MCP (default: mimo 2.6 flash)
echo    hermes                         Autonomous Hermes agent loop (--toolsets all)
echo    pi "^<prompt^>"                Pi coding agent hand (Qwen 2.5 Coder 32B)
echo    cursor                         Cursor agent CLI runner
echo    free "^<prompt^>"              Zero-cost Cloudflare / Free Forge routing
echo    local "^<prompt^>"             Local offline inference (Ollama / EasyLM)
echo    swarm "^<task^>"               Multi-agent swarm (Architect, Coder, Auditor)
echo    serve [--port 7777]            OpenAI-compatible gateway for agents
echo ===============================================================================
echo.

python -m hydra_cli agent --model "mimo 2.6 flash"

echo %cmdcmdline% | findstr /i /c:"/k" >nul
if errorlevel 1 (
    cmd /k
)
