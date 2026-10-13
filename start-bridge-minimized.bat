@echo off
cd /d "%~dp0"
if not exist "bridge.py" (
    echo bridge.py not found.
    pause
    exit /b 1
)
start "D2PT Bridge" /min "%~dp0start-bridge.bat"
timeout /t 2 /nobreak >nul
echo D2PT Bridge with built-in Dota screen reader started.
echo Open https://dota2protracker.com/drafter and refresh the page.
echo Keep the D2PT Bridge window open while using the extension.

