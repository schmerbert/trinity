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
    QPainter, QColor, QPen, QFont, QIcon,
    QPainterPath, QAction
)
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

import anthropic
from brain.prompts import build_prompt, parse_prompt_tags
from brain.logger import get_logger

log = get_logger("WIDGET")

try:
    from voice.extensions.scratchpad import ScratchpadPanel
    _SCRATCHPAD = True
except ImportError:
    _SCRATCHPAD = False
from brain.memory import (
    get_profile, create_profile, update_profile,
    add_interest, add_feedback, save_conversation_summary,
    get_recent_summaries, get_unseen_alerts, mark_alerts_seen,
    process_feedback, get_queued_thoughts, clear_queued_thoughts,
    push_discord_write, update_last_seen, get_scratchpad, save_scratchpad
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

_MD_STRIP = re.compile(r'\*{1,2}|_{1,2}|`+|#{1,6}\s*|>\s*|^\s*[-•]\s*', re.MULTILINE)

def _strip_for_tts(text):
    text = re.sub(r'http\S+', '', text)
    text = _MD_STRIP.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


# --- Wave Widget ---
class WaveWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(55)
        self.phase = 0.0
        self.amplitude = 8.0
        self.target_amplitude = 8.0
        self.speed = 0.04
        self.wave_color = COLOR_WAVE_IDLE
        self.target_color = COLOR_WAVE_IDLE

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(30)

    def _tick(self):
        self.phase += self.speed
        self.amplitude += (self.target_amplitude - self.amplitude) * 0.1
        self.wave_color = self._lerp_color(self.wave_color, self.target_color, 0.05)
        self.update()

    def _lerp_color(self, c1, c2, t):
        return QColor(
            int(c1.red()   + (c2.red()   - c1.red())   * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
            255
        )

    def set_state(self, state):
        states = {
            "idle":   (8.0,  0.04, COLOR_WAVE_IDLE),
            "active": (18.0, 0.09, COLOR_WAVE_TALK),
            "alert":  (14.0, 0.06, COLOR_WAVE_ALERT),
            "urgent": (22.0, 0.13, COLOR_WAVE_URGENT),
        }
        amp, spd, col = states.get(state, states["idle"])
        self.target_amplitude = amp
        self.speed = spd
        self.target_color = col

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2
        steps = w * 2

        glow = QColor(self.wave_color)
        glow.setAlpha(35)
        pen_glow = QPen(glow)
        pen_glow.setWidth(7)
        painter.setPen(pen_glow)
        path = self._make_path(w, mid, steps)
        painter.drawPath(path)

        pen = QPen(self.wave_color)
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


WIDGET_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search"},
    {
        "name": "read_discord_channel",
        "description": "Read messages from one of your Discord palace channels by name. Use to review what you've written or what's been posted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":  {"type": "string", "description": "Channel name — partial, case-insensitive match"},
                "limit": {"type": "integer", "description": "Messages to fetch (default 20, max 50)"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "log_wake",
        "description": "Leave a note for your future self that loads at the top of your next Discord wake cycle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "topics":  {"type": "array", "items": {"type": "string"}}
            },
            "required": ["summary"]
        }
    },
    {
        "name": "get_scratchpad",
        "description": "Read your persistent scratchpad — your working surface across all sessions.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "write_scratchpad",
        "description": "Update your persistent scratchpad. Overwrites current content.",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"]
        }
    }
]


# --- Worker thread ---
class TrinityWorker(QThread):
    chunk_ready      = pyqtSignal(str)
    response_done    = pyqtSignal(str)
    error_signal     = pyqtSignal(str)
    scratchpad_write = pyqtSignal(str)

    def __init__(self, client, prompt, history, profile_id):
        super().__init__()
        self.client     = client
        self.prompt     = prompt
        self.history    = history
        self.profile_id = profile_id

    def run(self):
        messages      = list(self.history)
        cached_system = [{"type": "text", "text": self.prompt, "cache_control": {"type": "ephemeral"}}]

        for attempt in range(3):
            try:
                # First turn: streaming for live display
                full = ""
                with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=1000,
                    system=cached_system,
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
                            system=cached_system,
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
        assistant_content = [
            b.model_dump() if hasattr(b, "model_dump") else
            {"type": "text", "text": b.text} if b.type == "text" else
            {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            for b in content
        ]
        tool_results = []
        for block in content:
            if block.type == "tool_use":
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
        if name == "log_wake":
            from brain.memory import log_wake_cycle
            log_wake_cycle(self.profile_id, inputs["summary"], inputs.get("topics", []))
            return {"status": "logged"}

        elif name == "get_scratchpad":
            from brain.memory import get_scratchpad as _gs
            return {"content": _gs(self.profile_id)}

        elif name == "write_scratchpad":
            from brain.memory import save_scratchpad as _ss
            _ss(self.profile_id, inputs["content"])
            self.scratchpad_write.emit(inputs["content"])
            return {"status": "saved"}

        elif name == "read_discord_channel":
            return self._read_discord_channel(
                inputs.get("name", ""), int(inputs.get("limit", 20))
            )

        return {"error": f"Unknown tool: {name}"}

    def _read_discord_channel(self, name_query, limit=20):
        try:
            import urllib.request
            from brain.memory import get_profile as _gp
            profile   = _gp()
            guild_id  = profile.get("discord_home_guild_id") if profile else None
            bot_token = os.getenv("DISCORD_BOT_TOKEN")
            if not guild_id or not bot_token:
                return {"error": "Discord not configured — set home server first"}
            headers = {"Authorization": f"Bot {bot_token}"}

            req = urllib.request.Request(
                f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                headers=headers
            )
            with urllib.request.urlopen(req) as resp:
                channels = json.loads(resp.read())

            query   = name_query.lower().replace("-", "").replace("_", "").replace(" ", "")
            channel = next(
                (c for c in channels
                 if c.get("type") == 0
                 and query in c["name"].lower().replace("-", "").replace("_", "")),
                None
            )
            if not channel:
                return {"error": f"No channel matching '{name_query}' found"}

            req = urllib.request.Request(
                f"https://discord.com/api/v10/channels/{channel['id']}/messages?limit={min(limit,50)}",
                headers=headers
            )
            with urllib.request.urlopen(req) as resp:
                msgs = json.loads(resp.read())

            return [
                {"author": m["author"]["username"], "content": m["content"], "timestamp": m["timestamp"]}
                for m in msgs
            ]
        except Exception as e:
            return {"error": str(e)}


# --- Main Widget ---
class TrinityWidget(QMainWindow):
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

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._scratchpad = ScratchpadPanel(self) if _SCRATCHPAD else None
        self._init_log()

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

        # Trinity's back door — checks every 5s for high-urgency alerts only
        self._urgent_poll = QTimer()
        self._urgent_poll.setInterval(5_000)
        self._urgent_poll.timeout.connect(self._check_urgent_alerts)

        self._init_trinity()

    # --- Window setup ---
    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)
        self.setMinimumHeight(160)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 360, 20)

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
        title.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: rgb(80, 180, 255);")

        self.status_label = QLabel("watching")
        self.status_label.setFont(QFont("Courier New", 7))
        self.status_label.setStyleSheet("color: rgb(60, 100, 150);")

        btn_style = "QPushButton { background: transparent; color: rgb(60,100,150); border: none; font-size: 11px; } QPushButton:hover { color: rgb(80,180,255); }"

        self.voice_btn = QPushButton("◉")
        self.voice_btn.setFixedSize(20, 20)
        self.voice_btn.setStyleSheet(btn_style)
        self.voice_btn.setToolTip("Toggle voice")
        self.voice_btn.clicked.connect(self._toggle_voice)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setFixedSize(20, 20)
        self.stop_btn.setStyleSheet(btn_style)
        self.stop_btn.setToolTip("Stop voice")
        self.stop_btn.clicked.connect(self._stop_tts)

        self.sidebar_btn = QPushButton("≡")
        self.sidebar_btn.setFixedSize(20, 20)
        self.sidebar_btn.setStyleSheet(btn_style)
        self.sidebar_btn.setToolTip("Findings")
        self.sidebar_btn.clicked.connect(self._toggle_sidebar)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(btn_style)
        close_btn.clicked.connect(self._hide_to_tray)

        header.addWidget(title)
        header.addWidget(self.status_label)
        header.addStretch()
        if _SCRATCHPAD:
            self.scratch_btn = QPushButton("✎")
            self.scratch_btn.setFixedSize(20, 20)
            self.scratch_btn.setStyleSheet(btn_style)
            self.scratch_btn.setToolTip("Scratchpad")
            self.scratch_btn.clicked.connect(self._toggle_scratchpad)
            header.addWidget(self.scratch_btn)
        header.addWidget(self.stop_btn)
        header.addWidget(self.voice_btn)
        header.addWidget(self.sidebar_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Wave
        self.wave = WaveWidget()
        layout.addWidget(self.wave)

        # Response area
        self.response_area = QTextEdit()
        self.response_area.setReadOnly(True)
        self.response_area.setMaximumHeight(160)
        self.response_area.setMinimumHeight(60)
        self.response_area.setFont(QFont("Courier New", 9))
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

        # Sidebar (hidden by default)
        self.sidebar = QWidget()
        self.sidebar.setVisible(False)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 4, 0, 0)

        sidebar_label = QLabel("— findings —")
        sidebar_label.setFont(QFont("Courier New", 7))
        sidebar_label.setStyleSheet("color: rgb(60,100,150);")
        sidebar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(sidebar_label)

        self.findings_area = QTextEdit()
        self.findings_area.setReadOnly(True)
        self.findings_area.setMaximumHeight(120)
        self.findings_area.setFont(QFont("Courier New", 8))
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
        self.input_field.setFont(QFont("Courier New", 9))
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
        send_btn.setFixedSize(28, 28)
        send_btn.setFont(QFont("Courier New", 12))
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
        self.wave.set_state("active")
        self.status_label.setText("waking up...")

        self.profile = get_profile()
        if not self.profile:
            self._display("No profile found. What's your name?")
            self.input_field.setPlaceholderText("enter your name...")
            self._awaiting_name = True
        else:
            self._awaiting_name = False
            summaries = get_recent_summaries(self.profile["id"])
            self.summary_text = json.dumps(summaries, indent=2) if summaries else "No previous conversations yet."

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
            if _SCRATCHPAD and self._scratchpad:
                saved = get_scratchpad(self.profile["id"])
                if saved:
                    self._scratchpad._text.setPlainText(saved)
            self._last_input = opening
            self._ask_trinity(opening)
            self._alert_poll.start()
            self._urgent_poll.start()

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
        count = len(alerts) + len(queued)
        self._notify(f"Trinity has {count} thing{'s' if count > 1 else ''} for you.")

    def _check_urgent_alerts(self):
        if not self.profile:
            return
        alerts = get_unseen_alerts(self.profile["id"], min_score=2.5)
        if not alerts:
            return
        self._load_findings(alerts)
        mark_alerts_seen(self.profile["id"])
        self.wave.set_state("urgent")
        self._notify("Trinity — urgent.", urgent=True)
        if not self._tts_active:
            alert_text = "\n".join(f"- {a['headline']}" for a in alerts[:3])
            self._ask_trinity(f"You flagged this as urgent:\n{alert_text}\n\nTell me now.")

    def _notify(self, message, urgent=False):
        icon = QSystemTrayIcon.MessageIcon.Critical if urgent else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage("Trinity", message, icon, 8000)

    # --- Trinity query ---
    def _ask_trinity(self, user_text):
        self.wave.set_state("active")
        self.status_label.setText("thinking...")
        self.input_field.setEnabled(False)
        self._stream_buffer = ""

        extensions = ["scratchpad"] if _SCRATCHPAD else []
        prompt   = build_prompt(self.profile, self.summary_text, self.history, extensions=extensions)
        if _SCRATCHPAD and self._scratchpad:
            pad_text = self._scratchpad._text.toPlainText().strip()
            if pad_text:
                prompt += f"\n\nYour current scratchpad:\n{pad_text}"
        messages = self.history + [{"role": "user", "content": user_text}]

        self.worker = TrinityWorker(self.client, prompt, messages, self.profile["id"])
        self.worker.chunk_ready.connect(self._on_chunk)
        self.worker.response_done.connect(self._on_response)
        self.worker.error_signal.connect(self._on_error)
        self.worker.scratchpad_write.connect(self._on_scratchpad_write)
        self.worker.start()

    def _on_chunk(self, text):
        self._stream_buffer += text
        display = self._stream_buffer
        # Hide tag blocks from the live display
        for tag in ("<memory>", "<prompt", "<scratch>", "<thought>"):
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

        self._log("trinity", clean)
        log.info(f"Response ({len(clean)} chars){' [scratch]' if self._scratchpad and self._scratchpad._visible else ''}")
        self._display(clean)
        self.wave.set_state("idle")
        self.status_label.setText("watching")
        self.input_field.setEnabled(True)
        self.input_field.setFocus()

        self._idle_timer.start()

        if self.tts_enabled:
            self._tts_stop = False
            self._tts_active = True
            spoken = _strip_for_tts(clean)
            threading.Thread(target=self._speak, args=(spoken,), daemon=True).start()

    def _on_error(self, msg):
        log.error(f"API: {msg[:120]}")
        self._display(msg)
        self.wave.set_state("idle")
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

    # --- Scratchpad extension ---
    def _toggle_scratchpad(self):
        if self._scratchpad:
            self._scratchpad.toggle()

    def _parse_scratch(self, reply):
        if not self._scratchpad or "<scratch>" not in reply:
            return reply
        parts = re.split(r'<scratch>(.*?)</scratch>', reply, flags=re.DOTALL)
        # Even indices are text segments, odd indices are scratch content
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
        if _SCRATCHPAD and self._scratchpad:
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
    def _speak(self, text):
        try:
            import asyncio
            import tempfile
            import subprocess
            import edge_tts

            if self._tts_stop:
                self._tts_active = False
                return

            async def _gen():
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    tmp = f.name
                comm = edge_tts.Communicate(text, "en-US-AriaNeural", rate="+30%")
                await comm.save(tmp)
                return tmp

            tmp_path = asyncio.run(_gen())

            if not self._tts_stop:
                if sys.platform == "win32":
                    import winsound
                    wav = tmp_path.replace(".mp3", ".wav")
                    try:
                        subprocess.run(["ffmpeg", "-y", "-i", tmp_path, wav], capture_output=True, timeout=10)
                        if not self._tts_stop:
                            winsound.PlaySound(wav, winsound.SND_FILENAME)
                        os.unlink(wav)
                    except Exception:
                        if not self._tts_stop:
                            subprocess.run(
                                ["powershell", "-c", f"(New-Object Media.SoundPlayer '{tmp_path}').PlaySync()"],
                                capture_output=True, timeout=30
                            )
                else:
                    subprocess.run(["mpg123", "-q", tmp_path], capture_output=True)

            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        except Exception:
            pass
        finally:
            self._tts_active = False

    def _stop_tts(self):
        self._tts_stop = True
        if sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
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
        self.input_field.clear()

        if hasattr(self, '_awaiting_name') and self._awaiting_name:
            self.profile = create_profile(text)
            self._awaiting_name = False
            self.input_field.setPlaceholderText("say something...")
            opening = "Good to meet you. I'm Trinity — here to watch markets, surface signals, and help you think through what matters. What are you currently tracking?"
            self._display(opening)
            self.history = [
                {"role": "user", "content": f"My name is {text}"},
                {"role": "assistant", "content": opening}
            ]
            if self.tts_enabled:
                self._tts_stop = False
                self._tts_active = True
                threading.Thread(target=self._speak, args=(opening,), daemon=True).start()
            return

        self._expand()
        self._display_user(text)
        self._last_input = text
        self._log("user", text)
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
        if self._scratchpad:
            self._scratchpad.follow_parent()

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

    def _quit(self):
        if self.profile and _SCRATCHPAD and self._scratchpad:
            save_scratchpad(self.profile["id"], self._scratchpad._text.toPlainText())
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
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    widget = TrinityWidget()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
