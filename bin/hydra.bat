@echo off
setlocal EnableExtensions
REM Hydra Windows launcher — prefers Python, falls back to Node.
REM Keep this file in lockstep with install.ps1's hydra.cmd shim.
set "ROOT=%~dp0.."
where python >nul 2>nul
if errorlevel 1 goto try_node
python "%~dp0hydra" %*
exit /b %ERRORLEVEL%
:try_node
where node >nul 2>nul
if errorlevel 1 goto missing
node "%~dp0hydra.js" %*
exit /b %ERRORLEVEL%
:missing
echo [ERROR] Hydra needs Python 3 or Node.js 18+.
exit /b 1
