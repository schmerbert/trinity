@echo off
cd /d %USERPROFILE%\Trinity
call venv\Scripts\activate.bat
taskkill /F /IM pythonw.exe /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Trinity - Live Log" /T >nul 2>&1
start /min "" venv\Scripts\pythonw.exe nervous_system\watcher.py
start /min "" venv\Scripts\pythonw.exe voice\discord_interface.py
python voice\widget.py
