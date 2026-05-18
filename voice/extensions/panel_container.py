"""PanelContainer — tabbed side panel that extends left from the main widget.

Panels are registered explicitly below. To add a new panel:
  1. Create a Panel subclass in voice/extensions/<name>.py
  2. Import it here and add it to _PANEL_REGISTRY
  3. Add an entry in panel_config.json
"""
import json
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget, QFrame
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor

from voice.extensions.scratchpad import ScratchpadContent
from voice.extensions.hud import HUDContent

# Registry: name → Panel class. Order here is the fallback if config missing.
_PANEL_REGISTRY: dict[str, type] = {
    "scratchpad": ScratchpadContent,
    "hud":        HUDContent,
}

WIDTH  = 260
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "panel_config.json"
)

_TAB_BASE = (
    "QPushButton { background: transparent; color: rgba(60,100,150,180); "
    "border: none; font-family: 'Courier New'; font-size: 9px; "
    "padding: 2px 6px; border-bottom: 1px solid transparent; }"
    "QPushButton:hover { color: rgb(80,180,255); }"
)
_TAB_ACTIVE = (
    "QPushButton { background: transparent; color: rgb(80,180,255); "
    "border: none; font-family: 'Courier New'; font-size: 9px; "
    "padding: 2px 6px; border-bottom: 1px solid rgb(80,180,255); }"
)
_PANEL_STYLE = """
    QWidget#container {
        background: rgba(8,12,20,230);
        border: 1px solid rgba(40,140,255,70);
        border-right: none;
        border-radius: 6px;
    }
"""


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


class PanelContainer(QWidget):
    """Tabbed panel container. Sits left of the main widget."""

    def __init__(self, parent_widget: QWidget):
        super().__init__(None)
        self._parent  = parent_widget
        self._visible = False
        self._panels: list  = []  # (name, Panel instance)
        self._tab_btns: list = []
        self._active_idx = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        cfg = _load_config()
        self._build_panels(cfg)
        self._build_ui()
        self._reposition()

        # Refresh HUD and others on a 30s timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30_000)
        self._refresh_timer.timeout.connect(self._refresh_all)
        self._refresh_timer.start()

    # ── Panel instantiation ────────────────────────────────────────────────────

    def _build_panels(self, cfg: dict):
        panel_cfg = cfg.get("panels", {})
        ordered = sorted(
            [(name, cls) for name, cls in _PANEL_REGISTRY.items()],
            key=lambda item: panel_cfg.get(item[0], {}).get("order", 99)
        )
        for name, cls in ordered:
            entry = panel_cfg.get(name, {})
            if not entry.get("enabled", cls.DEFAULT_ENABLED):
                continue
            panel = cls()
            self._panels.append((name, panel))

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        if not self._panels:
            return

        container = QWidget(self)
        container.setObjectName("container")
        container.setStyleSheet(_PANEL_STYLE)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tab bar
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(6, 4, 6, 0)
        tab_bar.setSpacing(0)

        for i, (name, panel) in enumerate(self._panels):
            btn = QPushButton(panel.NAME or name)
            btn.setStyleSheet(_TAB_ACTIVE if i == 0 else _TAB_BASE)
            btn.clicked.connect(lambda checked, idx=i: self._switch(idx))
            tab_bar.addWidget(btn)
            self._tab_btns.append(btn)

        tab_bar.addStretch()
        layout.addLayout(tab_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(40,100,180,40);")
        layout.addWidget(sep)

        # Stacked content
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        for _, panel in self._panels:
            self._stack.addWidget(panel)
        layout.addWidget(self._stack)

        # Size the container to fill self
        self._container = container
        self._relayout()

    def _relayout(self):
        h = self._parent.height() if self._parent else 400
        self.setFixedSize(WIDTH, h)
        if hasattr(self, "_container"):
            self._container.setGeometry(0, 0, WIDTH, h)

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _switch(self, idx: int):
        self._active_idx = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_btns):
            btn.setStyleSheet(_TAB_ACTIVE if i == idx else _TAB_BASE)

    # ── Positioning ───────────────────────────────────────────────────────────

    def _reposition(self):
        if self._parent:
            pg = self._parent.geometry()
            self._relayout()
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

    def is_visible(self) -> bool:
        return self._visible

    # ── Profile ───────────────────────────────────────────────────────────────

    def set_profile_id(self, profile_id: str):
        for _, panel in self._panels:
            panel.set_profile_id(profile_id)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh_all(self):
        for _, panel in self._panels:
            panel.refresh()

    # ── State broadcast ───────────────────────────────────────────────────────

    def on_trinity_state(self, state: str):
        for _, panel in self._panels:
            panel.on_trinity_state(state)

    # ── Scratchpad access (backward-compat) ───────────────────────────────────

    @property
    def scratchpad(self) -> "ScratchpadContent | None":
        for name, panel in self._panels:
            if name == "scratchpad":
                return panel
        return None
