@echo off
cd /d "%~dp0"
if exist "dist\koeminer\koeminer.exe" (
    start "" "dist\koeminer\koeminer.exe"
) else (
    start "" ".venv\Scripts\pythonw.exe" "run.py"
)
