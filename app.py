"""Tibia Apps Companion – okno główne: respy, snajper, hunty."""
from __future__ import annotations

import gc
import json
import os
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
import theme as T
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

# kolory, czcionki i odstępy: theme.py


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
        lay = T.vbox(self, (T.S6, T.S6, T.S6, T.S4), T.S4)
        head = T.vbox(spacing=T.S1)
        head.addWidget(T.label(f"Zaloguj się na {name}", "pageTitle"))
        head.addWidget(T.label(f"Do: {purpose}. Logujesz się bezpośrednio na stronie – aplikacja nie zna Twojego "
                               "hasła. Gdy strona pokaże, że jesteś zalogowany, kliknij „Gotowe”.", "muted", wrap=True))
        lay.addLayout(head)
        bar = QFrame()
        bar.setObjectName("panel")
        bar.setFixedHeight(T.CONTROL_H)
        bl = T.hbox(bar, (T.S3, 0, T.S3, 0), T.S2)
        bl.addWidget(T.icon_label("lock", T.TEXT_3, 14))
        self.addr = ElidedLabel(url)
        T.set_role(self.addr, "muted")
        bl.addWidget(self.addr, 1)
        lay.addWidget(bar)
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(web.profile, self.view))
        self.view.urlChanged.connect(lambda u: self.addr.setText(u.toString()))
        self.view.load(QUrl(url))
        lay.addWidget(self.view, 1)
        row = T.hbox(spacing=T.S2)
        row.addStretch(1)
        c = T.button("Anuluj")
        c.clicked.connect(self.reject)
        row.addWidget(c)
        ok = T.button("Gotowe", kind="primary")
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
        self.setFixedHeight(76)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
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
        return QRect(0, 20, self.width(), 32)

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
        p.setBrush(QColor(T.TL_FREE))
        p.drawRoundedRect(r, T.RADIUS, T.RADIUS)
        p.setClipRect(r)
        # przeszłość (od pełnej godziny do teraz)
        x = self._x(now)
        p.setBrush(QColor(T.TL_PAST))
        p.drawRect(QRect(r.left(), r.top(), int(x - r.left()), r.height()))
        # za limitem początku – można tylko dokończyć rezerwację
        xl = self._x(lim)
        c = QColor(T.TL_LIMIT)
        c.setAlpha(90)
        p.setBrush(c)
        p.drawRect(QRect(int(xl), r.top(), int(r.right() - xl) + 1, r.height()))
        p.setBrush(QBrush(QColor(T.TL_HATCH), Qt.BDiagPattern))
        p.drawRect(QRect(int(xl), r.top(), int(r.right() - xl) + 1, r.height()))
        for res in self.res:
            if res.end <= t0 or res.start >= t1:
                continue
            x1, x2 = self._x(res.start), self._x(res.end)
            mine = self.me and self.me in res.authors
            p.setBrush(QColor(T.INFO if mine else T.TL_TAKEN))
            p.drawRoundedRect(QRect(int(x1), r.top() + 4, max(int(x2 - x1), 2), r.height() - 8), 4, 4)
        if self.sel:
            s, e = self.sel
            if e > t0 and s < t1:
                sel = QColor(T.TL_SEL)
                sel.setAlpha(36)
                p.setBrush(sel)
                p.setPen(QPen(QColor(T.TL_SEL), 2))
                x1, x2 = self._x(s), self._x(e)
                p.drawRoundedRect(QRect(int(x1) + 1, r.top() + 1, max(int(x2 - x1) - 2, 2), r.height() - 2), 5, 5)
        p.setClipping(False)
        p.setPen(QPen(QColor(T.TL_NOW), 2))
        p.drawLine(int(x), r.top() - 3, int(x), r.bottom() + 3)
        p.setPen(QPen(QColor(T.TEXT_3), 1, Qt.DashLine))
        p.drawLine(int(xl), r.top() - 3, int(xl), r.bottom() + 3)

        f = QFont(self.font())
        f.setPixelSize(T.FS_CAPTION)
        p.setFont(f)
        fm = p.fontMetrics()
        hours = int((t1 - t0).total_seconds() // 3600)
        step = 2 if r.width() / max(hours, 1) >= 14 else 4
        for i in range(hours + 1):
            t = t0 + timedelta(hours=i)
            xt = int(self._x(t))
            if t.hour == 0:
                p.setPen(QPen(QColor(T.TEXT_3), 1))
                p.drawLine(xt, r.top(), xt, r.bottom())
                p.setPen(QColor(T.TEXT))
                p.drawText(QRect(xt + 4, 0, 120, 16), Qt.AlignLeft | Qt.AlignVCenter,
                           f"{self.DAYS_PL[t.weekday()]} {t:%d.%m}")
            if t.hour % step == 0 and r.left() + 8 <= xt <= r.right() - 8:
                p.setPen(QColor(T.TEXT_3))
                p.drawText(QRect(xt - 20, r.bottom() + 6, 40, 16), Qt.AlignCenter, f"{t.hour:02d}")
        midnight = (t0 + timedelta(days=1)).replace(hour=0)
        label = f"{self.DAYS_PL[t0.weekday()]} {t0:%d.%m}"
        if t0.hour != 0 and self._x(midnight) - r.left() > fm.horizontalAdvance(label) + 16:
            p.setPen(QColor(T.TEXT_2))
            p.drawText(QRect(r.left(), 0, 200, 16), Qt.AlignLeft | Qt.AlignVCenter, label)
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
        self.setObjectName("time")
        self.setFixedWidth(T.S6 * 3)
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

page_header = T.page_header


def flow_box(layout) -> QWidget:
    """Opakowuje FlowLayout w widżet, żeby siatka znała wysokość po zawinięciu rzędów."""
    w = QWidget()
    w.setLayout(layout)
    sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    sp.setHeightForWidth(True)
    w.setSizePolicy(sp)
    return w


class AutoList(QListWidget):
    """Lista wierszy-widżetów, których wysokość zależy od szerokości (zawijany tekst) – przelicza się sama."""

    def __init__(self):
        super().__init__()
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._fix_timer = QTimer(self)
        self._fix_timer.setSingleShot(True)
        self._fix_timer.setInterval(0)
        self._fix_timer.timeout.connect(self._fix_heights)

    def schedule_fix(self):
        self._fix_timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.schedule_fix()

    def showEvent(self, e):
        super().showEvent(e)
        self.schedule_fix()

    def _fix_heights(self):
        w = self.viewport().width()
        for i in range(self.count()):
            it = self.item(i)
            holder = self.itemWidget(it)
            if holder is None:
                continue
            h = holder.heightForWidth(w) if holder.hasHeightForWidth() else holder.sizeHint().height()
            if h > 0 and it.sizeHint().height() != h:
                it.setSizeHint(QSize(10, h))


def list_row(lst: QListWidget, widget: QWidget, data=None, gap: int = T.S2) -> QListWidgetItem:
    """Wiersz listy jako widżet z odstępem pod spodem (zamiast ramek i separatorów)."""
    holder = QWidget()
    hl = T.vbox(holder, (0, 0, 0, gap))
    hl.addWidget(widget)
    it = QListWidgetItem()
    if data is not None:
        it.setData(Qt.UserRole, data)
    it.setSizeHint(QSize(10, widget.sizeHint().height() + gap))
    lst.addItem(it)
    lst.setItemWidget(it, holder)
    return it


def row_widget(item: QListWidgetItem, lst: QListWidget) -> QWidget | None:
    holder = lst.itemWidget(item)
    if holder is None or holder.layout() is None or holder.layout().count() == 0:
        return None
    return holder.layout().itemAt(0).widget()


def list_message(lst: QListWidget, text: str):
    """Pusta lista: jedno zdanie, wyśrodkowane, bez ramki."""
    it = QListWidgetItem(text)
    it.setFlags(Qt.NoItemFlags)
    it.setForeground(QColor(T.TEXT_3))
    it.setTextAlignment(Qt.AlignCenter)
    it.setSizeHint(QSize(10, T.S6 * 3))
    lst.addItem(it)


def repolish(w: QWidget, name: str):
    if w.objectName() != name:
        w.setObjectName(name)
        w.style().unpolish(w)
        w.style().polish(w)


# ---------------------------------------------------------------- wiersz ulubionego respa
class SpotCard(QFrame):
    def __init__(self, spot: Spot, me: str, on_unfav, selected: bool = False):
        super().__init__()
        self.setObjectName("rowActive" if selected else "row")
        self.setCursor(Qt.PointingHandCursor)
        lay = T.vbox(self, (T.S3, T.S2, T.S1, T.S3), T.S1)
        top = T.hbox(spacing=T.S2)
        name = T.label(spot.name, "strong", wrap=True)
        top.addWidget(name, 1)
        star = T.icon_button("star-fill", "Usuń z ulubionych", color=T.TEXT_2)
        star.clicked.connect(lambda: QTimer.singleShot(0, lambda: on_unfav(spot.id)))
        top.addWidget(star, 0, Qt.AlignTop)
        lay.addLayout(top)
        now = now_server()
        upcoming = [r for r in spot.reservations if r.end >= now]
        mine = [r for r in upcoming if me and me in r.authors]
        st = T.hbox(spacing=T.S2)
        if spot.is_taken:
            st.addWidget(_dot(T.TEXT_3))
            st.addWidget(T.label(f"Zajęty · {spot.holder} {spot.holder_time}".strip(), "muted"), 1)
        elif spot.status:
            st.addWidget(_dot(T.OK))
            st.addWidget(T.label("Wolny", "muted"), 1)
        else:
            st.addWidget(_dot(T.TEXT_3))
            st.addWidget(T.label("Status nieznany", "muted"), 1)
        future = [r for r in upcoming if r.start > now]
        if future:
            cnt = T.label(f"{len(future)} zaplan.", "hint")
            cnt.setToolTip(f"Zaplanowanych rezerwacji: {len(future)}")
            st.addWidget(cnt)
        lay.addLayout(st)
        if future:
            d = (future[0].start.date() - now.date()).days
            day = {0: "dziś", 1: "jutro"}.get(d, f"{future[0].start:%d.%m}")
            nxt = f"Następna {day} {future[0].label} · {future[0].who}"
        else:
            nxt = f"Następna: {spot.next_text or 'brak'}"
        lay.addWidget(T.label(nxt, "hint", wrap=True))
        if mine:
            def _d(r):
                d = (r.start.date() - now.date()).days
                return {0: "dziś", 1: "jutro"}.get(d, f"{r.start:%d.%m}")
            m = T.label("Twoja: " + ", ".join(f"{_d(r)} {r.label}" for r in mine), "hint", "info", wrap=True)
            lay.addWidget(m)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)


def _dot(color: str) -> QLabel:
    l = QLabel()
    l.setPixmap(T.dot(color, 8))
    l.setFixedSize(8, 16)
    l.setAlignment(Qt.AlignCenter)
    return l


class NavButton(QPushButton):
    """Pozycja menu: ikona i tekst w stałych miejscach (ikona zawsze na tej samej osi, 12 px przerwy)."""

    def __init__(self, icon_name: str, text: str, tone: str = T.TEXT_2):
        super().__init__()
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._icon_name, self._tone = icon_name, tone
        h = T.hbox(self, (T.S3, 0, T.S3, 0), T.S3)
        self._ic = QLabel()
        self._ic.setFixedSize(T.ICON_NAV, T.ICON_NAV)
        self._txt = QLabel(text)
        self._txt.setObjectName("strong")
        for w in (self._ic, self._txt):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        h.addWidget(self._ic)
        h.addWidget(self._txt, 1)
        self.setProperty("full", text)
        self.setToolTip(text)
        self.toggled.connect(lambda _: self._paint())
        self._hover = False
        self._paint()

    def set_label(self, text: str):
        self.setProperty("full", text)
        self._txt.setText(text)

    def set_compact(self, compact: bool, width: int = 0):
        """Zwinięty pasek: sama ikona – na środku przycisku (stały rozmiar = wyśrodkowanie przez układ)."""
        self._txt.setVisible(not compact)
        self.layout().setContentsMargins(*((0, 0, 0, 0) if compact else (T.S3, 0, T.S3, 0)))

    def enterEvent(self, e):
        self._hover = True
        self._paint()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._paint()
        super().leaveEvent(e)

    def _paint(self):
        on = self.isChecked() or self._hover
        color = T.TEXT if on else self._tone
        self._ic.setPixmap(T.pixmap(self._icon_name, color, T.ICON_NAV))
        self._txt.setStyleSheet(f"color: {color};")


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
            QTimer.singleShot(1000, lambda: T.warn(
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
        v = T.vbox(root)
        self.refresh_btn = T.button("Odśwież", "refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.user_lbl = QLabel("")
        hdr, self.resp_sub = page_header("Respy", "Tablica rezerwacji tibialoot.com", [self.refresh_btn])
        v.addWidget(hdr)

        split = QSplitter(Qt.Horizontal)
        self.split = split
        split.setChildrenCollapsible(False)
        split.setHandleWidth(T.S6)
        v.addWidget(split, 1)

        # lewa strona – listy respów
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        fav_w = QWidget()
        fl = T.vbox(fav_w, (0, T.S3, 0, 0))
        self.fav_list = AutoList()
        self.fav_list.itemClicked.connect(lambda it: QTimer.singleShot(0, lambda sid=it.data(Qt.UserRole): self.select_spot(sid)))
        fl.addWidget(self.fav_list)
        login_btn = T.button("Zaloguj się")
        login_btn.clicked.connect(lambda: self.login_site("tibia"))
        self.fav_empty = T.EmptyState("user", "Zaloguj się na tibialoot.com",
                                      "Respy i rezerwacje pojawią się po zalogowaniu.", login_btn)
        self.fav_list.hide()
        fl.addWidget(self.fav_empty)
        self.tabs.addTab(fav_w, "Ulubione")

        all_w = QWidget()
        al = T.vbox(all_w, (0, T.S3, 0, 0), T.S2)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Szukaj respa lub regionu")
        self.search.addAction(T.icon("search", T.TEXT_3), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._fill_all)
        al.addWidget(self.search)
        self.only_free = QCheckBox("Tylko wolne teraz")
        self.only_free.toggled.connect(self._fill_all)
        al.addWidget(self.only_free)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "Resp", "Status", "Następna"])
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(T.ICON, T.ICON))
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.ElideRight)
        hdr_view = self.tree.header()
        hdr_view.setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        hdr_view.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr_view.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr_view.setSectionResizeMode(2, QHeaderView.Fixed)
        hdr_view.setSectionResizeMode(3, QHeaderView.Fixed)
        self.tree.setColumnWidth(0, T.CONTROL_H)
        self.tree.setColumnWidth(2, T.S6 * 3)
        self.tree.setColumnWidth(3, T.S6 * 4)
        self.tree.setColumnHidden(3, True)      # „następna” jest w podpowiedzi i w panelu respa
        self.tree.setCursor(Qt.PointingHandCursor)
        self.tree.itemClicked.connect(self._tree_clicked)
        al.addWidget(self.tree, 1)
        self.tabs.addTab(all_w, "Wszystkie")

        mine_w = QWidget()
        ml = T.vbox(mine_w, (0, T.S3, 0, 0))
        self.mine_list = AutoList()
        self.mine_list.itemClicked.connect(
            lambda it: it.data(Qt.UserRole) and QTimer.singleShot(0, lambda sid=it.data(Qt.UserRole): self.select_spot(sid)))
        ml.addWidget(self.mine_list)
        self.tabs.addTab(mine_w, "Moje")
        self.tabs.setMinimumWidth(T.S6 * 11)
        split.addWidget(self.tabs)

        # ===== strona „Snajper”
        snipe_page = QWidget()
        sl = T.vbox(snipe_page)
        new_snipe = T.button("Pilnuj terminu", "target")
        new_snipe.clicked.connect(lambda: self._snipe_from_nav("snipe"))
        hdr2, _ = page_header("Snajper",
                              "Pilnowane terminy: zamknięte zarezerwuję w sekundzie otwarcia, zajęte – gdy ktoś je zwolni.",
                              [new_snipe])
        sl.addWidget(hdr2)
        self.snipe_list = AutoList()
        sl.addWidget(self.snipe_list, 1)
        go_resp = T.button("Przejdź do respów")
        go_resp.clicked.connect(lambda: self.go_page(0))
        self.snipe_empty = T.EmptyState("target", "Nie pilnujesz jeszcze żadnego terminu",
                                        "Wybierz resp na stronie Respy i kliknij „Pilnuj”. "
                                        "Aplikacja musi być wtedy włączona.", go_resp)
        sl.addWidget(self.snipe_empty, 1)
        self._snipe_page = snipe_page
        self._resp_page = root

        # prawa strona – wybrany resp i rezerwacja
        right = QWidget()
        rl = T.vbox(right)
        head = T.hbox(spacing=T.S2)
        tcol = T.vbox(spacing=T.S1)
        self.spot_title = T.label("Wybierz resp z listy", "title", wrap=True)
        tcol.addWidget(self.spot_title)
        self.spot_sub = T.label("Ulubione, wszystkie respy i Twoje rezerwacje są po lewej.", "muted", wrap=True)
        tcol.addWidget(self.spot_sub)
        head.addLayout(tcol, 1)
        self.snipe_btn = T.button("Pilnuj", "target",
                                  tip="Termin jeszcze zamknięty – zarezerwuję go w sekundzie otwarcia.\n"
                                      "Termin zajęty – dam znać (albo zarezerwuję), gdy ktoś go zwolni.")
        self.snipe_btn.clicked.connect(lambda: self.new_snipe("snipe"))
        head.addWidget(self.snipe_btn, 0, Qt.AlignTop)
        self.open_btn = T.icon_button("external", "Otwórz na stronie tibialoot.com")
        self.open_btn.clicked.connect(lambda: self.cur_spot and webbrowser.open(api.spot_url(self.guild, self.cur_spot)))
        head.addWidget(self.open_btn, 0, Qt.AlignTop)
        rl.addLayout(head)
        rl.addSpacing(T.S4)

        self.timeline = Timeline()
        self.timeline.setToolTip("Kliknij oś, aby wybrać godzinę")
        self.timeline.picked.connect(self._timeline_pick)
        rl.addWidget(self.timeline)
        rl.addSpacing(T.S1)
        sw = lambda c, ch="■": f"<span style='color:{c}'>{ch}</span>"
        self.legend = QLabel(f"{sw(T.OK)} wolne &nbsp;&nbsp;{sw(T.TL_TAKEN)} zajęte &nbsp;&nbsp;{sw(T.INFO)} Twoje "
                             f"&nbsp;&nbsp;{sw(T.TL_SEL, '□')} wybrany &nbsp;&nbsp;{sw(T.TEXT_3, '┆')} ostatni możliwy początek")
        self.legend.setObjectName("hint")
        self.legend.setMinimumWidth(10)
        self.legend.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        rl.addWidget(self.legend)
        rl.addSpacing(T.S4)

        lists = QBoxLayout(QBoxLayout.LeftToRight)
        lists.setSpacing(T.S4)
        self.lists_box = lists
        c1 = T.vbox(spacing=T.S2)
        c1.addWidget(T.caption("Wolne okna – kliknij, aby wybrać"))
        self.free_list = QListWidget()
        self.free_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.free_list.setCursor(Qt.PointingHandCursor)
        self.free_list.itemClicked.connect(self._free_clicked)
        c1.addWidget(self.free_list, 1)
        c2 = T.vbox(spacing=T.S2)
        c2.addWidget(T.caption("Nadchodzące rezerwacje"))
        self.res_list = QListWidget()
        self.res_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        c2.addWidget(self.res_list, 1)
        lists.addLayout(c1, 1)
        lists.addLayout(c2, 1)
        rl.addLayout(lists, 1)
        rl.addSpacing(T.S4)

        # --- formularz nowej rezerwacji
        form = QFrame()
        form.setObjectName("panel")
        fl2 = T.vbox(form, (T.S4, T.S4, T.S4, T.S4), T.S3)
        grid = QGridLayout()
        grid.setHorizontalSpacing(T.S3)
        grid.setVerticalSpacing(T.S3)
        grid.setColumnMinimumWidth(0, T.S6 * 3)

        def row_label(t):
            l = T.label(t, "muted")
            l.setFixedHeight(T.CONTROL_H)
            return l

        # początek: dzień + godzina z przyciskami przesunięć
        r1 = FlowLayout(hspacing=T.S2, vspacing=T.S2)
        self.sday_group = QButtonGroup(self)
        self.sday_btns = []
        for i in range(2):
            b = QPushButton()
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setCursor(Qt.PointingHandCursor)
            self.sday_group.addButton(b, i)
            self.sday_btns.append(b)
            r1.addWidget(b)
        self.sday_group.idClicked.connect(self._start_day_clicked)
        self.shift_btns = []
        for mins, lbl in ((-60, "−1 h"), (-15, "−15")):
            b = T.button(lbl, kind="step")
            b.setFixedWidth(T.S6 + T.S4)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, m=mins: self._shift_start(m))
            r1.addWidget(b)
            self.shift_btns.append(b)
        self.start_time = TimeField()
        self.start_time.committed.connect(self._start_time_changed)
        self.start_time.stepped.connect(self._shift_start)
        r1.addWidget(self.start_time)
        for mins, lbl in ((15, "+15"), (60, "+1 h")):
            b = T.button(lbl, kind="step")
            b.setFixedWidth(T.S6 + T.S4)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, m=mins: self._shift_start(m))
            r1.addWidget(b)
            self.shift_btns.append(b)
        self.asap_btn = T.button("Najwcześniej", kind="link", tip="Pierwszy wolny moment na tym respie")
        self.asap_btn.clicked.connect(lambda: self._preselect_first_free())
        r1.addWidget(self.asap_btn)
        grid.addWidget(row_label("Początek"), 0, 0, Qt.AlignTop)
        grid.addLayout(r1, 0, 1)

        # długość: gotowe wartości + suwak do dokładnego ustawienia
        r3 = FlowLayout(hspacing=T.S2, vspacing=T.S2)
        self.len_group = QButtonGroup(self)
        self.len_group.setExclusive(False)
        self.len_btns = []
        short = {30: "30 min", 60: "1 h", 90: "1,5 h", 120: "2 h", 150: "2,5 h", 180: "3 h"}
        for mins in (30, 60, 90, 120, 150, 180):
            b = QPushButton(short[mins])
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(fmt_len(mins))
            b.setProperty("mins", mins)
            b.clicked.connect(lambda _=False, m=mins: self._set_len(m))
            r3.addWidget(b)
            self.len_btns.append(b)
        self.len_slider = QSlider(Qt.Horizontal)
        self.len_slider.setSingleStep(5)
        self.len_slider.setPageStep(15)
        self.len_slider.setFixedWidth(T.S6 * 4)
        self.len_slider.setToolTip("Dokładna długość (co 5 min)")
        self.len_slider.valueChanged.connect(self._len_slider_changed)
        exact = QWidget()                     # suwak i wartość zawijają się razem
        el = T.hbox(exact, spacing=T.S3)
        el.addWidget(self.len_slider)
        self.len_lbl = T.label("", "strong")
        self.len_lbl.setFixedSize(T.S6 * 3, T.CONTROL_H)
        el.addWidget(self.len_lbl)
        r3.addWidget(exact)
        grid.addWidget(row_label("Długość"), 1, 0, Qt.AlignTop)
        grid.addLayout(r3, 1, 1)
        self.cap_lbl = T.label("", "hint", wrap=True)
        grid.addWidget(self.cap_lbl, 2, 1)
        grid.setColumnStretch(1, 1)
        fl2.addLayout(grid)

        # podsumowanie + przycisk
        r2 = T.hbox(spacing=T.S4)
        sumcol = T.vbox(spacing=T.S1)
        self.summary_lbl = T.label("", "title", wrap=True)
        sumcol.addWidget(self.summary_lbl)
        vrow = T.hbox(spacing=T.S2)
        self.valid_icon = QLabel()
        self.valid_icon.setFixedSize(T.ICON, T.ICON)
        vrow.addWidget(self.valid_icon)
        self.valid_lbl = T.label("", "muted", wrap=True)
        vrow.addWidget(self.valid_lbl, 1)
        sumcol.addLayout(vrow)
        r2.addLayout(sumcol, 1)
        self.book_btn = T.button("Zarezerwuj", kind="primary")
        self.book_btn.setMinimumWidth(T.S6 * 6)
        self.book_btn.clicked.connect(self.book)
        r2.addWidget(self.book_btn, 0, Qt.AlignVCenter)
        fl2.addLayout(r2)
        self.limit_lbl = T.label("Najwyżej 3 godziny, początek najwyżej dobę do przodu. Czas serwera (CET).", "hint", wrap=True)
        fl2.addWidget(self.limit_lbl)
        rl.addWidget(form)

        self.detail_stack = QStackedWidget()
        self.detail_empty = T.EmptyState("calendar", "Wybierz resp",
                                         "Kliknij resp na liście po lewej – zobaczysz wolne godziny i zarezerwujesz termin.")
        self.detail_stack.addWidget(self.detail_empty)
        self.detail_stack.addWidget(right)
        split.addWidget(self.detail_stack)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 700])

        # pasek stanu: ikona + tekst skracany „…”
        sb = QWidget()
        sbl = T.hbox(sb, (T.S4, 0, T.S4, 0), T.S2)
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(T.ICON - 2, T.ICON - 2)
        sbl.addWidget(self.status_icon)
        self.status_lbl = ElidedLabel()
        self.status_lbl.setObjectName("hint")
        sbl.addWidget(self.status_lbl, 1)
        self.statusBar().addWidget(sb, 1)
        self.statusBar().setSizeGripEnabled(False)

        # ===== strona „Hunty”
        hunt_page = QWidget()
        hl = T.vbox(hunt_page)
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
            hl.addWidget(T.EmptyState("alert", "Moduł huntów nie uruchomił się",
                                      "Szczegóły w Ustawienia → Dane i log. Najczęściej brakuje biblioteki: "
                                      "pip install playwright"), 1)

        # ===== strona „Imbuementy”
        imbu_page = QWidget()
        il = T.vbox(imbu_page)
        hdr4, _ = page_header("Imbuementy", "Kalkulator kosztu: sprawdza, czy taniej kupić materiały na markecie, "
                                            "czy pakiet za Gold Tokeny. Wpisane ceny są zapamiętywane.", [])
        il.addWidget(hdr4)
        from imbu import ImbuPage
        self.imbu = ImbuPage(self.cfg, lambda m: self.log(m, "Imbuementy"))
        il.addWidget(self.imbu, 1)

        # ===== powłoka: pasek boczny + strony
        shell = QWidget()
        sh = T.hbox(shell)
        sh.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        for w in (self._resp_page, self._snipe_page, hunt_page, imbu_page):
            wrap = QWidget()
            wl = T.vbox(wrap, (T.S6, T.S6, T.S6, T.S4))
            wl.addWidget(w)
            self.pages.addWidget(wrap)
        sh.addWidget(self.pages, 1)
        self.setCentralWidget(shell)
        self.go_page(int(self.cfg.data.get("main_tab", 0)))
        self._reservation_enabled(False)

    # ---------- powłoka
    NAV = (("calendar", "Respy"), ("target", "Snajper"), ("upload", "Hunty"), ("calculator", "Imbuementy"))
    SIDE_W, SIDE_W_COMPACT = 240, 88

    def _nav_button(self, icon_name: str, text: str) -> "NavButton":
        return NavButton(icon_name, text)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(self.SIDE_W)
        self.sidebar = side
        self._compact_hide: list[QWidget] = []
        v = T.vbox(side, (T.S3, T.S4, T.S3, T.S3))
        self._side_layout = v
        brand = QWidget()
        brand.setFixedHeight(T.ROW_H)
        brow = T.hbox(brand, (T.S3, 0, 0, 0), T.S3)
        logo = QLabel()
        if ICON_PATH.exists():
            logo.setPixmap(QIcon(str(ICON_PATH)).pixmap(28, 28))
        logo.setFixedSize(28, 28)
        brow.addWidget(logo)
        name = T.label("Tibia Apps Companion", "strong")
        brow.addWidget(name, 1)
        self._brand_row, self._logo = brow, logo
        self._compact_hide.append(name)
        v.addWidget(brand)
        v.addSpacing(T.S6)

        self.nav_group = QButtonGroup(self)
        self.nav_btns = []
        for i, (ic, name_) in enumerate(self.NAV):
            b = self._nav_button(ic, name_)
            self.nav_group.addButton(b, i)
            self.nav_btns.append(b)
            v.addWidget(b)
            v.addSpacing(T.S1)
        self.nav_group.idClicked.connect(self.go_page)
        v.addStretch(1)

        def padded(w, top=0):
            box = QWidget()
            l = T.hbox(box, (T.S3, top, 0, 0))
            l.addWidget(w)
            return box

        lbl_today = padded(T.caption("Dziś"))
        v.addWidget(lbl_today)
        v.addSpacing(T.S2)
        from today import TodayWidget
        self.today = TodayWidget(self.cfg.dir / "today", now_server, lambda m: self.log(m, "Dziś"))
        self._today_box = padded(self.today)
        v.addWidget(self._today_box)
        v.addSpacing(T.S6)
        lbl_acc = padded(T.caption("Konta"))
        v.addWidget(lbl_acc)
        v.addSpacing(T.S1)
        self._compact_hide += [lbl_today, lbl_acc]
        self.acc_widgets = {}
        for key in ("tibia", "hunt"):
            row = QPushButton()
            row.setObjectName("account")
            row.setCursor(Qt.PointingHandCursor)
            row.clicked.connect(lambda _=False, k=key: self.login_site(k))
            rl = T.hbox(row, (T.S3, 0, T.S3, 0), T.S3)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setAttribute(Qt.WA_TransparentForMouseEvents)
            rl.addWidget(dot)
            col = T.vbox()
            nm = T.label(SITES[key][0], "strong")
            st = T.label("", "hint")
            for l in (nm, st):
                l.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(nm)
            col.addWidget(st)
            self._compact_hide += [nm, st]
            rl.addLayout(col, 1)
            v.addWidget(row)
            self.acc_widgets[key] = (row, dot, st)
            self._acc_layouts = getattr(self, "_acc_layouts", []) + [rl]
            self._paint_account(key)
        v.addSpacing(T.S4)
        self.update_btn = QPushButton("")
        self.update_btn.setObjectName("update")
        self.update_btn.setIcon(T.icon("update", T.OK, T.ICON_NAV))
        self.update_btn.setIconSize(QSize(T.ICON_NAV, T.ICON_NAV))
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(lambda: self.pending_update and self.install_update(self.pending_update))
        v.addWidget(self.update_btn)
        v.addSpacing(T.S1)
        sett = self._nav_button("settings", "Ustawienia")
        sett.setCheckable(False)
        self.sett_btn = sett
        sett.clicked.connect(lambda: self.open_settings())
        v.addWidget(sett)
        v.addSpacing(T.S2)
        self.clock_lbl = T.label("", "hint")
        self._clock_box = padded(self.clock_lbl)
        v.addWidget(self._clock_box)
        self._compact_hide.append(self._clock_box)
        return side

    # ---------- układ zależny od szerokości okna
    COMPACT_SIDEBAR_BELOW = 1150   # szerokość okna, poniżej której pasek boczny pokazuje same ikony
    STACK_BELOW = 900              # szerokość strony, poniżej której panele układają się pionowo

    def _apply_responsive(self):
        if not hasattr(self, "sidebar"):
            return
        compact = self.width() < self.COMPACT_SIDEBAR_BELOW
        if getattr(self, "_compact", None) != compact:
            self._compact = compact
            self.sidebar.setFixedWidth(self.SIDE_W_COMPACT if compact else self.SIDE_W)
            for w in self._compact_hide:
                w.setVisible(not compact)
            # zwinięty pasek: logo, ikony, kafelki i kropki kont na jednej osi
            self._brand_row.setContentsMargins(0 if compact else T.S3, 0, 0, 0)
            inner = self.SIDE_W_COMPACT - 2 * T.S3
            for b in self.nav_btns + [self.sett_btn]:
                b.set_compact(compact, inner)
            self.update_btn.setText("" if compact else (self.update_btn.property("full") or ""))
            self.update_btn.setStyleSheet(f"padding-left: {(inner - T.ICON_NAV) // 2}px;" if compact else "")
            for rl in self._acc_layouts:      # kropka na osi ikon menu
                m = (self.SIDE_W_COMPACT - 2 * T.S3 - 8) // 2
                rl.setContentsMargins(*((m, 0, m, 0) if compact else (T.S3, 0, T.S3, 0)))
            self._today_box.layout().setContentsMargins(0 if compact else T.S3, 0, 0, 0)
            self.today.set_vertical(compact)
            self._paint_update_btn()
            self._fill_snipes_nav()
        page_w = self.width() - self.sidebar.width()
        stacked = page_w < self.STACK_BELOW
        if getattr(self, "_stacked", None) != stacked:
            self._stacked = stacked
            self.split.setOrientation(Qt.Vertical if stacked else Qt.Horizontal)
            if stacked:
                self.split.setSizes([300, 900])
            else:
                self.split.setSizes([300, 700])
        if self.hunt:
            self.hunt.set_stacked(page_w < 900)
        self.lists_box.setDirection(QBoxLayout.TopToBottom if page_w < 640 else QBoxLayout.LeftToRight)

    def _paint_update_btn(self):
        rel = getattr(self, "pending_update", None)
        if rel is None:
            return
        self.update_btn.setProperty("full", f"Aktualizacja {rel.version}")
        self.update_btn.setText("" if getattr(self, "_compact", False) else f"Aktualizacja {rel.version}")
        self.update_btn.setToolTip(f"Zainstaluj wersję {rel.version}")

    def go_page(self, i: int):
        i = max(0, min(int(i), self.pages.count() - 1))
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
        color, txt, role = ((T.OK, "zalogowano", "faint") if st else
                            (T.TEXT_3, "sprawdzam…", "faint") if st is None else (T.ERR, "kliknij, aby zalogować", "err"))
        if key == "tibia" and st and self.board and self.board.author:
            txt = self.board.author
        row, dot, lbl = self.acc_widgets[key]
        dot.setPixmap(T.dot(color, 8))
        lbl.setText(txt)
        T.set_role(lbl, role)
        row.setToolTip(f"{name} – {purpose}: {txt}. " + ("Kliknij, aby się wylogować." if st else "Kliknij, aby się zalogować."))

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

    STATUS_ICON = {"err": ("alert-circle", T.ERR), "ok": ("check-circle", T.OK), "warn": ("alert", T.WARN),
                   "info": ("info", T.INFO)}

    _status_seq = 0
    _status_transient = False

    def status(self, msg: str, role: str = "muted", timeout: int = 0):
        """timeout (ms) > 0: komunikat znika sam (np. podpowiedzi po kliknięciu osi czasu)."""
        self._status_seq += 1
        self._status_transient = bool(timeout)
        if timeout:
            seq = self._status_seq
            QTimer.singleShot(timeout, lambda: seq == self._status_seq and self.status(""))
        # znaczki ✔ ✖ ⚠ z logu zamieniamy na ikonę – w pasku stanu nie ma emoji
        for sym in ("✔ ", "✖ ", "⚠ ", "✔", "✖", "⚠"):
            msg = msg.replace(sym, "")
        self.status_lbl.setText(msg if len(msg) <= 140 else msg[:137] + "…")
        self.status_lbl.setToolTip(msg)
        T.set_role(self.status_lbl, {"err": "err", "ok": "ok", "warn": "warn", "info": "info"}.get(role, "muted"))
        ic = self.STATUS_ICON.get(role)
        self.status_icon.setPixmap(T.pixmap(ic[0], ic[1], T.ICON - 2) if ic else QPixmap())
        self.status_icon.setVisible(bool(ic))

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
            if T.ask(self, "Konto", f"Jesteś zalogowany na {SITES[key][0]}.\n\nWylogować z aplikacji?") \
                    == QMessageBox.Yes:
                self.logout_site(key)
            return
        if not self.web:
            T.info(self, "Logowanie", "Wbudowana przeglądarka niedostępna (brak QtWebEngine).")
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
        box = T.message(self, "info", "Nowa wersja",
                        f"<b>Dostępna wersja {rel.version}</b> (masz {VERSION}).<br><br>"
                        + (notes.replace("\n", "<br>") + "<br><br>" if notes else "")
                        + "Aplikacja pobierze aktualizację, zamknie się na chwilę i uruchomi ponownie. "
                          "Ustawienia, ulubione, snajpy i logowania zostaną.", QMessageBox.NoButton)
        box.addButton("Później", QMessageBox.RejectRole).setCursor(Qt.PointingHandCursor)
        yes = box.addButton("Zaktualizuj teraz", QMessageBox.AcceptRole)
        yes.setObjectName("primary")
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is not yes:
            return
        if sys.platform != "win32":
            T.info(self, "Aktualizacja", f"Pobierz ręcznie: {rel.url}")
            return
        if not updater.can_write_app_dir():
            T.warn(self, "Aktualizacja",
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
                T.warn(self, "Aktualizacja", f"Nie udało się uruchomić aktualizacji:\n{e}")
                return
            self.log(f"Instaluję wersję {rel.version} – aplikacja uruchomi się ponownie.", "Aktualizacje")
            # pewne zakończenie procesu, nawet gdy jakiś wątek w tle (np. przeglądarka huntów) jeszcze działa –
            # skrypt aktualizacji czeka właśnie na zamknięcie tego procesu
            import threading
            threading.Timer(4.0, os._exit, args=(0,)).start()
            self.close()
            QApplication.quit()

        def err(e):
            prog.close()
            T.warn(self, "Aktualizacja", f"Nie udało się pobrać aktualizacji:\n{e}\n\nMożesz pobrać ją ręcznie: {rel.url}")
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
            self.log("Najpierw wybierz resp z listy, potem „Pilnuj”.", role="warn")
            return
        self.new_snipe(kind)

    def _build_tray(self):
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else None
            if icon is None:
                pm = QPixmap(32, 32)
                pm.fill(QColor(T.ACCENT))
                icon = QIcon(pm)
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.setToolTip(APP_NAME)
            self.tray.show()
            self.setWindowIcon(icon)

    def _update_clock(self):
        self.clock_lbl.setText(now_server().strftime("Czas serwera %H:%M:%S"))
        if self.board:   # snajpy potrzebują zalogowania
            self.sniper.tick(now_server())
        if now_server().second == 0:
            if self.cur_spot:
                self._update_spot_view()   # okno doby przesuwa się z minutą
            self._fill_snipes()

    def _reservation_enabled(self, on: bool):
        for w in (self.start_time, self.len_slider, self.book_btn, self.open_btn,
                  *self.sday_btns, *self.len_btns, *self.shift_btns, self.asap_btn, self.snipe_btn):
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
        self.resp_sub.setText(f"Rezerwujesz jako <span style='color:{T.TEXT}'>{board.author or '?'}</span> · "
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
            self.fav_list.hide()
            self.fav_empty.set("user", "Zaloguj się na tibialoot.com", "Respy i rezerwacje pojawią się po zalogowaniu.")
            self.fav_empty.show()
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
                 title_role: str, right_text: str, button=None, active=False, data=None) -> QListWidgetItem:
        """Wiersz listy terminów: kolorowy znacznik, godziny, dzień i opis, tekst po prawej, opcjonalny przycisk."""
        row = QFrame()
        row.setObjectName("rowActive" if active else "row")
        h = T.hbox(row, (T.S3, T.S2, T.S2 if button is not None else T.S3, T.S2), T.S3)
        stripe = QFrame()
        stripe.setFixedWidth(3)
        stripe.setStyleSheet(f"background:{bar}; border-radius:1px;")
        h.addWidget(stripe)
        col = T.vbox(spacing=0)
        end_txt = f"{end:%H:%M}"
        if end.date() != start.date():
            end_txt = "24:00" if (end.hour, end.minute) == (0, 0) else f"{end:%H:%M} ({self._daytxt(end)})"
        tl = T.label(f"{start:%H:%M} – {end_txt}", "strong")
        col.addWidget(tl)
        sub = T.hbox(spacing=0)
        d = T.label(day.capitalize() + (" · " if title else ""), "hint")
        sub.addWidget(d)
        if title:
            ti = ElidedLabel(title)
            ti.setObjectName("hint")
            T.set_role(ti, title_role)
            sub.addWidget(ti, 1)
        else:
            sub.addStretch(1)
        col.addLayout(sub)
        h.addLayout(col, 1)
        if right_text:
            r_ = T.label(right_text, "hint")
            r_.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(r_, 0, Qt.AlignVCenter)
        if button is not None:
            h.addWidget(button, 0, Qt.AlignVCenter)
        for l in row.findChildren(QLabel) + [stripe]:
            l.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.setFixedHeight(T.S6 * 2 + T.S2 * 2 - 4)    # 52 px
        return list_row(lst, row, data)

    def _mark_active_window(self, s: datetime):
        for i in range(self.free_list.count()):
            it = self.free_list.item(i)
            w, row = it.data(Qt.UserRole), row_widget(it, self.free_list)
            if not w or row is None:
                continue
            repolish(row, "rowActive" if w[0] <= s <= w[1] else "row")

    def _empty_row(self, lst: QListWidget, text: str):
        list_message(lst, text)

    def _delete_button(self, r: Reservation) -> QPushButton:
        b = T.icon_button("trash", "Usuń tę rezerwację na stronie", kind="danger")
        b.clicked.connect(lambda _=False, r=r: QTimer.singleShot(0, lambda: self.delete_reservation(r)))
        return b

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
            card.setObjectName("row")
            card.setCursor(Qt.PointingHandCursor)
            h = T.hbox(card, (T.S3, T.S2, T.S2, T.S2), T.S2)
            col = T.vbox(spacing=0)
            col.addWidget(T.label(sp.name if sp else r.spot_id, "strong", wrap=True))
            col.addWidget(T.label(f"{self._daytxt(r.start).capitalize()} {r.label}" + (f" · {r.status}" if r.status else ""),
                                  "hint", "info"))
            h.addLayout(col, 1)
            h.addWidget(self._delete_button(r), 0, Qt.AlignVCenter)
            for l in card.findChildren(QLabel):
                l.setAttribute(Qt.WA_TransparentForMouseEvents)
            list_row(self.mine_list, card, r.spot_id)
        self._fix_row_height(self.mine_list)
        if not mine:
            list_message(self.mine_list, "Nie masz nadchodzących rezerwacji.")
        self.tabs.setTabText(2, f"Moje · {len(mine)}" if mine else "Moje")

    def delete_reservation(self, r: Reservation):
        if not r.can_delete:
            return
        sp = self.board.spots.get(r.spot_id) if self.board else None
        question = (f"Usunąć rezerwację {r.who} na {sp.name if sp else 'respie'}, "
                    f"{self._daytxt(r.start)} ({r.start:%d.%m}) {r.label}?")
        if T.ask(self, "Usuń rezerwację", question, warn=True) != QMessageBox.Yes:
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
                T.warn(self, "Nie udało się usunąć", msg)

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
        self.fav_empty.set("star", "Brak ulubionych respów", "W zakładce „Wszystkie” kliknij gwiazdkę przy respie.",
                           show_action=False)
        self.fav_empty.setVisible(not favs)
        self.fav_list.setVisible(bool(favs))
        for spot in favs:
            spot.reservations = self.spot_reservations(spot.id)
            card = SpotCard(spot, self.board.author, self.toggle_fav, selected=spot.id == self.cur_spot)
            list_row(self.fav_list, card, spot.id)
        self._fix_row_height(self.fav_list)

    def _mark_selected_fav(self):
        for i in range(self.fav_list.count()):
            it = self.fav_list.item(i)
            row = row_widget(it, self.fav_list)
            if row is not None:
                repolish(row, "rowActive" if it.data(Qt.UserRole) == self.cur_spot else "row")

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
                reg = QTreeWidgetItem(["", spot.region or "Inne"])
                reg.setFirstColumnSpanned(False)
                f = reg.font(1)
                f.setWeight(QFont.DemiBold)
                reg.setFont(1, f)
                reg.setForeground(1, QColor(T.TEXT))
                self.tree.addTopLevelItem(reg)
                regions[spot.region] = reg
            fav = spot.id in self.cfg.favorites
            it = QTreeWidgetItem(["", spot.name,
                                  "Zajęty" if spot.is_taken else ("Wolny" if spot.status else ""),
                                  spot.next_text if spot.next_text not in ("Brak", "") else ""])
            it.setData(0, Qt.UserRole, spot.id)
            it.setIcon(0, T.icon("star-fill", T.TEXT_2) if fav else T.icon("star", T.TEXT_3))
            it.setForeground(1, QColor(T.TEXT_2))
            it.setForeground(2, QColor(T.TEXT_3 if spot.is_taken else T.OK))
            it.setToolTip(0, "Kliknij, aby dodać/usunąć z ulubionych")
            nxt = spot.next_text if spot.next_text not in ("Brak", "") else "brak"
            it.setToolTip(1, f"{spot.name}\nNastępna: {nxt}")
            it.setToolTip(3, spot.next_text)
            reg.addChild(it)
        for reg in regions.values():
            reg.setExpanded(bool(q) or len(regions) < 4)
            self._paint_region(reg)

    @staticmethod
    def _paint_region(reg: QTreeWidgetItem):
        reg.setIcon(0, T.icon("chevron-down" if reg.isExpanded() else "chevron-right", T.TEXT_3))
        n = reg.childCount()
        reg.setText(2, str(n))
        reg.setForeground(2, QColor(T.TEXT_3))

    def _tree_clicked(self, it: QTreeWidgetItem, col: int):
        sid = it.data(0, Qt.UserRole)
        if not sid:
            it.setExpanded(not it.isExpanded())
            self._paint_region(it)
            return
        if col == 0:
            on = self.toggle_fav(sid)
            it.setIcon(0, T.icon("star-fill", T.TEXT_2) if on else T.icon("star", T.TEXT_3))
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
        self.detail_stack.setCurrentIndex(1)
        self._reservation_enabled(True)
        self._mark_selected_fav()
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
                b.setText(f"{'Dziś' if i == 0 else 'Jutro'} {d:%d.%m}")
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
        self.cap_lbl.setVisible(bool(self.cap_lbl.text()))
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
        lim = start_limit(now)
        if dt > lim:
            self.status(f"Najpóźniejszy początek: {lim:%d.%m %H:%M}.", "warn", timeout=5000)
            return
        wins = free_windows(self.spot_reservations(self.cur_spot), lo, hi, now)
        for win in wins:
            if win[0] <= dt <= win[1]:
                s = max(dt, win[0])
                self._set_range(s, min(s + MAX_LEN, win[1]))
                if self._status_transient:
                    self.status("")     # poprzednia podpowiedź z osi już nieaktualna
                return
        # kliknięcie w zajęty termin albo w przeszłość → najbliższy wolny moment po nim
        nxt = next((w for w in wins if w[0] >= dt), None)
        if nxt and nxt[0] <= lim:
            self._set_range(nxt[0], min(nxt[0] + MAX_LEN, nxt[1]))
            why = "Ten moment już minął" if dt < now else "Ten moment jest zajęty"
            self.status(f"{why} – wybrano najbliższy wolny: {self._daytxt(nxt[0])} {nxt[0]:%H:%M}.",
                        "info", timeout=5000)
            return
        self.status("Po tej godzinie nie ma już wolnego terminu do zarezerwowania.", "warn", timeout=5000)

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
        self.summary_lbl.setText(f"{self._daytxt(s).capitalize()} {s:%H:%M} – {e:%H:%M}{end_day}"
                                 f"<span style='color:{T.TEXT_2}; font-weight:{T.W_REGULAR}'>"
                                 f"  ·  {fmt_len(self.sel_len) if self.sel_len else '—'}</span>")
        if problem:
            self.valid_lbl.setText(problem)
            T.set_role(self.valid_lbl, "err")
            self.valid_icon.setPixmap(T.pixmap("alert-circle", T.ERR, T.ICON))
            self.book_btn.setEnabled(False)
            self.book_btn.setText("Zarezerwuj")
        else:
            self.valid_lbl.setText("Termin wolny")
            T.set_role(self.valid_lbl, "ok")
            self.valid_icon.setPixmap(T.pixmap("check-circle", T.OK, T.ICON))
            self.book_btn.setEnabled(True)
            self.book_btn.setText(f"Zarezerwuj {s:%H:%M}–{e:%H:%M}")
        self.timeline.set_data(res, (s, e), self.board.author if self.board else "")
        self._mark_active_window(s)

    def _update_spot_view(self):
        if not self.cur_spot or not self.board:
            return
        spot = self.board.spots.get(self.cur_spot)
        if not spot:
            return
        self.spot_title.setText(spot.name)
        now_txt = (f"teraz zajęty – {spot.holder} {spot.holder_time}" if spot.is_taken
                   else "teraz wolny" if spot.status else "")
        self.spot_sub.setText(" · ".join(x for x in (spot.region, now_txt) if x))

        res = self.spot_reservations(spot.id)
        now = now_server()
        lo, hi = booking_range(now)
        lim = start_limit(now)
        self.limit_lbl.setText(f"Początek od teraz do {self._daytxt(lim)} {lim:%H:%M}, najwyżej 3 h. Czas serwera (CET).")
        me = self.board.author
        self.res_list.clear()
        upcoming = [r for r in res if r.end > now and r.start < hi]
        for r in upcoming:
            mine = r.can_delete or (me and me in r.authors)
            color = T.INFO if mine else (T.TEXT_2 if r.status == "Trwa" else T.TL_TAKEN)
            btn = self._delete_button(r) if r.can_delete else None
            self._add_row(self.res_list, bar=color, day=self._daytxt(r.start), start=r.start, end=r.end,
                          title=r.who + (" (Ty)" if mine else ""), title_role="info" if mine else "muted",
                          right_text=(r.status or "").lower(), button=btn)
        if not upcoming:
            self._empty_row(self.res_list, "Brak rezerwacji – wolne całą dobę.")

        self.free_list.clear()
        wins = free_windows(res, lo, hi, now)
        sel = self._selection()
        for w in wins:
            mins = int((w[1] - w[0]).total_seconds() // 60)
            note = f"start najpóźniej {self._daytxt(lim)} {lim:%H:%M}" if w[1] > lim else \
                ("od teraz" if w[0] <= now + timedelta(minutes=1) else "")
            active = bool(sel[0] and w[0] <= sel[0] <= w[1])
            self._add_row(self.free_list, bar=T.OK, day=self._daytxt(w[0]), start=w[0], end=w[1],
                          title=note, title_role="faint", right_text=fmt_len(mins), active=active, data=w)
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
        if T.ask(self, "Potwierdź rezerwację",
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
        # domyślnie okno proponuje najbliższy termin, który jeszcze się nie otworzył
        dlg = SnipeDialog(self, kind, spot.name, self.spot_reservations(spot.id), now_server(), TimeField, None)
        if dlg.exec() == QDialog.Accepted:
            job = dlg.job()
            job.update(spot_id=spot.id, spot_name=spot.name)
            self.sniper.add(job)
            self.go_page(1)
            if job["kind"] == "watch":
                self.sniper.check_watches(now_server())
        import shiboken6
        shiboken6.delete(dlg)

    def _snipe_notify(self, title: str, text: str, job: dict, hit, sound: bool = True):
        if sound:
            self.sounds.play("alarm", 3)
        if self.tray:
            self.tray.showMessage(title, text)
        QApplication.alert(self, 0)
        box = T.message(self, "info", title, text, QMessageBox.Close)
        if hit:
            show = box.addButton("Pokaż i wybierz", QMessageBox.AcceptRole)
            show.setObjectName("primary")
            def go(job=job, hit=hit):
                self.select_spot(job["spot_id"])
                self._apply(start=hit[0], want=int((min(hit[1], hit[0] + MAX_LEN) - hit[0]).total_seconds() // 60))
                self.raise_()
                self.activateWindow()
            show.clicked.connect(go)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setModal(False)
        box.show()

    SNIPE_STATUS = {"active": ("aktywny", None, "muted"), "done": ("gotowe", "ok", "ok"),
                    "failed": ("nieudany", "err", "err"), "expired": ("minął", None, "faint")}

    def _fill_snipes(self):
        self.snipe_list.clear()
        now = now_server()
        order = {"active": 0, "done": 1, "failed": 2, "expired": 3}
        jobs = sorted(self.sniper.jobs, key=lambda j: (order.get(j["status"], 9), j.get("start", j.get("from", ""))))
        n_active = 0
        for j in jobs:
            n_active += j["status"] == "active"
            card = QFrame()
            card.setObjectName("row")
            lay = T.vbox(card, (T.S4, T.S3, T.S2, T.S3), T.S1)
            top = T.hbox(spacing=T.S3)
            top.addWidget(T.icon_label("bell" if (j["kind"] == "watch" or j.get("notify_only")) else "target", T.TEXT_2))
            name = T.label(j["spot_name"], "strong", wrap=True)
            top.addWidget(name, 1)
            text, pill_role, msg_role = self.SNIPE_STATUS.get(j["status"], (j["status"], None, "muted"))
            top.addWidget(T.pill(text, pill_role))
            rm = T.icon_button("x", "Usuń z listy")
            rm.clicked.connect(lambda _=False, jid=j["id"]: QTimer.singleShot(0, lambda: self.sniper.remove(jid)))
            top.addWidget(rm)
            lay.addLayout(top)
            if j["kind"] == "snipe":
                s0, e0 = datetime.strptime(j["start"], "%Y-%m-%dT%H:%M"), datetime.strptime(j["end"], "%Y-%m-%dT%H:%M")
                ft = fire_time(s0)
                parts = [("Powiadomienie o otwarciu" if j.get("notify_only") else "Rezerwacja w chwili otwarcia")
                         + f" · {self._daytxt(s0)} {s0:%H:%M}–{e0:%H:%M}"]
                if j.get("follow"):
                    parts.append("podąża za rezerwacją przed nim")
                    if j.get("floor") or j.get("cap"):
                        fl = datetime.strptime(j["floor"], "%Y-%m-%dT%H:%M") if j.get("floor") else None
                        cp = datetime.strptime(j["cap"], "%Y-%m-%dT%H:%M") if j.get("cap") else None
                        parts.append("widełki " + (f"od {fl:%H:%M} " if fl else "") + (f"do {cp:%H:%M}" if cp else ""))
                if j["status"] == "active":
                    left = ft - now
                    if left.total_seconds() > 0:
                        h, rem = divmod(int(left.total_seconds()), 3600)
                        parts.append(f"otwarcie {self._daytxt(ft)} {ft:%H:%M} (za {h} h {rem // 60} min)")
                desc = " · ".join(p_.strip() for p_ in parts)
            else:
                f0, t0 = datetime.strptime(j["from"], "%Y-%m-%dT%H:%M"), datetime.strptime(j["to"], "%Y-%m-%dT%H:%M")
                desc = ((f"Czekam na zwolnienie · {self._daytxt(f0)} {f0:%H:%M}–{t0:%H:%M} · min. {j['min_len']} min")
                        + (" · zarezerwuję od razu" if j.get("auto") else " · dam znać"))
            ind = T.S4 + T.S3      # tekst pod nazwą, nie pod ikoną
            d = T.label(desc, "muted", wrap=True)
            d.setContentsMargins(ind, 0, T.S2, 0)
            lay.addWidget(d)
            if j.get("msg"):
                m = T.label(j["msg"], "hint", msg_role, wrap=True)
                m.setContentsMargins(ind, 0, T.S2, 0)
                lay.addWidget(m)
            list_row(self.snipe_list, card)
            self._fix_row_height(self.snipe_list)
        self.snipe_list.setVisible(bool(jobs))
        self.snipe_empty.setVisible(not jobs)
        self._n_active = n_active
        self._fill_snipes_nav()

    def _fill_snipes_nav(self):
        n = getattr(self, "_n_active", 0)
        full = f"Snajper · {n}" if n else "Snajper"
        b = self.nav_btns[1]
        b.set_label(full)
        b.setToolTip(f"Snajper – aktywne: {n}" if n else "Snajper")

    @staticmethod
    def _fix_row_height(lst: QListWidget):
        if isinstance(lst, AutoList):
            lst.schedule_fix()

    def _booked(self, res, sid, s):
        ok, msg = res
        if ok:
            self.log(f"✔ {msg}")
        else:
            T.warn(self, "Nie udało się", msg)
            self.log("✖ Rezerwacja odrzucona: " + msg.splitlines()[0])
            self._on_time_changed()

    def _book_err(self, e):
        T.warn(self, "Błąd", str(e))
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


STYLE = ""     # arkusz stylów: theme.stylesheet()


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
    T.install(app)
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
