import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QColor, QFont

WIDTH  = 260
HEIGHT = 380

_STYLE_BTN = (
    "QPushButton { background: transparent; color: rgb(60,100,150); border: none; font-size: 11px; }"
    "QPushButton:hover { color: rgb(80,180,255); }"
    "QPushButton:checked { color: rgb(80,180,255); }"
)
_STYLE_PANEL = """
    QWidget#panel {
        background: rgba(8,12,20,230);
        border: 1px solid rgba(40,140,255,70);
        border-right: none;
        border-radius: 6px;
    }
"""


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


class ScratchpadPanel(QWidget):
    """
    Trinity's left hand — extends from the widget body.
    Drop voice/extensions/scratchpad.py to enable; remove it to disable.
    """

    def __init__(self, parent_widget: QWidget):
        super().__init__(None)
        self._parent  = parent_widget
        self._visible = False
        self._queue   = ""

        # Animated writer
        self._write_timer = QTimer(self)
        self._write_timer.setInterval(35)
        self._write_timer.timeout.connect(self._tick_write)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(WIDTH, HEIGHT)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        panel = QWidget(self)
        panel.setObjectName("panel")
        panel.setGeometry(0, 0, WIDTH, HEIGHT)
        panel.setStyleSheet(_STYLE_PANEL)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)

        # toolbar
        bar = QHBoxLayout()
        self._btn_text  = QPushButton("✎")
        self._btn_draw  = QPushButton("✏")
        self._btn_clear = QPushButton("✕")
        label = QPushButton("scratch")
        label.setEnabled(False)
        label.setStyleSheet("QPushButton { background: transparent; color: rgba(60,100,150,180); border: none; font-size: 9px; font-family: Courier New; }")

        for btn in (self._btn_text, self._btn_draw, self._btn_clear):
            btn.setFixedSize(22, 18)
            btn.setStyleSheet(_STYLE_BTN)
            btn.setCheckable(True)

        self._btn_clear.setCheckable(False)
        bar.addWidget(label)
        bar.addStretch()
        bar.addWidget(self._btn_text)
        bar.addWidget(self._btn_draw)
        bar.addWidget(self._btn_clear)
        layout.addLayout(bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(40,100,180,40);")
        layout.addWidget(sep)

        # text area
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

        # draw canvas (hidden initially)
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

    # ── Positioning ───────────────────────────────────────────────────────────

    def _reposition(self):
        if self._parent:
            pg = self._parent.geometry()
            self.move(pg.left() - WIDTH, pg.top())

    def follow_parent(self):
        if self._visible:
            self._reposition()

    # ── Toggle ────────────────────────────────────────────────────────────────

    def toggle(self):
        if self._visible:
            self.hide()
            self._visible = False
        else:
            self._reposition()
            self.show()
            self._visible = True

    # ── Trinity writes to the text layer, animated ───────────────────────────

    def write_trinity(self, text: str):
        if not self._visible:
            self.toggle()
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
        from PyQt6.QtGui import QTextCursor
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(ch)
        self._text.setTextCursor(cursor)
        self._text.verticalScrollBar().setValue(
            self._text.verticalScrollBar().maximum()
        )

    # ── Persistence helpers (caller manages save/restore) ────────────────────

    def get_state(self) -> dict:
        return {
            "text":   self._text.toPlainText(),
            "canvas": self._canvas.to_data(),
        }

    def set_state(self, state: dict):
        self._text.setPlainText(state.get("text", ""))
        self._canvas.from_data(state.get("canvas", []))
