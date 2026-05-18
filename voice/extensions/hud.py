from PyQt6.QtWidgets import QVBoxLayout, QLabel, QFrame, QScrollArea, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from voice.extensions.base import Panel

_HEADER_STYLE = (
    "QLabel { color: rgba(60,100,150,200); font-family: 'Courier New'; "
    "font-size: 8px; letter-spacing: 2px; }"
)
_CONTENT_STYLE = (
    "QLabel { color: rgb(170,210,255); font-family: 'Courier New'; "
    "font-size: 9px; padding: 2px 0px; }"
)
_FEED_STYLE = (
    "QLabel { color: rgba(100,140,180,200); font-family: 'Courier New'; "
    "font-size: 8px; padding: 1px 0px; }"
)
_FEED_HEADER_STYLE = (
    "QLabel { color: rgba(40,80,120,200); font-family: 'Courier New'; "
    "font-size: 8px; letter-spacing: 2px; }"
)


def _sep():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(40,100,180,30);")
    return line


class HUDContent(Panel):
    """Live scratchpad sections (arc, pending, shelf-summary) + wake cycle feed strip."""

    NAME = "hud"
    DESCRIPTION = "Live state: arc, pending, shelf, recent cycles"

    # Sections shown in order (must match Trinity's scratchpad section keys)
    SECTIONS = [
        ("arc",           "ARC"),
        ("pending",       "PENDING"),
        ("shelf-summary", "SHELF"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._section_labels: dict[str, QLabel] = {}
        self._feed_labels: list[QLabel] = []
        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 3px; background: transparent; }
            QScrollBar::handle:vertical { background: rgba(40,100,180,120); border-radius: 1px; }
        """)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 4, 6, 8)
        layout.setSpacing(6)

        for key, heading in self.SECTIONS:
            hdr = QLabel(heading)
            hdr.setStyleSheet(_HEADER_STYLE)
            layout.addWidget(hdr)

            lbl = QLabel("—")
            lbl.setStyleSheet(_CONTENT_STYLE)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
            layout.addWidget(lbl)
            self._section_labels[key] = lbl

            layout.addWidget(_sep())

        # Feed strip
        feed_hdr = QLabel("RECENT CYCLES")
        feed_hdr.setStyleSheet(_FEED_HEADER_STYLE)
        layout.addWidget(feed_hdr)

        for _ in range(3):
            lbl = QLabel("—")
            lbl.setStyleSheet(_FEED_STYLE)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
            self._feed_labels.append(lbl)

        layout.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def refresh(self):
        if not self._profile_id:
            return
        try:
            from brain.memory import get_scratchpad, get_wake_history
            pad = get_scratchpad(self._profile_id)
            for key, _ in self.SECTIONS:
                text = (pad.get(key) or "").strip() or "—"
                # truncate very long sections for display
                if len(text) > 400:
                    text = text[:400] + "…"
                self._section_labels[key].setText(text)

            history = get_wake_history(self._profile_id, limit=3)
            for i, lbl in enumerate(self._feed_labels):
                if i < len(history):
                    entry = history[-(i + 1)]
                    at = entry.get("at", "")[:16].replace("T", " ")
                    summary = entry.get("summary", "—")
                    if len(summary) > 60:
                        summary = summary[:60] + "…"
                    lbl.setText(f"{at}  {summary}")
                else:
                    lbl.setText("—")
        except Exception:
            pass

    def on_profile_ready(self):
        self.refresh()
