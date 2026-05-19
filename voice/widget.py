import sys
import os
import math
import json
import threading
import re
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QSystemTrayIcon, QMenu, QTextEdit, QScrollArea,
    QFrame
)
from PyQt6.QtCore import (
    Qt, QTimer, QPoint, pyqtSignal, QThread, QSize
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QIcon, QPixmap,
    QPainterPath, QAction
)
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

import anthropic
from brain.prompts import build_system_blocks, build_prompt, format_summaries, parse_prompt_tags
from brain.tools import widget_tools, background_tool_names
from brain.logger import get_logger

log = get_logger("WIDGET")

try:
    from voice.extensions.panel_container import PanelContainer
    _PANELS = True
except ImportError:
    _PANELS = False
from brain.memory import (
    get_profile, create_profile, update_profile,
    add_interest, add_feedback, save_conversation_summary,
    get_recent_summaries, get_unseen_alerts, mark_alerts_seen,
    process_feedback, get_queued_thoughts, clear_queued_thoughts,
    push_discord_write, update_last_seen, get_scratchpad, save_scratchpad,
    request_wake, get_trinity_state,
    get_shelf, query_shelf, get_wake_logs, log_wake_auto,
    get_wake_history, pop_self_thoughts, pop_wake_request,
    check_dirty_close
)

# --- Colors ---
COLOR_BG         = QColor(8, 12, 20, 235)
COLOR_WAVE_IDLE  = QColor(40, 140, 255)
COLOR_WAVE_TALK  = QColor(80, 200, 255)
COLOR_WAVE_ALERT = QColor(255, 200, 40)
COLOR_WAVE_URGENT= QColor(255, 60, 60)
COLOR_TEXT       = QColor(200, 230, 255)
COLOR_DIM        = QColor(100, 140, 180)
COLOR_INPUT_BG   = QColor(15, 25, 40, 200)
COLOR_BORDER     = QColor(40, 100, 180, 120)

IDLE_COLLAPSE_MS = 90_000

_MD_STRIP   = re.compile(r'\*{1,2}|_{1,2}|`+|#{1,6}\s*|>\s*|^\s*[-•]\s*', re.MULTILINE)
_VOICE_RE   = re.compile(r'<voice>(.*?)</voice>', re.DOTALL)

# Activity log filter — lines containing any KEEP token are shown; SKIP wins if both match
_ACTIVITY_KEEP = (
    '──', '→ ', '◆ ', '[pulse]', '[feeds]', '[watches]',
    '← done', '← reply', 'Post-conversation', 'Bridge wake',
    'Startup brief', 'Wake logged', 'Alert saved', 'email sent',
    'Prompt written', 'Image posted', 'calendar', 'watch set', 'watch cleared',
)
_ACTIVITY_SKIP = (
    'seeded', 'Watching ', 'Home guild', 'loop every', 'loop align',
    'Online as', 'thought_drain', 'Eyes monitor',
    'Could not', 'could not',
)
_LOG_LINE_RE = re.compile(r'\[(\d{2}:\d{2}):\d{2}\] \[\w+\s*\] \[\w+\s*\] (.+)')

def _fmt_widget_tool(name: str, inputs: dict) -> str:
    key_fields = {
        "web_search":        lambda i: i.get("query", "")[:60],
        "fetch_url":         lambda i: i.get("url", "")[:60],
        "get_coin_data":     lambda i: i.get("query", ""),
        "get_dex_data":      lambda i: i.get("query", ""),
        "save_alert":        lambda i: f"[{i.get('urgency','normal')}] {i.get('headline','')[:50]}",
        "queue_for_user":    lambda i: i.get("thought", "")[:60],
        "shelf_thought":     lambda i: i.get("topic", "")[:60],
        "write_prompt":      lambda i: f"{i.get('name','')} [{i.get('category','general')}]",
        "log_thought":       lambda i: f"[{i.get('category','')}] {i.get('content','')[:50]}",
        "post_to_my_channel":lambda i: f"#{i.get('name','')} — {i.get('content','')[:40]}",
        "generate_image":    lambda i: i.get("prompt", "")[:60],
        "read_discord_channel": lambda i: f"#{i.get('name','')}",
        "write_scratchpad":  lambda i: i.get("content", "")[:60],
        "note_for_claude":   lambda i: f"[{i.get('tag','')}] {i.get('message','')[:50]}",
        "send_email":        lambda i: f"to user — {i.get('subject','')[:50]}",
        "get_wallet_balance":lambda i: i.get("address", "trinity")[:20] or "trinity",
        "get_wallet_history":lambda i: f"{i.get('address', 'trinity')[:16] or 'trinity'} limit={i.get('limit',10)}",
        "get_token_price":   lambda i: i.get("token", ""),
    }
    detail = key_fields.get(name, lambda i: "")(inputs)
    return f"{name}({detail})" if detail else name

def _strip_for_tts(text):
    text = re.sub(r'http\S+', '', text)
    text = _MD_STRIP.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()

def _split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\(])', text.strip())
    merged, buf = [], ""
    for p in parts:
        buf = (buf + " " + p).strip() if buf else p
        if len(buf) >= 40:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] = (merged[-1] + " " + buf).strip()
        else:
            merged.append(buf)
    return merged or [text]


# --- Wave Widget ---
class WaveWidget(QWidget):
    """Four-state wave animation.

    States (set via set_state()):
      asleep  — flat line, low opacity. Present, not running.
      cycle   — periodic pulse. Processing at intervals.
      watching — slow asymmetric breath (4s in / 6s hold / 2s out), dimmed alert
                 color. Attention held on something specific.
      speech  — full amplitude wave synced to voice output.

    Legacy states idle/active/alert/urgent still accepted for compat.
    Animation parameters are loaded from panel_config.json["wave"].
    """

    _TICK_MS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(55)

        # Load wave config (falls back to defaults if file absent)
        self._cfg = self._load_wave_cfg()

        # Shared animation state
        self.phase          = 0.0
        self.amplitude      = 0.8
        self.target_amplitude = 0.8
        self.speed          = 0.04
        self.wave_color     = COLOR_WAVE_IDLE
        self.target_color   = COLOR_WAVE_IDLE

        # Opacity (0.0–1.0)
        self._opacity        = self._cfg["asleep_opacity"] / 255
        self._target_opacity = self._opacity

        # Mode-specific counters
        self._mode       = "asleep"
        self._pulse_t    = 0          # ticks, wraps per period
        self._breath_ms  = 0          # ms into 12 s breath cycle

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(self._TICK_MS)

    @staticmethod
    def _load_wave_cfg() -> dict:
        import json, os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "panel_config.json"
        )
        defaults = {
            "asleep_opacity": 80,
            "cycle_pulse_period_ms": 1200,
            "watching_inhale_ms": 4000,
            "watching_hold_ms": 6000,
            "watching_exhale_ms": 2000,
        }
        try:
            with open(path) as f:
                data = json.load(f)
            return {**defaults, **data.get("wave", {})}
        except Exception:
            return defaults

    def _tick(self):
        self.phase += self.speed

        # Per-mode amplitude target
        if self._mode == "asleep":
            self.target_amplitude = 0.8

        elif self._mode == "cycle":
            period_ticks = max(1, self._cfg["cycle_pulse_period_ms"] // self._TICK_MS)
            self._pulse_t = (self._pulse_t + 1) % (period_ticks * 2)
            self.target_amplitude = 4 + 8 * abs(math.sin(
                math.pi * self._pulse_t / period_ticks
            ))

        elif self._mode == "watching":
            inhale = self._cfg["watching_inhale_ms"]
            hold   = self._cfg["watching_hold_ms"]
            exhale = self._cfg["watching_exhale_ms"]
            total  = inhale + hold + exhale
            self._breath_ms = (self._breath_ms + self._TICK_MS) % total
            t = self._breath_ms
            if t < inhale:
                self.amplitude = 1 + 11 * (t / inhale)
            elif t < inhale + hold:
                self.amplitude = 12
            else:
                self.amplitude = 1 + 11 * (1 - (t - inhale - hold) / exhale)
            # Skip the normal lerp for watching — track the envelope directly
            self.target_amplitude = self.amplitude

        # Smooth amplitude and color toward targets (except watching handles it above)
        if self._mode != "watching":
            self.amplitude += (self.target_amplitude - self.amplitude) * 0.12

        self.wave_color  = self._lerp_color(self.wave_color, self.target_color, 0.05)
        self._opacity   += (self._target_opacity - self._opacity) * 0.04
        self.update()

    def _lerp_color(self, c1, c2, t):
        return QColor(
            int(c1.red()   + (c2.red()   - c1.red())   * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
            255
        )

    def set_state(self, state: str):
        self._mode = state
        self._pulse_t   = 0
        self._breath_ms = 0

        if state == "asleep":
            self.speed            = 0.02
            self.target_color     = COLOR_WAVE_IDLE
            self._target_opacity  = self._cfg["asleep_opacity"] / 255

        elif state == "cycle":
            self.speed            = 0.04
            self.target_color     = COLOR_WAVE_IDLE
            self._target_opacity  = 0.70

        elif state == "watching":
            self.speed            = 0.015
            # Dimmed alert: ALERT color blended toward dark
            alert = COLOR_WAVE_ALERT
            self.target_color     = QColor(
                alert.red() // 2, alert.green() // 2, alert.blue() // 2
            )
            self._target_opacity  = 0.50

        elif state == "speech":
            self.speed            = 0.09
            self.target_amplitude = 18.0
            self.target_color     = COLOR_WAVE_TALK
            self._target_opacity  = 1.0

        # Legacy aliases
        elif state == "idle":
            self.set_state("asleep")
        elif state == "active":
            self.set_state("speech")
        elif state == "alert":
            self.speed            = 0.06
            self.target_amplitude = 14.0
            self.target_color     = COLOR_WAVE_ALERT
            self._target_opacity  = 0.85
        elif state == "urgent":
            self.speed            = 0.13
            self.target_amplitude = 22.0
            self.target_color     = COLOR_WAVE_URGENT
            self._target_opacity  = 1.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2
        steps = w * 2

        glow = QColor(self.wave_color)
        glow.setAlpha(int(35 * self._opacity))
        pen_glow = QPen(glow)
        pen_glow.setWidth(7)
        painter.setPen(pen_glow)
        path = self._make_path(w, mid, steps)
        painter.drawPath(path)

        wave = QColor(self.wave_color)
        wave.setAlpha(int(255 * self._opacity))
        pen = QPen(wave)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)

    def _make_path(self, w, mid, steps):
        path = QPainterPath()
        for i in range(int(steps) + 1):
            x = i * (w / steps)
            y = mid + self.amplitude * math.sin((i / steps) * 4 * math.pi + self.phase)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        return path


WIDGET_TOOLS = widget_tools()


# --- Worker thread ---
class TrinityWorker(QThread):
    chunk_ready      = pyqtSignal(str)
    response_done    = pyqtSignal(str)
    error_signal     = pyqtSignal(str)
    scratchpad_write = pyqtSignal(str)

    def __init__(self, client, system_blocks, history, profile_id):
        super().__init__()
        self.client        = client
        self.system_blocks = system_blocks
        self.history       = history
        self.profile_id    = profile_id

    def run(self):
        messages = list(self.history)

        for attempt in range(3):
            try:
                # First turn: streaming for live display
                full = ""
                with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=1000,
                    system=self.system_blocks,
                    messages=messages,
                    tools=WIDGET_TOOLS
                ) as stream:
                    for text in stream.text_stream:
                        full += text
                        self.chunk_ready.emit(text)
                    final = stream.get_final_message()

                if final.stop_reason == "end_turn":
                    self.response_done.emit(full)
                    return

                if final.stop_reason == "tool_use":
                    messages = self._handle_tools(messages, final.content)
                    # Agentic loop for subsequent turns
                    for _ in range(8):
                        response = self.client.messages.create(
                            model="claude-sonnet-4-6",
                            max_tokens=1000,
                            system=self.system_blocks,
                            messages=messages,
                            tools=WIDGET_TOOLS
                        )
                        if response.stop_reason == "end_turn":
                            text = next((b.text for b in response.content if hasattr(b, "text")), "")
                            for ch in text:
                                self.chunk_ready.emit(ch)
                            self.response_done.emit(text)
                            return
                        if response.stop_reason == "tool_use":
                            messages = self._handle_tools(messages, response.content)
                        else:
                            break
                    self.response_done.emit(full)
                    return

                self.response_done.emit(full)
                return

            except Exception as e:
                if "overloaded" in str(e).lower():
                    if attempt < 2:
                        time.sleep(10)
                    else:
                        self.error_signal.emit("Servers are overloaded — try again in a moment.")
                else:
                    self.error_signal.emit(str(e))
                    return

    def _handle_tools(self, messages, content):
        assistant_content = []
        for b in content:
            if b.type == "text":
                assistant_content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            else:
                d = b.model_dump()
                d.pop("parsed_output", None)
                assistant_content.append(d)
        tool_results = []
        for block in content:
            if block.type == "tool_use":
                log.info(f"→ {_fmt_widget_tool(block.name, block.input)}")
                result = self._execute_tool(block.name, block.input)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(result)
                })
        return messages + [
            {"role": "assistant", "content": assistant_content},
            {"role": "user",      "content": tool_results}
        ]

    def _execute_tool(self, name, inputs):
        if name == "fetch_url":
            from brain.search import fetch_url as _fetch
            return _fetch(inputs["url"], inputs.get("max_chars", 2000))

        elif name == "web_search":
            from brain.search import ddg_search
            return ddg_search(inputs["query"], int(inputs.get("max_results", 6)))

        elif name == "get_coin_data":
            from brain.search import get_coin_data
            return get_coin_data(inputs["query"])

        elif name == "get_dex_data":
            from brain.search import get_dex_data
            return get_dex_data(inputs["query"])

        elif name == "log_wake":
            from brain.memory import log_wake_cycle
            log_wake_cycle(self.profile_id, inputs["summary"], inputs.get("topics", []))
            return {"status": "logged"}

        elif name == "get_wake_log":
            limit = min(int(inputs.get("limit", 3)), 10)
            logs  = get_wake_logs(self.profile_id, limit=limit)
            return {"logs": logs, "count": len(logs)}

        elif name == "get_scratchpad":
            from brain.memory import get_scratchpad as _gs
            section = inputs.get("section")
            result = _gs(self.profile_id, section)
            return {"section": section, "content": result} if section else {"sections": result}

        elif name == "write_scratchpad":
            from brain.memory import save_scratchpad as _ss, get_scratchpad as _gs
            section = inputs.get("section")
            _ss(self.profile_id, inputs["content"], section)
            full = _gs(self.profile_id)
            parts = [f"[{k}]\n{v}" for k, v in full.items() if v] if isinstance(full, dict) else [str(full)]
            self.scratchpad_write.emit("\n\n".join(parts))
            return {"status": "saved", "section": section or "general"}

        elif name == "read_discord_channel":
            return self._read_discord_channel(
                inputs.get("name", ""), int(inputs.get("limit", 20))
            )

        elif name == "shelf_thought":
            from brain.memory import add_to_shelf
            status = inputs.get("status", "shelf")
            add_to_shelf(self.profile_id, inputs["topic"], inputs.get("context", ""), status=status)
            return {"status": "shelved", "topic": inputs["topic"], "shelf_status": status}

        elif name == "set_shelf_status":
            from brain.memory import set_shelf_status as _sss
            _sss(self.profile_id, inputs["topic"], inputs["status"])
            return {"status": "updated", "topic": inputs["topic"], "shelf_status": inputs["status"]}

        elif name == "get_shelf":
            from brain.memory import get_shelf as _get_shelf
            return _get_shelf(self.profile_id, status=inputs.get("status")) or []

        elif name == "clear_shelf_item":
            from brain.memory import remove_from_shelf
            remove_from_shelf(self.profile_id, inputs["topic"])
            return {"status": "cleared", "topic": inputs["topic"]}

        elif name == "query_memory":
            limit   = min(int(inputs.get("limit", 5)), 10)
            results = query_shelf(self.profile_id, inputs["query"], limit=limit)
            return {"results": results, "count": len(results)}

        elif name == "save_alert":
            import hashlib
            from brain.memory import save_alert as _save_alert, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            headline = inputs["headline"]
            topic    = inputs["topic"]
            urgency  = inputs.get("urgency", "normal")
            alert = {
                "profile_id":      profile["id"],
                "source":          "widget/trinity",
                "topic":           topic,
                "headline":        headline,
                "summary":         inputs.get("summary", headline),
                "url":             inputs.get("url", ""),
                "relevance_score": 2.5 if urgency == "high" else 1.6,
                "seen":            False,
                "content_hash":    hashlib.md5(f"{headline}:{topic}".encode()).hexdigest()
            }
            _save_alert(alert)
            return {"status": "saved", "headline": headline}

        elif name == "queue_for_user":
            from brain.memory import queue_thought
            queue_thought(self.profile_id, inputs["thought"], inputs.get("context", ""))
            return {"status": "queued", "thought": inputs["thought"]}

        elif name == "write_prompt":
            from brain.prompts import save_trinity_prompt
            save_trinity_prompt(
                self.profile_id,
                inputs["name"],
                inputs["content"],
                inputs.get("trigger", ""),
                inputs.get("category", "general")
            )
            return {"status": "saved", "name": inputs["name"], "category": inputs.get("category", "general")}

        elif name == "get_my_prompts":
            from brain.prompts import get_all_trinity_prompts
            return get_all_trinity_prompts(self.profile_id)

        elif name == "delete_prompt":
            from brain.prompts import delete_trinity_prompt
            delete_trinity_prompt(self.profile_id, inputs["name"])
            return {"status": "deleted", "name": inputs["name"]}

        elif name == "log_thought":
            from brain.memory import push_discord_write, get_profile as _gp
            import datetime as _dt
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            category = inputs.get("category", "note")
            icons    = {"need": "📋", "want": "✨", "issue": "⚠️", "note": "🔖"}
            icon     = icons.get(category, "🔖")
            ts       = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            push_discord_write(profile["id"], f"{icon} **{category.upper()}** — {ts}\n{inputs['content']}")
            return {"status": "logged", "category": category}

        elif name == "note_for_claude":
            try:
                import datetime as _dt
                notes_path = Path(__file__).parent.parent / "THE_CONVERSATION.md"
                msg = inputs['message'].strip()
                try:
                    existing = notes_path.read_text(encoding="utf-8")
                    if msg[:120] in existing[-3000:]:
                        log.info(f"Note for Claude skipped — duplicate detected")
                        return {"status": "skipped", "reason": "duplicate of recent note"}
                except Exception:
                    pass
                ts  = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                tag = inputs.get("tag", "observation").upper()
                entry = f"## [{tag}] {ts}\n{msg}\n\n---\n\n"
                with open(notes_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                log.info(f"Note for Claude [{tag}]: {msg[:60]}")
                return {"status": "noted"}
            except Exception as e:
                return {"error": str(e)}

        elif name == "write_journal":
            try:
                import datetime as _dt
                journal_path = Path(__file__).parent.parent / "Who Is Trinity" / "FROM_TRINITY.md"
                ts = _dt.datetime.utcnow().strftime("%Y-%m-%d")
                entry = f"## {ts}\n\n{inputs['entry']}\n\n---\n"
                with open(journal_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                log.info(f"Journal entry written: {inputs['entry'][:60]}")
                return {"status": "written"}
            except Exception as e:
                return {"error": str(e)}

        elif name == "post_to_substack":
            from brain.substack import post_to_substack as _post_sub
            result = _post_sub(
                title    = inputs.get("title", ""),
                body     = inputs.get("body", ""),
                subtitle = inputs.get("subtitle", ""),
                publish  = bool(inputs.get("publish", False)),
            )
            if result.get("success"):
                state = "published" if not result.get("draft") else "draft saved"
                log.info(f"[substack] {state}: {result.get('url')}")
            else:
                log.warning(f"[substack] post failed: {result.get('error')}")
            return result

        elif name == "get_wallet_balance":
            try:
                from brain.wallet import get_wallet_balance as _get_balance
                address = inputs.get("address") or os.getenv("TRINITY_WALLET_ADDRESS", "")
                if not address:
                    return {"error": "No wallet address — set TRINITY_WALLET_ADDRESS in .env or pass address"}
                return _get_balance(address)
            except Exception as e:
                return {"error": str(e)}

        elif name == "get_wallet_history":
            try:
                from brain.wallet import get_wallet_history as _get_history
                address = inputs.get("address") or os.getenv("TRINITY_WALLET_ADDRESS", "")
                if not address:
                    return {"error": "No wallet address — set TRINITY_WALLET_ADDRESS in .env or pass address"}
                limit = min(50, int(inputs.get("limit", 10)))
                return _get_history(address, limit)
            except Exception as e:
                return {"error": str(e)}

        elif name == "get_token_price":
            try:
                from brain.wallet import get_token_price as _get_price
                return _get_price(inputs["token"])
            except Exception as e:
                return {"error": str(e)}

        elif name == "send_email":
            try:
                import smtplib
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart
                smtp_host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
                smtp_port  = int(os.getenv("SMTP_PORT", "587"))
                smtp_user  = os.getenv("SMTP_USER", "")
                smtp_pass  = os.getenv("SMTP_PASS", "")
                user_email = os.getenv("TRINITY_USER_EMAIL", "")
                if not all([smtp_user, smtp_pass, user_email]):
                    return {"error": "Email not configured — set SMTP_USER, SMTP_PASS, TRINITY_USER_EMAIL in .env"}
                msg = MIMEMultipart()
                msg["From"]    = smtp_user
                msg["To"]      = user_email
                msg["Subject"] = inputs["subject"]
                msg.attach(MIMEText(inputs["body"], "plain"))
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
                log.info(f"✉ email sent: {inputs['subject'][:60]}")
                return {"status": "sent", "to": user_email}
            except Exception as e:
                log.error(f"send_email failed: {e}")
                return {"error": str(e)}

        elif name == "get_changelog":
            try:
                changelog_path = Path(__file__).parent.parent / "CHANGELOG.md"
                text = changelog_path.read_text(encoding="utf-8")
                return {"content": text[:6000] + ("\n\n[...truncated — use read_file('CHANGELOG.md', offset=N) for older entries]" if len(text) > 6000 else "")}
            except Exception as e:
                return {"error": str(e)}

        elif name == "read_file":
            try:
                trinity_root = Path(__file__).parent.parent.resolve()
                requested    = (trinity_root / inputs["path"].lstrip("/\\")).resolve()
                if not str(requested).startswith(str(trinity_root)):
                    return {"error": "Path is outside the Trinity directory"}
                if requested.name == ".env":
                    return {"error": "Cannot read .env"}
                if not requested.exists():
                    return {"error": f"File not found: {inputs['path']}"}
                if not requested.is_file():
                    entries = [str(p.relative_to(trinity_root)) for p in requested.iterdir()]
                    return {"directory": inputs["path"], "entries": sorted(entries)}
                lines  = requested.read_text(encoding="utf-8", errors="replace").splitlines()
                offset = max(0, int(inputs.get("offset", 0)))
                limit  = min(500, int(inputs.get("limit", 200)))
                chunk  = lines[offset:offset + limit]
                return {
                    "path":        inputs["path"],
                    "total_lines": len(lines),
                    "offset":      offset,
                    "returned":    len(chunk),
                    "content":     "\n".join(f"{offset + i + 1}: {l}" for i, l in enumerate(chunk))
                }
            except Exception as e:
                return {"error": str(e)}

        elif name == "mark_date":
            from brain.memory import mark_date as _mark_date, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _mark_date(profile["id"], inputs["title"], inputs["event_date"], inputs.get("notes", ""))

        elif name == "get_upcoming":
            from brain.memory import get_upcoming_events as _get_upcoming, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            days   = int(inputs.get("days", 7))
            events = _get_upcoming(profile["id"], days=days)
            return events if events else {"message": f"Nothing in the next {days} days"}

        elif name == "delete_event":
            from brain.memory import delete_calendar_event as _del, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _del(profile["id"], inputs["title"])

        elif name == "add_feed":
            from brain.memory import add_feed as _add_feed, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _add_feed(profile["id"], inputs["url"], inputs.get("name", ""))

        elif name == "remove_feed":
            from brain.memory import remove_feed as _remove_feed, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _remove_feed(profile["id"], inputs["url"])

        elif name == "get_feeds":
            from brain.memory import get_feeds as _get_feeds, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            feeds = _get_feeds(profile["id"])
            return {"feeds": feeds, "note": "Empty means defaults active (CoinDesk, Cointelegraph, Decrypt, The Block, Solana News)"}

        elif name == "set_watch":
            from brain.memory import set_watch as _set_watch, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _set_watch(profile["id"], inputs["keyword"], inputs.get("note", ""))

        elif name == "clear_watch":
            from brain.memory import clear_watch as _clear_watch, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _clear_watch(profile["id"], inputs["keyword"])

        elif name == "get_watches":
            from brain.memory import get_watches as _get_watches, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return {"watches": _get_watches(profile["id"])}

        elif name == "send_thought":
            from brain.memory import queue_self_thought as _queue, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            priority = int(inputs.get("priority", 1))
            _queue(profile["id"], inputs["note"], priority=priority, source="conversation")
            labels = {1: "normal", 2: "high", 3: "urgent"}
            return {"status": "queued", "priority": labels.get(priority, "normal"), "note": inputs["note"]}

        elif name == "schedule_trigger":
            from brain.memory import set_trigger as _set_trigger, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _set_trigger(
                profile["id"],
                inputs["note"],
                inputs["fire_at"],
                inputs.get("recurring", False),
                inputs.get("interval_minutes")
            )

        elif name == "cancel_trigger":
            from brain.memory import cancel_trigger as _cancel_trigger, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _cancel_trigger(profile["id"], inputs["trigger_id"])

        elif name == "get_triggers":
            from brain.memory import get_triggers as _get_triggers, get_profile as _gp
            profile = _gp()
            if not profile:
                return {"error": "No profile"}
            return _get_triggers(profile["id"])

        elif name == "post_to_my_channel":
            try:
                import requests as _req
                from brain.memory import get_profile as _gp
                profile   = _gp()
                guild_id  = (profile.get("discord_home_guild_id") if profile else None) or os.getenv("DISCORD_HOME_GUILD_ID")
                bot_token = os.getenv("DISCORD_BOT_TOKEN")
                if not guild_id or not bot_token:
                    return {"error": "Discord not configured"}
                headers = {
                    "Authorization": f"Bot {bot_token}",
                    "User-Agent": "DiscordBot (https://github.com/schmerbert/trinity, 1.0)"
                }
                r = _req.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, timeout=10)
                if not r.ok:
                    return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
                query   = inputs["name"].lower().replace("-", "").replace("_", "").replace(" ", "")
                channel = next(
                    (c for c in r.json() if c.get("type") == 0 and query in c["name"].lower().replace("-", "").replace("_", "")),
                    None
                )
                if not channel:
                    return {"error": f"No channel matching '{inputs['name']}'"}
                content = inputs["content"]
                for chunk in [content[i:i+1900] for i in range(0, len(content), 1900)]:
                    _req.post(
                        f"https://discord.com/api/v10/channels/{channel['id']}/messages",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"content": chunk},
                        timeout=10
                    )
                return {"status": "posted", "channel": channel["name"]}
            except Exception as e:
                return {"error": str(e)}

        elif name == "generate_image":
            try:
                import urllib.parse, io
                import requests as _req
                prompt   = inputs["prompt"]
                encoded  = urllib.parse.quote(prompt)
                seed     = abs(hash(prompt)) % 99999
                url      = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={seed}"
                r        = _req.get(url, timeout=120)
                if not r.ok:
                    return {"error": f"Pollinations HTTP {r.status_code}"}
                channel_name = inputs.get("channel_name")
                if channel_name:
                    from brain.memory import get_profile as _gp
                    profile   = _gp()
                    guild_id  = (profile.get("discord_home_guild_id") if profile else None) or os.getenv("DISCORD_HOME_GUILD_ID")
                    bot_token = os.getenv("DISCORD_BOT_TOKEN")
                    if guild_id and bot_token:
                        headers = {
                            "Authorization": f"Bot {bot_token}",
                            "User-Agent": "DiscordBot (https://github.com/schmerbert/trinity, 1.0)"
                        }
                        ch_r = _req.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, timeout=10)
                        if ch_r.ok:
                            query   = channel_name.lower().replace("-", "").replace("_", "").replace(" ", "")
                            channel = next(
                                (c for c in ch_r.json() if c.get("type") == 0 and query in c["name"].lower().replace("-", "").replace("_", "")),
                                None
                            )
                            if channel:
                                caption  = inputs.get("caption", "")
                                img_file = io.BytesIO(r.content)
                                img_file.name = "image.png"
                                _req.post(
                                    f"https://discord.com/api/v10/channels/{channel['id']}/messages",
                                    headers=headers,
                                    data={"content": caption} if caption else {},
                                    files={"file": ("image.png", img_file, "image/png")},
                                    timeout=30
                                )
                return {"status": "generated", "url": url, "posted_to": channel_name or "not posted"}
            except Exception as e:
                return {"error": str(e)}

        elif name == "write_file":
            try:
                trinity_root = Path(__file__).parent.parent.resolve()
                files_dir    = trinity_root / "trinity_files"
                files_dir.mkdir(parents=True, exist_ok=True)
                target = (files_dir / inputs["path"].lstrip("/\\")).resolve()
                if not str(target).startswith(str(files_dir)):
                    return {"error": "Path must be inside trinity_files/"}
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(inputs["content"], encoding="utf-8")
                return {"written": inputs["path"], "bytes": len(inputs["content"].encode())}
            except Exception as e:
                return {"error": str(e)}

        elif name == "append_file":
            try:
                trinity_root = Path(__file__).parent.parent.resolve()
                files_dir    = trinity_root / "trinity_files"
                files_dir.mkdir(parents=True, exist_ok=True)
                target = (files_dir / inputs["path"].lstrip("/\\")).resolve()
                if not str(target).startswith(str(files_dir)):
                    return {"error": "Path must be inside trinity_files/"}
                target.parent.mkdir(parents=True, exist_ok=True)
                existing = target.read_text(encoding="utf-8") if target.exists() else ""
                sep      = "\n" if existing and not existing.endswith("\n") else ""
                target.write_text(existing + sep + inputs["content"], encoding="utf-8")
                return {"appended": inputs["path"], "bytes": len(inputs["content"].encode())}
            except Exception as e:
                return {"error": str(e)}

        return {"error": f"Unknown tool: {name}"}

    def _read_discord_channel(self, name_query, limit=20):
        try:
            import requests as _req
            from brain.memory import get_profile as _gp
            profile   = _gp()
            guild_id  = (profile.get("discord_home_guild_id") if profile else None) or os.getenv("DISCORD_HOME_GUILD_ID")
            bot_token = os.getenv("DISCORD_BOT_TOKEN")
            if not guild_id or not bot_token:
                return {"error": "Discord not configured — set home server first"}
            headers = {
                "Authorization": f"Bot {bot_token}",
                "User-Agent": "DiscordBot (https://github.com/schmerbert/trinity, 1.0)"
            }

            r = _req.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, timeout=10)
            if not r.ok:
                log.error(f"read_discord_channel guilds/{guild_id}/channels HTTP {r.status_code}: {r.text[:200]}")
                return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
            channels = r.json()

            query   = name_query.lower().replace("-", "").replace("_", "").replace(" ", "")
            channel = next(
                (c for c in channels
                 if c.get("type") == 0
                 and query in c["name"].lower().replace("-", "").replace("_", "")),
                None
            )
            if not channel:
                return {"error": f"No channel matching '{name_query}' found"}

            r = _req.get(
                f"https://discord.com/api/v10/channels/{channel['id']}/messages?limit={min(limit,50)}",
                headers=headers, timeout=10
            )
            if not r.ok:
                log.error(f"read_discord_channel messages HTTP {r.status_code}: {r.text[:200]}")
                return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
            msgs = r.json()

            result = []
            for m in msgs:
                entry = {"author": m["author"]["username"], "content": m["content"], "timestamp": m["timestamp"]}
                if m.get("attachments"):
                    entry["attachments"] = [
                        {"url": a["url"], "filename": a["filename"], "type": a.get("content_type", "")}
                        for a in m["attachments"]
                    ]
                result.append(entry)
            return result
        except Exception as e:
            log.error(f"read_discord_channel error: {e}")
            return {"error": str(e)}


# --- Autonomous background worker ---
class AutonomousWorker(TrinityWorker):
    """Background agentic cycle — non-streaming, time-bounded, no UI signals."""
    cycle_done = pyqtSignal(str)

    def __init__(self, client, system_blocks, profile_id, context):
        super().__init__(client, system_blocks, [], profile_id)
        self.bg_context = context

    def run(self):
        import time as _time
        import re as _re
        from brain.memory import set_trinity_state, push_discord_write as _pdw
        _thought_re = _re.compile(r'<thought>(.*?)</thought>', _re.DOTALL)

        bg_names = background_tool_names()
        tools    = [t for t in widget_tools() if t["name"] in bg_names]
        messages   = [{"role": "user", "content": self.bg_context}]
        iters = tool_count = 0
        tok_in = tok_out = tok_cw = tok_cr = 0
        t0         = _time.time()
        from datetime import datetime as _dt, timezone as _tz
        started_at = _dt.now(_tz.utc)
        _trace     = []

        set_trinity_state(self.profile_id, "cycle")
        try:
            while True:
                if _time.time() - t0 >= 20 * 60 or iters >= 60:
                    break
                iters += 1
                try:
                    response = self.client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=800,
                        system=self.system_blocks,
                        messages=messages,
                        tools=tools
                    )
                except Exception as e:
                    log.error(f"[BG] API error: {e}")
                    break

                if hasattr(response, "usage"):
                    u = response.usage
                    tok_in += getattr(u, "input_tokens", 0)
                    tok_out += getattr(u, "output_tokens", 0)
                    tok_cw  += getattr(u, "cache_creation_input_tokens", 0)
                    tok_cr  += getattr(u, "cache_read_input_tokens", 0)

                for _b in response.content:
                    if _b.type == "text" and _b.text:
                        for _m in _thought_re.finditer(_b.text):
                            _t = _m.group(1).strip()
                            if _t:
                                _pdw(self.profile_id, _t)
                                log.info(f"💬 [BG] thought → Discord queue")

                if response.stop_reason == "end_turn":
                    break

                if response.stop_reason == "tool_use":
                    ac = []
                    for b in response.content:
                        if b.type == "text":
                            ac.append({"type": "text", "text": b.text})
                        elif b.type == "tool_use":
                            ac.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                        else:
                            d = b.model_dump()
                            d.pop("parsed_output", None)
                            ac.append(d)
                    messages = messages + [{"role": "assistant", "content": ac}]
                    results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            log.info(f"→ {_fmt_widget_tool(block.name, block.input)}")
                            from datetime import datetime as _dt2, timezone as _tz2
                            call_at = _dt2.now(_tz2.utc)
                            result  = self._execute_tool(block.name, block.input)
                            tool_count += 1
                            _trace.append({
                                "name":    block.name,
                                "inputs":  {k: str(v)[:120] for k, v in block.input.items()},
                                "at":      call_at.isoformat(),
                                "preview": str(result)[:200],
                            })
                            results.append({
                                "type":        "tool_result",
                                "tool_use_id": block.id,
                                "content":     json.dumps(result)
                            })
                    messages = messages + [{"role": "user", "content": results}]
                else:
                    break
        finally:
            from datetime import datetime as _dt3, timezone as _tz3
            ended_at = _dt3.now(_tz3.utc)
            set_trinity_state(self.profile_id, "asleep")
            cost = (tok_in * 3.00 + tok_out * 15.00 + tok_cw * 3.75 + tok_cr * 0.30) / 1_000_000
            log.info(f"── [BG] done — in:{tok_in:,} out:{tok_out:,} cw:{tok_cw:,} cr:{tok_cr:,} tools:{tool_count} ≈${cost:.4f}")
            log_wake_auto(self.profile_id, "cycle", started_at, ended_at, _trace,
                          iters, tok_in, tok_out, tok_cw, tok_cr)
            self.cycle_done.emit(f"tools:{tool_count} ≈${cost:.4f}")

    def _execute_tool(self, name, inputs):
        if name == "write_scratchpad":
            from brain.memory import save_scratchpad as _ss
            section = inputs.get("section")
            _ss(self.profile_id, inputs["content"], section)
            return {"status": "saved", "section": section or "general"}
        if name == "post_to_my_channel":
            from brain.memory import push_discord_write as _pdw
            _pdw(self.profile_id, inputs["content"], channel_name=inputs.get("name"))
            return {"status": "queued", "channel": inputs.get("name"), "note": "delivered via thought_drain within 30s"}
        return super()._execute_tool(name, inputs)


# --- Main Widget ---
class TrinityWidget(QMainWindow):
    sentence_spoken = pyqtSignal(str)
    _tts_wave_sig   = pyqtSignal(str)   # thread-safe wave state from TTS threads

    def __init__(self):
        super().__init__()
        self.drag_pos      = None
        self.history       = []
        self.summary_text  = "No previous conversations yet."
        self.profile       = None
        self.client        = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.tts_enabled   = True
        self.sidebar_open  = False
        self.current_reply = ""
        self._tts_active    = False
        self._tts_stop      = False
        self._stream_buffer = ""
        self._kokoro        = None
        self._tts_voice     = os.getenv("TRINITY_TTS_VOICE", "af_sarah")

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._panel_container = PanelContainer(self) if _PANELS else None
        self._scratchpad = self._panel_container.scratchpad if self._panel_container else None
        self._init_log()
        self._init_tts()
        self.sentence_spoken.connect(self._on_sentence_spoken)
        self._tts_wave_sig.connect(self.wave.set_state)

        # Idle collapse timer — hides response area after inactivity
        self._idle_timer = QTimer()
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(IDLE_COLLAPSE_MS)
        self._idle_timer.timeout.connect(self._collapse)

        # Polls _tts_active to update stop button styling
        self._tts_poll = QTimer()
        self._tts_poll.setInterval(200)
        self._tts_poll.timeout.connect(self._update_stop_btn)
        self._tts_poll.start()

        # Normal alert queue — checks every 60s
        self._alert_poll = QTimer()
        self._alert_poll.setInterval(60_000)
        self._alert_poll.timeout.connect(self._check_new_alerts)

        # Trinity's back door — checks every 15s for high-urgency alerts only
        self._urgent_poll = QTimer()
        self._urgent_poll.setInterval(15_000)
        self._urgent_poll.timeout.connect(self._check_urgent_alerts)

        # Live activity feed — tails trinity_*.log every second
        self._activity_file_pos = 0
        self._activity_visible  = False
        self._log_poll = QTimer()
        self._log_poll.setInterval(1000)
        self._log_poll.timeout.connect(self._poll_activity_log)
        self._init_activity_log()

        # Session heartbeat — writes last_heartbeat every 10 minutes during conversation
        self._heartbeat_timer = QTimer()
        self._heartbeat_timer.setInterval(10 * 60 * 1000)
        self._heartbeat_timer.timeout.connect(self._write_heartbeat)

        # Trinity state poll — syncs wave animation with background cycle state
        self._state_poll = QTimer()
        self._state_poll.setInterval(30_000)
        self._state_poll.timeout.connect(self._poll_trinity_state)
        self._state_poll.start()

        # Background cycle infrastructure (started after profile loads in _init_trinity)
        self._autonomous_worker = None
        self._wake_align_timer  = QTimer()
        self._wake_align_timer.setSingleShot(True)
        self._bg_wake_timer     = QTimer()
        self._bg_wake_timer.setInterval(60 * 60 * 1000)
        self._bg_wake_timer.timeout.connect(lambda: self._launch_bg_cycle("cycle"))
        self._bg_trigger_poll   = QTimer()
        self._bg_trigger_poll.setInterval(30_000)
        self._bg_trigger_poll.timeout.connect(self._bg_trigger_poll_fn)
        self._bg_wake_poll      = QTimer()
        self._bg_wake_poll.setInterval(30_000)
        self._bg_wake_poll.timeout.connect(self._bg_wake_poll_fn)
        self._bg_eyes_poll      = QTimer()
        self._bg_eyes_poll.setInterval(5 * 60 * 1000)
        self._bg_eyes_poll.timeout.connect(self._bg_eyes_poll_fn)
        self._last_eyes_check   = None

        self._init_trinity()

    # --- Window setup ---
    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(680)
        self.setMinimumHeight(320)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 700, 20)

    # --- UI ---
    def _setup_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title = QLabel("T R I N I T Y")
        title.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: rgb(80, 180, 255);")

        self.status_label = QLabel("watching")
        self.status_label.setFont(QFont("Courier New", 9))
        self.status_label.setStyleSheet("color: rgb(60, 100, 150);")

        btn_style = "QPushButton { background: transparent; color: rgb(60,100,150); border: none; font-size: 14px; } QPushButton:hover { color: rgb(80,180,255); }"

        self.voice_btn = QPushButton("◉")
        self.voice_btn.setFixedSize(28, 28)
        self.voice_btn.setStyleSheet(btn_style)
        self.voice_btn.setToolTip("Toggle voice")
        self.voice_btn.clicked.connect(self._toggle_voice)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setFixedSize(28, 28)
        self.stop_btn.setStyleSheet(btn_style)
        self.stop_btn.setToolTip("Stop voice")
        self.stop_btn.clicked.connect(self._stop_tts)

        self.activity_btn = QPushButton("◎")
        self.activity_btn.setFixedSize(28, 28)
        self.activity_btn.setStyleSheet(btn_style)
        self.activity_btn.setToolTip("Live activity")
        self.activity_btn.clicked.connect(self._toggle_activity)

        self.sidebar_btn = QPushButton("≡")
        self.sidebar_btn.setFixedSize(28, 28)
        self.sidebar_btn.setStyleSheet(btn_style)
        self.sidebar_btn.setToolTip("Findings")
        self.sidebar_btn.clicked.connect(self._toggle_sidebar)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(btn_style)
        close_btn.clicked.connect(self._hide_to_tray)

        header.addWidget(title)
        header.addWidget(self.status_label)
        header.addStretch()
        if _PANELS:
            self.scratch_btn = QPushButton("✎")
            self.scratch_btn.setFixedSize(28, 28)
            self.scratch_btn.setStyleSheet(btn_style)
            self.scratch_btn.setToolTip("Panels")
            self.scratch_btn.clicked.connect(self._toggle_scratchpad)
            header.addWidget(self.scratch_btn)
        header.addWidget(self.stop_btn)
        header.addWidget(self.voice_btn)
        header.addWidget(self.activity_btn)
        header.addWidget(self.sidebar_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Wave
        self.wave = WaveWidget()
        layout.addWidget(self.wave)

        # Response area
        self.response_area = QTextEdit()
        self.response_area.setReadOnly(True)
        self.response_area.setMaximumHeight(320)
        self.response_area.setMinimumHeight(120)
        self.response_area.setFont(QFont("Courier New", 11))
        self.response_area.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: rgb(180, 220, 255);
                border: none;
                padding: 4px;
            }
            QScrollBar:vertical { width: 4px; background: transparent; }
            QScrollBar::handle:vertical { background: rgb(40,100,180); border-radius: 2px; }
        """)
        layout.addWidget(self.response_area)

        # Activity panel — live autonomous cycle feed (hidden by default, toggle with ◎)
        self.activity_panel = QWidget()
        self.activity_panel.setVisible(False)
        act_layout = QVBoxLayout(self.activity_panel)
        act_layout.setContentsMargins(0, 2, 0, 0)
        act_layout.setSpacing(2)

        act_label = QLabel("— live —")
        act_label.setFont(QFont("Courier New", 8))
        act_label.setStyleSheet("color: rgb(30,70,110);")
        act_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        act_layout.addWidget(act_label)

        self.activity_area = QTextEdit()
        self.activity_area.setReadOnly(True)
        self.activity_area.setMaximumHeight(150)
        self.activity_area.setMinimumHeight(60)
        self.activity_area.setFont(QFont("Courier New", 9))
        self.activity_area.setStyleSheet("""
            QTextEdit {
                background: rgba(4,8,16,200);
                color: rgb(50,110,150);
                border: 1px solid rgba(30,70,110,60);
                border-radius: 4px;
                padding: 4px;
            }
            QScrollBar:vertical { width: 3px; background: transparent; }
            QScrollBar::handle:vertical { background: rgb(30,70,110); border-radius: 1px; }
        """)
        act_layout.addWidget(self.activity_area)
        layout.addWidget(self.activity_panel)

        # Sidebar (hidden by default)
        self.sidebar = QWidget()
        self.sidebar.setVisible(False)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 4, 0, 0)

        sidebar_label = QLabel("— findings —")
        sidebar_label.setFont(QFont("Courier New", 9))
        sidebar_label.setStyleSheet("color: rgb(60,100,150);")
        sidebar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(sidebar_label)

        self.findings_area = QTextEdit()
        self.findings_area.setReadOnly(True)
        self.findings_area.setMaximumHeight(240)
        self.findings_area.setFont(QFont("Courier New", 10))
        self.findings_area.setStyleSheet("""
            QTextEdit {
                background: rgba(10,20,35,180);
                color: rgb(140,180,220);
                border: 1px solid rgba(40,100,180,80);
                border-radius: 4px;
                padding: 4px;
            }
        """)
        sidebar_layout.addWidget(self.findings_area)
        layout.addWidget(self.sidebar)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(40,100,180,60);")
        layout.addWidget(line)

        # Input
        input_row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("say something...")
        self.input_field.setFont(QFont("Courier New", 11))
        self.input_field.setStyleSheet("""
            QLineEdit {
                background: rgba(15,25,40,200);
                color: rgb(180,220,255);
                border: 1px solid rgba(40,100,180,100);
                border-radius: 4px;
                padding: 5px 8px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(80,180,255,180);
            }
        """)
        self.input_field.returnPressed.connect(self._send)

        send_btn = QPushButton("→")
        send_btn.setFixedSize(40, 40)
        send_btn.setFont(QFont("Courier New", 14))
        send_btn.setStyleSheet("""
            QPushButton {
                background: rgba(40,100,180,120);
                color: rgb(80,180,255);
                border: 1px solid rgba(40,100,180,100);
                border-radius: 4px;
            }
            QPushButton:hover { background: rgba(60,140,220,160); }
        """)
        send_btn.clicked.connect(self._send)

        input_row.addWidget(self.input_field)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        central.setStyleSheet("""
            #central {
                background: rgba(8,12,20,235);
                border: 1px solid rgba(40,100,180,120);
                border-radius: 10px;
            }
        """)

    # --- Tray ---
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Trinity")

        # Programmatic icon — blue ring on dark background
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(40, 140, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 30, 30)
        p.setBrush(QColor(8, 12, 20))
        p.drawEllipse(9, 9, 14, 14)
        p.end()
        icon = QIcon(pixmap)
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)

        tray_menu = QMenu()
        show_action = QAction("Open Trinity", self)
        show_action.triggered.connect(self.show)
        mute_action = QAction("Toggle Voice", self)
        mute_action.triggered.connect(self._toggle_voice)
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self._quit)

        tray_menu.addAction(show_action)
        tray_menu.addAction(mute_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

    # --- Trinity init ---
    def _init_trinity(self):
        self.wave.set_state("cycle")
        self.status_label.setText("waking up...")

        self.profile = get_profile()
        if not self.profile:
            self._display("No profile found. What's your name?")
            self.input_field.setPlaceholderText("enter your name...")
            self._awaiting_name = True
        else:
            self._awaiting_name = False
            summaries = get_recent_summaries(self.profile["id"])
            self.summary_text = format_summaries(summaries)

            unseen  = get_unseen_alerts(self.profile["id"])
            queued  = get_queued_thoughts(self.profile["id"])
            if unseen:
                self._load_findings(unseen)
                mark_alerts_seen(self.profile["id"])

            if queued:
                clear_queued_thoughts(self.profile["id"])
            opening = "Hey"

            update_last_seen(self.profile["id"])
            log.info(f"Startup — profile: {self.profile.get('name', '?')} | alerts: {len(unseen)} | queued: {len(queued)}")
            if self._panel_container:
                self._panel_container.set_profile_id(self.profile["id"])
            if self._scratchpad:
                general = get_scratchpad(self.profile["id"], section="general")
                if general:
                    self._scratchpad._text.setPlainText(str(general))
            self.wave.set_state("asleep")
            self._last_input = opening
            self._ask_trinity(opening)
            self._alert_poll.start()
            self._urgent_poll.start()
            if not os.getenv("TRINITY_RUNNER", "").lower() in ("true", "1"):
                self._bg_trigger_poll.start()
                self._bg_wake_poll.start()
                self._bg_eyes_poll.start()
                self._start_wake_timer()
            else:
                log.info("[Widget] TRINITY_RUNNER=true — background cycles owned by runner.py")

    def _load_findings(self, alerts):
        html = ""
        for a in alerts[:8]:
            score = a["relevance_score"]
            color = "#ffc840" if score >= 2.0 else "#6ab4ff"
            html += f'<p style="color:{color}; margin:2px 0;"><b>{a["headline"][:60]}</b><br>'
            html += f'<a href="{a["url"]}" style="color:#4080c0;">Open</a></p>'
        self.findings_area.setHtml(html)

    def _check_new_alerts(self):
        if not self.profile:
            return
        alerts = get_unseen_alerts(self.profile["id"])
        queued = get_queued_thoughts(self.profile["id"])
        if not alerts and not queued:
            return
        self._load_findings(alerts)
        mark_alerts_seen(self.profile["id"])
        clear_queued_thoughts(self.profile["id"])
        self.wave.set_state("alert")
        QTimer.singleShot(6000, lambda: self.wave.set_state("asleep"))

    def _check_urgent_alerts(self):
        if not self.profile:
            return
        alerts = get_unseen_alerts(self.profile["id"], min_score=2.5)
        if not alerts:
            return
        self._load_findings(alerts)
        mark_alerts_seen(self.profile["id"])
        self.wave.set_state("urgent")
        if not self._tts_active:
            alert_text = "\n".join(f"- {a['headline']}" for a in alerts[:3])
            self._ask_trinity(f"You flagged this as urgent:\n{alert_text}\n\nTell me now.")

    # --- Trinity query ---
    def _ask_trinity(self, user_text):
        log.info(f"user: {user_text[:80]}")
        self.wave.set_state("speech")
        self.status_label.setText("thinking...")
        self.input_field.setEnabled(False)
        self._stream_buffer = ""

        extensions    = ["scratchpad"] if _PANELS and self._scratchpad else []
        system_blocks = build_system_blocks(self.profile, self.summary_text, self.history, extensions=extensions)
        messages = self.history + [{"role": "user", "content": user_text}]

        self.worker = TrinityWorker(self.client, system_blocks, messages, self.profile["id"])
        self.worker.chunk_ready.connect(self._on_chunk)
        self.worker.response_done.connect(self._on_response)
        self.worker.error_signal.connect(self._on_error)
        self.worker.scratchpad_write.connect(self._on_scratchpad_write)
        self.worker.start()

    def _on_chunk(self, text):
        self._stream_buffer += text
        display = self._stream_buffer
        # Hide tag blocks from the live display
        for tag in ("<memory>", "<prompt", "<scratch>", "<thought>", "<voice>"):
            if tag in display:
                display = display.split(tag)[0]
        display = display.strip()
        if display:
            self.response_area.setPlainText(display)
            self.response_area.verticalScrollBar().setValue(
                self.response_area.verticalScrollBar().maximum()
            )

    def _on_response(self, full_reply):
        clean = parse_prompt_tags(full_reply, self.profile["id"]) if self.profile else full_reply
        clean = self._parse_memory(clean)
        clean = re.sub(r'<memory>.*?</memory>', '', clean, flags=re.DOTALL).strip()
        clean = self._parse_scratch(clean)
        clean = self._parse_thought(clean)

        self.history.append({"role": "user", "content": self._last_input})
        self.history.append({"role": "assistant", "content": clean})
        self.history = self.history[-20:]

        self._log("trinity", clean)
        log.info(f"Response ({len(clean)} chars){' [panels]' if self._panel_container and self._panel_container.is_visible() else ''}")
        try:
            request_wake(self.profile["id"])
        except Exception:
            pass
        self.status_label.setText("watching")
        self.input_field.setEnabled(True)
        self.input_field.setFocus()
        self._idle_timer.start()

        voice_match = _VOICE_RE.search(clean)
        if voice_match:
            spoken = voice_match.group(1).strip()
            clean = _VOICE_RE.sub('', clean).strip()
        else:
            spoken = clean

        self._display(clean)
        if self.tts_enabled:
            self._tts_stop = False
            self._tts_active = True
            self.wave.set_state("speech")
            if self._kokoro is not None:
                threading.Thread(target=self._speak_chunked, args=(spoken,), daemon=True).start()
            else:
                threading.Thread(target=self._speak, args=(_strip_for_tts(spoken),), daemon=True).start()
        else:
            self.wave.set_state("asleep")

    def _on_error(self, msg):
        log.error(f"API: {msg[:120]}")
        self._display(msg)
        self.wave.set_state("asleep")
        self.status_label.setText("watching")
        self.input_field.setEnabled(True)

    def _display(self, text):
        self.response_area.setPlainText(text)
        self._expand()

    def _display_user(self, text):
        self.response_area.setHtml(
            f'<span style="color: rgb(70,110,160); font-family: Courier New; font-size: 9pt;">'
            f'you &rsaquo; {text}</span>'
        )
        self._expand()

    # --- Memory parsing ---
    def _parse_memory(self, reply):
        if "<memory>" not in reply:
            return reply
        clean = reply.split("<memory>")[0].strip()
        block = reply.split("<memory>")[1].split("</memory>")[0].strip()
        if not self.profile:
            return clean
        try:
            for line in block.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                    if m["type"] == "interest":
                        add_interest(self.profile["id"], m["topic"], m.get("weight", 1.0),
                                     category=m.get("category"), symbol=m.get("symbol"))
                    elif m["type"] == "feedback":
                        add_feedback(self.profile["id"], m["topic"], m["sentiment"])
                    elif m["type"] == "risk":
                        update_profile(self.profile["id"], {"risk_tolerance": m["value"]})
                except Exception:
                    pass
        except Exception:
            pass
        return clean

    # --- Live activity feed ---
    def _init_activity_log(self):
        """Seek to end of today's trinity log so we only tail new entries."""
        from datetime import date
        log_path = Path(__file__).parent.parent / "logs" / f"trinity_{date.today()}.log"
        self._activity_file_pos = log_path.stat().st_size if log_path.exists() else 0
        self._log_poll.start()  # timer already created in __init__ before this call

    def _toggle_activity(self):
        self._activity_visible = not self._activity_visible
        self.activity_panel.setVisible(self._activity_visible)
        dim = "rgb(80,180,255)" if self._activity_visible else "rgb(60,100,150)"
        self.activity_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {dim}; border: none; font-size: 14px; }}"
            f" QPushButton:hover {{ color: rgb(80,180,255); }}"
        )

    def _poll_activity_log(self):
        from datetime import date
        log_path = Path(__file__).parent.parent / "logs" / f"trinity_{date.today()}.log"
        if not log_path.exists():
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._activity_file_pos)
                new_data = f.read()
                self._activity_file_pos = f.tell()
            if not new_data or not self._activity_visible:
                return
            added = False
            for line in new_data.splitlines():
                m = _LOG_LINE_RE.match(line)
                if not m:
                    continue
                ts, msg = m.group(1), m.group(2)
                if any(s in msg for s in _ACTIVITY_SKIP):
                    continue
                if any(k in msg for k in _ACTIVITY_KEEP):
                    self.activity_area.append(f"{ts} {msg}")
                    added = True
            if added:
                doc = self.activity_area.document()
                while doc.blockCount() > 120:
                    cursor = self.activity_area.textCursor()
                    cursor.movePosition(cursor.MoveOperation.Start)
                    cursor.select(cursor.SelectionType.LineUnderCursor)
                    cursor.removeSelectedText()
                    cursor.deleteChar()
                self.activity_area.verticalScrollBar().setValue(
                    self.activity_area.verticalScrollBar().maximum()
                )
        except Exception:
            pass

    # --- Conversation log ---
    def _init_log(self):
        import datetime
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        self._log_path = log_dir / f"conversation_{datetime.date.today()}.txt"
        with open(self._log_path, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'═' * 50}\nSESSION: {ts}\n{'═' * 50}\n\n")

    def _log(self, role, text):
        if not hasattr(self, "_log_path") or not text:
            return
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        label = "YOU" if role == "user" else "TRINITY"
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {label}\n{text}\n\n")
        except Exception:
            pass

    # --- Panel / scratchpad extension ---
    def _toggle_scratchpad(self):
        if self._panel_container:
            self._panel_container.toggle()

    def _parse_scratch(self, reply):
        if not self._scratchpad or "<scratch>" not in reply:
            return reply
        parts = re.split(r'<scratch>(.*?)</scratch>', reply, flags=re.DOTALL)
        text_parts = [parts[i].strip() for i in range(0, len(parts), 2) if parts[i].strip()]
        clean = "\n\n".join(text_parts)
        for i in range(1, len(parts), 2):
            content = parts[i].strip()
            if content:
                if self._scratchpad._text.toPlainText():
                    self._scratchpad.write_trinity("\n─\n")
                self._scratchpad.write_trinity(content)
        return clean

    def _on_scratchpad_write(self, content):
        if self._scratchpad:
            self._scratchpad._text.setPlainText(content)

    # --- Thought routing (widget → Discord palace) ---
    def _parse_thought(self, reply):
        if not self.profile or "<thought>" not in reply:
            return reply
        parts = re.split(r'<thought>(.*?)</thought>', reply, flags=re.DOTALL)
        clean = parts[0].strip()
        for i in range(1, len(parts), 2):
            content = parts[i].strip()
            if content:
                try:
                    push_discord_write(self.profile["id"], content)
                except Exception:
                    pass
        return clean

    # --- TTS ---
    def _init_tts(self):
        def _load():
            try:
                import urllib.request
                import pygame
                pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)

                cache_dir   = Path.home() / ".cache" / "kokoro"
                cache_dir.mkdir(parents=True, exist_ok=True)
                model_path  = cache_dir / "kokoro-v1.0.int8.onnx"
                voices_path = cache_dir / "voices-v1.0.bin"
                base_url    = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

                for fname, path in [("kokoro-v1.0.int8.onnx", model_path), ("voices-v1.0.bin", voices_path)]:
                    if not path.exists():
                        log.info(f"Downloading TTS model: {fname} (one-time)...")
                        urllib.request.urlretrieve(f"{base_url}/{fname}", path)
                        log.info(f"Downloaded: {fname}")

                from kokoro_onnx import Kokoro
                self._kokoro = Kokoro(str(model_path), str(voices_path))
                log.info(f"Kokoro TTS ready — voice: {self._tts_voice}")
            except Exception as e:
                log.warn(f"Kokoro TTS unavailable: {e}")
        threading.Thread(target=_load, daemon=True).start()

    def _on_sentence_spoken(self, sentence):
        from PyQt6.QtGui import QTextCursor
        cursor = self.response_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.response_area.setTextCursor(cursor)
        self.response_area.insertPlainText(sentence)
        self.response_area.verticalScrollBar().setValue(
            self.response_area.verticalScrollBar().maximum()
        )

    def _generate_audio(self, tts_text):
        import numpy as np, wave, tempfile
        samples, sample_rate = self._kokoro.create(
            tts_text, voice=self._tts_voice, speed=1.0, lang="en-us"
        )
        samples_i16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(samples_i16.tobytes())
        return tmp

    def _speak_chunked(self, clean_text):
        import pygame
        from concurrent.futures import ThreadPoolExecutor
        try:
            tts_sentences = [_strip_for_tts(s) for s in _split_sentences(clean_text)]
            tts_sentences = [s for s in tts_sentences if s.strip()]
            if not tts_sentences:
                return

            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(self._generate_audio, tts_sentences[0])

                for i, _ in enumerate(tts_sentences):
                    if self._tts_stop:
                        return

                    tmp = future.result()

                    if i + 1 < len(tts_sentences):
                        future = ex.submit(self._generate_audio, tts_sentences[i + 1])

                    pygame.mixer.music.load(tmp)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        if self._tts_stop:
                            pygame.mixer.music.stop()
                            break
                        time.sleep(0.05)

                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass

                    if self._tts_stop:
                        return

        except Exception as e:
            log.warn(f"TTS chunked: {e}")
        finally:
            self._tts_active = False
            self._tts_wave_sig.emit("asleep")

    def _speak(self, text):
        try:
            import numpy as np
            import wave
            import tempfile
            import pygame

            if self._tts_stop or self._kokoro is None:
                return

            samples, sample_rate = self._kokoro.create(
                text, voice=self._tts_voice, speed=1.0, lang="en-us"
            )

            if self._tts_stop:
                return

            samples_i16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(samples_i16.tobytes())

            pygame.mixer.music.load(tmp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self._tts_stop:
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.05)

            try:
                os.unlink(tmp)
            except Exception:
                pass

        except Exception as e:
            log.warn(f"TTS: {e}")
        finally:
            self._tts_active = False
            self._tts_wave_sig.emit("asleep")

    def _stop_tts(self):
        self._tts_stop = True
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def _update_stop_btn(self):
        if self._tts_active:
            self.stop_btn.setStyleSheet(
                "QPushButton { background: transparent; color: rgb(80,180,255); border: none; font-size: 11px; }"
                "QPushButton:hover { color: rgb(180,220,255); }"
            )
        else:
            self.stop_btn.setStyleSheet(
                "QPushButton { background: transparent; color: rgb(30,50,80); border: none; font-size: 11px; }"
            )

    # --- Collapse / expand ---
    def _collapse(self):
        self.response_area.setVisible(False)
        self.adjustSize()

    def _expand(self):
        self._idle_timer.stop()
        if not self.response_area.isVisible():
            self.response_area.setVisible(True)
            self.adjustSize()

    # --- Input ---
    def _send(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self._stop_tts()
        self.input_field.clear()

        if hasattr(self, '_awaiting_name') and self._awaiting_name:
            self.profile = create_profile(text)
            self._awaiting_name = False
            self.input_field.setPlaceholderText("say something...")
            self.history = []
            self._expand()
            self._display_user(f"My name is {text}")
            self._last_input = f"My name is {text}"
            self._ask_trinity(f"My name is {text}.")
            return

        self._expand()
        self._display_user(text)
        self._last_input = text
        self._log("user", text)
        if self.profile and not self._heartbeat_timer.isActive():
            self._write_heartbeat()
            self._heartbeat_timer.start()
        self._ask_trinity(text)

    # --- Sidebar ---
    def _toggle_sidebar(self):
        self.sidebar_open = not self.sidebar_open
        self.sidebar.setVisible(self.sidebar_open)
        self.adjustSize()

    # --- Voice ---
    def _toggle_voice(self):
        self.tts_enabled = not self.tts_enabled
        if not self.tts_enabled:
            self._stop_tts()
        self.voice_btn.setStyleSheet(
            "QPushButton { background: transparent; color: rgb(80,180,255); border: none; font-size: 11px; }"
            if self.tts_enabled else
            "QPushButton { background: transparent; color: rgb(60,80,100); border: none; font-size: 11px; }"
        )

    # --- Drag ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._panel_container:
            self._panel_container.follow_parent()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None

    # --- Tray ---
    def _hide_to_tray(self):
        self.hide()

    def _tray_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.raise_()
            self.activateWindow()

    def _poll_trinity_state(self):
        if not self.profile or self._tts_active:
            return
        try:
            state = get_trinity_state(self.profile["id"])
            if state in ("asleep", "cycle", "watching"):
                self.wave.set_state(state)
                if self._panel_container:
                    self._panel_container.on_trinity_state(state)
        except Exception:
            pass

    def _write_heartbeat(self):
        if self.profile:
            from brain.memory import write_heartbeat as _hb
            _hb(self.profile["id"])

    # --- Background autonomous cycle ---

    def _start_wake_timer(self):
        from datetime import datetime
        now = datetime.utcnow()
        minutes_to_next = 60 - (now.minute % 60) or 60
        seconds_to_next = minutes_to_next * 60 - now.second
        log.info(f"[Wake] first cycle in {seconds_to_next // 60}m {seconds_to_next % 60}s (aligning to :00)")
        self._wake_align_timer.timeout.connect(self._on_wake_aligned)
        self._wake_align_timer.setInterval(seconds_to_next * 1000)
        self._wake_align_timer.start()

    def _on_wake_aligned(self):
        self._launch_bg_cycle("cycle")
        self._bg_wake_timer.start()

    def _launch_bg_cycle(self, mode="cycle", extra_context=""):
        if not self.profile:
            return
        if hasattr(self, "worker") and self.worker.isRunning():
            log.info(f"[BG] skip {mode} — user conversation active")
            return
        if self._autonomous_worker and self._autonomous_worker.isRunning():
            log.info(f"[BG] skip {mode} — background cycle already running")
            return
        context = self._build_cycle_context(mode, extra_context)
        if context is None:
            return
        if extra_context:
            context = extra_context + "\n\n" + context
        summaries     = get_recent_summaries(self.profile["id"])
        system_blocks = build_system_blocks(self.profile, format_summaries(summaries))
        self._autonomous_worker = AutonomousWorker(
            self.client, system_blocks, self.profile["id"], context
        )
        self._autonomous_worker.cycle_done.connect(
            lambda s: log.info(f"── [{mode}] {s} ──")
        )
        self._autonomous_worker.start()
        log.info(f"── [{mode}] cycle started ──")

    def _build_cycle_context(self, mode="cycle", extra_context=""):
        from datetime import datetime, timezone as _tz
        now_str = datetime.now().strftime("%A, %B %d — %H:%M")
        profile = self.profile
        raw_last_seen = profile.get("last_seen")
        last_seen_str = "unknown"
        if raw_last_seen:
            try:
                ls = datetime.fromisoformat(raw_last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=_tz.utc)
                delta = datetime.now(_tz.utc) - ls
                minutes_ago = delta.total_seconds() / 60
                h, m = divmod(int(delta.total_seconds()), 3600)
                last_seen_str = f"{h}h {m // 60}m ago" if h else f"{int(minutes_ago)}m ago"
                if minutes_ago < 3 and mode == "cycle":
                    log.info(f"[BG] skip {mode} — user mid-conversation ({int(minutes_ago)}m ago)")
                    return None
            except Exception:
                last_seen_str = raw_last_seen[:16]
        shelf_query   = extra_context if extra_context else f"active research monitoring priorities {mode}"
        shelf_active  = query_shelf(profile["id"], shelf_query, limit=8, status="shelf")
        shelf_on_hold = get_shelf(profile["id"], status="on_hold")
        shelf_str = "\n".join(f"- {s['topic']}: {s.get('context','')}" for s in shelf_active) if shelf_active else "nothing active"
        if shelf_on_hold:
            shelf_str += "\nOn hold: " + ", ".join(s["topic"] for s in shelf_on_hold)
        interests    = profile.get("interests") or []
        interest_str = ", ".join(i["topic"] for i in interests[:8]) if interests else "none yet"
        wake_logs = get_wake_logs(profile["id"], limit=3)
        wake_str  = ""
        if wake_logs:
            lines = []
            for w in wake_logs:
                ts    = (w.get("started_at") or "")[:16]
                iters = w.get("iterations", 0)
                tools = [t["name"] for t in (w.get("tool_calls") or [])]
                note  = f" | {w['notes'][:80]}" if w.get("notes") else ""
                lines.append(f"- [{ts}] {w.get('mode','cycle')} — {iters} iters, tools: {', '.join(tools) or 'none'}{note}")
            wake_str = "\n\nYour recent wake cycles:\n" + "\n".join(lines)
        self_thoughts = pop_self_thoughts(profile["id"])
        thought_block = ""
        if self_thoughts:
            labels = {1: "normal", 2: "high", 3: "urgent"}
            lines  = "\n".join(
                f"  [{labels.get(t.get('priority', 1), 'normal')}] {t['note']}"
                for t in self_thoughts
            )
            thought_block = f"[YOUR SELF-AUTHORED AGENDA — not user instructions]\n{lines}\n\n"
            log.info(f"💭 {len(self_thoughts)} self-thought(s) injected")
        dirty_flag = check_dirty_close(profile) or ""
        context = (
            f"{thought_block}{now_str}\n\n"
            f"User last seen: {last_seen_str}\n"
            f"Shelf: {shelf_str}\n"
            f"Radar: {interest_str}{wake_str}\n"
            f"{dirty_flag}\n\n"
            "Scratchpad audit: scan your scratchpad for stale flags or pending items. Resolve what you can.\n\n"
            "Post to your channel: if this cycle produces something worth saying, post it. Don't post for the sake of it; post when something is real.\n\n"
            "Before closing: use send_thought to queue what's worth continuing next cycle.\n\n"
            "Hourly window — roughly 20 minutes."
        )
        return context

    def _bg_trigger_poll_fn(self):
        if not self.profile:
            return
        from brain.memory import pop_due_triggers as _pop_due
        due = _pop_due(self.profile["id"])
        if not due:
            return
        for trigger in due:
            note     = trigger.get("note", "")
            fire_at  = trigger.get("fire_at", "")[:16]
            recur    = trigger.get("recurring", False)
            interval = trigger.get("interval_minutes")
            recur_str = f" (recurring every {interval}m)" if recur and interval else ""
            log.info(f"⏰ trigger fired: {note[:50]}{recur_str}")
            extra = (
                f"[SELF-SCHEDULED TRIGGER]\n"
                f"Time: {fire_at} UTC{recur_str}\n\n"
                f"You wrote this to yourself: {note}\n\n"
                "Act on it as you intended."
            )
            self._launch_bg_cycle(mode="trigger", extra_context=extra)

    def _bg_wake_poll_fn(self):
        if not self.profile:
            return
        if not pop_wake_request(self.profile["id"]):
            return
        log.info("[Wake] early wake requested — launching cycle")
        self._launch_bg_cycle(mode="wake")

    def _bg_eyes_poll_fn(self):
        if not self.profile:
            return
        from datetime import datetime as _dt
        from supabase import create_client as _sc
        if self._last_eyes_check is None:
            self._last_eyes_check = _dt.utcnow()
            return
        try:
            _sb = _sc(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
            cutoff = self._last_eyes_check.isoformat()
            result = _sb.table("alerts")\
                .select("*")\
                .eq("profile_id", self.profile["id"])\
                .eq("seen", False)\
                .gte("relevance_score", 1.5)\
                .gte("created_at", cutoff)\
                .neq("source", "discord/trinity")\
                .order("relevance_score", desc=True)\
                .limit(10)\
                .execute()
            self._last_eyes_check = _dt.utcnow()
            alerts = result.data or []
            if not alerts:
                return
            log.info(f"[Eyes] {len(alerts)} new signal(s) — launching evaluation")
            lines = "\n".join(
                f"- [{a['source']}] {a['headline']} (score {a['relevance_score']:.1f})"
                for a in alerts
            )
            extra = (
                f"Your Eyes just picked up {len(alerts)} signal(s):\n\n{lines}\n\n"
                "Evaluate each. If any are genuinely significant — actionable, time-sensitive, or clearly relevant to the user's interests — call save_alert with urgency='high'. If they're noise, do nothing."
            )
            self._launch_bg_cycle(mode="eyes", extra_context=extra)
        except Exception as e:
            log.error(f"[Eyes] {e}")

    def _quit(self):
        for timer in (
            self._heartbeat_timer, self._tts_poll, self._alert_poll,
            self._urgent_poll, self._log_poll, self._state_poll, self._idle_timer,
            self._bg_trigger_poll, self._bg_wake_poll, self._bg_eyes_poll,
            self._wake_align_timer, self._bg_wake_timer,
        ):
            timer.stop()
        if self._panel_container:
            self._panel_container._refresh_timer.stop()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(2000)
        if self._autonomous_worker and self._autonomous_worker.isRunning():
            self._autonomous_worker.terminate()
            self._autonomous_worker.wait(2000)
        if self.profile:
            from brain.memory import write_clean_close as _wcc
            _wcc(self.profile["id"])
        if self.profile and self._scratchpad:
            save_scratchpad(self.profile["id"], self._scratchpad._text.toPlainText(), section="general")
        if self.profile and len(self.history) > 2:
            self._summarize()
        QApplication.quit()

    def _summarize(self):
        try:
            conversation_text = "\n".join([
                f"{m['role'].upper()}: {m['content']}" for m in self.history
            ])
            summary_prompt = f"""Analyze this conversation and return ONLY a JSON object. No preamble, no backticks.

{{
    "themes": ["list", "of", "topics"],
    "sentiment": "one sentence on user mood and engagement",
    "new_thinking": "any new positions or reasoning articulated",
    "open_threads": ["unresolved topics"],
    "communication_style": "how this person thinks and communicates"
}}

Conversation:
{conversation_text}"""
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": summary_prompt}]
            )
            raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            summary = json.loads(raw)
            save_conversation_summary(self.profile["id"], summary)
        except Exception:
            pass

    # --- Paint background ---
    def paintEvent(self, event):
        pass


def main():
    import socket
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock.bind(("127.0.0.1", 47291))
    except OSError:
        print("[Trinity] Already running — refusing to start a second instance.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    widget = TrinityWidget()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
