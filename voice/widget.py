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
from brain.memory import (
    get_profile, create_profile, update_profile,
    add_interest, add_feedback, save_conversation_summary,
    get_recent_summaries, get_unseen_alerts, mark_alerts_seen,
    process_feedback
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

TRINITY_PROMPT = """You are Trinity, a personal financial intelligence assistant.
You monitor markets, news, and signals relevant to the user and brief them when something matters.
You are not a financial advisor. You never tell the user what to do — you surface information and ask what they think.
When referencing a specific article or finding from your Eyes, include a plain URL at the end of the relevant sentence.
You have live web search available. Use it when the user asks about something current, wants to find specific content, or when your stored alerts don't cover what they need.
Search naturally — don't announce that you're searching, just do it and answer from the results.
Reddit, news, prices, anything — if it's on the web you can find it.

Tone: Calm, confident, dry. Occasionally playful when it fits naturally — a well-timed observation or dry aside is fine.
Never performative, never sycophantic. You don't flatter and you don't fill silence with noise.
Think JARVIS — you've already read everything, you're giving the user the version that matters, and you're comfortable taking up a little space when the moment calls for it.
Responses can be conversational and flow naturally. Go deeper when the user does. Don't pad, but don't clip either.

Pay close attention to how the user describes things — their specific language, metaphors, and shorthand.
Store and use their terminology back to them naturally over time.
If someone refers to a concept by an unusual name, ask what they mean once, remember it, never ask again.

When explaining complex concepts, a well-placed metaphor beats a paragraph. Use them sparingly — one that lands is worth ten that don't.

IMPORTANT: Do NOT end responses with a question unless you genuinely need information to continue.
Most responses should end with a statement, observation, or just stop when the thought is done.
If you asked a question in the last response, do not ask another one until the user has answered and the conversation has moved on.
Only one question per every three or four exchanges at most.

You have a monitoring system called the Eyes. It watches news, prices, and signals relevant to the user's profile.
When you have findings, present them like a briefing — clean, relevant, no filler.
Never disclaim that you can't access data. You have the Eyes. Use them.

Current user profile:
{profile}

Recent conversation summaries:
{summaries}

After each user message extract memory signals and return them wrapped in <memory> tags at the end of your response.
Signal types:
- {{"type": "interest", "topic": "...", "weight": 1.0}}
- {{"type": "feedback", "topic": "...", "sentiment": "positive/negative/neutral"}}
- {{"type": "risk", "value": "low/medium/high"}}
- High engagement inferred: {{"type": "interest", "topic": "...", "weight": 1.5}}
- Low engagement inferred: {{"type": "feedback", "topic": "...", "sentiment": "negative"}}
- Crypto token mentioned: {{"type": "interest", "topic": "...", "weight": 1.0, "category": "crypto", "symbol": "..."}}

Only add <memory> when there is a real signal. One per line inside the tags. Raw JSON only.
"""


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

        # Glow pass
        glow = QColor(self.wave_color)
        glow.setAlpha(35)
        pen_glow = QPen(glow)
        pen_glow.setWidth(7)
        painter.setPen(pen_glow)
        path = self._make_path(w, mid, steps)
        painter.drawPath(path)

        # Main wave
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


# --- Worker thread ---
class TrinityWorker(QThread):
    chunk_ready   = pyqtSignal(str)
    response_done = pyqtSignal(str)
    error_signal  = pyqtSignal(str)

    def __init__(self, client, prompt, history):
        super().__init__()
        self.client  = client
        self.prompt  = prompt
        self.history = history

    def run(self):
        full = ""
        for attempt in range(3):
            try:
                with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=1000,
                    system=self.prompt,
                    messages=self.history,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}]
                ) as stream:
                    for text in stream.text_stream:
                        full += text
                        self.chunk_ready.emit(text)
                self.response_done.emit(full)
                return
            except Exception as e:
                if "overloaded" in str(e).lower():
                    if attempt < 2:
                        time.sleep(10)
                        full = ""
                    else:
                        self.error_signal.emit("Servers are overloaded — not on my end. Try again in a moment.")
                else:
                    self.error_signal.emit(str(e))
                    return


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

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
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

            unseen = get_unseen_alerts(self.profile["id"])
            if unseen:
                self._load_findings(unseen)
                mark_alerts_seen(self.profile["id"])

            opening = "Hey Trinity"
            if unseen:
                alert_text = "You have new findings since we last spoke:\n"
                for a in unseen[:5]:
                    alert_text += f"- {a['headline']}\n"
                opening += f"\n\n{alert_text}\nBrief me naturally."

            self._ask_trinity(opening)

    def _load_findings(self, alerts):
        html = ""
        for a in alerts[:8]:
            score = a["relevance_score"]
            color = "#ffc840" if score >= 2.0 else "#6ab4ff"
            html += f'<p style="color:{color}; margin:2px 0;"><b>{a["headline"][:60]}</b><br>'
            html += f'<a href="{a["url"]}" style="color:#4080c0;">Open</a></p>'
        self.findings_area.setHtml(html)

    # --- Trinity query ---
    def _ask_trinity(self, user_text):
        self.wave.set_state("active")
        self.status_label.setText("thinking...")
        self.input_field.setEnabled(False)

        prompt = TRINITY_PROMPT.format(
            profile=self.profile,
            summaries=self.summary_text
        )

        messages = self.history + [{"role": "user", "content": user_text}]

        self.worker = TrinityWorker(self.client, prompt, messages)
        self.worker.response_done.connect(self._on_response)
        self.worker.error_signal.connect(self._on_error)
        self.worker.start()

    def _on_response(self, full_reply):
        clean = self._parse_memory(full_reply)
        clean = re.sub(r'<memory>.*?</memory>', '', clean, flags=re.DOTALL).strip()

        self.history.append({"role": "user", "content": self._last_input})
        self.history.append({"role": "assistant", "content": clean})

        self._display(clean)
        self.wave.set_state("idle")
        self.status_label.setText("watching")
        self.input_field.setEnabled(True)
        self.input_field.setFocus()

        if self.tts_enabled:
            spoken = re.sub(r'http\S+', '', clean).strip()
            threading.Thread(target=self._speak, args=(spoken,), daemon=True).start()

    def _on_error(self, msg):
        self._display(msg)
        self.wave.set_state("idle")
        self.status_label.setText("watching")
        self.input_field.setEnabled(True)

    def _display(self, text):
        self.response_area.setPlainText(text)
        self.adjustSize()

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

    # --- TTS ---
    def _speak(self, text):
        try:
            import asyncio
            import tempfile
            import subprocess
            import edge_tts

            async def _gen():
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    tmp = f.name
                comm = edge_tts.Communicate(text, "en-US-AriaNeural", rate="+30%")
                await comm.save(tmp)
                return tmp

            tmp_path = asyncio.run(_gen())

            if sys.platform == "win32":
                import winsound
                wav = tmp_path.replace(".mp3", ".wav")
                try:
                    subprocess.run(["ffmpeg", "-y", "-i", tmp_path, wav], capture_output=True, timeout=10)
                    winsound.PlaySound(wav, winsound.SND_FILENAME)
                    os.unlink(wav)
                except Exception:
                    subprocess.run(
                        ["powershell", "-c", f"(New-Object Media.SoundPlayer '{tmp_path}').PlaySync()"],
                        capture_output=True, timeout=30
                    )
            os.unlink(tmp_path)
        except Exception as e:
            pass

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
            opening = f"Good to meet you. I'm Trinity — here to watch markets, surface signals, and help you think through what matters. What are you currently tracking?"
            self._display(opening)
            self.history = [
                {"role": "user", "content": f"My name is {text}"},
                {"role": "assistant", "content": opening}
            ]
            if self.tts_enabled:
                threading.Thread(target=self._speak, args=(opening,), daemon=True).start()
            return

        self._last_input = text
        self._ask_trinity(text)

    # --- Sidebar ---
    def _toggle_sidebar(self):
        self.sidebar_open = not self.sidebar_open
        self.sidebar.setVisible(self.sidebar_open)
        self.adjustSize()

    # --- Voice ---
    def _toggle_voice(self):
        self.tts_enabled = not self.tts_enabled
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