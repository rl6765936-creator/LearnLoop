@echo off
chcp 65001 >nul
title LearnLoop - brain
rem Works wherever the LearnLoop folder is: %~dp0 = the folder of this file
cd /d "%~dp0"
set PYTHONPATH=src
rem Approved 26.11.2026: real material may go to the AI provider (second network lock)
set LEARNLOOP_ALLOW_NETWORK=1
".venv\Scripts\python.exe" -m brain.api.server --live --port 8765
pause
