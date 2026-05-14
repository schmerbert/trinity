@echo off
cd /d %USERPROFILE%\Trinity
call venv\Scripts\activate.bat
taskkill /F /IM pythonw.exe /T >nul 2>&1
start /min "" venv\Scripts\pythonw.exe nervous_system\watcher.py
python voice\interface.py --quick