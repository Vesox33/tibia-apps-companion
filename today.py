"""Sekcja „Dziś”: boosted creature, boosted boss i miasto Rashida (z mapą po najechaniu).

Mapa: wycinki z tibia-map-data (tibiamaps.io) dołączone w assets/maps – działają offline."""
from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

API = "https://api.tibiadata.com/v4"
RASHID_ICON = Path(__file__).with_name("assets") / "rashid.png"
TILE = 64              # kafelek (px) – mieści sprite potwora 64×64 w skali 1:1
CARD_SCALE = 2         # powiększenie w podpowiedzi – zawsze całkowite, żeby pixel art został ostry


def _content_rect(img: QImage):
    w, h = img.width(), img.height()
    left, top, right, bottom = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() > 16:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    return None if right < 0 else (left, top, right - left + 1, bottom - top + 1)


def fit_sprite(pm: QPixmap, size: int, scale: int = 1) -> QPixmap:
    """Przycina obrazek do samej postaci i wyśrodkowuje w kwadracie `size`.
    Skaluje WYŁĄCZNIE o całkowitą wielokrotność (1×, 2×…) bez wygładzania – pixel art zostaje ostry,
    a postacie zachowują proporcje z gry. Gdy postać się nie mieści, skala spada do mniejszej całkowitej."""
    if pm.isNull():
        return pm
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    r = _content_rect(img)
    if r is None:
        return pm
    crop = img.copy(*r)
    cw, ch = crop.width(), crop.height()
    f = scale
    while f > 1 and max(cw, ch) * f > size:
        f -= 1
    if max(cw, ch) * f > size:          # większa niż kafelek nawet w 1:1 – wyjątkowo zmniejszamy
        scaled = QPixmap.fromImage(crop).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    else:
        scaled = QPixmap.fromImage(crop.scaled(cw * f, ch * f, Qt.IgnoreAspectRatio, Qt.FastTransformation))
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return out
SERVER_SAVE_HOUR = 10          # czas serwera (CET/CEST)

import theme as T

# Rashid – stały grafik tygodniowy (zmienia miasto o server save)
RASHID = {   # miasto, miejsce, (x, y) depo z danych mapy tibiamaps – tawerna Rashida jest tuż obok
    0: ("Svargrond", "tawerna Dankwarta, na południe od świątyni", (32263, 31140)),
    1: ("Liberty Bay", "tawerna Lyonela, na zachód od depo", (32327, 32835)),
    2: ("Port Hope", "tawerna Clyde'a, na zachód od depo", (32623, 32746)),
    3: ("Ankrahmun", "tawerna Arito, nad pocztą", (33127, 32843)),
    4: ("Darashia", "tawerna Miraii, na południe od guildhalli", (33214, 32455)),
    5: ("Edron", "tawerna Mirabell, nad depo", (33169, 31812)),
    6: ("Carlin", "depo, piętro wyżej", (32336, 31784)),
}
DAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def game_day(now: datetime) -> datetime:
    """Dzień w grze – zmienia się o server save, nie o północy."""
    return (now - timedelta(hours=SERVER_SAVE_HOUR)).replace(hour=0, minute=0, second=0, microsecond=0)


def rashid(now: datetime) -> tuple[str, str, str]:
    wd = game_day(now).weekday()
    city, place, _ = RASHID[wd]
    return city, place, DAYS_PL[wd]


def rashid_xy(now: datetime) -> tuple[int, int]:
    return RASHID[game_day(now).weekday()][2]


# ---------------------------------------------------------------- dane z TibiaData
def _find_boosted(obj):
    """Szuka {"boosted": {...}} niezależnie od dokładnego kształtu odpowiedzi."""
    if isinstance(obj, dict):
        b = obj.get("boosted")
        if isinstance(b, dict) and b.get("name"):
            return {"name": b["name"], "image": b.get("image_url") or b.get("image") or ""}
        for v in obj.values():
            r = _find_boosted(v)
            if r:
                return r
    return None


def fetch_boosted(timeout=15) -> dict:
    out = {}
    for key, path in (("creature", "creatures"), ("boss", "boostablebosses")):
        r = requests.get(f"{API}/{path}", timeout=timeout, headers={"User-Agent": "TibiaAppsCompanion"})
        r.raise_for_status()
        b = _find_boosted(r.json())
        if not b:
            raise ValueError(f"brak „boosted” w odpowiedzi /{path}")
        out[key] = b
    return out


def fetch_image(url: str, cache: Path, timeout=15) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / (hashlib.sha1(url.encode()).hexdigest()[:16] + Path(url).suffix[:5])
    if f.exists() and f.stat().st_size:
        return f.read_bytes()
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "TibiaAppsCompanion"})
    r.raise_for_status()
    f.write_bytes(r.content)
    return r.content


# ---------------------------------------------------------------- schematyczna mapa
# Przybliżone współrzędne świata Tibii (x, y). Mapa jest poglądowa – rysowana od zera.
CITIES = {
    "Svargrond": (32270, 31150), "Carlin": (32360, 31780), "Ab'Dendriel": (32730, 31630),
    "Kazordoon": (32650, 31920), "Thais": (32370, 32220), "Venore": (32950, 32080),
    "Edron": (33220, 31810), "Darashia": (33215, 32450), "Ankrahmun": (33190, 32850),
    "Port Hope": (32595, 32750), "Liberty Bay": (32315, 32830), "Yalahar": (32790, 31250),
    "Farmine": (33020, 31520), "Rathleton": (33600, 31900),
}
LANDS = [   # wielokąty lądu (schemat)
    [(32080, 31700), (32330, 31560), (32640, 31480), (32880, 31560), (33060, 31800), (33070, 32150),
     (32900, 32330), (32560, 32380), (32250, 32350), (32100, 32150), (32040, 31900)],      # kontynent główny
    [(33080, 31700), (33330, 31690), (33360, 31930), (33150, 31980)],                         # Edron
    [(33020, 32330), (33380, 32300), (33450, 32650), (33330, 32980), (33060, 32980), (32990, 32640)],  # Darama
    [(32470, 32560), (32760, 32540), (32820, 32800), (32650, 32960), (32470, 32880)],         # Tiquanda
    [(32230, 32740), (32400, 32760), (32380, 32900), (32240, 32900)],                         # Liberty Bay
    [(32150, 31050), (32400, 31060), (32410, 31260), (32160, 31260)],                         # Hrodmir
    [(32700, 31170), (32890, 31170), (32880, 31330), (32700, 31330)],                         # Yalahar
    [(32950, 31430), (33100, 31440), (33090, 31600), (32950, 31590)],                         # Zao
    [(33480, 31780), (33720, 31790), (33700, 32010), (33480, 32000)],                         # Oramond
]
X0, X1, Y0, Y1 = 31980, 33780, 31000, 33060


def draw_map(city: str, w=300, h=300) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor(T.MAP_SEA))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    def pt(x, y):
        return QPointF((x - X0) / (X1 - X0) * w, (y - Y0) / (Y1 - Y0) * h)

    pen = QPen(QColor(T.MAP_LAND), 14)          # gruba obwódka w kolorze lądu = zaokrąglone brzegi
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QColor(T.MAP_LAND))
    for poly in LANDS:
        path = QPainterPath(pt(*poly[0]))
        for q in poly[1:]:
            path.lineTo(pt(*q))
        path.closeSubpath()
        p.drawPath(path)

    f = QFont()
    f.setPointSizeF(7.5)
    p.setFont(f)
    for name, (x, y) in CITIES.items():
        c = pt(x, y)
        on = name == city
        is_rashid = name in {v[0] for v in RASHID.values()}   # noqa
        if on:
            for r, a in ((16, 50), (11, 90)):
                p.setPen(Qt.NoPen)
                c_ = QColor(T.ACCENT)
                c_.setAlpha(a)
                p.setBrush(c_)
                p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(T.MAP_SEA), 1))
        p.setBrush(QColor(T.ACCENT) if on else QColor(T.MAP_TOWN_RASHID if is_rashid else T.MAP_TOWN))
        rad = 5.5 if on else (3.2 if is_rashid else 2.4)
        p.drawEllipse(c, rad, rad)
        if on or is_rashid:
            p.setPen(QColor(T.TEXT if on else T.MAP_LABEL))
            fb = QFont(f)
            fb.setBold(on)
            fb.setPointSizeF(9 if on else 7.5)
            p.setFont(fb)
            left = c.x() > w * 0.72
            rect = QRectF(c.x() - 110 if left else c.x() + 8, c.y() - 9, 102, 18)
            p.drawText(rect, (Qt.AlignRight if left else Qt.AlignLeft) | Qt.AlignVCenter, name)
            p.setFont(f)
    p.setPen(QColor(T.MAP_NOTE))
    p.drawText(QRectF(0, h - 16, w - 6, 14), Qt.AlignRight, "mapa poglądowa")
    p.end()
    return pm


# ---------------------------------------------------------------- prawdziwa mapa (tibiamaps.io)
# Wycinki mapy wokół miejsc Rashida są dołączone do aplikacji (assets/maps) – działają offline
# i od pierwszej sekundy. Źródło: tibia-map-data (tibiamaps.io), które zachęca do użycia w innych projektach.
# Odtworzenie wycinków: tools/make_rashid_maps.py.
MAPS_DIR = Path(__file__).with_name("assets") / "maps"


def map_file(city: str) -> Path:
    return MAPS_DIR / (city.lower().replace(" ", "_").replace("'", "") + ".png")


def real_map(src: QPixmap) -> QPixmap:
    """Wycinek mapy okolicy, w której stoi Rashid, powiększony ×2 bez wygładzania (bez znacznika –
    dokładne pole w tawernie nie jest znane, więc lepiej nie wskazywać złego miejsca)."""
    pm = src.scaled(src.width() * 2, src.height() * 2, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    size = pm.width()
    p = QPainter(pm)
    p.setPen(QColor(255, 255, 255, 170))
    f = QFont()
    f.setPointSizeF(7)
    p.setFont(f)
    p.drawText(QRectF(0, size - 15, size - 6, 13), Qt.AlignRight, "mapa: tibiamaps.io")
    p.end()
    return pm


# ---------------------------------------------------------------- ikona kupca (własny piktogram)
def merchant_icon(size=44) -> QPixmap:
    """Sakiewka z monetą – prosty, własny znak dla „kupca podróżnika”."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size
    bag = QPainterPath()
    bag.moveTo(s * .36, s * .30)
    bag.cubicTo(s * .10, s * .45, s * .12, s * .92, s * .50, s * .92)
    bag.cubicTo(s * .88, s * .92, s * .90, s * .45, s * .64, s * .30)
    bag.closeSubpath()
    p.setPen(QPen(QColor(T.BAG["edge"]), 2))
    p.setBrush(QColor(T.BAG["body"]))
    p.drawPath(bag)
    p.setBrush(QColor(T.BAG["rim"]))
    p.drawEllipse(QPointF(s * .50, s * .24), s * .17, s * .09)          # wywinięty brzeg
    p.setPen(QPen(QColor(T.BAG["cord"]), 2.5))
    p.drawLine(QPointF(s * .36, s * .33), QPointF(s * .64, s * .33))     # sznurek
    p.setPen(QPen(QColor(T.BAG["coin_edge"]), 1.5))
    p.setBrush(QColor(T.BAG["coin"]))
    p.drawEllipse(QPointF(s * .62, s * .68), s * .15, s * .15)          # moneta
    p.setPen(QPen(QColor(T.BAG["coin_ring"]), 1.5))
    p.drawEllipse(QPointF(s * .62, s * .68), s * .09, s * .09)
    p.end()
    return pm


# ---------------------------------------------------------------- karta po najechaniu
class HoverCard(QFrame):
    """Podpowiedź rysowana WEWNĄTRZ okna aplikacji (nakładka), a nie jako osobne okno systemowe –
    dzięki temu pojawia się i znika natychmiast, bez opóźnień menedżera okien."""

    def __init__(self):
        super().__init__(None)
        self.setObjectName("hoverCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet(f"QFrame#hoverCard {{ background:{T.SURFACE_2}; border:1px solid {T.LINE_2}; "
                           f"border-radius:{T.RADIUS_L}px; }}")
        v = T.vbox(self, (T.S3, T.S3, T.S3, T.S3), T.S1)
        self.title = T.label("", "title")
        v.addWidget(self.title)
        self.sub = T.label("", "muted")
        v.addWidget(self.sub)
        v.addSpacing(T.S1)
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        v.addWidget(self.img)
        self.hide()

    def show_at(self, anchor: QWidget, title: str, sub: str, pix: QPixmap | None):
        host = anchor.window()
        if self.parent() is not host:
            self.setParent(host)
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        if pix is not None and not pix.isNull():
            self.img.setPixmap(pix)
            self.img.show()
        else:
            self.img.clear()
            self.img.hide()
        self.adjustSize()
        pos = anchor.mapTo(host, QPoint(anchor.width() + T.S3, 0))
        pos.setY(max(T.S2, min(pos.y(), host.height() - self.height() - T.S2)))
        self.move(pos)
        self.raise_()
        self.show()

    def schedule_hide(self):
        self.hide()


# ---------------------------------------------------------------- widżet w pasku bocznym
class _Bridge(QObject):
    data = Signal(object)
    image = Signal(str, bytes)
    error = Signal(str)


class Tile(QLabel):
    def __init__(self, on_enter, on_leave):
        super().__init__()
        self.setFixedSize(TILE, TILE)
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName("todayTile")
        self.setAttribute(Qt.WA_Hover, True)
        self._enter, self._leave = on_enter, on_leave

    def enterEvent(self, e):
        self._enter(self)

    def leaveEvent(self, e):
        self._leave()


class TodayWidget(QWidget):
    def __init__(self, cache_dir: Path, now_fn, log=lambda m: None):
        super().__init__()
        self.cache = cache_dir
        self.now_fn = now_fn
        self.log = log
        self.data: dict = {}
        self.pix: dict[str, QPixmap] = {}
        self._map_cache: dict[str, QPixmap] = {}
        self.day_loaded = None
        self._loading = False
        self._retry_at = datetime.min
        self.bridge = _Bridge()
        self.bridge.data.connect(self._got)
        self.bridge.image.connect(self._got_image)
        self.bridge.error.connect(self._err)
        self.card = HoverCard()

        h = QBoxLayout(QBoxLayout.LeftToRight, self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(T.S2)
        self._box = h
        self.t_creature = Tile(lambda t: self._hover("creature", t), self.card.schedule_hide)
        self.t_boss = Tile(lambda t: self._hover("boss", t), self.card.schedule_hide)
        self.t_rashid = Tile(lambda t: self._hover("rashid", t), self.card.schedule_hide)
        if RASHID_ICON.exists():
            self.pix["rashid_tile"] = fit_sprite(QPixmap(str(RASHID_ICON)), TILE - 4)
            self.t_rashid.setPixmap(self.pix["rashid_tile"])
        else:
            self.t_rashid.setPixmap(merchant_icon())
        for t in (self.t_creature, self.t_boss, self.t_rashid):
            h.addWidget(t)
        h.addStretch(1)
        for t in (self.t_creature, self.t_boss):
            t.setPixmap(T.pixmap("clock", T.TEXT_3, 20))
            t.setToolTip("Pobieram…")

        self._load_cache()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(60 * 1000)
        QTimer.singleShot(1500, self.tick)
        QTimer.singleShot(0, self._rashid_map)      # przygotuj mapę od razu

    # --- mapa
    def _rashid_map(self) -> QPixmap:
        city = rashid(self.now_fn())[0]
        if city not in self._map_cache:
            f = map_file(city)
            src = QPixmap(str(f)) if f.exists() else QPixmap()
            self._map_cache[city] = real_map(src) if not src.isNull() else draw_map(city)
        return self._map_cache[city]

    # --- boosted
    def _cache_file(self) -> Path:
        return self.cache / "boosted.json"

    def _img_path(self, url: str) -> Path:
        return self.cache / "img" / (hashlib.sha1(url.encode()).hexdigest()[:16] + Path(url).suffix[:5])

    def _load_cache(self):
        try:
            d = json.loads(self._cache_file().read_text(encoding="utf-8"))
        except Exception:
            return
        self.data = d
        self.day_loaded = d.get("day")
        for key in ("creature", "boss"):
            url = (d.get(key) or {}).get("image")
            if url and self._img_path(url).exists():
                self._got_image(key, self._img_path(url).read_bytes())

    def tick(self):
        today = game_day(self.now_fn()).strftime("%Y-%m-%d")
        if self.day_loaded == today or self._loading or datetime.now() < self._retry_at:
            return
        self._loading = True
        day = today

        def work():
            try:
                d = fetch_boosted()
                d["day"] = day
                self.bridge.data.emit(d)
                for k in ("creature", "boss"):
                    if d[k].get("image"):
                        self.bridge.image.emit(k, fetch_image(d[k]["image"], self.cache / "img"))
            except Exception as e:
                self.bridge.error.emit(f"{e.__class__.__name__}: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _got(self, d):
        self._loading = False
        old = self.data
        self.data = d
        self.day_loaded = d["day"]
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            self._cache_file().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        if (old.get("creature") or {}).get("name") != d["creature"]["name"]:
            self.log(f"Boosted dziś: {d['creature']['name']} · boss: {d['boss']['name']}")

    def _got_image(self, key, data):
        tile = self.t_creature if key == "creature" else self.t_boss
        pm = QPixmap()
        if pm.loadFromData(data):
            self.pix[key] = fit_sprite(pm, 128, CARD_SCALE)
            tile.setPixmap(fit_sprite(pm, TILE - 4))
            tile.setToolTip("")

    def _err(self, msg):
        self._loading = False
        self._retry_at = datetime.now() + timedelta(minutes=10)
        if not self.data:
            for t in (self.t_creature, self.t_boss):
                t.setPixmap(T.pixmap("help", T.TEXT_3, 20))
                t.setToolTip("Nie udało się pobrać – spróbuję za 10 min")
        self.log(f"⚠ Nie udało się pobrać boosted ({msg}) – spróbuję za 10 min.")

    # --- najechanie
    def _hover(self, key, tile):
        if key == "rashid":
            city, place, day = rashid(self.now_fn())
            self.card.show_at(tile, f"Rashid: {city}", f"{day.capitalize()} · {place}", self._rashid_map())
            return
        b = self.data.get(key) or {}
        label = "Boosted creature" if key == "creature" else "Boosted boss"
        self.card.show_at(tile, b.get("name") or "jeszcze nie wiem", f"{label} · dziś", self.pix.get(key))

    def set_vertical(self, vertical: bool):
        """Zwinięty pasek boczny – ikonki jedna pod drugą."""
        self._box.setDirection(QBoxLayout.TopToBottom if vertical else QBoxLayout.LeftToRight)

    def hide_card(self):
        self.card.hide()

    def rashid_city(self) -> str:
        return rashid(self.now_fn())[0]
