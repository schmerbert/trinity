@echo off
echo Installing PyInstaller...
venv\Scripts\pip install pyinstaller

echo.
echo Building Trinity.exe...
venv\Scripts\pyinstaller ^
    --onefile ^
    --noconsole ^
    --name Trinity ^
    --add-data "voice;voice" ^
    --add-data "brain;brain" ^
    --add-data "eyes;eyes" ^
    launcher.py

echo.
echo Done. Trinity.exe is in the dist\ folder.
pause
