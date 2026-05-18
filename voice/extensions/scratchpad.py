import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QTextCursor

from voice.extensions.base import Panel

_STYLE_BTN = (
    "QPushButton { background: transparent; color: rgb(60,100,150); border: none; font-size: 11px; }"
    "QPushButton:hover { color: rgb(80,180,255); }"
    "QPushButton:checked { color: rgb(80,180,255); }"
)


class DrawCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._strokes: list[list[QPoint]] = []
        self._current: list[QPoint] = []
        self._color   = QColor(100, 180, 255)
        self._width   = 2
        self.setMinimumHeight(180)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setStyleSheet("background: rgba(6,10,18,200); border-radius: 4px;")

    def mousePressEvent(self, e):
        self._current = [e.position().toPoint()]

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._current.append(e.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, e):
        if self._current:
            self._strokes.append(list(self._current))
            self._current = []

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, self._width,
                   Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for stroke in self._strokes:
            for i in range(1, len(stroke)):
                p.drawLine(stroke[i - 1], stroke[i])
        for i in range(1, len(self._current)):
            p.drawLine(self._current[i - 1], self._current[i])

    def clear(self):
        self._strokes.clear()
        self.update()

    def to_data(self):
        return [[(pt.x(), pt.y()) for pt in s] for s in self._strokes]

    def from_data(self, data):
        self._strokes = [[QPoint(x, y) for x, y in s] for s in data]
        self.update()


class ScratchpadContent(Panel):
    """Trinity's left hand — the text/draw scratchpad panel."""

    NAME = "scratch"
    DESCRIPTION = "Text and drawing scratchpad"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = ""

        self._write_timer = QTimer(self)
        self._write_timer.setInterval(35)
        self._write_timer.timeout.connect(self._tick_write)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        self._btn_text  = QPushButton("✎")
        self._btn_draw  = QPushButton("✏")
        self._btn_clear = QPushButton("✕")

        for btn in (self._btn_text, self._btn_draw, self._btn_clear):
            btn.setFixedSize(22, 18)
            btn.setStyleSheet(_STYLE_BTN)
            btn.setCheckable(True)

        self._btn_clear.setCheckable(False)
        bar.addStretch()
        bar.addWidget(self._btn_text)
        bar.addWidget(self._btn_draw)
        bar.addWidget(self._btn_clear)
        layout.addLayout(bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(40,100,180,40);")
        layout.addWidget(sep)

        self._text = QTextEdit()
        self._text.setFont(QFont("Courier New", 9))
        self._text.setPlaceholderText("notes...")
        self._text.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: rgb(170,210,255);
                border: none;
                padding: 2px;
            }
            QScrollBar:vertical { width: 3px; background: transparent; }
            QScrollBar::handle:vertical { background: rgba(40,100,180,120); border-radius: 1px; }
        """)
        layout.addWidget(self._text)

        self._canvas = DrawCanvas()
        self._canvas.hide()
        layout.addWidget(self._canvas)

        self._btn_text.setChecked(True)
        self._btn_text.clicked.connect(lambda: self._set_mode("text"))
        self._btn_draw.clicked.connect(lambda: self._set_mode("draw"))
        self._btn_clear.clicked.connect(self._clear)

    def _set_mode(self, mode):
        self._btn_text.setChecked(mode == "text")
        self._btn_draw.setChecked(mode == "draw")
        self._text.setVisible(mode == "text")
        self._canvas.setVisible(mode == "draw")

    def _clear(self):
        self._text.clear()
        self._canvas.clear()

    def write_trinity(self, text: str):
        self._queue += text
        if not self._write_timer.isActive():
            self._write_timer.start()

    def _tick_write(self):
        if not self._queue:
            self._write_timer.stop()
            return
        ch = self._queue[0]
        self._queue = self._queue[1:]
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(ch)
        self._text.setTextCursor(cursor)
        self._text.verticalScrollBar().setValue(
            self._text.verticalScrollBar().maximum()
        )

    def get_state(self) -> dict:
        return {
            "text":   self._text.toPlainText(),
            "canvas": self._canvas.to_data(),
        }

    def set_state(self, state: dict):
        self._text.setPlainText(state.get("text", ""))
        self._canvas.from_data(state.get("canvas", []))
