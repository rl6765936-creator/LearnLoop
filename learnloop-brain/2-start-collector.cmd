@echo off
chcp 65001 >nul
title LearnLoop - Collector
rem The Collector folder sits next to this one (..\learnloop-collector)
cd /d "%~dp0..\learnloop-collector"
set PYTHONPATH=src
".venv\Scripts\python.exe" -m collector.main watch
pause
