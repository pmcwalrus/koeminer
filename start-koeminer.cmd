@echo off
cd /d "%~dp0"
if exist "dist\local-selection-field\koeminer\koeminer.exe" (
    start "" "dist\local-selection-field\koeminer\koeminer.exe"
) else if exist "dist\local-selection\koeminer\koeminer.exe" (
    start "" "dist\local-selection\koeminer\koeminer.exe"
) else if exist "dist\local-columns\koeminer\koeminer.exe" (
    start "" "dist\local-columns\koeminer\koeminer.exe"
) else if exist "dist\local-yandex\koeminer\koeminer.exe" (
    start "" "dist\local-yandex\koeminer\koeminer.exe"
) else if exist "dist\local-free\koeminer\koeminer.exe" (
    start "" "dist\local-free\koeminer\koeminer.exe"
) else if exist "dist\local-sources\koeminer\koeminer.exe" (
    start "" "dist\local-sources\koeminer\koeminer.exe"
) else if exist "dist\local-simple\koeminer\koeminer.exe" (
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
