@echo off
cd /d "%~dp0"
if exist "dist\local-simple\koeminer\koeminer.exe" (
    start "" "dist\local-simple\koeminer\koeminer.exe"
) else if exist "dist\local-preload\koeminer\koeminer.exe" (
    start "" "dist\local-preload\koeminer\koeminer.exe"
) else if exist "dist\local\koeminer\koeminer.exe" (
    start "" "dist\local\koeminer\koeminer.exe"
) else if exist "dist\v0.2.0\koeminer\koeminer.exe" (
    start "" "dist\v0.2.0\koeminer\koeminer.exe"
) else if exist "dist\koeminer\koeminer.exe" (
    start "" "dist\koeminer\koeminer.exe"
) else (
    start "" ".venv\Scripts\pythonw.exe" "run.py"
)
