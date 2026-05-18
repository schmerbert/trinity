from PyQt6.QtWidgets import QWidget


class Panel(QWidget):
    """Base class for all Trinity side panels.

    Drop a subclass in voice/extensions/ and register it in panel_config.json
    to add a new panel to the container.
    """
    NAME: str = ""
    DESCRIPTION: str = ""
    DEFAULT_ENABLED: bool = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile_id: str | None = None

    def set_profile_id(self, profile_id: str):
        self._profile_id = profile_id
        self.on_profile_ready()

    def on_profile_ready(self):
        """Called once the profile_id is available. Override to do initial load."""
        pass

    def refresh(self):
        """Pull fresh data and update display. Called on a timer by the container."""
        pass

    def on_trinity_state(self, state: str):
        """Called when Trinity's state changes. Override if panel reacts to it."""
        pass
