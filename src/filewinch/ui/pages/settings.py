"""Settings: output folder, carved-file toggles, lossy codec, theme."""

from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
                             QVBoxLayout, QWidget)

from filewinch.ui import theme as theme_mod
from filewinch.ui.widgets.common import button, hint, title


class SettingsPage(QWidget):
    name = "settings"
    label = "Settings"

    def __init__(self, state, apply_theme, parent=None):
        super().__init__(parent)
        self.state = state
        self.apply_theme = apply_theme
        s = state.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Settings"))

        out = QGroupBox("Output")
        f = QFormLayout(out)
        row = QHBoxLayout()
        self.out_edit = QLineEdit(s.output_dir)
        self.out_edit.editingFinished.connect(self._save)
        pick = button("Choose")
        pick.clicked.connect(self._pick)
        row.addWidget(self.out_edit, 1)
        row.addWidget(pick)
        f.addRow("Output folder", row)
        f.addRow("", hint("Recovered files go here. It must be on a different drive than the one you "
                          "recover from; FileWinch checks every time."))
        lay.addWidget(out)

        carved = QGroupBox("Carved files")
        v = QVBoxLayout(carved)
        self.rename = QCheckBox("Rename photos and videos by capture date (exiftool)")
        self.rename.setChecked(s.rename_by_date)
        self.auto = QCheckBox("Send every carved video to Repair when a carve finishes")
        self.auto.setChecked(s.auto_repair_videos)
        for c in (self.rename, self.auto):
            c.toggled.connect(self._save)
            v.addWidget(c)
        v.addWidget(hint("Both are off by default and both are also buttons on the Results page."))
        lay.addWidget(carved)

        rep = QGroupBox("Repair")
        f2 = QFormLayout(rep)
        self.codec = QComboBox()
        self.codec.addItem("H.265 (smaller, default)", "h265")
        self.codec.addItem("H.264 (older players)", "h264")
        self.codec.setCurrentIndex(0 if s.lossy_codec == "h265" else 1)
        self.codec.currentIndexChanged.connect(self._save)
        f2.addRow("Lossy re-encode codec", self.codec)
        lay.addWidget(rep)

        look = QGroupBox("Theme")
        f3 = QFormLayout(look)
        self.theme = QComboBox()
        for t in theme_mod.available():
            self.theme.addItem(t.name, t.key)
        idx = self.theme.findData(s.theme)
        self.theme.setCurrentIndex(max(0, idx))
        self.theme.currentIndexChanged.connect(self._save)
        self.follow = QCheckBox("Follow the system's light or dark setting (Light and Dark themes only)")
        self.follow.setChecked(s.theme_follows_system)
        self.follow.toggled.connect(self._save)
        f3.addRow("Theme", self.theme)
        f3.addRow("", self.follow)
        lay.addWidget(look)
        lay.addStretch(1)

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_edit.text())
        if d:
            self.out_edit.setText(d)
            self._save()

    def _save(self) -> None:
        s = self.state.settings
        old_theme = (s.theme, s.theme_follows_system)
        s.output_dir = self.out_edit.text().strip() or s.output_dir
        s.rename_by_date = self.rename.isChecked()
        s.auto_repair_videos = self.auto.isChecked()
        s.lossy_codec = self.codec.currentData()
        s.theme = self.theme.currentData()
        s.theme_follows_system = self.follow.isChecked()
        try:
            s.save()
        except OSError:
            pass
        if (s.theme, s.theme_follows_system) != old_theme:
            self.apply_theme()
        self.state.settings_changed.emit()
