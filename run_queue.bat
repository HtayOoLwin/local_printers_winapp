@echo off
cd /d %~dp0
.venv\Scripts\python.exe queue_worker.py
pause
