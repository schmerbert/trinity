from pathlib import Path
from datetime import datetime

_LOG_DIR = Path(__file__).parent.parent / "logs"


def get_logger(source: str):
    _LOG_DIR.mkdir(exist_ok=True)
    _src = source.upper().ljust(7)[:7]

    def _write(level: str, msg: str):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{_src}] [{level}] {msg}"
        print(line)
        try:
            log_path = _LOG_DIR / f"trinity_{datetime.now().strftime('%Y-%m-%d')}.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    class _Logger:
        def info(self,  msg): _write("INFO ", msg)
        def warn(self,  msg): _write("WARN ", msg)
        def error(self, msg): _write("ERROR", msg)
        def debug(self, msg): _write("DEBUG", msg)

    return _Logger()
