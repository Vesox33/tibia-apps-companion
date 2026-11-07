"""Jedno okno ustawień całej aplikacji."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

import api

C_TEXT, C_MUTED, C_FREE, C_ERR, C_PANEL, C_BORDER = "#e7e7ea", "#8b8f98", "#22c55e", "#ef4444", "#17181b", "#2a2c31"

SECTIONS = ["👤   Konta", "📅   Rezerwacje", "📤   Hunty", "🔊   Dźwięk i powiadomienia", "⬆   Aktualizacje", "🗂   Dane i log"]


def h1(text, sub=None):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 8)
    t = QLabel(text)
    t.setStyleSheet("font-size:18px; font-weight:800;")
    v.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setStyleSheet(f"color:{C_MUTED};")
        v.addWidget(s)
    return w


def group(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 10, 14, 12)
    v.setSpacing(6)
    t = QLabel(title.upper())
    t.setStyleSheet(f"color:{C_MUTED}; font-size:11px; font-weight:700;")
    v.addWidget(t)
    return f, v


def hint(text: str) -> QLabel:
    l = QLabel(text)
    l.setWordWrap(True)
    l.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
    return l


class SettingsDialog(QDialog):
    def __init__(self, win, section: int = 0):
        super().__init__(win)
        self.win = win
        self.cfg = win.cfg
        self.hs = win.hunt.settings if win.hunt else None
        self.setWindowTitle("Ustawienia – Tibia Apps Companion")
        self.resize(860, 600)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(230)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 18, 12, 12)
        t = QLabel("Ustawienia")
        t.setStyleSheet("font-size:18px; font-weight:800;")
        sv.addWidget(t)
        sv.addSpacing(10)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(SECTIONS)
        sv.addWidget(self.nav, 1)
        body.addWidget(side)

        self.stack = QStackedWidget()
        for build in (self.page_accounts, self.page_resp, self.page_hunt, self.page_sound, self.page_update, self.page_data):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(26, 20, 26, 12)
            lay.setSpacing(12)
            build(lay)
            lay.addStretch(1)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setWidget(page)
            self.stack.addWidget(sc)
        body.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(section)

        foot = QFrame()
        foot.setObjectName("footer")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 10, 16, 10)
        fl.addWidget(hint("Zmiany zapisują się po kliknięciu „Zapisz”. Konta i dźwięk działają od razu."), 1)
        c = QPushButton("Anuluj")
        c.clicked.connect(self.reject)
        fl.addWidget(c)
        ok = QPushButton("Zapisz")
        ok.setObjectName("primary")
        ok.clicked.connect(self.save)
        fl.addWidget(ok)
        outer.addWidget(foot)
        self.finished.connect(self._cleanup)

    # ------------------------------------------------------------ Konta
    def page_accounts(self, lay):
        lay.addWidget(h1("Konta", "Obie strony logujesz w ten sam sposób – w okienku aplikacji, bezpośrednio na stronie. "
                                  "Aplikacja nie zna Twoich haseł; sesja jest zapamiętywana."))
        self.acc_rows = {}
        from app import SITES
        for key in ("tibia", "hunt"):
            name, purpose = SITES[key]
            f = QFrame()
            f.setObjectName("card")
            h = QHBoxLayout(f)
            h.setContentsMargins(16, 12, 16, 12)
            dot = QLabel("●")
            h.addWidget(dot)
            col = QVBoxLayout()
            col.setSpacing(1)
            n = QLabel(name)
            n.setStyleSheet("font-size:15px; font-weight:800;")
            col.addWidget(n)
            p = QLabel(f"Do: {purpose}")
            p.setStyleSheet(f"color:{C_MUTED};")
            col.addWidget(p)
            st = QLabel("")
            col.addWidget(st)
            h.addLayout(col, 1)
            btn = QPushButton()
            btn.setMinimumWidth(120)
            btn.clicked.connect(lambda _=False, k=key: self._acc_clicked(k))
            h.addWidget(btn)
            lay.addWidget(f)
            self.acc_rows[key] = (dot, st, btn)
        lay.addWidget(hint("Formularz huntów wypełnia osobne okno przeglądarki (Edge/Chrome). Sesję z logowania "
                           "w aplikacji przekazuję mu automatycznie, a jeśli zalogujesz się w tamtym oknie – "
                           "przenoszę ją z powrotem do aplikacji."))
        self.refresh_accounts()

    def refresh_accounts(self):
        for key, (dot, st, btn) in self.acc_rows.items():
            s = self.win.accounts.get(key)
            color = C_FREE if s else (C_MUTED if s is None else C_ERR)
            dot.setStyleSheet(f"color:{color}; font-size:18px;")
            text = "Zalogowano" if s else ("Sprawdzam…" if s is None else "Niezalogowano")
            if key == "tibia" and s and self.win.board and self.win.board.author:
                text += f" · rezerwujesz jako {self.win.board.author}"
            st.setText(text)
            st.setStyleSheet(f"color:{color}; font-weight:600;")
            btn.setText("Wyloguj" if s else "Zaloguj")
            btn.setObjectName("" if s else "primary")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _acc_clicked(self, key):
        self.win.login_site(key)
        self.refresh_accounts()

    # ------------------------------------------------------------ Rezerwacje
    def page_resp(self, lay):
        lay.addWidget(h1("Rezerwacje", "Tablica respów z tibialoot.com."))
        g, v = group("Odświeżanie")
        r = QHBoxLayout()
        r.addWidget(QLabel("Odświeżaj tablicę co"))
        self.sp_refresh = QSpinBox()
        self.sp_refresh.setRange(30, 3600)
        self.sp_refresh.setSuffix(" s")
        self.sp_refresh.setValue(int(self.cfg.data["refresh_s"]))
        r.addWidget(self.sp_refresh)
        r.addStretch(1)
        v.addLayout(r)
        v.addWidget(hint("Od tego zależy, jak szybko obserwacja zauważy zwolnienie. Minimum 30 s, żeby nie obciążać strony."))
        lay.addWidget(g)

        g, v = group("Powiadomienia")
        self.chk_fav = QCheckBox("Powiadom, gdy ulubiony resp się zwolni")
        self.chk_fav.setChecked(bool(self.cfg.data.get("notify", True)))
        v.addWidget(self.chk_fav)
        lay.addWidget(g)

        g, v = group("Zaawansowane")
        r = QHBoxLayout()
        r.addWidget(QLabel("ID serwera Discord"))
        self.ed_guild = QLineEdit(self.cfg.data["guild_id"])
        r.addWidget(self.ed_guild, 1)
        v.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("Ciasteczko awaryjne"))
        self.ed_cookie = QLineEdit(self.cfg.data.get("cookie_header", ""))
        self.ed_cookie.setEchoMode(QLineEdit.Password)
        self.ed_cookie.setPlaceholderText("zostaw puste")
        r.addWidget(self.ed_cookie, 1)
        v.addLayout(r)
        v.addWidget(hint("Ciasteczko wpisuj tylko, gdy logowanie w aplikacji nie działa – przełącza pobieranie na tryb zapasowy."))
        lay.addWidget(g)

    # ------------------------------------------------------------ Hunty
    def page_hunt(self, lay):
        lay.addWidget(h1("Hunty", "Wysyłanie analizatorów na hunt-analyser.com."))
        if not self.hs:
            lay.addWidget(hint("Moduł huntów nie jest dostępny (szczegóły w „Dane i log”)."))
            return
        s = self.hs
        from huntuploader.qt_ui import BROWSER_CHOICES

        g, v = group("Po wypełnieniu formularza")
        self.rb_fill = QRadioButton("Tylko wypełnij – SAVE klikam sam (bezpieczniej)")
        self.rb_auto = QRadioButton("Wypełnij i od razu kliknij SAVE")
        bg = QButtonGroup(self)
        bg.addButton(self.rb_fill)
        bg.addButton(self.rb_auto)
        (self.rb_auto if s.auto_save else self.rb_fill).setChecked(True)
        v.addWidget(self.rb_fill)
        v.addWidget(self.rb_auto)
        self.chk_public = QCheckBox("Make it public (hunt widoczny dla innych)")
        self.chk_public.setChecked(s.make_public)
        v.addWidget(self.chk_public)
        self.chk_tpl = QCheckBox("Używaj templatów o nazwie spotu")
        self.chk_tpl.setChecked(s.use_templates)
        v.addWidget(self.chk_tpl)
        lay.addWidget(g)

        g, v = group("Analizatory")
        self.chk_listen = QCheckBox("Przechwytuj „Copy to clipboard” z gry (nasłuch schowka)")
        self.chk_listen.setChecked(s.clipboard_listen)
        v.addWidget(self.chk_listen)
        lay.addWidget(g)

        g, v = group("Postacie")
        self.rb_single = QRadioButton("Jedna postać – zostaw wybór strony")
        self.rb_multi = QRadioButton("Kilka postaci – wybieram przy huncie")
        bg2 = QButtonGroup(self)
        bg2.addButton(self.rb_single)
        bg2.addButton(self.rb_multi)
        (self.rb_multi if s.multi_char else self.rb_single).setChecked(True)
        v.addWidget(self.rb_single)
        v.addWidget(self.rb_multi)
        self.txt_chars = QPlainTextEdit("\n".join(s.characters))
        self.txt_chars.setPlaceholderText("Jedna postać w linii – dokładnie jak na stronie")
        self.txt_chars.setMaximumHeight(96)
        self.txt_chars.setEnabled(s.multi_char)
        self.rb_multi.toggled.connect(self.txt_chars.setEnabled)
        v.addWidget(self.txt_chars)
        lay.addWidget(g)

        g, v = group("Przeglądarka do wypełniania formularza")
        self.browser_btns = {}
        bg3 = QButtonGroup(self)
        cur = s.browser_channel if s.browser_channel in dict(BROWSER_CHOICES) else "auto"
        for val, label in BROWSER_CHOICES:
            rb = QRadioButton(label)
            rb.setChecked(val == cur)
            bg3.addButton(rb)
            v.addWidget(rb)
            self.browser_btns[val] = rb
        v.addWidget(hint("Chromium trzeba raz pobrać: python -m playwright install chromium"))
        lay.addWidget(g)

    # ------------------------------------------------------------ Dźwięk
    def page_sound(self, lay):
        lay.addWidget(h1("Dźwięk i powiadomienia", "Jedna głośność dla wszystkich dźwięków aplikacji."))
        g, v = group("Głośność")
        r = QHBoxLayout()
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setSingleStep(5)
        self.vol.setPageStep(10)
        self.vol.setValue(int(self.cfg.data.get("volume", 80)))
        r.addWidget(self.vol, 1)
        self.vol_lbl = QLabel()
        self.vol_lbl.setMinimumWidth(70)
        r.addWidget(self.vol_lbl)
        v.addLayout(r)
        r = QHBoxLayout()
        for name, label in (("alarm", "▶ Alarm snajpera"), ("clip", "▶ Przechwycony analizator"),
                            ("clip_dup", "▶ Analizator już wysłany")):
            b = QPushButton(label)
            b.setObjectName("small")
            b.clicked.connect(lambda _=False, n=name: self.win.sounds.play(n))
            r.addWidget(b)
        r.addStretch(1)
        v.addLayout(r)
        self.vol.valueChanged.connect(self._vol_changed)
        self.vol.sliderReleased.connect(lambda: self.win.sounds.play("alarm"))
        self._vol_label(self.vol.value())
        lay.addWidget(g)

        g, v = group("Kiedy grać")
        self.chk_clip_sound = QCheckBox("Dźwięk po przechwyceniu analizatora")
        self.chk_clip_sound.setChecked(bool(self.hs.sound) if self.hs else False)
        self.chk_clip_sound.setEnabled(self.hs is not None)
        v.addWidget(self.chk_clip_sound)
        v.addWidget(hint("Alarm snajpera i obserwacji gra zawsze (chyba że głośność = 0). "
                         "Powiadomienia pojawiają się też w zasobniku systemowym."))
        lay.addWidget(g)

    def _vol_label(self, v):
        self.vol_lbl.setText("wyciszone" if v == 0 else f"{v}%")

    def _vol_changed(self, v):
        self._vol_label(v)
        self.cfg.data["volume"] = v
        self.cfg.save()
        self.win.sounds.set_volume(v)

    # ------------------------------------------------------------ Aktualizacje
    def page_update(self, lay):
        from version import VERSION
        import updater
        lay.addWidget(h1("Aktualizacje", "Nowe wersje instalują się automatycznie: aplikacja pobiera je, zamyka się na chwilę "
                                         "i uruchamia ponownie. Ustawienia i dane zostają bez zmian."))
        g, v = group("Twoja wersja")
        r = QHBoxLayout()
        cur = QLabel(f"<span style='font-size:20px; font-weight:800'>{VERSION}</span>")
        r.addWidget(cur)
        r.addStretch(1)
        self.upd_btn = QPushButton("Sprawdź teraz")
        self.upd_btn.clicked.connect(self._check_now)
        r.addWidget(self.upd_btn)
        v.addLayout(r)
        self.upd_lbl = QLabel("")
        self.upd_lbl.setWordWrap(True)
        v.addWidget(self.upd_lbl)
        self.chk_auto_upd = QCheckBox("Sprawdzaj automatycznie (przy starcie i co 6 godzin)")
        self.chk_auto_upd.setChecked(bool(self.cfg.data.get("auto_update", True)))
        v.addWidget(self.chk_auto_upd)
        v.addWidget(hint(f"Folder aplikacji: {updater.APP_DIR}"))
        lay.addWidget(g)


    def _check_now(self):
        self.upd_btn.setEnabled(False)
        self.upd_lbl.setText("Sprawdzam…")

        def done(rel, err):
            try:
                self.upd_btn.setEnabled(True)
                if err:
                    self.upd_lbl.setText(f"<span style='color:{C_ERR}'>✕ {err}</span>")
                elif rel:
                    self.upd_lbl.setText(f"<span style='color:{C_FREE}'>Dostępna wersja {rel.version}</span>")
                else:
                    self.upd_lbl.setText(f"<span style='color:{C_FREE}'>✓ Masz najnowszą wersję.</span>")
            except RuntimeError:
                pass   # okno ustawień już zamknięte
        self.win.check_updates(silent=False, done=done)

    # ------------------------------------------------------------ Dane i log
    def page_data(self, lay):
        lay.addWidget(h1("Dane i log"))
        from huntuploader.storage import app_dir
        g, v = group("Foldery z danymi")
        rows = [("Rezerwacje, snajper, logowanie", self.cfg.dir)]
        if self.hs:
            rows.append(("Hunty: ustawienia, historia, spawny", app_dir()))
        for label, path in rows:
            r = QHBoxLayout()
            l = QLabel(f"{label}<br><span style='color:{C_MUTED}; font-size:11px'>{path}</span>")
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r.addWidget(l, 1)
            if sys.platform == "win32":
                b = QPushButton("Otwórz")
                b.setObjectName("small")
                b.clicked.connect(lambda _=False, p=path: os.startfile(p))
                r.addWidget(b)
            v.addLayout(r)
        lay.addWidget(g)

        if self.win.hunt:
            g, v = group("Spawny")
            db = self.win.hunt.db
            r = QHBoxLayout()
            r.addWidget(QLabel(f"Wersja {db.version or '?'} · {len(db.spots)} lokacji · aktualizacja raz na dobę"), 1)
            b = QPushButton("Aktualizuj teraz")
            b.setObjectName("small")
            b.clicked.connect(lambda: self.win.hunt.update_spawns(force=True))
            r.addWidget(b)
            v.addLayout(r)
            lay.addWidget(g)

        g, v = group("Log")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("analyzer")
        self.log_view.setMinimumHeight(220)
        self.log_view.setPlainText("\n".join(self.win.log_lines))
        self.log_view.moveCursor(QTextCursor.End)
        v.addWidget(self.log_view)
        self.win.log_views.append(self.log_view)
        lay.addWidget(g)

    # ------------------------------------------------------------ zapis
    def save(self):
        cfg = self.cfg.data
        cfg["refresh_s"] = self.sp_refresh.value()
        cfg["notify"] = self.chk_fav.isChecked()
        cfg["auto_update"] = self.chk_auto_upd.isChecked()
        guild = self.ed_guild.text().strip() or cfg["guild_id"]
        cookie = self.ed_cookie.text().strip()
        guild_changed = guild != cfg["guild_id"]
        cookie_changed = cookie != cfg.get("cookie_header", "")
        cfg["guild_id"] = guild
        cfg["cookie_header"] = cookie
        self.cfg.save()
        w = self.win
        w.client.guild_id = guild
        if cookie_changed:
            w.client.s.cookies.clear()
            if cookie:
                w.client.set_cookie_header(cookie)
        w.timer.start(cfg["refresh_s"] * 1000)

        if self.hs:
            s = self.hs
            chars = [l.strip() for l in self.txt_chars.toPlainText().splitlines() if l.strip()]
            if self.rb_multi.isChecked() and not chars:
                self.nav.setCurrentRow(2)  # Hunty
                QMessageBox.warning(self, "Postacie", "Wpisz przynajmniej jedną postać albo wybierz „Jedna postać”.")
                return
            old_browser = s.browser_channel
            s.auto_save = self.rb_auto.isChecked()
            s.make_public = self.chk_public.isChecked()
            s.use_templates = self.chk_tpl.isChecked()
            s.clipboard_listen = self.chk_listen.isChecked()
            s.multi_char = self.rb_multi.isChecked()
            s.characters = chars
            if s.last_character not in chars:
                s.last_character = chars[0] if chars else ""
            s.browser_channel = next((k for k, rb in self.browser_btns.items() if rb.isChecked()), "auto")
            s.sound = self.chk_clip_sound.isChecked()
            s.save()
            w.hunt.apply_settings()
            if old_browser != s.browser_channel:
                w.log("Zmieniono przeglądarkę – zadziała przy następnym otwarciu okna przeglądarki.", "Hunty")
        w.log("✔ Zapisano ustawienia.", "Aplikacja")
        if guild_changed or cookie_changed:
            QTimer.singleShot(0, w.refresh)
        self.accept()

    def _cleanup(self, *_):
        if getattr(self, "log_view", None) in self.win.log_views:
            self.win.log_views.remove(self.log_view)
