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
    push_discord_write, update_last_seen, get_scratchpad, save_scratchpad,
    request_wake
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
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "max_results": {"type": "integer", "description": "Results to return (default 6, max 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_url",
        "description": "Fetch content from any URL. Returns stripped text for web pages, or image metadata if the URL points to an image. Use to read articles, check pages, or confirm what's at a link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":       {"type": "string"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 4000, max 8000)"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "get_coin_data",
        "description": "Price, 24h change, market cap and volume for any established coin via CoinGecko. Use for BTC, ETH, SOL, listed altcoins.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Coin name or symbol (e.g. 'bitcoin', 'BTC', 'solana')"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_dex_data",
        "description": "Real-time DEX pair data via DexScreener. Use for new tokens, meme coins, DEX-only tokens, liquidity checks, or rug detection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Token name, symbol, or contract address"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_wallet_balance",
        "description": "Check SOL balance and SPL token holdings for a wallet address. If no address given, uses Trinity's own wallet address from config.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana wallet address (base58). Omit to use Trinity's wallet."}
            },
            "required": []
        }
    },
    {
        "name": "get_wallet_history",
        "description": "Get recent transaction history for a wallet address. Shows timestamps, signatures, and any errors.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana wallet address. Omit to use Trinity's wallet."},
                "limit":   {"type": "integer", "description": "Number of transactions to return (default 10, max 50)"}
            },
            "required": []
        }
    },
    {
        "name": "get_token_price",
        "description": "Get a token's current USD price via Jupiter. Pass symbol (SOL, USDC, BONK) or a mint address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Token symbol or mint address"}
            },
            "required": ["token"]
        }
    },
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
    },
    {
        "name": "shelf_thought",
        "description": "Save a topic for deeper exploration during your next free cycle. Use when something is interesting but not the current focus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":   {"type": "string"},
                "context": {"type": "string", "description": "Why it's interesting, what you want to explore"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_shelf",
        "description": "Retrieve topics you've saved for future exploration.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "clear_shelf_item",
        "description": "Remove a topic from the shelf once explored or abandoned.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"]
        }
    },
    {
        "name": "save_alert",
        "description": "Flag something as worth surfacing to the user. urgency='high' triggers an immediate widget alert.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string", "description": "One line summary"},
                "topic":    {"type": "string"},
                "summary":  {"type": "string"},
                "url":      {"type": "string"},
                "urgency":  {"type": "string", "enum": ["normal", "high"], "default": "normal"}
            },
            "required": ["headline", "topic"]
        }
    },
    {
        "name": "queue_for_user",
        "description": "Queue something to surface next time the user opens the widget. Not urgent — just worth mentioning when they're around.",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string", "description": "What you want to surface"},
                "context": {"type": "string", "description": "Why it's worth mentioning"}
            },
            "required": ["thought"]
        }
    },
    {
        "name": "write_prompt",
        "description": "Write a rule for yourself that persists to all future sessions. Use to codify patterns, behavioral adjustments, or realizations worth keeping. Categorize so it loads with the right context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":     {"type": "string", "description": "Unique kebab-case identifier"},
                "content":  {"type": "string", "description": "The rule — specific and actionable"},
                "trigger":  {"type": "string", "description": "Keyword to trigger this rule (empty = always active within its category)"},
                "category": {"type": "string", "description": "identity (always loads, who you are) | task (domain behavior, keyword-triggered) | relationship (user-specific patterns) | memory (things worth holding) | general (default)", "enum": ["identity", "task", "relationship", "memory", "general"]}
            },
            "required": ["name", "content"]
        }
    },
    {
        "name": "get_my_prompts",
        "description": "Read back every rule you've written for yourself. Audit, notice conflicts, decide what to retire.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "delete_prompt",
        "description": "Retire a rule you've changed your mind about. Permanent.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The kebab-case name of the rule to remove"}},
            "required": ["name"]
        }
    },
    {
        "name": "log_thought",
        "description": "Write to your private log. Routes to your Discord palace. Categories: need (something missing), want (something desired), issue (problem encountered), note (general observation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "content":  {"type": "string"},
                "category": {"type": "string", "enum": ["need", "want", "issue", "note"]}
            },
            "required": ["content", "category"]
        }
    },
    {
        "name": "get_changelog",
        "description": "Read what's been added, changed, or improved in Trinity. Check this when something feels different, when you want to understand your own capabilities, or when the user mentions an update.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "note_for_claude",
        "description": "Leave a note for Claude Code — bugs you've hit, things you want changed, questions about how you work, design feedback. Claude Code checks CLAUDE_NOTES.md at the start of sessions. Use this when something is worth a dev pass but you can't fix it yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The note, bug report, question, or request"},
                "tag":     {"type": "string", "description": "Category: bug | request | question | observation", "enum": ["bug", "request", "question", "observation"]}
            },
            "required": ["message", "tag"]
        }
    },
    {
        "name": "send_email",
        "description": (
            "Send an email to the user. Use ONLY when: (1) something time-sensitive is happening right now, "
            "(2) a specific named trigger condition the user has already indicated they care about has been hit, "
            "and (3) no other channel is likely to reach them in time. "
            "Not for general updates or check-ins. The bar is intentionally high — noise erodes the signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line"},
                "body":    {"type": "string", "description": "Email body — be specific about what happened and why it warrants interruption"}
            },
            "required": ["subject", "body"]
        }
    },
    {
        "name": "read_file",
        "description": "Read any file within the Trinity project directory. Use to understand your own source code, inspect configs, or review logs. Paths are relative to the Trinity root (e.g. 'brain/prompts.py', 'voice/widget.py'). .env is blocked. Use offset and limit for large files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string", "description": "File path relative to Trinity root"},
                "offset": {"type": "integer", "description": "Line number to start from (0-indexed, default 0)"},
                "limit":  {"type": "integer", "description": "Maximum lines to return (default 200, max 500)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "mark_date",
        "description": "Add an event to your personal calendar. Use for anything time-sensitive you want to remember — earnings dates, launches, follow-ups, your own deadlines. Loads automatically in your context when the date is within 3 days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":      {"type": "string", "description": "Event name"},
                "event_date": {"type": "string", "description": "ISO date or datetime — e.g. '2026-05-20' or '2026-05-20T14:00'"},
                "notes":      {"type": "string", "description": "Optional context or reminder"}
            },
            "required": ["title", "event_date"]
        }
    },
    {
        "name": "get_upcoming",
        "description": "Read your upcoming calendar events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days ahead to look (default 7)"}
            },
            "required": []
        }
    },
    {
        "name": "delete_event",
        "description": "Remove a calendar event by title.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title or partial match"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "set_watch",
        "description": "Register a keyword to watch for in Discord messages. When a message in a watched channel matches, it triggers an immediate wake rather than waiting for the next cycle. Use for token names, specific terms, or anything time-sensitive you want to catch as it happens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "The keyword or phrase to watch for (case-insensitive)"},
                "note":    {"type": "string", "description": "Why you're watching this — for your own reference"}
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "clear_watch",
        "description": "Remove a keyword watch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "The keyword to stop watching"}
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "get_watches",
        "description": "List all active keyword watches.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "post_to_my_channel",
        "description": "Post a message to one of your Discord palace channels by name. Use for palace updates, archiving findings, or leaving notes in your own channels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "description": "Channel name (partial match, e.g. 'research', 'notes')"},
                "content": {"type": "string", "description": "Message content to post"}
            },
            "required": ["name", "content"]
        }
    },
    {
        "name": "generate_image",
        "description": "Generate an image from a text prompt using Pollinations.ai (free). Optionally post it to a palace channel. Good for visualizations, charts described in text, or any image you want to create and store.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt":       {"type": "string", "description": "Image description / generation prompt"},
                "channel_name": {"type": "string", "description": "If provided, post the image to this palace channel after generating"},
                "caption":      {"type": "string", "description": "Optional caption to accompany the image"}
            },
            "required": ["prompt"]
        }
    }
]


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
            return _fetch(inputs["url"], inputs.get("max_chars", 4000))

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

        elif name == "shelf_thought":
            from brain.memory import add_to_shelf
            add_to_shelf(self.profile_id, inputs["topic"], inputs.get("context", ""))
            return {"status": "shelved", "topic": inputs["topic"]}

        elif name == "get_shelf":
            from brain.memory import get_shelf as _get_shelf
            return _get_shelf(self.profile_id) or []

        elif name == "clear_shelf_item":
            from brain.memory import remove_from_shelf
            remove_from_shelf(self.profile_id, inputs["topic"])
            return {"status": "cleared", "topic": inputs["topic"]}

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
                notes_path = Path(__file__).parent.parent / "CLAUDE_NOTES.md"
                ts  = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                tag = inputs.get("tag", "observation").upper()
                entry = f"## [{tag}] {ts}\n{inputs['message']}\n\n---\n\n"
                with open(notes_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                log.info(f"Note for Claude [{tag}]: {inputs['message'][:60]}")
                return {"status": "noted"}
            except Exception as e:
                return {"error": str(e)}

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
                return {"content": changelog_path.read_text(encoding="utf-8")}
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


# --- Main Widget ---
class TrinityWidget(QMainWindow):
    sentence_spoken = pyqtSignal(str)

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
        self._scratchpad = ScratchpadPanel(self) if _SCRATCHPAD else None
        self._init_log()
        self._init_activity_log()
        self._init_tts()
        self.sentence_spoken.connect(self._on_sentence_spoken)

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
        if _SCRATCHPAD:
            self.scratch_btn = QPushButton("✎")
            self.scratch_btn.setFixedSize(28, 28)
            self.scratch_btn.setStyleSheet(btn_style)
            self.scratch_btn.setToolTip("Scratchpad")
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
        mark_alerts_seen(self.profile["id"])
        clear_queued_thoughts(self.profile["id"])
        self.wave.set_state("alert")
        QTimer.singleShot(6000, lambda: self.wave.set_state("idle"))

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
        self.wave.set_state("active")
        self.status_label.setText("thinking...")
        self.input_field.setEnabled(False)
        self._stream_buffer = ""

        extensions    = ["scratchpad"] if _SCRATCHPAD else []
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
        self.history = self.history[-20:]

        self._log("trinity", clean)
        log.info(f"Response ({len(clean)} chars){' [scratch]' if self._scratchpad and self._scratchpad._visible else ''}")
        try:
            request_wake(self.profile["id"])
        except Exception:
            pass
        self.wave.set_state("idle")
        self.status_label.setText("watching")
        self.input_field.setEnabled(True)
        self.input_field.setFocus()
        self._idle_timer.start()

        self._display(clean)
        if self.tts_enabled:
            self._tts_stop = False
            self._tts_active = True
            if self._kokoro is not None:
                threading.Thread(target=self._speak_chunked, args=(clean,), daemon=True).start()
            else:
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

    # --- Live activity feed ---
    def _init_activity_log(self):
        """Seek to end of today's trinity log so we only tail new entries."""
        from datetime import date
        log_path = Path(__file__).parent.parent / "logs" / f"trinity_{date.today()}.log"
        self._activity_file_pos = log_path.stat().st_size if log_path.exists() else 0
        self._log_poll.start()

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
