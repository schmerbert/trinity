@echo off
cd /d %USERPROFILE%\Trinity
call venv\Scripts\activate.bat

:: Kill widget by singleton port (47291)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":47291 "') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Kill background services (discord_interface, watcher run as pythonw)
taskkill /F /IM pythonw.exe /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Trinity - Live Log" /T >nul 2>&1

:: Wait for ports to release
timeout /t 1 /nobreak >nul

start /min "" venv\Scripts\pythonw.exe nervous_system\watcher.py
start /min "" venv\Scripts\pythonw.exe voice\discord_interface.py
start /min "" venv\Scripts\python.exe runner.py
python voice\widget.py
