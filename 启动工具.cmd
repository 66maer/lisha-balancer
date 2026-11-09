@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0main.py" --quit
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit /b 0
)
if exist "%~dp0dist\LishaOverlay\LishaOverlay.exe" (
    start "" "%~dp0dist\LishaOverlay\LishaOverlay.exe"
    exit /b 0
)
echo Please install dependencies or use the packaged LishaOverlay.exe.
pause
