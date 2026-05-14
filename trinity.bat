@echo off
cd /d %USERPROFILE%\Trinity
call venv\Scripts\activate.bat
start /min "" venv\Scripts\pythonw.exe nervous_system\watcher.py
python voice\interface.py --quick
pause