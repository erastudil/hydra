@echo off
color 0A
title Hydra Sovereign Terminal - Multi-Headed AI Shell

cd /d "C:\Users\jpm05\Documents"

set "PYTHONPATH=C:\Users\jpm05\Documents\hydra;%PYTHONPATH%"
set "PATH=%PATH%;C:\Users\jpm05\Documents\bin;C:\Users\jpm05\AppData\Local\hermes\bin;C:\Users\jpm05\AppData\Local\Programs\cursor\resources\app\bin"

set "HYDRA_ORCHESTRATOR_MODEL=glm 5.3 flash"
set "HYDRA_DEFAULT_MODEL=glm 5.3 flash"

doskey hydra=python -m hydra_cli $*
doskey hermes=C:\Users\jpm05\AppData\Local\hermes\bin\hermes.exe --toolsets all $*
doskey pi=python -m hydra_cli pi $*
doskey cursor=C:\Users\jpm05\AppData\Local\Programs\cursor\resources\app\bin\cursor.cmd $*
doskey free=python -m hydra_cli free $*
doskey local=python -m hydra_cli local $*
doskey swarm=python -m hydra_cli swarm $*
doskey agent=python -m hydra_cli agent --model "glm 5.3 flash" $*
doskey orchestrator=python -m hydra_cli agent --model "glm 5.3 flash" $*
doskey glm=python -m hydra_cli "glm 5.3 flash" $*
doskey serve=python -m hydra_cli serve $*
doskey cls=cls $T python -m hydra_cli banner
doskey clear=cls $T python -m hydra_cli banner

cls
python -m hydra_cli banner

echo ===============================================================================
echo  SOVEREIGN HYDRA INTERACTIVE SHELL  [PHOSPHOR GREEN 0A . PROGEN IRON CORE]
echo ===============================================================================
echo  Default Orchestrator: GLM 5.3 Flash (Host: CheaperInference ^| Fallback: OpenRouter)
echo ===============================================================================
echo  Commands available:
echo    hydra ^<alias^> "^<prompt^>"   Frontier reasoning (glm 5.3 flash, opus 5.5, sonnet 5.5, sol 6.1)
echo    orchestrator "^<task^>"        Sovereign orchestrator loop (glm 5.3 flash via cheaperinference)
echo    agent "^<task^>"               Autonomous ReAct loop with MCP (default: glm 5.3 flash)
echo    hermes                         Autonomous Hermes agent loop (--toolsets all)
echo    pi "^<prompt^>"                Pi coding agent hand (Qwen 2.5 Coder 32B)
echo    cursor                         Cursor agent CLI runner
echo    free "^<prompt^>"              Zero-cost Cloudflare / Free Forge routing
echo    local "^<prompt^>"             Local offline inference (Ollama / EasyLM)
echo    swarm "^<task^>"               Multi-agent swarm (Architect, Coder, Auditor)
echo    serve [--port 7777]            OpenAI-compatible gateway for agents
echo ===============================================================================
echo.

python -m hydra_cli agent --model "glm 5.3 flash"

echo %cmdcmdline% | findstr /i /c:"/k" >nul
if errorlevel 1 (
    cmd /k
)

