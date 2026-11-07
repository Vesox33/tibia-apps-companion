"""Hunt Uploader – strona „Hunty” aplikacji Tibia Apps Companion.

Logika (Playwright, analizatory, spawny, historia) jest w browser.py / parser.py / spawns.py / storage.py
i nie zależy od interfejsu – ten plik jest przeniesieniem dawnego okna Tkinter do PySide6.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
                               QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from . import parser, spawns
from .browser import PREY_TYPES, BrowserWorker, FillJob
from .storage import History, Settings, app_dir

DATA = Path(__file__).with_name("data")
NO_TEMPLATE = "(bez templatu)"
NO_SPOT = "(nie wykryto)"
NO_FLOOR = "(bez piętra)"
FLOOR_PLACEHOLDER = "wybierz piętro…"
PAGE_CHARACTER = "(jak na stronie)"
EVENTS = ["Double XP", "Double Loot", "Rapid Respawn", "Double XP & Skill"]
BROWSER_CHOICES = [("auto", "Automatycznie (Edge → Chrome → Brave → Chromium)"), ("msedge", "Microsoft Edge"),
                   ("chrome", "Google Chrome"), ("brave", "Brave"), ("chromium", "Chromium (Playwright)")]

C = dict(text="#e7e7ea", muted="#8b8f98", ok="#22c55e", warn="#f59e0b", err="#ef4444", info="#38bdf8",
         accent="#f97316")


def colored(label: QLabel, role: str):
    label.setStyleSheet(f"color:{C.get(role, C['text'])};")


def section(text: str) -> QLabel:
    l = QLabel(text.upper())
    l.setStyleSheet(f"color:{C['muted']}; font-size:11px; font-weight:700;")
    return l


def small_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("small")
    return b


class _Bridge(QObject):
    """Zdarzenia z wątku przeglądarki/sieci → wątek GUI."""
    event = Signal(str, object)


# ---------------------------------------------------------------- prey
class PreyRow(QWidget):
    def __init__(self, ui: "HuntUploaderWidget", data=None):
        super().__init__()
        data = data or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.cb_type = QComboBox()
        self.cb_type.addItems(list(PREY_TYPES))
        self.cb_type.setCurrentText(data.get("type", PREY_TYPES[0]))
        lay.addWidget(self.cb_type)
        self.cb_level = QComboBox()
        self.cb_level.addItems([f"{i}★" for i in range(1, 11)])
        self.cb_level.setCurrentText(f"{data.get('level', 10)}★")
        self.cb_level.setFixedWidth(64)
        lay.addWidget(self.cb_level)
        self.cb_creature = QComboBox()
        self.cb_creature.setEditable(True)
        self.cb_creature.setCurrentText(data.get("creature", ""))
        lay.addWidget(self.cb_creature, 1)
        rm = small_btn("✕")
        rm.setFixedWidth(30)
        rm.clicked.connect(lambda: QTimer.singleShot(0, lambda: ui.remove_prey(self)))
        lay.addWidget(rm)

    def set_creatures(self, names):
        cur = self.cb_creature.currentText()
        self.cb_creature.clear()
        self.cb_creature.addItems(names)
        self.cb_creature.setCurrentText(cur or (names[0] if names else ""))

    def value(self) -> dict:
        return {"type": self.cb_type.currentText(), "level": int(self.cb_level.currentText().rstrip("★") or 10),
                "creature": self.cb_creature.currentText().strip()}


# ---------------------------------------------------------------- główny widżet
class HuntUploaderWidget(QWidget):
    """host: okno główne – wspólny log/status (host.log), dźwięki (host.sounds), konta (host.set_account)."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.settings = Settings.load()
        self.history = History()
        self.db = spawns.SpawnDB(spawns.load_local())
        self.detect: spawns.DetectResult | None = None
        self.name_edited = False
        self.template_manual = False
        self._detected_text = None
        self._mode_text = ""
        self.prey_rows: list[PreyRow] = []

        self.bridge = _Bridge()
        self.bridge.event.connect(self._handle)
        self._build()

        self.browser = BrowserWorker(self.settings, lambda k, p: self.bridge.event.emit(k, p))
        # schowek: tekst sprzed uruchomienia programu nie jest brany pod uwagę
        self.clip = QGuiApplication.clipboard()
        self._last_clip = self.clip.text()
        self.clip.dataChanged.connect(self._on_clip_changed)
        self.apply_settings()

        self.log(f"Ustawienia i dane: {app_dir()}")
        self.log(f"Spawny: wersja {self.db.version or '?'} ({len(self.db.spots)} lokacji).")
        if spawns.needs_update():
            self.update_spawns()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        body = QHBoxLayout()
        body.setSpacing(22)
        v.addLayout(body, 1)

        # --- lewa: analizatory
        self.nb = QTabWidget()
        self.texts: dict[str, QPlainTextEdit] = {}
        self.text_info: dict[str, QLabel] = {}
        self.tab_index: dict[str, int] = {}
        self._debounce: dict[str, QTimer] = {}
        mono = QFont("Cascadia Mono" if sys.platform == "win32" else "DejaVu Sans Mono", 9)
        for i, kind in enumerate((parser.HUNT, parser.PARTY, parser.INPUT)):
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(0, 8, 0, 0)
            t = QPlainTextEdit()
            t.setLineWrapMode(QPlainTextEdit.NoWrap)
            t.setFont(mono)
            t.setObjectName("analyzer")
            wl.addWidget(t, 1)
            bar = QHBoxLayout()
            info = QLabel("")
            bar.addWidget(info, 1)
            clr = small_btn("Wyczyść")
            clr.clicked.connect(lambda _=False, k=kind: self.set_text(k, ""))
            bar.addWidget(clr)
            wl.addLayout(bar)
            self.nb.addTab(w, self._tab_title(kind, False))
            self.tab_index[kind] = i
            self.texts[kind] = t
            self.text_info[kind] = info
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(350)
            timer.timeout.connect(lambda k=kind: self._on_text_changed(k))
            self._debounce[kind] = timer
            t.textChanged.connect(timer.start)
        body.addWidget(self.nb, 5)

        # --- prawa: formularz (przewijany, gdy okno jest niskie)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        right = QWidget()
        scroll.setWidget(right)
        r = QVBoxLayout(right)
        r.setContentsMargins(0, 0, 6, 0)

        r.addWidget(section("Spot"))
        sf = QHBoxLayout()
        self.cb_spot = QComboBox()
        self.cb_spot.currentIndexChanged.connect(lambda _: self._on_spot_selected())
        sf.addWidget(self.cb_spot, 1)
        self.cb_floor = QComboBox()
        self.cb_floor.setMinimumWidth(140)
        self.cb_floor.currentIndexChanged.connect(lambda _: self._update_name())
        sf.addWidget(self.cb_floor)
        r.addLayout(sf)
        self.lbl_spot_info = QLabel("")
        self.lbl_spot_info.setWordWrap(True)
        colored(self.lbl_spot_info, "warn")
        self.lbl_spot_info.hide()
        r.addWidget(self.lbl_spot_info)

        r.addSpacing(10)
        r.addWidget(section("Nazwa huntu"))
        nf = QHBoxLayout()
        self.ed_name = QLineEdit()
        self.ed_name.textEdited.connect(lambda _: setattr(self, "name_edited", True))
        self.ed_name.editingFinished.connect(self.refresh_template_choice)
        nf.addWidget(self.ed_name, 1)
        rs = small_btn("↺")
        rs.setToolTip("Przywróć nazwę ze spotu")
        rs.clicked.connect(self._reset_name)
        nf.addWidget(rs)
        r.addLayout(nf)

        tf = QHBoxLayout()
        self.lbl_template = QLabel("")
        self.lbl_template.setWordWrap(True)
        tf.addWidget(self.lbl_template, 1)
        self.btn_tpl_change = small_btn("zmień")
        self.btn_tpl_change.clicked.connect(self._toggle_template)
        tf.addWidget(self.btn_tpl_change)
        r.addLayout(tf)
        self.tpl_row = QWidget()
        tl = QHBoxLayout(self.tpl_row)
        tl.setContentsMargins(0, 0, 0, 0)
        self.cb_template = QComboBox()
        self.cb_template.activated.connect(lambda _: self._on_template_manual())
        tl.addWidget(self.cb_template, 1)
        rf = small_btn("⟳")
        rf.setToolTip("Odczytaj listę templatów ze strony")
        rf.clicked.connect(self.refresh_templates)
        tl.addWidget(rf)
        self.tpl_row.hide()
        r.addWidget(self.tpl_row)

        self.char_box = QWidget()
        cl = QVBoxLayout(self.char_box)
        cl.setContentsMargins(0, 10, 0, 0)
        cl.addWidget(section("Postać"))
        self.cb_char = QComboBox()
        self.cb_char.activated.connect(lambda _: self._on_char_selected())
        cl.addWidget(self.cb_char)
        r.addWidget(self.char_box)

        ob = QHBoxLayout()
        self.btn_prey = small_btn("+ Prey")
        self.btn_prey.clicked.connect(self._toggle_prey)
        ob.addWidget(self.btn_prey)
        self.btn_extras = small_btn("+ Dodatki")
        self.btn_extras.clicked.connect(self._toggle_extras)
        ob.addWidget(self.btn_extras)
        ob.addStretch(1)
        r.addSpacing(10)
        r.addLayout(ob)

        self.prey_box = QWidget()
        pl = QVBoxLayout(self.prey_box)
        pl.setContentsMargins(0, 8, 0, 0)
        ph = QHBoxLayout()
        ph.addWidget(section("Preye"))
        ph.addStretch(1)
        self.btn_add_prey = small_btn("+ dodaj")
        self.btn_add_prey.clicked.connect(lambda: self.add_prey())
        ph.addWidget(self.btn_add_prey)
        z = small_btn("zwiń")
        z.clicked.connect(self._toggle_prey)
        ph.addWidget(z)
        pl.addLayout(ph)
        self.prey_list = QVBoxLayout()
        pl.addLayout(self.prey_list)
        self.prey_box.hide()
        r.addWidget(self.prey_box)

        self.extras_box = QWidget()
        xl = QVBoxLayout(self.extras_box)
        xl.setContentsMargins(0, 8, 0, 0)
        eh = QHBoxLayout()
        eh.addWidget(section("Dodatki do opisu"))
        eh.addStretch(1)
        z2 = small_btn("zwiń")
        z2.clicked.connect(self._toggle_extras)
        eh.addWidget(z2)
        xl.addLayout(eh)
        g = QGridLayout()
        self.chk_xp = QCheckBox("XP Boost")
        g.addWidget(self.chk_xp, 0, 0)
        self.chk_boosted = QCheckBox("Boosted creature")
        g.addWidget(self.chk_boosted, 1, 0)
        self.cb_boosted = QComboBox()
        self.cb_boosted.setEditable(True)
        g.addWidget(self.cb_boosted, 1, 1)
        self.chk_event = QCheckBox("Event")
        g.addWidget(self.chk_event, 2, 0)
        self.cb_event = QComboBox()
        self.cb_event.setEditable(True)
        self.cb_event.addItems(EVENTS)
        g.addWidget(self.cb_event, 2, 1)
        g.setColumnStretch(1, 1)
        xl.addLayout(g)
        self.lbl_extras = QLabel("")
        self.lbl_extras.setWordWrap(True)
        colored(self.lbl_extras, "muted")
        xl.addWidget(self.lbl_extras)
        for chk in (self.chk_xp, self.chk_boosted, self.chk_event):
            chk.toggled.connect(lambda _: self._update_extras())
        self.cb_boosted.currentTextChanged.connect(lambda t: self._update_extras(auto=self.chk_boosted if t.strip() else None))
        self.cb_event.currentTextChanged.connect(lambda t: self._update_extras(auto=self.chk_event if t.strip() else None))
        self.extras_box.hide()
        r.addWidget(self.extras_box)

        r.addStretch(1)
        self.lbl_mode = QLabel("")
        self.lbl_mode.setAlignment(Qt.AlignCenter)
        r.addWidget(self.lbl_mode)
        self.btn_send = QPushButton("Wypełnij formularz")
        self.btn_send.setObjectName("primary")
        self.btn_send.setMinimumHeight(42)
        self.btn_send.clicked.connect(self.send)
        r.addWidget(self.btn_send)
        body.addWidget(scroll, 4)

        for k in self.texts:
            self._set_info_empty(k)
        self._update_extras()

    @staticmethod
    def _tab_title(kind, filled):
        short = {parser.HUNT: "Hunt Analyzer", parser.PARTY: "Party Hunt", parser.INPUT: "Input Analyzer"}[kind]
        return f"{'●' if filled else '○'}  {short}"

    # ------------------------------------------------------------------ ustawienia
    def apply_settings(self):
        s = self.settings
        if s.multi_char and s.characters:
            self.cb_char.clear()
            self.cb_char.addItems([PAGE_CHARACTER] + s.characters)
            self.cb_char.setCurrentText(s.last_character if s.last_character in s.characters else PAGE_CHARACTER)
            self.char_box.show()
        else:
            self.char_box.hide()
        extra = ["SAVE kliknie program" if s.auto_save else "SAVE klikasz sam",
                 "publiczny" if s.make_public else "prywatny"]
        if not s.clipboard_listen:
            extra.append("nasłuch schowka wyłączony")
        self._mode_text = " · ".join(extra)
        for k in self.texts:
            if not self.get_text(k):
                self._set_info_empty(k)
        self.refresh_template_choice()
        self._update_send_state()

    def _on_char_selected(self):
        v = self.cb_char.currentText()
        self.settings.last_character = "" if v == PAGE_CHARACTER else v
        self.settings.save()

    # ------------------------------------------------------------------ zwijane sekcje
    def _toggle_template(self):
        vis = not self.tpl_row.isVisible()
        self.tpl_row.setVisible(vis)
        self.btn_tpl_change.setText("ukryj" if vis else "zmień")

    def _toggle_prey(self):
        if self.prey_box.isVisible():
            self.prey_box.hide()
        else:
            self.prey_box.show()
            if not self.prey_rows:
                self.add_prey()
        self._update_option_buttons()

    def _toggle_extras(self):
        self.extras_box.setVisible(not self.extras_box.isVisible())
        self._update_option_buttons()

    def _update_option_buttons(self):
        n = len(self.prey_rows)
        self.btn_prey.setText(f"Prey ({n})" if n else "+ Prey")
        x = sum(1 for c in (self.chk_xp, self.chk_boosted, self.chk_event) if c.isChecked())
        self.btn_extras.setText(f"Dodatki ({x})" if x else "+ Dodatki")

    # ------------------------------------------------------------------ preye
    def add_prey(self, data=None):
        if len(self.prey_rows) >= 3:
            return
        row = PreyRow(self, data)
        self.prey_list.addWidget(row)
        self.prey_rows.append(row)
        row.set_creatures(self._killed_names())
        self.btn_add_prey.setEnabled(len(self.prey_rows) < 3)
        self._update_option_buttons()

    def remove_prey(self, row):
        if row not in self.prey_rows:
            return
        self.prey_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.btn_add_prey.setEnabled(True)
        if not self.prey_rows:
            self.prey_box.hide()
        self._update_option_buttons()

    def _killed_names(self) -> list[str]:
        kills = parser.parse_killed_monsters(self.get_text(parser.HUNT))
        counts: dict[str, int] = {}
        for game, n in kills.items():
            name = self.db.index.match(game) or game.title()
            counts[name] = counts.get(name, 0) + n
        return [n for n, _ in sorted(counts.items(), key=lambda x: -x[1])]

    # ------------------------------------------------------------------ dodatki
    def _extras_line(self) -> str:
        parts = []
        if self.chk_xp.isChecked():
            parts.append("XP Boost")
        if self.chk_boosted.isChecked() and self.cb_boosted.currentText().strip():
            parts.append(f"Boosted creature: {self.cb_boosted.currentText().strip()}")
        if self.chk_event.isChecked() and self.cb_event.currentText().strip():
            parts.append(f"Event: {self.cb_event.currentText().strip()}")
        return " · ".join(parts)

    def _update_extras(self, auto=None):
        if auto is not None and self.extras_box.isVisible():
            auto.blockSignals(True)
            auto.setChecked(True)
            auto.blockSignals(False)
        line = self._extras_line()
        self.lbl_extras.setText(f"Do opisu: {line}" if line else
                                "Nie ma ich na stronie – zostaną dopisane do opisu huntu.")
        self._update_option_buttons()

    # ------------------------------------------------------------------ log / status
    def log(self, msg):
        self.host.log(msg, "Hunty")

    def status(self, msg, role="muted"):
        self.host.status(msg, role)

    # ------------------------------------------------------------------ analizatory
    def get_text(self, kind) -> str:
        return self.texts[kind].toPlainText().strip()

    def set_text(self, kind, text):
        self.texts[kind].setPlainText(text or "")
        self._debounce[kind].stop()
        self._on_text_changed(kind)

    def _set_info_empty(self, kind):
        hint = "pusto – w grze kliknij „Copy to clipboard”" if self.settings.clipboard_listen else \
            "pusto – wklej tekst z analizatora (Ctrl+V)"
        self.text_info[kind].setText(hint)
        colored(self.text_info[kind], "muted")
        self.nb.setTabText(self.tab_index[kind], self._tab_title(kind, False))

    def _on_text_changed(self, kind):
        text = self.get_text(kind)
        if not text:
            self._set_info_empty(kind)
        else:
            sess = (parser.session_line(text) or "").replace("Session data: ", "")
            dup = self.history.find([parser.fingerprint(text)])
            info = sess or f"{len(text.splitlines())} linii"
            if dup:
                info = f"⚠ już wysłany ({dup.get('sent_at', '')}) · " + info
            self.text_info[kind].setText(info)
            colored(self.text_info[kind], "err" if dup else "ok")
            self.nb.setTabText(self.tab_index[kind], self._tab_title(kind, True))
        if kind == parser.HUNT and text != self._detected_text:
            self._detected_text = text
            self.detect_spot()
            names = self._killed_names()
            for row in self.prey_rows:
                row.set_creatures(names)
            cur = self.cb_boosted.currentText()
            self.cb_boosted.blockSignals(True)
            self.cb_boosted.clear()
            self.cb_boosted.addItems(names)
            self.cb_boosted.setCurrentText(cur)
            self.cb_boosted.blockSignals(False)

    def _fingerprints(self):
        fps = [parser.fingerprint(self.get_text(k)) for k in (parser.PARTY, parser.HUNT) if self.get_text(k)]
        if not fps and self.get_text(parser.INPUT):
            fps = [parser.fingerprint(self.get_text(parser.INPUT))]
        return fps

    # ------------------------------------------------------------------ schowek
    def _on_clip_changed(self):
        if not self.settings.clipboard_listen:
            return
        text = self.clip.text()
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        self.on_clipboard(text)

    def on_clipboard(self, text):
        kind = parser.classify(text)
        if not kind:
            return
        text = parser.normalize_text(text)
        if text == self.get_text(kind):
            return
        self.set_text(kind, text)
        self.nb.setCurrentIndex(self.tab_index[kind])
        dup = self.history.find([parser.fingerprint(text)])
        if self.settings.sound:
            self.host.sounds.play("clip_dup" if dup else "clip")
        if dup:
            self.log(f"⚠ {parser.KIND_LABELS[kind]}: ten tekst był już wysłany wcześniej.")
        else:
            self.log(f"Schowek: rozpoznano {parser.KIND_LABELS[kind]}.")

    # ------------------------------------------------------------------ spot
    def detect_spot(self):
        kills = parser.parse_killed_monsters(self.get_text(parser.HUNT))
        self.cb_spot.blockSignals(True)
        self.cb_spot.clear()
        if not kills:
            self.detect = None
            self.cb_spot.addItem(NO_SPOT)
            self.cb_spot.blockSignals(False)
            self.cb_floor.clear()
            self.cb_floor.hide()
            self._spot_info("Brak listy „Killed Monsters” w Hunt Analyzerze." if self.get_text(parser.HUNT) else "")
            self.refresh_template_choice()
            self._update_send_state()
            return
        self.detect = self.db.detect(kills)
        self.template_manual = False
        labels = []
        for c in self.detect.candidates:
            extra = f" – {c.spot.area}" if c.spot.area and c.spot.area not in c.spot.display else ""
            labels.append(f"{c.spot.display}{extra} · {c.coverage:.0%}")
        info = []
        if labels:
            self.cb_spot.addItems(labels)
            self.cb_spot.setCurrentIndex(0)
            if self.detect.ambiguous:
                info.append(f"Pasuje kilka lokacji ({len(labels)}) – wybierz właściwą z listy.")
        else:
            self.cb_spot.addItem(NO_SPOT)
            info.append("Nie znaleziono lokacji dla tych potworów.")
        self.cb_spot.blockSignals(False)
        if self.detect.unknown:
            info.append("Nieznane potwory: " + ", ".join(self.detect.unknown[:8]))
        self._spot_info("  ".join(info))
        self._on_spot_selected()

    def _spot_info(self, text):
        self.lbl_spot_info.setText(text)
        self.lbl_spot_info.setVisible(bool(text))

    def current_spot(self):
        if not self.detect or not self.detect.candidates:
            return None
        i = self.cb_spot.currentIndex()
        return self.detect.candidates[i].spot if 0 <= i < len(self.detect.candidates) else None

    def _on_spot_selected(self):
        sp = self.current_spot()
        floors = sp.floors if sp else []
        self.cb_floor.blockSignals(True)
        self.cb_floor.clear()
        if floors:
            self.cb_floor.addItems([FLOOR_PLACEHOLDER, NO_FLOOR] + floors)
            self.cb_floor.setCurrentIndex(0)
            self.cb_floor.show()
        else:
            self.cb_floor.hide()
        self.cb_floor.blockSignals(False)
        self.name_edited = False
        self._update_name()

    def current_floor(self):
        f = self.cb_floor.currentText() if self.cb_floor.isVisibleTo(self) else ""
        return None if not f or f in (NO_FLOOR, FLOOR_PLACEHOLDER) else f

    def floor_missing(self) -> bool:
        sp = self.current_spot()
        return bool(sp and sp.floors and self.cb_floor.currentText() == FLOOR_PLACEHOLDER)

    def _reset_name(self):
        self.name_edited = False
        self._update_name()

    def _update_name(self):
        sp = self.current_spot()
        if sp and not self.name_edited:
            self.ed_name.setText(sp.hunt_name(self.current_floor()))
        self.refresh_template_choice()
        self._update_send_state()

    def _update_send_state(self):
        if self.floor_missing():
            self.btn_send.setEnabled(False)
            self.lbl_mode.setText("Wybierz piętro albo „(bez piętra)”")
            colored(self.lbl_mode, "warn")
        else:
            self.btn_send.setEnabled(True)
            self.lbl_mode.setText(self._mode_text)
            colored(self.lbl_mode, "muted")

    # ------------------------------------------------------------------ templaty
    def _on_template_manual(self):
        self.template_manual = True
        self._show_template_state()

    def _match_template(self, templates):
        sp = self.current_spot()
        return spawns.find_template(self.ed_name.text(), sp.display if sp else "", templates)

    def refresh_template_choice(self):
        current = self.cb_template.currentText()
        self.cb_template.blockSignals(True)
        self.cb_template.clear()
        self.cb_template.addItems([NO_TEMPLATE] + list(self.settings.templates))
        if self.template_manual:
            self.cb_template.setCurrentText(current or NO_TEMPLATE)
        else:
            match = self._match_template(self.settings.templates) if self.settings.use_templates else None
            self.cb_template.setCurrentText(match or NO_TEMPLATE)
        self.cb_template.blockSignals(False)
        self._show_template_state()

    def _show_template_state(self):
        t = self.cb_template.currentText()
        role = "info"
        if not self.settings.use_templates:
            msg, role = "Templaty wyłączone w ustawieniach", "muted"
            self.tpl_row.hide()
            self.btn_tpl_change.hide()
        else:
            self.btn_tpl_change.show()
            if t and t != NO_TEMPLATE and self.settings.templates and t not in self.settings.templates:
                msg, role = f"Template „{t}” nie istnieje już na stronie", "err"
            elif t and t != NO_TEMPLATE:
                msg = f"Template: {t}" + (" (wybrany ręcznie)" if self.template_manual else "")
            elif self.template_manual:
                msg, role = "Template: brak (wybrane ręcznie)", "muted"
            elif not self.settings.templates:
                msg, role = "Template: lista zostanie pobrana przy wypełnianiu", "muted"
            else:
                msg, role = "Template: brak dla tego spotu", "muted"
        self.lbl_template.setText(msg)
        colored(self.lbl_template, role)

    def refresh_templates(self):
        self.log("Odczytuję listę templatów ze strony (nic nie jest zapisywane)…")
        self.browser.list_templates()

    # ------------------------------------------------------------------ akcje
    def open_browser(self):
        self.browser.open()

    def spawns_url(self) -> str:
        """Spawny leżą w repozytorium aplikacji – tym samym, z którego idą aktualizacje."""
        repo = getattr(self.host, "update_repo", "")
        return spawns.url_for_repo(repo) if repo else self.settings.spawns_url

    def update_spawns(self, force=False):
        def work():
            data, msg = spawns.download(self.spawns_url())
            self.bridge.event.emit("spawns", {"data": data, "msg": msg})
        if force or spawns.needs_update():
            self.log("Sprawdzam aktualizację spawnów…")
            threading.Thread(target=work, daemon=True).start()

    def _ask(self, title, text, warn=False) -> bool:
        icon = QMessageBox.Warning if warn else QMessageBox.Question
        box = QMessageBox(icon, title, text, QMessageBox.Yes | QMessageBox.No, self)
        return box.exec() == QMessageBox.Yes

    def send(self):
        s = self.settings
        if self.floor_missing():
            QMessageBox.warning(self, "Piętro", "Wybierz piętro albo „(bez piętra)”.")
            self.cb_floor.setFocus()
            return
        party, hunt, inp = (self.get_text(k) for k in (parser.PARTY, parser.HUNT, parser.INPUT))
        if not party and not hunt:
            QMessageBox.warning(self, "Brak danych", "Potrzebny jest Party Hunt Analyzer albo Hunt Analyzer.")
            return
        name = self.ed_name.text().strip()
        if not name and not self._ask("Brak nazwy", "Nazwa huntu jest pusta. Kontynuować?"):
            return
        fps = self._fingerprints()
        dup = self.history.find(fps)
        if dup and not self._ask("Hunt już wysłany",
                                 f"Ten hunt był już wysłany {dup.get('sent_at', '')} jako „{dup.get('hunt_name', '')}”.\n\n"
                                 "Wysłać mimo to?", warn=True):
            return
        character = ""
        if s.multi_char and s.characters and self.cb_char.currentText() not in ("", PAGE_CHARACTER):
            character = self.cb_char.currentText()
        preys = [row.value() for row in self.prey_rows]
        for i, pr in enumerate(preys, 1):
            if not pr["creature"]:
                QMessageBox.warning(self, "Prey", f"Prey {i}: wybierz albo wpisz potwora.")
                return
        if self.chk_boosted.isChecked() and not self.cb_boosted.currentText().strip():
            QMessageBox.warning(self, "Boosted creature", "Zaznaczono „Boosted creature”, ale nie wybrano potwora.")
            return
        tpl = self.cb_template.currentText()
        sp = self.current_spot()
        job = FillJob(hunt_name=name, party=party, hunt=hunt, input=inp,
                      character=character, make_public=s.make_public,
                      use_templates=s.use_templates,
                      template_manual=self.template_manual,
                      template=(tpl if tpl and tpl != NO_TEMPLATE else None),
                      match_names=(name, sp.display if sp else ""),
                      auto_save=s.auto_save, fingerprints=fps,
                      preys=preys, description_extra=self._extras_line())
        self.log(f"Wypełniam formularz: „{name}”" + (f", postać „{character}”" if character else "")
                 + (f", preye: {len(preys)}" if preys else "")
                 + (", SAVE automatycznie." if job.auto_save else ", SAVE klikasz sam."))
        self.browser.fill(job)

    # ------------------------------------------------------------------ zdarzenia z wątków
    def _handle(self, kind, p):
        p = p or {}
        if kind == "log":
            self.log(p["msg"])
        elif kind == "need_login":
            self.host.set_account("hunt", False)
            self.status("hunt-analyser.com: zaloguj się w otwartym oknie przeglądarki", "warn")
        elif kind in ("logged_in", "opened"):
            self.host.set_account("hunt", True)
            self.status("Przeglądarka gotowa", "ok")
        elif kind == "cookies":
            self.host.sync_cookies_from_filler(p.get("cookies") or [])
        elif kind == "closed":
            self.status("Przeglądarka zamknięta")
        elif kind == "error":
            self.log(f"✖ Błąd: {p.get('msg', '')} – szczegóły w Ustawienia → Dane i log")
        elif kind == "filled":
            self.status("Formularz wypełniony" + ("" if p.get("auto_save") else " – kliknij SAVE w przeglądarce"), "ok")
        elif kind == "templates":
            self.settings.templates = p["names"]
            self.settings.templates_updated = time.time()
            self.settings.save()
            if p["names"]:
                self.log("Templaty: " + ", ".join(p["names"]))
            self.refresh_template_choice()
        elif kind == "fill_aborted":
            self.status("Wypełnianie przerwane", "err")
            QMessageBox.warning(self, "Formularz nie został wypełniony", p.get("msg", ""))
        elif kind == "template_used":
            self.log(f"Template: „{p['name']}”." if p.get("name") else "Bez templatu.")
        elif kind == "saved":
            job = p.get("job")
            self.host.set_account("hunt", True)
            if job:
                self.history.add(job.fingerprints, job.hunt_name, p.get("location", ""))
                self.log(f"✔ Hunt „{job.hunt_name}” zapisany na stronie.")
                for k in self.texts:
                    self._on_text_changed(k)
            else:
                self.log("✔ Strona zapisała hunt (wysłany ręcznie, bez programu).")
        elif kind == "save_failed":
            self.log(f"✖ Strona odrzuciła zapis (HTTP {p.get('status')}). Sprawdź komunikat w przeglądarce.")
        elif kind == "spawns":
            self.log(p["msg"])
            if p["data"]:
                self.db = spawns.SpawnDB(p["data"])
                if self.get_text(parser.HUNT):
                    self.detect_spot()

    def shutdown(self):
        self.settings.save()
        self.browser.shutdown()


