"""Tibia Apps Companion – okno główne: respy, snajper, hunty."""
from __future__ import annotations

import gc
import json
import sys
import traceback
import webbrowser
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import (QEvent, QDate, QDateTime, QObject, QRect, QRunnable, QSize, Qt,
                            QThreadPool, QTime, QTimer, QUrl, Signal, QStandardPaths)
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDateEdit, QDateTimeEdit, QDialog,
                               QDialogButtonBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QSplitter, QSpinBox,
                               QSlider, QSystemTrayIcon, QTabWidget, QTimeEdit, QToolTip, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget, QFormLayout, QCheckBox, QGridLayout, QBoxLayout, QProgressDialog, QStackedWidget, QPlainTextEdit)

import api
import paths
from flowlayout import FlowLayout
import updater
from version import UPDATE_REPO, VERSION
from api import NotLoggedIn, RequestsClient, board_from_html, interpret_post
from parser import Board, Reservation, Spot, parse_spot_page
from sniper import SnipeDialog, Sniper, fire_time
from sounds import Sounds
from slots import MAX_LEN, booking_range, ceil_minutes, free_windows, start_limit, validate

APP_NAME = "Tibia Apps Companion"   # dane: paths.DATA
WINDOW_TITLE = "Tibia Apps Companion"
ICON_PATH = Path(__file__).with_name("assets") / "icon.png"
DEFAULT_GUILD = "806152499760201738"
TZ = ZoneInfo("Europe/Warsaw")   # „Czas serwera (CET)”

C_BG = "#0f1012"
C_PANEL = "#17181b"
C_BORDER = "#2a2c31"
C_TEXT = "#e7e7ea"
C_MUTED = "#8b8f98"
C_ACCENT = "#f97316"
C_FREE = "#22c55e"
C_MINE = "#38bdf8"


def fmt_len(mins: int) -> str:
    h, m = divmod(int(mins), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def now_server() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def to_qdt(dt: datetime) -> QDateTime:
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute))


def from_qdt(q: QDateTime) -> datetime:
    d, t = q.date(), q.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())


# ---------------------------------------------------------------- konfiguracja
class Config:
    def __init__(self):
        base = paths.DATA
        base.mkdir(parents=True, exist_ok=True)
        self.dir = base
        self.path = base / "config.json"
        self.data = {"guild_id": DEFAULT_GUILD, "favorites": [], "refresh_s": 60,
                     "notify": True, "cookie_header": ""}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    @property
    def favorites(self) -> list[str]:
        return self.data["favorites"]

    def toggle_fav(self, sid: str) -> bool:
        if sid in self.favorites:
            self.favorites.remove(sid)
            on = False
        else:
            self.favorites.append(sid)
            on = True
        self.save()
        return on


# ---------------------------------------------------------------- wątki
class _Sig(QObject):
    done = Signal(object)
    fail = Signal(object)


class Job(QRunnable):
    def __init__(self, fn, ok, err):
        super().__init__()
        self.fn, self.sig = fn, _Sig()
        if ok:
            self.sig.done.connect(ok)
        if err:
            self.sig.fail.connect(err)

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # noqa: BLE001
            e.tb = traceback.format_exc()
            self.sig.fail.emit(e)
        else:
            self.sig.done.emit(res)


# ---------------------------------------------------------------- konta i wbudowana przeglądarka
SITES = {
    # klucz: (nazwa wyświetlana, do czego służy)
    "tibia": ("tibialoot.com", "rezerwacje respów"),
    "hunt": ("hunt-analyser.com", "wysyłanie huntów"),
}


class WebSession(QObject):
    """Wspólna wbudowana przeglądarka z trwałym profilem – jedno logowanie do obu stron.
    Zapytania idą przez fetch() w ukrytej karcie danej domeny, więc używają tej samej sesji."""

    TIMEOUT_MS = 30000
    cookie_changed = Signal(str)     # domena

    def __init__(self, cfg: Config):
        super().__init__()
        from PySide6.QtWebEngineCore import QWebEngineProfile
        self.profile = QWebEngineProfile("celesta", self)
        self.profile.setPersistentStoragePath(str(cfg.dir / "web"))
        self.profile.setCachePath(str(cfg.dir / "web-cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.profile.setHttpAcceptLanguage("pl-PL,pl;q=0.9,en;q=0.5")   # strona odpowiada po polsku
        self._cookies: dict[tuple, object] = {}
        store = self.profile.cookieStore()
        store.cookieAdded.connect(self._cookie_added)
        store.cookieRemoved.connect(self._cookie_removed)
        store.loadAllCookies()
        self._anchors: dict[str, dict] = {}   # origin -> {page, ok, loading, waiting}
        self._seq = 0
        self._pending: dict = {}

    # --- ciasteczka (do przekazania przeglądarce wypełniającej formularz)
    @staticmethod
    def _key(c):
        return (c.domain(), c.path(), bytes(c.name()).decode())

    def _cookie_added(self, c):
        self._cookies[self._key(c)] = QNetworkCookie(c)
        self.cookie_changed.emit(c.domain())

    def _cookie_removed(self, c):
        self._cookies.pop(self._key(c), None)

    def cookies_for(self, host: str) -> list[dict]:
        """Ciasteczka danej domeny w formacie Playwright."""
        out = []
        for c in self._cookies.values():
            dom = c.domain()
            if not host.endswith(dom.lstrip(".")):
                continue
            d = {"name": bytes(c.name()).decode(), "value": bytes(c.value()).decode(),
                 "domain": dom, "path": c.path() or "/", "httpOnly": c.isHttpOnly(), "secure": c.isSecure()}
            if c.expirationDate().isValid():
                d["expires"] = c.expirationDate().toSecsSinceEpoch()
            else:
                d["expires"] = -1
            ss = {QNetworkCookie.SameSite.Strict: "Strict", QNetworkCookie.SameSite.Lax: "Lax",
                  QNetworkCookie.SameSite.None_: "None"}.get(c.sameSitePolicy())
            if ss:
                d["sameSite"] = ss
            out.append(d)
        return out

    def import_cookies(self, cookies: list[dict]):
        """Ciasteczka z przeglądarki wypełniającej formularz → wspólny profil."""
        store = self.profile.cookieStore()
        for d in cookies:
            c = QNetworkCookie(d["name"].encode(), d["value"].encode())
            c.setDomain(d.get("domain", ""))
            c.setPath(d.get("path", "/"))
            c.setHttpOnly(bool(d.get("httpOnly")))
            c.setSecure(bool(d.get("secure")))
            if d.get("expires", -1) and d.get("expires", -1) > 0:
                c.setExpirationDate(QDateTime.fromSecsSinceEpoch(int(d["expires"])))
            host = d.get("domain", "").lstrip(".")
            store.setCookie(c, QUrl(f"https://{host}/"))

    def forget(self, host: str):
        store = self.profile.cookieStore()
        for c in list(self._cookies.values()):
            if host.endswith(c.domain().lstrip(".")):
                store.deleteCookie(c)

    # --- zapytania
    @staticmethod
    def _origin(url: str) -> str:
        q = QUrl(url)
        port = f":{q.port()}" if q.port() > 0 else ""
        return f"{q.scheme()}://{q.host()}{port}"

    def _ensure_anchor(self, origin: str, then):
        a = self._anchors.get(origin)
        if a is None:
            from PySide6.QtWebEngineCore import QWebEnginePage
            page = QWebEnginePage(self.profile, self)
            a = {"page": page, "ok": False, "loading": False, "waiting": []}
            page.loadFinished.connect(lambda _ok, o=origin: self._anchor_loaded(o))
            self._anchors[origin] = a
        if a["ok"]:
            then(a["page"])
            return
        a["waiting"].append(then)
        if not a["loading"]:
            a["loading"] = True
            a["page"].load(QUrl(origin + "/robots.txt"))   # lekka strona z tej samej domeny

    def _anchor_loaded(self, origin):
        a = self._anchors[origin]
        a["loading"], a["ok"] = False, True
        waiting, a["waiting"] = a["waiting"], []
        for fn in waiting:
            fn(a["page"])

    def request(self, method: str, url: str, ok, err, data: dict | None = None, headers: dict | None = None):
        self._ensure_anchor(self._origin(url), lambda page: self._send(page, method, url, ok, err, data, headers or {}))

    def _send(self, page, method, url, ok, err, data, headers):
        self._seq += 1
        rid = f"r{self._seq}"
        js = """(function(){
  window.__tl = window.__tl || {};
  var id = %s; window.__tl[id] = null;
  var opts = {method: %s, credentials: 'include', headers: %s, redirect: 'follow'};
  var body = %s;
  if (body) opts.body = new URLSearchParams(body);
  fetch(%s, opts).then(function(r){
    var h = {}; r.headers.forEach(function(v,k){ h[k] = v; });
    return r.text().then(function(t){
      window.__tl[id] = {status: r.status, url: r.url, redirected: r.redirected, headers: h, text: t};
    });
  }).catch(function(e){ window.__tl[id] = {error: String(e)}; });
})();""" % (json.dumps(rid), json.dumps(method), json.dumps(headers), json.dumps(data), json.dumps(url))
        page.runJavaScript(js, 0, lambda _r: None)
        timer = QTimer(self)
        self._pending[rid] = (timer, ok, err, datetime.now(), page, url)
        timer.timeout.connect(lambda rid=rid: self._poll(rid))
        timer.start(150)

    def _poll(self, rid):
        if rid not in self._pending:
            return
        timer, ok, err, t0, page, url = self._pending[rid]
        if (datetime.now() - t0).total_seconds() * 1000 > self.TIMEOUT_MS:
            self._finish(rid)
            err(TimeoutError(f"Brak odpowiedzi od {QUrl(url).host()}"))
            return
        js = "(function(){var v = window.__tl && window.__tl[%s]; if (!v) return null; delete window.__tl[%s]; return JSON.stringify(v);})()" % (json.dumps(rid), json.dumps(rid))

        def got_and_resume(val, rid=rid):
            if rid not in self._pending:
                return
            if val:
                self._finish(rid)
                resp = json.loads(val)
                if resp.get("error"):
                    err(ConnectionError(resp["error"]))
                else:
                    ok(resp)
            else:
                self._pending[rid][0].start(150)
        timer.stop()   # wznowimy, jeśli wynik jeszcze nie gotowy
        page.runJavaScript(js, 0, got_and_resume)

    def _finish(self, rid):
        timer = self._pending.pop(rid)[0]
        timer.stop()
        timer.deleteLater()

    def close_pages(self):
        """Strony muszą zniknąć przed profilem (inaczej Qt ostrzega przy zamykaniu)."""
        import shiboken6
        for a in self._anchors.values():
            shiboken6.delete(a["page"])
        self._anchors.clear()


class LoginDialog(QDialog):
    """Jedno okno logowania – takie samo dla każdej strony."""

    def __init__(self, web: "WebSession", site: str, url: str, parent=None):
        super().__init__(parent)
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        name, purpose = SITES[site]
        self.setWindowTitle(f"Logowanie – {name}")
        self.resize(1040, 780)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        head = QHBoxLayout()
        col = QVBoxLayout()
        t = QLabel(f"Zaloguj się na {name}")
        t.setStyleSheet("font-size:17px; font-weight:800;")
        col.addWidget(t)
        sub = QLabel(f"Do: {purpose}. Logujesz się bezpośrednio na stronie – aplikacja nie zna Twojego hasła. "
                     "Gdy strona pokaże, że jesteś zalogowany, kliknij „Gotowe”.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{C_MUTED};")
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)
        self.addr = QLabel(url)
        self.addr.setStyleSheet(f"color:{C_MUTED}; font-size:11px; background:{C_PANEL}; border:1px solid {C_BORDER};"
                                "border-radius:6px; padding:4px 8px;")
        lay.addWidget(self.addr)
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(web.profile, self.view))
        self.view.urlChanged.connect(lambda u: self.addr.setText("🔒 " + u.toString()))
        self.view.load(QUrl(url))
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Anuluj")
        c.clicked.connect(self.reject)
        row.addWidget(c)
        ok = QPushButton("Gotowe")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)


# ---------------------------------------------------------------- oś czasu
class Timeline(QWidget):
    """Oś czasu od bieżącej godziny przez całą dostępną dobę (+3 h na ostatnią rezerwację)."""
    picked = Signal(object)   # datetime
    DAYS_PL = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self.res: list[Reservation] = []
        self.sel: tuple[datetime, datetime] | None = None
        self.me = ""

    def set_data(self, res: list[Reservation], sel, me: str):
        self.res, self.sel, self.me = res, sel, me
        self.update()

    def _range(self):
        now = now_server()
        t0 = now.replace(minute=0, second=0, microsecond=0)
        t1 = start_limit(now) + MAX_LEN
        t1 = t1.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return t0, t1

    def _bar(self) -> QRect:
        return QRect(10, 22, self.width() - 20, 40)

    def _x(self, dt: datetime) -> float:
        t0, t1 = self._range()
        frac = (dt - t0).total_seconds() / max((t1 - t0).total_seconds(), 1)
        frac = min(max(frac, 0), 1)
        r = self._bar()
        return r.left() + frac * r.width()

    def _dt_at(self, x: int) -> datetime:
        t0, t1 = self._range()
        r = self._bar()
        frac = min(max((x - r.left()) / max(r.width(), 1), 0), 1)
        return t0 + timedelta(minutes=int(frac * (t1 - t0).total_seconds() / 60))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._bar()
        t0, t1 = self._range()
        now = now_server()
        lim = start_limit(now)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C_FREE).darker(260))
        p.drawRoundedRect(r, 6, 6)
        # przeszłość (od pełnej godziny do teraz)
        p.setBrush(QColor("#222327"))
        x = self._x(now)
        p.drawRect(QRect(r.left(), r.top(), int(x - r.left()), r.height()))
        # za limitem początku – można tylko dokończyć rezerwację
        xl = self._x(lim)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRect(QRect(int(xl), r.top(), int(r.right() - xl), r.height()))
        p.setBrush(QBrush(QColor("#6b7280"), Qt.BDiagPattern))
        p.drawRect(QRect(int(xl), r.top(), int(r.right() - xl), r.height()))
        for res in self.res:
            if res.end <= t0 or res.start >= t1:
                continue
            x1, x2 = self._x(res.start), self._x(res.end)
            mine = self.me and self.me in res.authors
            p.setBrush(QColor(C_MINE if mine else C_ACCENT))
            p.drawRoundedRect(QRect(int(x1), r.top() + 3, max(int(x2 - x1), 2), r.height() - 6), 3, 3)
        if self.sel:
            s, e = self.sel
            if e > t0 and s < t1:
                p.setBrush(QColor(255, 255, 255, 40))
                p.setPen(QPen(QColor("#ffffff"), 2))
                x1, x2 = self._x(s), self._x(e)
                p.drawRoundedRect(QRect(int(x1), r.top(), max(int(x2 - x1), 2), r.height()), 3, 3)
        p.setPen(QPen(QColor("#ef4444"), 2))
        p.drawLine(int(x), r.top() - 4, int(x), r.bottom() + 4)
        p.setPen(QPen(QColor("#9ca3af"), 1, Qt.DashLine))
        p.drawLine(int(xl), r.top() - 4, int(xl), r.bottom() + 4)

        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        hours = int((t1 - t0).total_seconds() // 3600)
        for i in range(hours + 1):
            t = t0 + timedelta(hours=i)
            xt = int(self._x(t))
            if t.hour == 0:
                p.setPen(QPen(QColor(C_TEXT), 1))
                p.drawLine(xt, r.top() - 2, xt, r.bottom() + 6)
                fb = QFont(f)
                fb.setBold(True)
                p.setFont(fb)
                p.drawText(QRect(xt + 4, 2, 120, 16), Qt.AlignLeft | Qt.AlignVCenter,
                           f"{self.DAYS_PL[t.weekday()]} {t:%d.%m}")
                p.setFont(f)
            if t.hour % 2 == 0:
                p.setPen(QColor(C_MUTED))
                p.drawLine(xt, r.bottom() + 2, xt, r.bottom() + 6)
                p.drawText(QRect(xt - 20, r.bottom() + 8, 40, 16), Qt.AlignCenter, f"{t.hour:02d}")
        midnight = (t0 + timedelta(days=1)).replace(hour=0)
        if t0.hour != 0 and self._x(midnight) - r.left() > 90:
            p.setPen(QColor(C_MUTED))
            p.drawText(QRect(r.left(), 2, 200, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{self.DAYS_PL[t0.weekday()]} {t0:%d.%m}")
        p.end()

    def mouseMoveEvent(self, ev):
        dt = self._dt_at(int(ev.position().x()))
        tip = f"{self.DAYS_PL[dt.weekday()]} {dt:%H:%M}"
        for res in self.res:
            if res.start <= dt <= res.end:
                tip = f"{res.label}  {res.who}"
                break
        else:
            if dt > start_limit(now_server()):
                tip += "  – za daleko, żeby tu zacząć"
            elif dt >= now_server():
                tip += "  – wolne (kliknij, aby wybrać)"
        QToolTip.showText(ev.globalPosition().toPoint(), tip, self)

    def mousePressEvent(self, ev):
        dt = self._dt_at(int(ev.position().x()))
        self.picked.emit(ceil_minutes(dt, 5) if dt.minute % 5 else dt)


# ---------------------------------------------------------------- pole godziny
class TimeField(QLineEdit):
    """Godzina HH:MM: wpisz i Enter, kółko myszy ±5 min (z Shift ±1 h), strzałki ↑↓ ±5 min, PgUp/PgDn ±1 h."""
    committed = Signal(object)   # QTime
    stepped = Signal(int)        # przesunięcie w minutach

    def __init__(self):
        super().__init__()
        self.setInputMask("99:99")
        self.setAlignment(Qt.AlignCenter)
        self.setToolTip("Wpisz godzinę i Enter • kółko myszy: ±5 min (Shift: ±1 h) • strzałki ↑↓")
        self.editingFinished.connect(self._commit)

    def set_time(self, t: QTime):
        self.blockSignals(True)
        self.setText(t.toString("HH:mm"))
        self.blockSignals(False)

    def _commit(self):
        t = QTime.fromString(self.text(), "HH:mm")
        if t.isValid():
            self.committed.emit(t)
        else:
            self.committed.emit(None)   # przywróci poprzednią wartość

    def wheelEvent(self, ev):
        step = 60 if ev.modifiers() & Qt.ShiftModifier else 5
        self.stepped.emit(step if ev.angleDelta().y() > 0 else -step)
        ev.accept()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            self.stepped.emit(5 if k == Qt.Key_Up else -5)
        elif k in (Qt.Key_PageUp, Qt.Key_PageDown):
            self.stepped.emit(60 if k == Qt.Key_PageUp else -60)
        else:
            super().keyPressEvent(ev)


# ---------------------------------------------------------------- wspólne elementy wyglądu
class ElidedLabel(QLabel):
    """Jednolinijkowa etykieta, która przy braku miejsca skraca tekst wielokropkiem."""

    def __init__(self, text: str = ""):
        super().__init__()
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str):
        self._full = text
        self._elide()

    def text(self) -> str:
        return self._full

    def minimumSizeHint(self):
        return QSize(10, super().minimumSizeHint().height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width() - 4, 10)))

def page_header(title: str, subtitle: str, buttons: list) -> tuple[QWidget, QLabel]:
    """Jednakowy nagłówek każdej strony: tytuł, opis, przyciski po prawej."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 12)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = QLabel(title)
    t.setStyleSheet("font-size:22px; font-weight:800;")
    col.addWidget(t)
    sub = QLabel(subtitle)
    sub.setWordWrap(True)
    sub.setStyleSheet(f"color:{C_MUTED};")
    col.addWidget(sub)
    h.addLayout(col, 1)
    for b in buttons:
        h.addWidget(b, 0, Qt.AlignTop)
    return w, sub


def flow_box(layout) -> QWidget:
    """Opakowuje FlowLayout w widżet, żeby siatka znała wysokość po zawinięciu rzędów."""
    w = QWidget()
    w.setLayout(layout)
    sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    sp.setHeightForWidth(True)
    w.setSizePolicy(sp)
    return w


def section_label(text: str) -> QLabel:
    l = QLabel(text.upper())
    l.setStyleSheet(f"color:{C_MUTED}; font-size:11px; font-weight:700; padding:6px 2px 2px 2px;")
    return l


# ---------------------------------------------------------------- karta ulubionego respa
def pill(text: str, color: str) -> QLabel:
    l = QLabel(text)
    c = QColor(color)
    rgba = lambda a: f"rgba({c.red()},{c.green()},{c.blue()},{a})"
    l.setStyleSheet(f"color:{color}; border:1px solid {rgba(0.35)}; background:{rgba(0.13)};"
                    "border-radius:9px; padding:1px 8px; font-size:11px; font-weight:600;")
    return l


class SpotCard(QFrame):
    def __init__(self, spot: Spot, me: str, on_unfav):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        top = QHBoxLayout()
        name = QLabel(spot.name)
        name.setStyleSheet("font-weight:700; font-size:14px;")
        name.setWordWrap(True)
        top.addWidget(name, 1)
        star = QPushButton("★")
        star.setObjectName("star")
        star.setToolTip("Usuń z ulubionych")
        star.setFixedSize(28, 28)
        star.clicked.connect(lambda: QTimer.singleShot(0, lambda: on_unfav(spot.id)))
        top.addWidget(star)
        lay.addLayout(top)
        row = QHBoxLayout()
        now = now_server()
        upcoming = [r for r in spot.reservations if r.end >= now]
        mine = [r for r in upcoming if me and me in r.authors]
        if spot.is_taken:
            row.addWidget(pill("Zajęty", C_ACCENT))
            row.addWidget(QLabel(f"{spot.holder}  {spot.holder_time}".strip()))
        elif spot.status:
            row.addWidget(pill("Wolny", C_FREE))
        else:
            row.addWidget(pill("?", C_MUTED))
        row.addStretch(1)
        lay.addLayout(row)
        future = [r for r in upcoming if r.start > now]
        nxt = QLabel(("Następna: " + (f"{future[0].start:%d.%m} {future[0].label}  {future[0].who}"
                                       if future else (spot.next_text or "brak"))))
        nxt.setStyleSheet(f"color:{C_MUTED};")
        lay.addWidget(nxt)
        if future:
            cnt = QLabel(f"Zaplanowanych rezerwacji: {len(future)}")
            cnt.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
            lay.addWidget(cnt)
        if mine:
            m = QLabel("Twoja: " + ", ".join(f"{r.start:%d.%m} {r.label}" for r in mine))
            m.setStyleSheet(f"color:{C_MINE}; font-weight:600;")
            lay.addWidget(m)


# ---------------------------------------------------------------- okno główne
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1280, 820)
        self.cfg = Config()
        self.client = RequestsClient(self.cfg.data["guild_id"])
        if self.cfg.data.get("cookie_header"):
            self.client.set_cookie_header(self.cfg.data["cookie_header"])
        self.pool = QThreadPool.globalInstance()
        self.board: Board | None = None
        self.spot_res_cache: dict[str, tuple[datetime, list[Reservation]]] = {}
        self.board_time: datetime | None = None
        self.cur_spot: str | None = None
        self._jobs: set = set()
        self.sel_start: datetime | None = None
        self.want_len = 120      # długość, o którą prosi użytkownik (min)
        self.sel_len = 0         # faktyczna długość po przycięciu do wolnego miejsca
        self._busy = False
        self._prev_status: dict[str, bool] = {}

        try:
            self.web = WebSession(self.cfg)
        except Exception:  # brak QtWebEngine – zostaje ręczne wklejenie ciasteczka
            log_error(traceback.format_exc())
            self.web = None

        self.log_lines: list[str] = []
        self.log_views: list = []
        self.accounts: dict[str, bool | None] = {"tibia": None, "hunt": None}
        try:
            self.sounds = Sounds(self.cfg.dir, int(self.cfg.data.get("volume", 80)))
        except Exception:
            log_error(traceback.format_exc())
            self.sounds = Sounds.__new__(Sounds)
            self.sounds.volume = 0
        self.sniper = Sniper(self.cfg, self.post_reservation, self.spot_reservations, self._snipe_notify,
                             lambda: self.board.author if self.board else "", now_server,
                             prefetch=lambda: self.refresh())

        self._build_ui()
        self._build_tray()
        self.sniper.changed.connect(lambda: QTimer.singleShot(0, self._fill_snipes))
        self._fill_snipes()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(30, int(self.cfg.data["refresh_s"])) * 1000)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._update_clock)
        self.tick.start(1000)
        for m in MIGRATION_MSGS:
            self.log(m, "Aplikacja")
        if self.cfg.data.get("last_version") and self.cfg.data["last_version"] != VERSION:
            self.log(f"✔ Zaktualizowano do wersji {VERSION}.", "Aktualizacje")
        self.cfg.data["last_version"] = VERSION
        self.cfg.save()
        if updater.FAIL_MARKER.exists():
            updater.FAIL_MARKER.unlink(missing_ok=True)
            QTimer.singleShot(1000, lambda: QMessageBox.warning(
                self, "Aktualizacja", "Poprzednia aktualizacja się nie udała – część plików była zablokowana "
                "(np. przez antywirusa albo drugie okno aplikacji). Spróbuj ponownie: Ustawienia → Aktualizacje."))
        QTimer.singleShot(900, self.refresh)  # chwila na wczytanie ciasteczek z profilu
        self.pending_update: updater.Release | None = None
        self._update_asked: set[str] = set()
        QTimer.singleShot(6000, lambda: self.check_updates(silent=True))
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(lambda: self.check_updates(silent=True))
        self.update_timer.start(6 * 3600 * 1000)
        QTimer.singleShot(1200, self.check_hunt_login)
        if self.hunt and not self.hunt.settings.setup_done:   # pierwsze uruchomienie
            self.hunt.settings.setup_done = True
            self.hunt.settings.save()
            QTimer.singleShot(1500, lambda: self.open_settings(0))

    # ---------- UI
    def _build_ui(self):
        # ===== strona „Respy”
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        self.refresh_btn = QPushButton("⟳  Odśwież")
        self.refresh_btn.clicked.connect(self.refresh)
        self.user_lbl = QLabel("")
        hdr, self.resp_sub = page_header("Respy", "Tablica rezerwacji tibialoot.com", [self.refresh_btn])
        v.addWidget(hdr)

        split = QSplitter(Qt.Horizontal)
        self.split = split
        split.setChildrenCollapsible(False)
        v.addWidget(split, 1)

        # lewa strona
        self.tabs = QTabWidget()
        fav_w = QWidget()
        fl = QVBoxLayout(fav_w)
        fl.setContentsMargins(0, 8, 0, 0)
        self.fav_list = QListWidget()
        self.fav_list.setSpacing(4)
        self.fav_list.itemClicked.connect(lambda it: QTimer.singleShot(0, lambda sid=it.data(Qt.UserRole): self.select_spot(sid)))
        fl.addWidget(self.fav_list)
        self.fav_empty = QLabel("Nie masz ulubionych respów.\nW zakładce „Wszystkie” kliknij ☆ przy respie.")
        self.fav_empty.setAlignment(Qt.AlignCenter)
        self.fav_empty.setStyleSheet(f"color:{C_MUTED};")
        fl.addWidget(self.fav_empty)
        self.tabs.addTab(fav_w, "★ Ulubione")

        all_w = QWidget()
        al = QVBoxLayout(all_w)
        al.setContentsMargins(0, 8, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Szukaj respa…")
        self.search.textChanged.connect(self._fill_all)
        al.addWidget(self.search)
        self.only_free = QCheckBox("Tylko wolne teraz")
        self.only_free.toggled.connect(self._fill_all)
        al.addWidget(self.only_free)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "Resp", "Status", "Następna"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setColumnWidth(0, 30)
        self.tree.setColumnWidth(1, 200)
        self.tree.setColumnWidth(2, 60)
        self.tree.itemClicked.connect(self._tree_clicked)
        al.addWidget(self.tree)
        self.tabs.addTab(all_w, "Wszystkie")

        mine_w = QWidget()
        ml = QVBoxLayout(mine_w)
        ml.setContentsMargins(0, 8, 0, 0)
        self.mine_list = QListWidget()
        self.mine_list.setSpacing(4)
        self.mine_list.itemClicked.connect(
            lambda it: it.data(Qt.UserRole) and QTimer.singleShot(0, lambda sid=it.data(Qt.UserRole): self.select_spot(sid)))
        ml.addWidget(self.mine_list)
        self.tabs.addTab(mine_w, "Moje")
        self.tabs.setMinimumWidth(260)
        split.addWidget(self.tabs)

        # ===== strona „Snajper”
        snipe_page = QWidget()
        sl = QVBoxLayout(snipe_page)
        sl.setContentsMargins(0, 0, 0, 0)
        new_snipe = QPushButton("🎯  Nowy snajp")
        new_snipe.clicked.connect(lambda: self._snipe_from_nav("snipe"))
        new_watch = QPushButton("🔔  Nowa obserwacja")
        new_watch.clicked.connect(lambda: self._snipe_from_nav("watch"))
        hdr2, _ = page_header("Snajper",
                              "🎯 Snajp rezerwuje termin w chwili, gdy strona go odblokuje (równo dobę wcześniej). "
                              "🔔 Obserwacja daje znać, gdy w przedziale ktoś zwolni miejsce. Aplikacja musi być włączona.",
                              [new_snipe, new_watch])
        sl.addWidget(hdr2)
        self.snipe_list = QListWidget()
        self.snipe_list.setSpacing(6)
        sl.addWidget(self.snipe_list, 1)
        self._snipe_page = snipe_page
        self._resp_page = root

        # prawa strona – rezerwacja
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 4, 4, 4)
        self.spot_title = QLabel("Wybierz resp z listy")
        self.spot_title.setStyleSheet("font-size:20px; font-weight:800;")
        self.spot_title.setWordWrap(True)
        rl.addWidget(self.spot_title)
        head = FlowLayout()
        self.open_btn = QPushButton("Otwórz na stronie")
        self.open_btn.clicked.connect(lambda: self.cur_spot and webbrowser.open(api.spot_url(self.guild, self.cur_spot)))
        head.addWidget(self.open_btn)
        self.snipe_btn = QPushButton("🎯 Snajpuj")
        self.snipe_btn.setToolTip("Zarezerwuj automatycznie termin, który jeszcze nie jest dostępny")
        self.snipe_btn.clicked.connect(lambda: self.new_snipe("snipe"))
        head.addWidget(self.snipe_btn)
        self.watch_btn = QPushButton("🔔 Obserwuj")
        self.watch_btn.setToolTip("Powiadom, gdy w wybranym przedziale zwolni się miejsce")
        self.watch_btn.clicked.connect(lambda: self.new_snipe("watch"))
        head.addWidget(self.watch_btn)
        rl.addLayout(head)
        self.spot_sub = QLabel("")
        self.spot_sub.setWordWrap(True)
        self.spot_sub.setStyleSheet(f"color:{C_MUTED};")
        rl.addWidget(self.spot_sub)

        self.limit_lbl = QLabel("")
        self.limit_lbl.setWordWrap(True)
        self.limit_lbl.setStyleSheet(f"color:{C_MUTED};")
        rl.addWidget(self.limit_lbl)

        self.timeline = Timeline()
        self.timeline.picked.connect(self._timeline_pick)
        rl.addWidget(self.timeline)
        legend = QLabel(f"<span style='color:{C_FREE}'>■</span> wolne &nbsp; "
                        f"<span style='color:{C_ACCENT}'>■</span> zajęte &nbsp; "
                        f"<span style='color:{C_MINE}'>■</span> Twoje &nbsp; "
                        f"<span style='color:#ffffff'>□</span> wybrany termin &nbsp; "
                        f"<span style='color:#9ca3af'>┆</span> ostatni możliwy początek &nbsp; — kliknij oś, aby wybrać godzinę")
        legend.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        legend.setWordWrap(True)
        rl.addWidget(legend)

        lists = QBoxLayout(QBoxLayout.LeftToRight)
        self.lists_box = lists
        c1 = QVBoxLayout()
        c1.addWidget(self._h("Wolne okna · kliknij, aby wybrać"))
        self.free_list = QListWidget()
        self.free_list.setObjectName("rows")
        self.free_list.setWordWrap(True)
        self.free_list.setSpacing(3)
        self.free_list.setCursor(Qt.PointingHandCursor)
        self.free_list.itemClicked.connect(self._free_clicked)
        c1.addWidget(self.free_list)
        c2 = QVBoxLayout()
        c2.addWidget(self._h("Nadchodzące rezerwacje"))
        self.res_list = QListWidget()
        self.res_list.setObjectName("rows")
        self.res_list.setWordWrap(True)
        self.res_list.setSpacing(3)
        c2.addWidget(self.res_list)
        lists.addLayout(c1, 1)
        lists.addLayout(c2, 1)
        rl.addLayout(lists, 1)

        form = QFrame()
        form.setObjectName("card")
        fl2 = QVBoxLayout(form)
        fl2.setContentsMargins(16, 12, 16, 12)
        fl2.setSpacing(10)
        ftitle = QLabel("Nowa rezerwacja")
        ftitle.setStyleSheet("font-size:14px; font-weight:800;")
        fl2.addWidget(ftitle)
        grid = QVBoxLayout()      # etykieta nad rzędem – rzędy mogą się zawijać przy wąskim oknie
        grid.setSpacing(6)

        def row_label(t):
            l = QLabel(t)
            l.setStyleSheet(f"color:{C_MUTED}; font-weight:600;")
            l.setMinimumWidth(70)
            return l

        # --- początek: dzień + godzina z przyciskami przesunięć
        r1 = FlowLayout()
        r1.setSpacing(6)
        self.sday_group = QButtonGroup(self)
        self.sday_btns = []
        for i in range(2):
            b = QPushButton()
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setMinimumWidth(104)
            self.sday_group.addButton(b, i)
            self.sday_btns.append(b)
            r1.addWidget(b)
        self.sday_group.idClicked.connect(self._start_day_clicked)
        r1.addSpacing(14)
        self.shift_btns = []
        for mins, lbl in ((-60, "−1 h"), (-15, "−15")):
            b = QPushButton(lbl)
            b.setObjectName("step")
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, m=mins: self._shift_start(m))
            r1.addWidget(b)
            self.shift_btns.append(b)
        self.start_time = TimeField()
        self.start_time.setObjectName("bigtime")
        self.start_time.setFixedWidth(84)
        self.start_time.committed.connect(self._start_time_changed)
        self.start_time.stepped.connect(self._shift_start)
        r1.addWidget(self.start_time)
        for mins, lbl in ((15, "+15"), (60, "+1 h")):
            b = QPushButton(lbl)
            b.setObjectName("step")
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, m=mins: self._shift_start(m))
            r1.addWidget(b)
            self.shift_btns.append(b)
        r1.addSpacing(14)
        self.asap_btn = QPushButton("Najwcześniej")
        self.asap_btn.setObjectName("small")
        self.asap_btn.setToolTip("Pierwszy wolny moment na tym respie")
        self.asap_btn.clicked.connect(lambda: self._preselect_first_free())
        r1.addWidget(self.asap_btn)
        r1.addStretch(1)
        grid.addWidget(row_label("Początek"))
        grid.addLayout(r1)
        grid.addSpacing(10)

        # --- długość: gotowe wartości + suwak do dokładnego ustawienia
        r3 = FlowLayout()
        r3.setSpacing(6)
        self.len_group = QButtonGroup(self)
        self.len_group.setExclusive(False)
        self.len_btns = []
        for mins in (30, 60, 90, 120, 150, 180):
            b = QPushButton(fmt_len(mins))
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setProperty("mins", mins)
            b.clicked.connect(lambda _=False, m=mins: self._set_len(m))
            r3.addWidget(b)
            self.len_btns.append(b)
        r3.addSpacing(14)
        self.len_slider = QSlider(Qt.Horizontal)
        self.len_slider.setSingleStep(5)
        self.len_slider.setPageStep(15)
        self.len_slider.setFixedWidth(170)
        self.len_slider.setToolTip("Dokładna długość (co 5 min)")
        self.len_slider.valueChanged.connect(self._len_slider_changed)
        r3.addWidget(self.len_slider)
        r3.addSpacing(10)
        self.len_lbl = QLabel("")
        self.len_lbl.setMinimumWidth(90)
        self.len_lbl.setStyleSheet("font-weight:700;")
        r3.addWidget(self.len_lbl)
        r3.addStretch(1)
        grid.addWidget(row_label("Długość"))
        grid.addLayout(r3)
        self.cap_lbl = QLabel("")
        self.cap_lbl.setWordWrap(True)
        self.cap_lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        grid.addWidget(self.cap_lbl)
        fl2.addLayout(grid)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color:{C_BORDER}; background:{C_BORDER}; max-height:1px;")
        fl2.addWidget(line)

        # --- podsumowanie + przycisk
        r2 = QHBoxLayout()
        sumcol = QVBoxLayout()
        sumcol.setSpacing(2)
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("font-size:17px; font-weight:800;")
        sumcol.addWidget(self.summary_lbl)
        self.valid_lbl = QLabel("")
        self.valid_lbl.setWordWrap(True)
        sumcol.addWidget(self.valid_lbl)
        r2.addLayout(sumcol, 1)
        self.book_btn = QPushButton("Zarezerwuj")
        self.book_btn.setObjectName("primary")
        self.book_btn.setMinimumHeight(40)
        self.book_btn.setMinimumWidth(210)
        self.book_btn.clicked.connect(self.book)
        r2.addWidget(self.book_btn, 0, Qt.AlignVCenter)
        fl2.addLayout(r2)
        hint = QLabel("Najwyżej 3 godziny, początek najwyżej dobę do przodu. Czas serwera (CET).")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        fl2.addWidget(hint)
        rl.addWidget(form)

        split.addWidget(right)
        split.setSizes([400, 880])

        self.status_lbl = ElidedLabel()   # długi tekst skraca się „…” zamiast poszerzać okno
        self.statusBar().addWidget(self.status_lbl, 1)

        # ===== strona „Hunty”
        hunt_page = QWidget()
        hl = QVBoxLayout(hunt_page)
        hl.setContentsMargins(0, 0, 0, 0)
        hdr3, _ = page_header("Hunty", "W grze kliknij „Copy to clipboard” w analizatorze – aplikacja sama go przechwyci, "
                                       "rozpozna spot i wypełni formularz na hunt-analyser.com.", [])
        hl.addWidget(hdr3)
        try:
            from huntuploader.qt_ui import HuntUploaderWidget
            self.hunt = HuntUploaderWidget(self)
            hl.addWidget(self.hunt, 1)
        except Exception:
            self.hunt = None
            log_error(traceback.format_exc())
            err = QLabel("Moduł huntów nie uruchomił się – szczegóły w Ustawienia → Dane i log.\n"
                         "Najczęściej brakuje biblioteki: pip install playwright")
            err.setAlignment(Qt.AlignCenter)
            hl.addWidget(err, 1)

        # ===== powłoka: pasek boczny + strony
        shell = QWidget()
        sh = QHBoxLayout(shell)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(0)
        sh.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        for w in (self._resp_page, self._snipe_page, hunt_page):
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(22, 16, 22, 12)
            wl.addWidget(w)
            self.pages.addWidget(wrap)
        sh.addWidget(self.pages, 1)
        self.setCentralWidget(shell)
        self.go_page(int(self.cfg.data.get("main_tab", 0)))
        self._reservation_enabled(False)

    def _h(self, t):
        l = QLabel(t)
        l.setStyleSheet(f"color:{C_MUTED}; font-size:11px; font-weight:700; text-transform:uppercase;")
        return l

    # ---------- powłoka
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(240)
        self.sidebar = side
        self._compact_hide: list[QWidget] = []
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(4)
        brow = QHBoxLayout()
        brow.setSpacing(10)
        logo = QLabel()
        if ICON_PATH.exists():
            logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(40, 40))
        logo.setFixedSize(40, 40)
        brow.addWidget(logo, 0, Qt.AlignLeft | Qt.AlignVCenter)   # na środku dwóch linijek nazwy
        self._brand_row, self._logo = brow, logo
        brand = QLabel("Tibia Apps<br>Companion")
        brand.setStyleSheet("font-size:16px; font-weight:800; line-height:110%;")
        brow.addWidget(brand, 1)
        v.addLayout(brow)
        tag = QLabel(f"respy · snajper · hunty · v{VERSION}")
        tag.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        v.addWidget(tag)
        self._compact_hide += [brand, tag]
        v.addSpacing(18)
        self.nav_group = QButtonGroup(self)
        self.nav_btns = []
        for i, (icon, name) in enumerate((("📅", "Respy"), ("🎯", "Snajper"), ("📤", "Hunty"))):
            b = QPushButton(f"{icon}   {name}")
            b.setProperty("full", f"{icon}   {name}")
            b.setProperty("short", icon)
            b.setToolTip(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            self.nav_group.addButton(b, i)
            self.nav_btns.append(b)
            v.addWidget(b)
        self.nav_group.idClicked.connect(self.go_page)
        v.addStretch(1)

        lbl_today = section_label("Dziś")
        v.addWidget(lbl_today)
        from today import TodayWidget
        self.today = TodayWidget(self.cfg.dir / "today", now_server, lambda m: self.log(m, "Dziś"))
        v.addWidget(self.today)
        v.addSpacing(8)
        lbl_acc = section_label("Konta")
        v.addWidget(lbl_acc)
        self._compact_hide += [lbl_today, lbl_acc]
        self.acc_widgets = {}
        for key in ("tibia", "hunt"):
            row = QPushButton()
            row.setObjectName("account")
            row.setMinimumHeight(48)
            row.clicked.connect(lambda _=False, k=key: self.login_site(k))
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 10, 6)
            dot = QLabel("●")
            dot.setAttribute(Qt.WA_TransparentForMouseEvents)
            rl.addWidget(dot)
            col = QVBoxLayout()
            col.setSpacing(0)
            nm = QLabel(SITES[key][0])
            nm.setStyleSheet("font-weight:700; background:transparent;")
            st = QLabel("")
            for l in (nm, st):
                l.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(nm)
            col.addWidget(st)
            self._compact_hide += [nm, st]
            rl.addLayout(col, 1)
            v.addWidget(row)
            self.acc_widgets[key] = (row, dot, st)
            self._paint_account(key)
        v.addSpacing(10)
        self.update_btn = QPushButton("")
        self.update_btn.setObjectName("update")
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(lambda: self.pending_update and self.install_update(self.pending_update))
        v.addWidget(self.update_btn)
        sett = QPushButton("⚙   Ustawienia")
        sett.setProperty("full", "⚙   Ustawienia")
        sett.setProperty("short", "⚙")
        sett.setToolTip("Ustawienia")
        self.sett_btn = sett
        sett.setObjectName("nav")
        sett.clicked.connect(lambda: self.open_settings())
        v.addWidget(sett)
        v.addSpacing(8)
        self.clock_lbl = QLabel()
        self.clock_lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        v.addWidget(self.clock_lbl)
        self._compact_hide.append(self.clock_lbl)
        self._side_layout = v
        return side

    # ---------- układ zależny od szerokości okna
    COMPACT_SIDEBAR_BELOW = 1150   # szerokość okna, poniżej której pasek boczny pokazuje same ikony
    STACK_BELOW = 980              # szerokość strony, poniżej której panele układają się pionowo

    def _apply_responsive(self):
        if not hasattr(self, "sidebar"):
            return
        compact = self.width() < self.COMPACT_SIDEBAR_BELOW
        if getattr(self, "_compact", None) != compact:
            self._compact = compact
            self.sidebar.setFixedWidth(78 if compact else 240)
            self._side_layout.setContentsMargins(7 if compact else 14, 18, 7 if compact else 14, 14)
            for w in self._compact_hide:
                w.setVisible(not compact)
            # zwinięty pasek: logo na środku, w jednej linii z ikonami menu
            self._brand_row.setAlignment(self._logo, (Qt.AlignHCenter if compact else Qt.AlignLeft) | Qt.AlignVCenter)
            for b in self.nav_btns + [self.sett_btn]:
                b.setText(b.property("short") if compact else b.property("full"))
                b.setStyleSheet("text-align:center; font-size:18px;" if compact else "")
            for row, dot, st in self.acc_widgets.values():
                row.setMinimumHeight(40 if compact else 48)
            self.today.set_vertical(compact)
            self._paint_update_btn()
        page_w = self.width() - self.sidebar.width()
        stacked = page_w < self.STACK_BELOW
        if getattr(self, "_stacked", None) != stacked:
            self._stacked = stacked
            self.split.setOrientation(Qt.Vertical if stacked else Qt.Horizontal)
            if stacked:
                self.split.setSizes([260, 900])
            else:
                self.split.setSizes([400, 880])
        if self.hunt:
            self.hunt.set_stacked(page_w < 900)
        self.lists_box.setDirection(QBoxLayout.TopToBottom if page_w < 760 else QBoxLayout.LeftToRight)

    def _paint_update_btn(self):
        rel = getattr(self, "pending_update", None)
        if rel is None:
            return
        self.update_btn.setText("⬆" if getattr(self, "_compact", False) else f"⬆   Aktualizacja {rel.version}")
        self.update_btn.setToolTip(f"Aktualizacja {rel.version}")

    def go_page(self, i: int):
        i = max(0, min(int(i), 2))
        self.pages.setCurrentIndex(i)
        self.nav_btns[i].setChecked(True)
        if self.cfg.data.get("main_tab") != i:
            self.cfg.data["main_tab"] = i
            self.cfg.save()

    def _paint_account(self, key: str):
        if not hasattr(self, "acc_widgets"):
            return
        name, purpose = SITES[key]
        st = self.accounts.get(key)
        color, txt = ((C_FREE, "zalogowano") if st else
                      (C_MUTED, "sprawdzam…") if st is None else ("#ef4444", "kliknij, aby zalogować"))
        if key == "tibia" and st and self.board and self.board.author:
            txt = self.board.author
        row, dot, lbl = self.acc_widgets[key]
        dot.setStyleSheet(f"color:{color}; font-size:14px; background:transparent;")
        lbl.setText(txt)
        lbl.setStyleSheet(f"color:{color if st is False else C_MUTED}; font-size:11px; background:transparent;")
        row.setToolTip(f"{name} – {purpose}. " + ("Kliknij, aby się wylogować." if st else "Kliknij, aby się zalogować."))

    def set_account(self, key: str, ok: bool | None):
        if self.accounts.get(key) == ok:
            return
        self.accounts[key] = ok
        self._paint_account(key)
        if getattr(self, "settings_dlg", None):
            self.settings_dlg.refresh_accounts()

    def log(self, msg: str, source: str = "Respy", role: str | None = None):
        line = f"[{datetime.now():%H:%M:%S}] {source}: {msg}"
        self.log_lines.append(line)
        del self.log_lines[:-2000]
        for view in list(self.log_views):
            try:
                view.appendPlainText(line)
            except RuntimeError:
                self.log_views.remove(view)
        if role is None:
            role = "err" if msg.startswith("✖") else "ok" if msg.startswith("✔") else \
                "warn" if msg.startswith("⚠") else "muted"
        self.status(f"{source}: {msg}", role)

    def status(self, msg: str, role: str = "muted"):
        colors = {"err": "#ef4444", "ok": C_FREE, "warn": "#f59e0b", "info": C_MINE, "muted": C_MUTED}
        self.status_lbl.setText(msg if len(msg) <= 140 else msg[:137] + "…")
        self.status_lbl.setToolTip(msg)
        self.status_lbl.setStyleSheet(f"color:{colors.get(role, C_MUTED)};")

    def sync_cookies_from_filler(self, cookies: list[dict]):
        """Logowanie zrobione w oknie przeglądarki wypełniającej → wspólny profil aplikacji."""
        if self.web and cookies:
            self.web.import_cookies(cookies)
            QTimer.singleShot(1500, self.check_hunt_login)

    def push_cookies_to_filler(self):
        if self.web and self.hunt:
            host = QUrl(self.hunt.settings.base_url).host()
            cookies = self.web.cookies_for(host)
            if cookies:
                self.hunt.browser.import_cookies(cookies)

    def check_hunt_login(self):
        if not (self.web and self.hunt):
            return
        url = self.hunt.settings.base_url.rstrip("/") + "/hunt_sessions/new"

        def ok(r):
            final = r.get("url", "")
            logged = 'id="hunt_name"' in r.get("text", "") and "login" not in final.lower()
            self.set_account("hunt", logged)
            if logged:
                self.push_cookies_to_filler()
        self.web.request("GET", url, ok, lambda e: self.set_account("hunt", False))

    def login_site(self, key: str):
        if self.accounts.get(key):
            if QMessageBox.question(self, "Konto", f"Jesteś zalogowany na {SITES[key][0]}.\n\nWylogować z aplikacji?") \
                    == QMessageBox.Yes:
                self.logout_site(key)
            return
        if not self.web:
            QMessageBox.information(self, "Logowanie", "Wbudowana przeglądarka niedostępna (brak QtWebEngine).")
            return
        url = api.board_url(self.guild) if key == "tibia" else \
            self.hunt.settings.base_url.rstrip("/") + "/hunt_sessions/new"
        dlg = LoginDialog(self.web, key, url, self)
        dlg.exec()
        import shiboken6
        shiboken6.delete(dlg)
        if key == "tibia":
            QTimer.singleShot(300, self.refresh)
        else:
            QTimer.singleShot(300, self.check_hunt_login)

    def logout_site(self, key: str):
        if key == "tibia":
            if self.web:
                self.web.forget(QUrl(api.BASE).host())
            self.cfg.data["cookie_header"] = ""
            self.cfg.save()
            self.client.s.cookies.clear()
            self.board = None
            self.fav_list.clear()
            self.tree.clear()
            self.set_account("tibia", False)
            self.log("Wylogowano z tibialoot.com.")
        else:
            if self.web and self.hunt:
                self.web.forget(QUrl(self.hunt.settings.base_url).host())
            self.set_account("hunt", False)
            self.log("Wylogowano z hunt-analyser.com (w aplikacji). Okno przeglądarki do wypełniania "
                     "ma własną kopię sesji – wyloguj się tam, jeśli chcesz.", "Hunty")

    # ---------- aktualizacje
    @property
    def update_repo(self) -> str:
        return (self.cfg.data.get("update_repo") or UPDATE_REPO).strip()

    def check_updates(self, silent: bool = False, done=None):
        if silent and (not self.cfg.data.get("auto_update", True) or not self.update_repo):
            return
        repo = self.update_repo

        def ok(rel):
            self.pending_update = rel
            if rel is None:
                self.update_btn.setVisible(False)
                if not silent:
                    self.log(f"✔ Masz najnowszą wersję ({VERSION}).", "Aktualizacje")
            else:
                self.update_btn.setVisible(True)
                self._paint_update_btn()
                self.log(f"Dostępna nowa wersja {rel.version} (masz {VERSION}).", "Aktualizacje", "info")
                if rel.version not in self._update_asked:
                    self._update_asked.add(rel.version)
                    QTimer.singleShot(0, lambda: self.install_update(rel))
            if done:
                done(rel, None)

        def err(e):
            msg = str(e) if isinstance(e, updater.UpdateError) else f"nie udało się sprawdzić ({e.__class__.__name__})"
            if not silent:
                self.log(f"✖ {msg}", "Aktualizacje")
            if done:
                done(None, msg)
        self.run_job(lambda: updater.check(repo), ok, err)

    def install_update(self, rel: "updater.Release"):
        notes = rel.notes[:1200] + ("…" if len(rel.notes) > 1200 else "")
        box = QMessageBox(QMessageBox.Information, "Nowa wersja",
                          f"<b>Dostępna wersja {rel.version}</b> (masz {VERSION}).<br><br>"
                          + (notes.replace("\n", "<br>") + "<br><br>" if notes else "")
                          + "Aplikacja pobierze aktualizację, zamknie się na chwilę i uruchomi ponownie. "
                            "Ustawienia, ulubione, snajpy i logowania zostaną.", QMessageBox.NoButton, self)
        yes = box.addButton("Zaktualizuj teraz", QMessageBox.AcceptRole)
        box.addButton("Później", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        if sys.platform != "win32":
            QMessageBox.information(self, "Aktualizacja", f"Pobierz ręcznie: {rel.url}")
            return
        if not updater.can_write_app_dir():
            QMessageBox.warning(self, "Aktualizacja",
                                f"Nie mogę zapisywać w folderze aplikacji:\n{updater.APP_DIR}\n\n"
                                "Przenieś folder aplikacji np. do Dokumentów (poza Program Files) i spróbuj ponownie.")
            return
        prog = QProgressDialog("Pobieranie aktualizacji…", None, 0, 0, self)
        prog.setWindowTitle("Aktualizacja")
        prog.setMinimumDuration(0)
        prog.setCancelButton(None)
        prog.setWindowModality(Qt.WindowModal)
        prog.show()

        def ok(root):
            prog.close()
            try:
                updater.launch(root)
            except Exception as e:
                QMessageBox.warning(self, "Aktualizacja", f"Nie udało się uruchomić aktualizacji:\n{e}")
                return
            self.log(f"Instaluję wersję {rel.version} – aplikacja uruchomi się ponownie.", "Aktualizacje")
            self.close()
            QApplication.quit()

        def err(e):
            prog.close()
            QMessageBox.warning(self, "Aktualizacja", f"Nie udało się pobrać aktualizacji:\n{e}\n\nMożesz pobrać ją ręcznie: {rel.url}")
        self.run_job(lambda: updater.prepare(rel), ok, err)

    def open_settings(self, section: int = 0):
        from settings_ui import SettingsDialog
        self.settings_dlg = SettingsDialog(self, section)
        self.settings_dlg.exec()
        import shiboken6
        shiboken6.delete(self.settings_dlg)
        self.settings_dlg = None

    def _snipe_from_nav(self, kind: str):
        if not self.cur_spot:
            self.go_page(0)
            self.log("Najpierw wybierz resp z listy, potem „🎯 Snajpuj” albo „🔔 Obserwuj”.", role="warn")
            return
        self.new_snipe(kind)

    def _build_tray(self):
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else None
            if icon is None:
                pm = QPixmap(32, 32)
                pm.fill(QColor(C_ACCENT))
                icon = QIcon(pm)
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.setToolTip(APP_NAME)
            self.tray.show()
            self.setWindowIcon(icon)

    def _update_clock(self):
        self.clock_lbl.setText(now_server().strftime("Serwer: %H:%M:%S"))
        if self.board:   # snajpy potrzebują zalogowania
            self.sniper.tick(now_server())
        if now_server().second == 0:
            if self.cur_spot:
                self._update_spot_view()   # okno doby przesuwa się z minutą
            self._fill_snipes()

    def _reservation_enabled(self, on: bool):
        for w in (self.start_time, self.len_slider, self.book_btn, self.open_btn,
                  *self.sday_btns, *self.len_btns, *self.shift_btns, self.asap_btn, self.snipe_btn, self.watch_btn):
            w.setEnabled(on)

    # ---------- dane
    def run_job(self, fn, ok, err):
        """Trzyma referencję do zadania, dopóki wynik nie wróci do wątku GUI (inaczej Python
        może usunąć obiekt sygnałów w trakcie – to powodowało losowe crashe)."""
        job = Job(fn, None, None)
        job.setAutoDelete(False)
        self._jobs.add(job)

        def done(res, job=job):
            self._jobs.discard(job)
            ok(res)

        def fail(e, job=job):
            self._jobs.discard(job)
            log_error(getattr(e, "tb", repr(e)))
            err(e)

        job.sig.done.connect(done)
        job.sig.fail.connect(fail)
        self.pool.start(job)

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.setEnabled(False)

        if self.use_web:
            url = api.board_url(self.guild)
            self.web.request("GET", url,
                             lambda r: self.run_job(lambda: board_from_html(r["text"], r.get("url", url)),
                                                    self._board_ok, self._board_err),
                             self._board_err)
        else:
            self.run_job(self.client.fetch_board, self._board_ok, self._board_err)
        for sid in {self.cur_spot, *(j["spot_id"] for j in self.sniper.active() if j.get("follow"))} - {None}:
            self._fetch_spot(sid)

    def _board_ok(self, board: Board):
        self._busy = False
        self.refresh_btn.setEnabled(True)
        self.board = board
        self.board_time = datetime.now()
        self.set_account("tibia", True)
        self._paint_account("tibia")
        self.resp_sub.setText(f"Rezerwujesz jako <b style='color:{C_TEXT}'>{board.author or '?'}</b> · "
                              f"zaktualizowano {now_server():%H:%M:%S} · odświeżanie co {self.cfg.data['refresh_s']} s")
        self._notify_changes()
        self.sniper.on_board(now_server())
        self._fill_mine()
        self._fill_favs()
        self._fill_all()
        self._update_spot_view()

    def _board_err(self, e):
        self._busy = False
        self.refresh_btn.setEnabled(True)
        if isinstance(e, NotLoggedIn):
            try:
                (self.cfg.dir / "ostatnia_odpowiedz.html").write_text(e.html, encoding="utf-8")
                log_error(f"Niezalogowany – odpowiedź z {e.url} zapisana w ostatnia_odpowiedz.html")
            except Exception:
                pass
            self.set_account("tibia", False)
            self.resp_sub.setText("Nie jesteś zalogowany na tibialoot.com – kliknij konto w pasku po lewej.")
        else:
            self.log(f"✖ Błąd odświeżania: {e}")

    def _fetch_spot(self, sid):
        def ok(res, sid=sid):
            self.spot_res_cache[sid] = (datetime.now(), res)
            if sid == self.cur_spot:
                self._update_spot_view()
        if self.use_web:
            self.web.request("GET", api.spot_url(self.guild, sid),
                             lambda r: self.run_job(lambda: parse_spot_page(r["text"], sid), ok, lambda e: None),
                             lambda e: None)
        else:
            self.run_job(lambda: self.client.fetch_spot_reservations(sid), ok, lambda e: None)

    @property
    def guild(self) -> str:
        return self.cfg.data["guild_id"]

    @property
    def use_web(self) -> bool:
        # ręcznie wklejone ciasteczko wymusza tryb zapasowy
        return self.web is not None and not self.cfg.data.get("cookie_header")

    def spot_reservations(self, sid: str) -> list[Reservation]:
        """Tablica jest źródłem prawdy (po niej widać skrócenia i usunięcia). Ze strony respa
        dokładamy tylko rezerwacje, których tablica nie pokazała – i tylko ze świeżego pobrania,
        żeby nie trzymać usuniętych."""
        merged: dict[str, Reservation] = {}
        cached = self.spot_res_cache.get(sid)
        if cached and (self.board_time is None or cached[0] >= self.board_time - timedelta(seconds=15)):
            for r in cached[1]:
                merged[r.id] = r
        if self.board and sid in self.board.spots:
            for r in self.board.spots[sid].reservations:
                merged[r.id] = r
        return sorted(merged.values(), key=lambda r: r.start)

    def _notify_changes(self):
        if not (self.tray and self.cfg.data.get("notify")):
            self._prev_status = {sid: s.is_taken for sid, s in self.board.spots.items()}
            return
        for sid in self.cfg.favorites:
            s = self.board.spots.get(sid)
            if not s or not s.status:
                continue
            was = self._prev_status.get(sid)
            if was is True and not s.is_taken:
                self.tray.showMessage("Resp wolny", f"{s.name} jest teraz wolny.")
        self._prev_status = {sid: s.is_taken for sid, s in self.board.spots.items() if s.status}

    # ---------- listy
    def _add_row(self, lst: QListWidget, bar: str, day: str, start: datetime, end: datetime, title: str,
                 title_color: str, pill_text: str, pill_color: str, button=None, active=False) -> QListWidgetItem:
        """Wiersz listy terminów: kolorowy pasek, dzień, godziny, opis, plakietka, opcjonalny przycisk."""
        row = QFrame()
        row.setObjectName("rowActive" if active else "row")
        row.setAttribute(Qt.WA_Hover, True)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 10, 0)
        h.setSpacing(10)
        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background:{bar}; border-radius:2px;")
        h.addWidget(stripe)
        col = QVBoxLayout()
        col.setContentsMargins(0, 6, 0, 6)
        col.setSpacing(1)
        end_txt = f"{end:%H:%M}"
        if end.date() != start.date():
            end_txt = "24:00" if (end.hour, end.minute) == (0, 0) else \
                f"{end:%H:%M}<span style='color:{C_MUTED}; font-size:11px; font-weight:600'>  ({self._daytxt(end)})</span>"
        tl = QLabel(f"{start:%H:%M} – {end_txt}")
        tl.setStyleSheet("font-size:14px; font-weight:800;")
        col.addWidget(tl)
        sub = f"<span style='color:{C_MUTED}; font-weight:700'>{day.upper()}</span>"
        if title:
            sub += f"<span style='color:{C_MUTED}'>  ·  </span><span style='color:{title_color}'>{title}</span>"
        ti = QLabel(sub)
        ti.setStyleSheet("font-size:12px;")
        col.addWidget(ti)
        h.addLayout(col, 1)
        if pill_text:
            p_ = pill(pill_text, pill_color)
            p_.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            h.addWidget(p_, 0, Qt.AlignVCenter)
        if button is not None:
            h.addWidget(button, 0, Qt.AlignVCenter)
        for l in (tl, ti, stripe):
            l.setAttribute(Qt.WA_TransparentForMouseEvents)
        it = QListWidgetItem()
        it.setSizeHint(QSize(100, 52))
        lst.addItem(it)
        lst.setItemWidget(it, row)
        return it

    def _mark_active_window(self, s: datetime):
        for i in range(self.free_list.count()):
            it = self.free_list.item(i)
            w, row = it.data(Qt.UserRole), self.free_list.itemWidget(it)
            if not w or row is None:
                continue
            name = "rowActive" if w[0] <= s <= w[1] else "row"
            if row.objectName() != name:
                row.setObjectName(name)
                row.style().unpolish(row)
                row.style().polish(row)

    def _empty_row(self, lst: QListWidget, text: str):
        it = QListWidgetItem(text)
        it.setFlags(Qt.NoItemFlags)
        it.setForeground(QColor(C_MUTED))
        it.setTextAlignment(Qt.AlignCenter)
        it.setSizeHint(QSize(100, 60))
        lst.addItem(it)

    def _res_row(self, text: str, r: Reservation) -> QWidget:
        """Wiersz Twojej rezerwacji z przyciskiem „Usuń”."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 2, 4, 2)
        l = QLabel(text)
        l.setStyleSheet(f"color:{C_MINE}; font-weight:600; background:transparent;")
        h.addWidget(l, 1)
        w.setMinimumHeight(36)
        b = QPushButton("Usuń")
        b.setObjectName("danger")
        b.setFixedHeight(28)
        b.setToolTip("Usuń tę rezerwację na stronie")
        b.clicked.connect(lambda _=False, r=r: QTimer.singleShot(0, lambda: self.delete_reservation(r)))
        h.addWidget(b)
        return w

    def _fill_mine(self):
        self.mine_list.clear()
        if not self.board:
            return
        now = now_server()
        mine = sorted((r for sp in self.board.spots.values() for r in sp.reservations
                       if r.can_delete and r.end > now), key=lambda r: r.start)
        for r in mine:
            sp = self.board.spots.get(r.spot_id)
            card = QFrame()
            card.setObjectName("card")
            v = QVBoxLayout(card)
            v.setContentsMargins(12, 8, 10, 8)
            top = QHBoxLayout()
            n = QLabel(sp.name if sp else r.spot_id)
            n.setStyleSheet("font-weight:700;")
            n.setWordWrap(True)
            top.addWidget(n, 1)
            b = QPushButton("Usuń")
            b.setObjectName("danger")
            b.clicked.connect(lambda _=False, r=r: QTimer.singleShot(0, lambda: self.delete_reservation(r)))
            top.addWidget(b)
            v.addLayout(top)
            t = QLabel(f"{self._daytxt(r.start)} {r.label}   ·   {r.status or ''}")
            t.setStyleSheet(f"color:{C_MINE}; font-weight:600;")
            v.addWidget(t)
            it = QListWidgetItem()
            it.setData(Qt.UserRole, r.spot_id)
            it.setSizeHint(card.sizeHint() + QSize(0, 4))
            self.mine_list.addItem(it)
            self.mine_list.setItemWidget(it, card)
        if not mine:
            it = QListWidgetItem("Nie masz nadchodzących rezerwacji.")
            it.setForeground(QColor(C_MUTED))
            it.setFlags(Qt.NoItemFlags)
            self.mine_list.addItem(it)
        self.tabs.setTabText(2, f"Moje ({len(mine)})" if mine else "Moje")

    def delete_reservation(self, r: Reservation):
        if not r.can_delete:
            return
        sp = self.board.spots.get(r.spot_id) if self.board else None
        question = (f"Usunąć rezerwację {r.who} na {sp.name if sp else 'respie'}, "
                    f"{self._daytxt(r.start)} ({r.start:%d.%m}) {r.label}?")
        if QMessageBox.question(self, "Usuń rezerwację", question) != QMessageBox.Yes:
            return
        self.log(f"Usuwam rezerwację {r.label}…")

        def done(ok, msg):
            if ok:
                self.log(f"✔ Usunięto rezerwację {sp.name if sp else ''} {self._daytxt(r.start)} {r.label}.")
                if sp:   # od razu z widoku, odświeżenie potwierdzi
                    sp.reservations = [x for x in sp.reservations if x.id != r.id]
                cached = self.spot_res_cache.get(r.spot_id)
                if cached:
                    self.spot_res_cache[r.spot_id] = (cached[0], [x for x in cached[1] if x.id != r.id])
                self._fill_mine()
                self._fill_favs()
                self._update_spot_view()
                QTimer.singleShot(500, self.refresh)
            else:
                self.log("✖ Nie udało się usunąć: " + msg.splitlines()[0])
                QMessageBox.warning(self, "Nie udało się usunąć", msg)

        def fail(e):
            log_error(repr(e))
            done(False, str(e))
        if self.use_web:
            self.web.request("POST", api.absolute(r.delete_path),
                             lambda resp: done(*api.interpret_delete(resp["status"], resp.get("text", ""))),
                             fail, headers=api.delete_headers(self.guild))
        else:
            self.run_job(lambda: self.client.delete_reservation(r.delete_path), lambda res: done(*res), fail)

    def _fill_favs(self):
        self.fav_list.clear()
        if not self.board:
            return
        favs = [self.board.spots[s] for s in self.cfg.favorites if s in self.board.spots]
        self.fav_empty.setVisible(not favs)
        self.fav_list.setVisible(bool(favs))
        for spot in favs:
            spot.reservations = self.spot_reservations(spot.id)
            card = SpotCard(spot, self.board.author, self.toggle_fav)
            it = QListWidgetItem()
            it.setData(Qt.UserRole, spot.id)
            it.setSizeHint(card.sizeHint() + QSize(0, 4))
            self.fav_list.addItem(it)
            self.fav_list.setItemWidget(it, card)

    def _fill_all(self):
        self.tree.clear()
        if not self.board:
            return
        q = self.search.text().strip().lower()
        regions: dict[str, QTreeWidgetItem] = {}
        for spot in sorted(self.board.spots.values(), key=lambda s: (s.region, s.name)):
            if q and q not in spot.name.lower() and q not in spot.region.lower():
                continue
            if self.only_free.isChecked() and spot.is_taken:
                continue
            reg = regions.get(spot.region)
            if reg is None:
                reg = QTreeWidgetItem(["▾", spot.region or "Inne"])
                reg.setFirstColumnSpanned(True)
                f = reg.font(1)
                f.setBold(True)
                reg.setFont(1, f)
                self.tree.addTopLevelItem(reg)
                regions[spot.region] = reg
            fav = spot.id in self.cfg.favorites
            it = QTreeWidgetItem(["★" if fav else "☆", spot.name,
                                  "Zajęty" if spot.is_taken else ("Wolny" if spot.status else ""),
                                  spot.next_text if spot.next_text not in ("Brak", "") else ""])
            it.setData(0, Qt.UserRole, spot.id)
            it.setForeground(0, QColor(C_ACCENT if fav else C_MUTED))
            it.setForeground(2, QColor(C_ACCENT if spot.is_taken else C_FREE))
            it.setToolTip(0, "Kliknij, aby dodać/usunąć z ulubionych")
            it.setToolTip(1, spot.name)
            it.setToolTip(3, spot.next_text)
            reg.addChild(it)
        for reg in regions.values():
            reg.setExpanded(bool(q) or len(regions) < 4)
            reg.setText(0, "▾" if reg.isExpanded() else "▸")

    def _tree_clicked(self, it: QTreeWidgetItem, col: int):
        sid = it.data(0, Qt.UserRole)
        if not sid:
            it.setExpanded(not it.isExpanded())
            it.setText(0, "▾" if it.isExpanded() else "▸")
            return
        if col == 0:
            on = self.toggle_fav(sid)
            it.setText(0, "★" if on else "☆")
            it.setForeground(0, QColor(C_ACCENT if on else C_MUTED))
        else:
            self.select_spot(sid)

    def toggle_fav(self, sid: str) -> bool:
        on = self.cfg.toggle_fav(sid)
        self._fill_favs()
        if self.tabs.currentIndex() == 1:
            pass
        else:
            self._fill_all()
        return on

    # ---------- panel rezerwacji
    def select_spot(self, sid: str):
        if not sid:
            return
        self.cur_spot = sid
        self._reservation_enabled(True)
        self._fetch_spot(sid)
        self._preselect_first_free()
        self._update_spot_view()

    def _preselect_first_free(self):
        if not self.cur_spot:
            return
        now = now_server()
        lo, hi = booking_range(now)
        wins = free_windows(self.spot_reservations(self.cur_spot), lo, hi, now)
        if wins:
            self._pick_window(wins[0])

    def _pick_window(self, win):
        s, e = win
        self._apply(start=s, want=max(self.want_len, int((min(s + MAX_LEN, e) - s).total_seconds() // 60)))

    def _set_range(self, s: datetime, e: datetime):
        self._apply(start=s, want=int((e - s).total_seconds() // 60))

    def _set_len(self, mins: int):
        self._apply(want=mins)

    def _shift_start(self, mins: int):
        if self.sel_start:
            self._apply(start=self.sel_start + timedelta(minutes=mins))

    def _start_day_clicked(self, i: int):
        if not self.sel_start:
            return
        d = now_server().date() + timedelta(days=i)
        self._apply(start=datetime.combine(d, self.sel_start.time()))

    def _start_time_changed(self, t):
        if not self.sel_start:
            return
        if t is None:
            self._apply()
            return
        d = self.sel_start.date()
        self._apply(start=datetime(d.year, d.month, d.day, t.hour(), t.minute()))

    def _len_slider_changed(self, v: int):
        if self._applying:
            return
        self._apply(want=v)

    _applying = False

    def _max_len_at(self, start: datetime) -> int:
        """Ile minut można zarezerwować od `start` (0 = zajęte)."""
        limit = MAX_LEN
        for r in self.spot_reservations(self.cur_spot or ""):
            if r.start <= start <= r.end:
                return 0
            if r.start > start:
                limit = min(limit, r.start - timedelta(minutes=1) - start)
                break
        return max(int(limit.total_seconds() // 60), 0)

    def _apply(self, start: datetime | None = None, want: int | None = None):
        """Jedno miejsce, które ustawia wybrany termin i pilnuje limitów."""
        if not self.cur_spot:
            return
        now = now_server()
        earliest = ceil_minutes(now, 1)
        latest = start_limit(now)
        if want is not None:
            self.want_len = max(5, min(int(want), 180))
        start = start or self.sel_start or earliest
        start = min(max(start.replace(second=0, microsecond=0), earliest), latest)
        self.sel_start = start
        max_len = self._max_len_at(start)
        self.sel_len = min(self.want_len, max_len)

        self._applying = True
        try:
            today = now.date()
            for i, b in enumerate(self.sday_btns):
                d = today + timedelta(days=i)
                b.setText(f"{'Dziś' if i == 0 else 'Jutro'}  {d:%d.%m}")
                b.setChecked(d == start.date())
            self.start_time.set_time(QTime(start.hour, start.minute))
            self.len_slider.setEnabled(max_len >= 5)
            self.len_slider.setRange(5, max(5, max_len))
            self.len_slider.setValue(max(5, self.sel_len))
        finally:
            self._applying = False

        self.len_lbl.setText(fmt_len(self.sel_len) if self.sel_len else "—")
        for b in self.len_btns:
            mins = b.property("mins")
            b.setEnabled(mins <= max_len)
            b.setChecked(mins == self.sel_len)
        if max_len < 5:
            self.cap_lbl.setText("")
        elif max_len < 180:
            nxt = start + timedelta(minutes=max_len + 1)
            self.cap_lbl.setText(f"Najwyżej {fmt_len(max_len)} – o {nxt:%H:%M} zaczyna się kolejna rezerwacja.")
        else:
            self.cap_lbl.setText("")
        self._on_time_changed()

    def _selection(self):
        if not self.sel_start:
            return None, None
        return self.sel_start, self.sel_start + timedelta(minutes=self.sel_len)

    def _timeline_pick(self, dt: datetime):
        if not self.cur_spot:
            return
        now = now_server()
        lo, hi = booking_range(now)
        if dt > start_limit(now):
            self.status(f"Najpóźniejszy początek: {start_limit(now):%d.%m %H:%M}.", "warn")
            return
        for win in free_windows(self.spot_reservations(self.cur_spot), lo, hi, now):
            if win[0] <= dt <= win[1]:
                s = max(dt, win[0])
                self._set_range(s, min(s + MAX_LEN, win[1]))
                return
        self.status("Ten moment jest zajęty albo w przeszłości.", "warn")

    def _free_clicked(self, it):
        win = it.data(Qt.UserRole) if it is not None else None
        if win:
            self._pick_window(win)

    def _on_time_changed(self, *_):
        if not self.cur_spot:
            return
        s, e = self._selection()
        if s is None:
            return
        res = self.spot_reservations(self.cur_spot)
        if self.sel_len < 5:
            problem = "Ta godzina jest zajęta – kliknij wolne okno albo zmień godzinę."
        else:
            problem = validate(res, s, e, now_server())
        end_day = "" if e.date() == s.date() else f" ({self._daytxt(e)})"
        self.summary_lbl.setText(f"{self._daytxt(s).capitalize()} {s:%H:%M} → {e:%H:%M}{end_day}"
                                 f"<span style='color:{C_MUTED}; font-weight:600; font-size:13px'>"
                                 f"  ·  {fmt_len(self.sel_len) if self.sel_len else '—'}</span>")
        if problem:
            self.valid_lbl.setText(f"<span style='color:#ef4444'>✕ {problem}</span>")
            self.book_btn.setEnabled(False)
            self.book_btn.setText("Zarezerwuj")
        else:
            self.valid_lbl.setText(f"<span style='color:{C_FREE}'>✓ Termin wolny</span>")
            self.book_btn.setEnabled(True)
            self.book_btn.setText(f"Zarezerwuj  {s:%H:%M}–{e:%H:%M}")
        self.timeline.set_data(res, (s, e), self.board.author if self.board else "")
        self._mark_active_window(s)

    def _update_spot_view(self):
        if not self.cur_spot or not self.board:
            return
        spot = self.board.spots.get(self.cur_spot)
        if not spot:
            return
        self.spot_title.setText(spot.name)
        now_txt = (f"Teraz: zajęty – {spot.holder} {spot.holder_time}" if spot.is_taken
                   else "Teraz: wolny" if spot.status else "")
        self.spot_sub.setText(" · ".join(x for x in (spot.region, now_txt) if x))

        res = self.spot_reservations(spot.id)
        now = now_server()
        lo, hi = booking_range(now)
        lim = start_limit(now)
        self.limit_lbl.setText(f"Możesz rezerwować z początkiem od teraz do {self._daytxt(lim)} {lim:%H:%M} (max 3 h).")
        me = self.board.author
        self.res_list.clear()
        upcoming = [r for r in res if r.end > now and r.start < hi]
        st_colors = {"Trwa": C_ACCENT, "Następna": "#a78bfa", "Zaplanowana": C_MUTED}
        for r in upcoming:
            mine = r.can_delete or (me and me in r.authors)
            color = C_MINE if mine else st_colors.get(r.status, C_MUTED)
            btn = None
            if r.can_delete:
                btn = QPushButton("Usuń")
                btn.setObjectName("danger")
                btn.setFixedHeight(28)
                btn.clicked.connect(lambda _=False, r=r: QTimer.singleShot(0, lambda: self.delete_reservation(r)))
            self._add_row(self.res_list, bar=color, day=self._daytxt(r.start), start=r.start, end=r.end,
                          title=r.who + ("  (Ty)" if mine else ""), title_color=C_MINE if mine else C_TEXT,
                          pill_text=r.status, pill_color=st_colors.get(r.status, C_MUTED), button=btn)
        if not upcoming:
            self._empty_row(self.res_list, "Brak nadchodzących rezerwacji – resp wolny przez całą dobę.")

        self.free_list.clear()
        wins = free_windows(res, lo, hi, now)
        sel = self._selection()
        for w in wins:
            mins = int((w[1] - w[0]).total_seconds() // 60)
            note = f"start najpóźniej {self._daytxt(lim)} {lim:%H:%M}" if w[1] > lim else \
                ("od teraz" if w[0] <= now + timedelta(minutes=1) else "")
            active = bool(sel[0] and w[0] <= sel[0] <= w[1])
            it = self._add_row(self.free_list, bar=C_FREE, day=self._daytxt(w[0]), start=w[0], end=w[1],
                               title=note, title_color=C_MUTED, pill_text=fmt_len(mins), pill_color=C_FREE,
                               active=active)
            it.setData(Qt.UserRole, w)
        if not wins:
            self._empty_row(self.free_list, "Brak wolnych okien w najbliższej dobie.")
        self._apply()   # przytnij wybrany termin do aktualnych limitów

    @staticmethod
    def _daytxt(dt: datetime) -> str:
        d = (dt.date() - now_server().date()).days
        return {0: "dziś", 1: "jutro"}.get(d, f"{dt:%d.%m}")

    # ---------- akcje
    def book(self):
        if not self.cur_spot or not self.board:
            return
        s, e = self._selection()
        if s is None or validate(self.spot_reservations(self.cur_spot), s, e, now_server()):
            self._apply()
            return
        spot = self.board.spots[self.cur_spot]
        if QMessageBox.question(self, "Potwierdź",
                                f"Zarezerwować {spot.name}\n{s:%d.%m.%Y %H:%M} – {e:%H:%M}\n"
                                f"dla {self.board.author}?") != QMessageBox.Yes:
            return
        self.book_btn.setEnabled(False)
        sid = self.cur_spot
        self.log("Wysyłam rezerwację…")
        self.post_reservation(sid, s, e, lambda ok, msg: self._booked((ok, msg), sid, s))

    def post_reservation(self, sid: str, s: datetime, e: datetime, cb):
        """Wysyła formularz rezerwacji; cb(ok: bool, msg: str)."""
        def fail(err):
            log_error(repr(err))
            cb(False, str(err))
        if self.use_web:
            def done(r):
                final = r.get("url", "")
                redirected = bool(r.get("redirected")) and "/reservations" in final and "login" not in final
                ok, msg = interpret_post(r["status"], r.get("headers", {}), r.get("text", ""), redirected)
                cb(ok, msg)
                if ok:
                    QTimer.singleShot(500, self.refresh)
            self.web.request("POST", api.board_url(self.guild), done, fail,
                             data=api.reservation_form(sid, s, e), headers=api.htmx_headers(self.guild))
        else:
            def done2(res):
                cb(*res)
                if res[0]:
                    QTimer.singleShot(500, self.refresh)
            self.run_job(lambda: self.client.create_reservation(sid, s, e), done2, fail)

    # ---------- snajper
    def new_snipe(self, kind: str):
        if not self.cur_spot or not self.board:
            return
        spot = self.board.spots[self.cur_spot]
        sel = self._selection()
        preset = sel if sel[0] else None
        dlg = SnipeDialog(self, kind, spot.name, self.spot_reservations(spot.id), now_server(), TimeField, preset)
        if dlg.exec() == QDialog.Accepted:
            job = dlg.job()
            job.update(spot_id=spot.id, spot_name=spot.name)
            self.sniper.add(job)
            self.go_page(1)
            if kind == "watch":
                self.sniper.check_watches(now_server())
        import shiboken6
        shiboken6.delete(dlg)

    def _snipe_notify(self, title: str, text: str, job: dict, hit, sound: bool = True):
        if sound:
            self.sounds.play("alarm", 3)
        if self.tray:
            self.tray.showMessage(title, text)
        QApplication.alert(self, 0)
        box = QMessageBox(QMessageBox.Information, title, text, QMessageBox.Close, self)
        if hit:
            show = box.addButton("Pokaż i wybierz", QMessageBox.AcceptRole)
            def go(job=job, hit=hit):
                self.select_spot(job["spot_id"])
                self._apply(start=hit[0], want=int((min(hit[1], hit[0] + MAX_LEN) - hit[0]).total_seconds() // 60))
                self.raise_()
                self.activateWindow()
            show.clicked.connect(go)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setModal(False)
        box.show()

    def _fill_snipes(self):
        self.snipe_list.clear()
        now = now_server()
        order = {"active": 0, "done": 1, "failed": 2, "expired": 3}
        jobs = sorted(self.sniper.jobs, key=lambda j: (order.get(j["status"], 9), j.get("start", j.get("from", ""))))
        n_active = 0
        for j in jobs:
            n_active += j["status"] == "active"
            card = QFrame()
            card.setObjectName("card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(3)
            top = QHBoxLayout()
            icon = "🎯" if j["kind"] == "snipe" else "🔔"
            name = QLabel(f"{icon} {j['spot_name']}")
            name.setStyleSheet("font-weight:700;")
            name.setWordWrap(True)
            top.addWidget(name, 1)
            colors = {"active": C_ACCENT, "done": C_FREE, "failed": "#ef4444", "expired": C_MUTED}
            labels = {"active": "aktywny", "done": "gotowe", "failed": "nieudany", "expired": "minął"}
            top.addWidget(pill(labels.get(j["status"], j["status"]), colors.get(j["status"], C_MUTED)))
            rm = QPushButton("✕")
            rm.setObjectName("star")
            rm.setToolTip("Usuń")
            rm.setFixedSize(24, 24)
            rm.clicked.connect(lambda _=False, jid=j["id"]: QTimer.singleShot(0, lambda: self.sniper.remove(jid)))
            top.addWidget(rm)
            lay.addLayout(top)
            if j["kind"] == "snipe":
                s0, e0 = datetime.strptime(j["start"], "%Y-%m-%dT%H:%M"), datetime.strptime(j["end"], "%Y-%m-%dT%H:%M")
                ft = fire_time(s0)
                desc = f"{self._daytxt(s0)} {s0:%H:%M}–{e0:%H:%M}"
                if j.get("follow"):
                    desc += "  ·  podąża za rezerwacją przed nim"
                    if j.get("floor") or j.get("cap"):
                        fl = datetime.strptime(j["floor"], "%Y-%m-%dT%H:%M") if j.get("floor") else None
                        cp = datetime.strptime(j["cap"], "%Y-%m-%dT%H:%M") if j.get("cap") else None
                        desc += "  ·  widełki " + (f"od {fl:%H:%M} " if fl else "") + (f"do {cp:%H:%M}" if cp else "")
                if j["status"] == "active":
                    left = ft - now
                    if left.total_seconds() > 0:
                        h, rem = divmod(int(left.total_seconds()), 3600)
                        desc += f"  ·  strzał {self._daytxt(ft)} {ft:%H:%M} (za {h} h {rem // 60} min)"
            else:
                f0, t0 = datetime.strptime(j["from"], "%Y-%m-%dT%H:%M"), datetime.strptime(j["to"], "%Y-%m-%dT%H:%M")
                desc = (f"{self._daytxt(f0)} {f0:%H:%M}–{t0:%H:%M}, min. {j['min_len']} min"
                        + ("  ·  auto-rezerwacja" if j.get("auto") else ""))
            d = QLabel(desc)
            d.setStyleSheet(f"color:{C_MUTED};")
            d.setWordWrap(True)
            lay.addWidget(d)
            if j.get("msg"):
                m = QLabel(j["msg"])
                m.setWordWrap(True)
                m.setStyleSheet(f"color:{colors.get(j['status'], C_TEXT)}; font-size:11px;")
                lay.addWidget(m)
            it = QListWidgetItem()
            it.setSizeHint(card.sizeHint() + QSize(0, 4))
            self.snipe_list.addItem(it)
            self.snipe_list.setItemWidget(it, card)
        if not jobs:
            it = QListWidgetItem("Nie masz jeszcze snajpów ani obserwacji.\n\n"
                                 "Wybierz resp na stronie „Respy” i kliknij „🎯 Snajpuj” albo „🔔 Obserwuj”.")
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QColor(C_MUTED))
            it.setFlags(Qt.NoItemFlags)
            it.setSizeHint(QSize(200, 140))
            self.snipe_list.addItem(it)
        self.nav_btns[1].setText(f"🎯   Snajper  ({n_active})" if n_active else "🎯   Snajper")

    def _booked(self, res, sid, s):
        ok, msg = res
        if ok:
            self.log(f"✔ {msg}")
        else:
            QMessageBox.warning(self, "Nie udało się", msg)
            self.log("✖ Rezerwacja odrzucona: " + msg.splitlines()[0])
            self._on_time_changed()

    def _book_err(self, e):
        QMessageBox.warning(self, "Błąd", str(e))
        self._on_time_changed()

    def changeEvent(self, ev):
        # podpowiedź „Dziś” nie może zostać na ekranie, gdy okno traci fokus lub się minimalizuje
        if getattr(self, "today", None) and ev.type() in (QEvent.ActivationChange, QEvent.WindowStateChange):
            if not self.isActiveWindow() or self.isMinimized():
                self.today.hide_card()
        super().changeEvent(ev)

    def resizeEvent(self, ev):
        if getattr(self, "today", None):
            self.today.hide_card()
        super().resizeEvent(ev)
        self._apply_responsive()

    def closeEvent(self, ev):
        if getattr(self, "hunt", None):
            self.hunt.shutdown()
        if getattr(self, "today", None):
            self.today.card.close()
        if self.web:  # strony muszą zniknąć przed profilem przeglądarki
            self.web.close_pages()
        super().closeEvent(ev)


STYLE = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; color: {C_TEXT}; }}
QMainWindow, QDialog, QWidget {{ background: {C_BG}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C_MUTED}; padding: 8px 14px; border-bottom: 2px solid transparent; font-weight:600; }}
QFrame#sidebar {{ background: #131417; border-right: 1px solid {C_BORDER}; }}
QFrame#sidebar QLabel {{ background: transparent; }}
QPushButton#nav {{ text-align: left; padding: 9px 12px; border: none; border-radius: 8px; background: transparent;
                  font-size: 14px; font-weight: 600; color: {C_MUTED}; }}
QPushButton#nav:hover {{ background: #1c1d21; color: {C_TEXT}; }}
QPushButton#nav:checked {{ background: rgba(249,115,22,0.14); color: {C_ACCENT}; }}
QPushButton#update {{ text-align: left; padding: 9px 12px; border-radius: 8px; font-weight: 700; color: #111;
                     background: {C_FREE}; border: none; }}
QPushButton#update:hover {{ background: #4ade80; }}
QLabel#todayTile {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 10px; color: {C_MUTED}; font-weight: 700; }}
QLabel#todayTile:hover {{ border-color: {C_ACCENT}; }}
QPushButton#account {{ text-align: left; background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 8px; padding: 0; }}
QPushButton#account:hover {{ border-color: #4a4d55; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 10px; border-radius: 8px; color: {C_MUTED}; font-weight: 600; }}
QListWidget#nav::item:selected {{ background: rgba(249,115,22,0.14); color: {C_ACCENT}; }}
QFrame#footer {{ border-top: 1px solid {C_BORDER}; background: #131417; }}
QStatusBar {{ border-top: 1px solid {C_BORDER}; background: #131417; }}
QStatusBar QLabel {{ padding: 2px 10px; }}
QPlainTextEdit {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 8px; padding: 4px; }}
QComboBox {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 6px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {C_PANEL}; selection-background-color: #26282d; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid #4a4d55; background: {C_PANEL}; }}
QRadioButton::indicator:checked {{ background: {C_ACCENT}; border-color: {C_ACCENT}; }}
QScrollArea {{ background: transparent; }}
QTabBar::tab:selected {{ color: {C_TEXT}; border-bottom: 2px solid {C_ACCENT}; }}
QListWidget, QTreeWidget {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 8px; padding: 4px; }}
QListWidget::item {{ padding: 4px; border-radius: 6px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: #26282d; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: #1f2125; }}
QHeaderView::section {{ background: {C_PANEL}; color: {C_MUTED}; border: none; padding: 4px; }}
QFrame#card {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 10px; }}
QFrame#card QLabel, QFrame#card QCheckBox, QFrame#card QRadioButton {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33363c; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #33363c; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; width: 0; height: 0; }}
QPushButton {{ background: #1d1f23; border: 1px solid {C_BORDER}; border-radius: 7px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: #4a4d55; }}
QPushButton:disabled {{ color: #555; }}
QPushButton#primary {{ background: {C_ACCENT}; color: #111; border: none; font-weight: 700; padding: 8px 22px; }}
QPushButton#primary:disabled {{ background: #3a2a1f; color: #776; }}
QPushButton#seg:checked {{ border-color: {C_ACCENT}; color: {C_ACCENT}; }}
QPushButton#small {{ padding: 5px 10px; }}
QListWidget#rows {{ padding: 6px; }}
QListWidget#rows::item, QListWidget#rows::item:hover, QListWidget#rows::item:selected {{ background: transparent; padding: 0; }}
QFrame#row, QFrame#rowActive {{ background: #1c1d21; border: 1px solid #24262b; border-radius: 8px; }}
QFrame#row:hover {{ background: #222429; border-color: #3a3d44; }}
QFrame#rowActive {{ background: rgba(34,197,94,0.08); border-color: rgba(34,197,94,0.45); }}
QFrame#row QLabel, QFrame#rowActive QLabel {{ background: transparent; border: none; }}
QPushButton#danger {{ padding: 4px 12px; color: #fca5a5; border-color: rgba(239,68,68,0.45); background: rgba(239,68,68,0.10); }}
QPushButton#danger:hover {{ background: rgba(239,68,68,0.22); border-color: #ef4444; color: #fff; }}
QPushButton#step {{ padding: 5px 0; min-width: 44px; font-weight: 600; }}
QPushButton#chip {{ padding: 5px 10px; border-radius: 14px; }}
QPushButton#chip:checked {{ background: rgba(249,115,22,0.18); border-color: {C_ACCENT}; color: {C_ACCENT}; font-weight: 700; }}
QPushButton#chip:disabled {{ color: #4a4d55; border-color: #222428; }}
QLineEdit#timefield {{ padding: 2px 4px; font-weight: 700; }}
QLineEdit#timefield:disabled {{ color: #555; }}
QLineEdit#bigtime {{ font-size: 16px; font-weight: 800; padding: 3px 4px; border-color: {C_ACCENT}; }}
QSlider::groove:horizontal {{ height: 6px; background: {C_BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {C_ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -6px 0; border-radius: 8px; background: {C_TEXT}; }}
QSlider::handle:horizontal:disabled {{ background: #555; }}
QPushButton#star {{ background: transparent; border: none; color: {C_ACCENT}; font-size: 16px; padding: 0; }}
QLineEdit, QDateEdit, QDateTimeEdit, QSpinBox {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 6px; padding: 5px 8px; }}
QAbstractSpinBox QLineEdit {{ padding: 0; border: none; background: transparent; }}
QLineEdit:focus, QDateTimeEdit:focus {{ border-color: {C_ACCENT}; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid #4a4d55; border-radius: 4px; background: {C_PANEL}; }}
QCheckBox::indicator:checked {{ background: {C_ACCENT}; border-color: {C_ACCENT}; }}
QSplitter::handle {{ background: {C_BG}; }}
QStatusBar {{ color: {C_MUTED}; }}
QToolTip {{ background: #222; color: {C_TEXT}; border: 1px solid {C_BORDER}; }}
"""


LOG_PATH: Path | None = None
MIGRATION_MSGS: list[str] = []


def log_error(text: str):
    try:
        if LOG_PATH:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
    except Exception:
        pass


def main():
    global LOG_PATH
    import faulthandler
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(WINDOW_TITLE)
    if sys.platform == "win32":
        try:
            import ctypes   # własna ikona na pasku zadań także przy uruchamianiu przez python.exe
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TibiaAppsCompanion.App")
        except Exception:
            pass
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setOrganizationName("TibiaAppsCompanion")
    app.setStyleSheet(STYLE)
    paths.setup()
    try:
        MIGRATION_MSGS[:] = paths.migrate()
    except Exception:
        MIGRATION_MSGS[:] = ["⚠ Przenoszenie danych ze starych folderów nie powiodło się:\n" + traceback.format_exc()]
    logdir = paths.DATA
    LOG_PATH = logdir / "error.log"
    crash_file = open(logdir / "crash.log", "a", encoding="utf-8")
    faulthandler.enable(crash_file)   # zapisze ślad, gdyby Qt się wysypał

    def hook(t, v, tb):
        log_error("".join(traceback.format_exception(t, v, tb)))
    sys.excepthook = hook
    # Odśmiecanie tylko w wątku GUI: gdy Python sprzątał obiekty Qt w wątku pobierającym
    # dane, aplikacja wysypywała się (segfault).
    gc.disable()
    gc_timer = QTimer()
    gc_timer.timeout.connect(lambda: gc.collect())
    gc_timer.start(3000)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
