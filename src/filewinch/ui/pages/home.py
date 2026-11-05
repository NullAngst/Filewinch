"""Home: one question, three answers."""

from __future__ import annotations

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from filewinch.ui.widgets.common import Banner, button, hint, title


class HomePage(QWidget):
    name = "home"
    label = "Home"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 32, 40, 32)
        lay.setSpacing(14)
        lay.addWidget(title("What happened?"))
        lay.addWidget(hint("Pick one. FileWinch walks you through the rest, and never writes to the drive "
                           "you are recovering from."))
        self.banner = Banner()
        lay.addWidget(self.banner)
        lay.addSpacing(10)

        b1 = button("Recover deleted files\nFrom a drive, USB stick, SD card, disc or disk image", big=True)
        b1.clicked.connect(lambda: state.navigate.emit("drives"))
        b2 = button("Repair a file\nA video, photo, archive, PDF or database that will not open", big=True)
        b2.clicked.connect(lambda: state.navigate.emit("repair"))
        b3 = button("Fix a partition table\nThe drive shows up but its partitions are gone", big=True)
        b3.clicked.connect(lambda: state.navigate.emit("partitions"))
        for b in (b1, b2, b3):
            lay.addWidget(b)
        lay.addStretch(1)
        state.deps_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        missing = self.state.report.missing_required()
        if missing:
            self.banner.show_message(
                "Required tools are missing: " + ", ".join(t.key for t in missing) +
                ". The Dependencies page has the install command.", "warn")
        else:
            self.banner.clear_message()
