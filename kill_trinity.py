"""
Kill all running Trinity processes.
Run with: venv\Scripts\python kill_trinity.py
Or double-click (if .py files open with Python).
"""
import subprocess

TRINITY_SCRIPTS = ["widget.py", "discord_interface.py", "scraper.py", "launcher.py"]


def find_trinity():
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-WmiObject Win32_Process | Where-Object { $_.Name -match 'python' } | "
        "ForEach-Object { $_.ProcessId.ToString() + '||' + $_.Name + '||' + $_.CommandLine }"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")

    procs = []
    for line in result.stdout.splitlines():
        if "||" not in line:
            continue
        parts = line.split("||", 2)
        if len(parts) < 3:
            continue
        pid, exe, cmdline = parts[0].strip(), parts[1].strip(), parts[2].strip()
        matched = next((s for s in TRINITY_SCRIPTS if s in cmdline.lower()), None)
        if matched:
            procs.append({"pid": int(pid), "exe": exe, "script": matched, "cmdline": cmdline})
    return procs


if __name__ == "__main__":
    procs = find_trinity()

    if not procs:
        print("No Trinity processes found.")
        input("\nPress Enter to exit...")
        raise SystemExit

    print(f"\n{'PID':<8} {'EXE':<16} SCRIPT")
    print("─" * 52)
    for p in procs:
        print(f"{p['pid']:<8} {p['exe']:<16} {p['script']}")

    print(f"\n{len(procs)} process(es) found.")
    confirm = input("Kill all? [Y/n]: ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("Cancelled.")
        input("\nPress Enter to exit...")
        raise SystemExit

    print()
    for p in procs:
        r = subprocess.run(["taskkill", "/PID", str(p["pid"]), "/F"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  ✓ killed  {p['script']}  (PID {p['pid']})")
        else:
            print(f"  ✗ failed  {p['script']}  (PID {p['pid']})  {r.stderr.strip()}")

    input("\nDone. Press Enter to exit...")
