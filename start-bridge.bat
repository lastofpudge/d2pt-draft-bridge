@echo off
cd /d "%~dp0"
set "APP_PY=%~dp0bridge_runtime\.venv\Scripts\python.exe"
if exist "%APP_PY%" (
	"%APP_PY%" bridge.py --port 8765
) else (
	echo Bridge Python environment not found; screen capture may be unavailable.
	py -3 bridge.py --port 8765
	if errorlevel 1 python bridge.py --port 8765
)
pause
