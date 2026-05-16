import subprocess
import sys
import os
from pathlib import Path

ROOT  = Path(__file__).parent
VENV  = ROOT / "venv" / "Scripts"
PY    = VENV / "python.exe"
PYW   = VENV / "pythonw.exe"

def launch():
    procs = []

    # Open log viewer in a separate console window
    subprocess.Popen(
        ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "log_viewer.ps1")],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

    eyes = subprocess.Popen(
        [str(PYW), str(ROOT / "eyes" / "scraper.py")],
        cwd=str(ROOT)
    )
    procs.append(eyes)

    discord = subprocess.Popen(
        [str(PYW), str(ROOT / "voice" / "discord_interface.py")],
        cwd=str(ROOT)
    )
    procs.append(discord)

    widget = subprocess.Popen(
        [str(PY), str(ROOT / "voice" / "widget.py")],
        cwd=str(ROOT)
    )
    procs.append(widget)

    try:
        widget.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    launch()
