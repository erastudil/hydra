@echo off
setlocal EnableExtensions
REM Repo-root Windows launcher. Prefer this over ad-hoc desktop bats so
REM every harness hits the same git-tracked CLI (bin/hydra -> hydra_cli).
call "%~dp0bin\hydra.bat" %*
exit /b %ERRORLEVEL%
