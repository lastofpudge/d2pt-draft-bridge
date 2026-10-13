import json
import sys
from urllib.request import Request, urlopen

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


RECOMMENDATIONS_URL = 'http://127.0.0.1:8765/recommendations'
DRAFT_URL = 'http://127.0.0.1:8765/draft'
WATCHING_URL = 'http://127.0.0.1:8765/watching'


class RecommendationPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('D2PT Recommendations')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(360)
        self._signature = None
        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def _build_ui(self):
        self.setStyleSheet('''
            QWidget {
                background: #101719;
                color: #edf5f2;
                font-family: "Segoe UI";
            }
            QLabel#eyebrow { color: #83d9c4; font-size: 10px; font-weight: 700; }
            QLabel#side { color: #101719; background: #83d9c4; border-radius: 9px;
                          padding: 4px 8px; font-size: 10px; font-weight: 700; }
            QLabel#subtitle { color: #91a29e; font-size: 11px; }
            QLabel#watchState { color: #83d9c4; font-size: 10px; font-weight: 700; }
            QLabel#emptyTitle { color: #edf5f2; font-size: 13px; font-weight: 700; }
            QLabel#emptyBody { color: #91a29e; font-size: 11px; }
            QPushButton#watchButton {
                background: #192326;
                border: 1px solid #293638;
                border-radius: 6px;
                padding: 5px 8px;
                color: #edf5f2;
                font-size: 10px;
            }
            QPushButton#watchButton:hover { background: #20312f; border-color: #58bea8; }
            QPushButton#watchButton:disabled { color: #63716e; border-color: #202b2d; }
            QPushButton#pickCard {
                text-align: left;
                background: #192326;
                border: 1px solid #293638;
                border-radius: 7px;
                padding: 8px;
            }
            QPushButton#pickCard:hover { background: #20312f; border-color: #58bea8; }
            QPushButton#pickCard QLabel { background: transparent; }
            QLabel#rank { color: #83d9c4; font-size: 11px; font-weight: 700; }
            QLabel#hero { color: #f3f7f5; font-size: 14px; font-weight: 700; }
            QLabel#detail { color: #91a29e; font-size: 10px; }
            QLabel#footer { color: #6f8580; font-size: 10px; }
        ''')

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel('D2PT PICKS')
        title.setObjectName('eyebrow')
        self.side_label = QLabel('WAITING')
        self.side_label.setObjectName('side')
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.side_label)
        root.addLayout(header)

        subtitle = QLabel('Recommended Picks')
        subtitle.setObjectName('subtitle')
        root.addWidget(subtitle)

        controls = QHBoxLayout()
        self.watch_state = QLabel('AUTO-SYNC ON')
        self.watch_state.setObjectName('watchState')
        self.pause_button = QPushButton('Pause')
        self.pause_button.setObjectName('watchButton')
        self.resume_button = QPushButton('Resume')
        self.resume_button.setObjectName('watchButton')
        self.pause_button.clicked.connect(lambda: self.set_watching(False))
        self.resume_button.clicked.connect(lambda: self.set_watching(True))
        controls.addWidget(self.watch_state)
        controls.addStretch(1)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.resume_button)
        root.addLayout(controls)

        self.items_layout = QVBoxLayout()
        self.items_layout.setSpacing(6)
        root.addLayout(self.items_layout)

        self.footer = QLabel('Click a name to copy it; pick manually in Dota.')
        self.footer.setObjectName('footer')
        self.footer.setWordWrap(True)
        root.addWidget(self.footer)

    def _clear_items(self):
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_empty(self, title, body, signature):
        if signature == self._signature:
            return
        self._signature = signature
        self._clear_items()
        self.side_label.setText('WAITING')
        heading = QLabel(title)
        heading.setObjectName('emptyTitle')
        description = QLabel(body)
        description.setObjectName('emptyBody')
        description.setWordWrap(True)
        self.items_layout.addWidget(heading)
        self.items_layout.addWidget(description)

    def _copy_hero(self, name):
        QGuiApplication.clipboard().setText(name)
        self.footer.setText(f'Copied {name}. Select it manually in Dota.')

    def set_watching(self, enabled):
        request = Request(
            WATCHING_URL,
            data=json.dumps({'enabled': enabled}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=1) as response:
                state = json.load(response)
        except (OSError, ValueError):
            self.footer.setText('Could not change auto-sync; check that the bridge is running.')
            return
        self._set_watch_state(state.get('watching', enabled))
        self.footer.setText('Auto-sync paused.' if not enabled else 'Auto-sync resumed; rereading Dota.')

    def _set_watch_state(self, enabled):
        self.watch_state.setText('AUTO-SYNC ON' if enabled else 'AUTO-SYNC PAUSED')
        self.pause_button.setEnabled(enabled)
        self.resume_button.setEnabled(not enabled)

    def _show_recommendations(self, payload):
        side = payload.get('side', '')
        items = payload.get('items', [])
        if not payload.get('updated') or not items:
            self._show_empty(
                'No live recommendations',
                'Open D2PT Drafter and add your current picks.',
                'empty',
            )
            return

        signature = json.dumps([side, items], sort_keys=True)
        if signature == self._signature:
            return
        self._signature = signature
        self._clear_items()
        self.side_label.setText(side.upper())
        self.footer.setText('Click a name to copy it; pick manually in Dota.')

        for index, item in enumerate(items[:5], 1):
            name = str(item.get('name', '')).strip()
            if not name:
                continue

            card = QPushButton()
            card.setObjectName('pickCard')
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setFixedHeight(62)
            card.setToolTip(f'Copy {name}; you choose the hero in Dota')
            card.clicked.connect(lambda checked=False, hero=name: self._copy_hero(hero))

            row = QHBoxLayout(card)
            row.setContentsMargins(2, 0, 2, 0)
            rank = QLabel(f'{index:02d}')
            rank.setObjectName('rank')
            row.addWidget(rank)

            labels = QVBoxLayout()
            labels.setSpacing(2)
            hero = QLabel(name)
            hero.setObjectName('hero')
            details = ' | '.join(
                part for part in (item.get('role', ''), item.get('matches', '')) if part
            )
            detail = QLabel(details)
            detail.setObjectName('detail')
            labels.addWidget(hero)
            labels.addWidget(detail)
            row.addLayout(labels, 1)
            self.items_layout.addWidget(card)

    def refresh(self):
        try:
            with urlopen(RECOMMENDATIONS_URL, timeout=0.4) as response:
                payload = json.load(response)
            with urlopen(DRAFT_URL, timeout=0.4) as response:
                draft = json.load(response)
        except (OSError, ValueError):
            self._show_empty(
                'Bridge offline',
                'Start the bridge to receive recommendations.',
                'offline',
            )
            return

        self._set_watch_state(draft.get('watching', True))
        self._show_recommendations(payload)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('D2PT Recommendations')
    panel = RecommendationPanel()
    panel.show()
    sys.exit(app.exec())