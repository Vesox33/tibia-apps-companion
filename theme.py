"""Wygląd aplikacji w jednym miejscu: kolory, typografia, odstępy, ikony i wspólne elementy.

Zasady:
- odstępy tylko ze skali SPACE (4 / 8 / 12 / 16 / 24 px),
- jedna wysokość kontrolek (CONTROL_H), te same promienie zaokrągleń,
- pięć rozmiarów tekstu (FS_*) i dwie grubości (400 / 600),
- pomarańczowy akcent tylko dla jednego głównego przycisku na ekranie (objectName "primary"),
- grupowanie odstępem i tłem, nie ramką.

Pozostałe pliki nie wpisują kolorów ani rozmiarów na sztywno – biorą je stąd.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QLibraryInfo, QRectF, QSize, Qt, QTranslator
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer      # import na górze: PyInstaller dołączy QtSvg do wydania
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

# ---------------------------------------------------------------- kolory
BG = "#0f1013"          # tło okna
SIDEBAR = "#131418"     # pasek boczny, stopka
SURFACE = "#17181c"     # panele, wiersze list
SURFACE_2 = "#1e2025"   # pola, przyciski, najechanie
SURFACE_3 = "#272a30"   # wybrane, wciśnięte
LINE = "#23252b"        # cienkie linie
LINE_2 = "#33363d"      # obrys pól wyboru, suwak
TEXT = "#ececef"
TEXT_2 = "#a1a5ae"      # opisy
TEXT_3 = "#6a6e77"      # podpisy, nieaktywne
ACCENT = "#f97316"      # z ikony aplikacji
ACCENT_HOVER = "#fb8636"
ACCENT_PRESS = "#e2640c"
ON_ACCENT = "#1a1009"
OK = "#4cc38a"
ERR = "#ef6461"
WARN = "#e5b350"
INFO = "#5aaef0"        # „Twoje” rezerwacje
GOLD = WARN             # Gold Tokeny

ROLE = {"text": TEXT, "muted": TEXT_2, "faint": TEXT_3, "ok": OK, "err": ERR, "warn": WARN, "info": INFO,
        "accent": ACCENT, "gold": GOLD}

# oś czasu
TL_FREE = "#1d3a2c"
TL_PAST = "#1b1c20"
TL_TAKEN = "#4a4e58"
TL_LIMIT = "#000000"
TL_HATCH = "#3d4048"
TL_NOW = TEXT
TL_SEL = "#ffffff"

# ilustracje (mapa poglądowa, sakiewka, zastępcze ikonki)
MAP_SEA = "#0d1b2a"
MAP_LAND = "#26372a"
MAP_TOWN = "#7b8290"
MAP_TOWN_RASHID = TEXT
MAP_LABEL = "#aab0bb"
MAP_NOTE = "#55606e"
BAG = {"edge": "#5b3a1e", "body": "#a86b32", "rim": "#c88a4a", "cord": "#e0b04a",
       "coin_edge": "#8a6512", "coin": "#f5c542", "coin_ring": "#b8860b"}

# ---------------------------------------------------------------- typografia i miary
FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
FS_CAPTION, FS_BODY, FS_TITLE, FS_PAGE, FS_DISPLAY = 12, 13, 15, 20, 28
W_REGULAR, W_SEMI = 400, 600

S1, S2, S3, S4, S6 = 4, 8, 12, 16, 24          # skala odstępów
CONTROL_H = 32
ROW_H = 36                                     # pozycje nawigacji
RADIUS, RADIUS_L = 8, 10
ICON = 16                                      # ikony w przyciskach i wierszach
ICON_NAV = 18
MONO = '"Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace'


def rgba(hex_color: str, a: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


# ---------------------------------------------------------------- ikony (własne, wektorowe, 24×24)
_SVG = {
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "target": '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v4.5M12 17.5V22M2 12h4.5M17.5 12H22"/>',
    "upload": '<path d="M12 15V4.5M7.5 9 12 4.5 16.5 9"/><path d="M4.5 14.5V18a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3.5"/>',
    "calculator": '<rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M8.5 7.5h7"/>'
                  '<path d="M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 16h.01M12 16h.01M15.5 16h.01" stroke-width="2.2"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "refresh": '<path d="M19.5 11A7.5 7.5 0 0 0 6.2 6.8L4.5 8.5"/><path d="M4.5 4.5v4h4"/>'
               '<path d="M4.5 13a7.5 7.5 0 0 0 13.3 4.2l1.7-1.7"/><path d="M19.5 19.5v-4h-4"/>',
    "external": '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "bell": '<path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "star": '<path d="M12 3.8l2.5 5.1 5.6.8-4.05 3.95.95 5.6L12 16.6l-5 2.65.95-5.6L3.9 9.7l5.6-.8z"/>',
    "x": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h6a1.5 1.5 0 0 0 1.5-1.5l1-12.5"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5v-3a4 4 0 0 1 8 0v3"/>',
    "unlock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5v-3a4 4 0 0 1 7.6-1.7"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "undo": '<path d="M4.5 5v5h5"/><path d="M5 10a7.5 7.5 0 1 1 1.6 6.5"/>',
    "chevron-down": '<path d="M6.5 9.5 12 15l5.5-5.5"/>',
    "chevron-up": '<path d="M6.5 14.5 12 9l5.5 5.5"/>',
    "chevron-right": '<path d="M9.5 6.5 15 12l-5.5 5.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.3l2.8 2.8L16 9.8"/>',
    "alert": '<path d="M12 4.5 3 19.5h18z"/><path d="M12 10v4M12 16.8h.01"/>',
    "alert-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.3h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.7h.01"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.9.8c0 1.7-2.5 2.2-2.5 3.8M12 16.8h.01"/>',
    "user": '<circle cx="12" cy="8.5" r="3.7"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    "volume": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9.2a4 4 0 0 1 0 5.6M18 6.7a7.5 7.5 0 0 1 0 10.6"/>',
    "update": '<circle cx="12" cy="12" r="9"/><path d="M12 16.5v-9M8 11.5l4-4 4 4"/>',
    "folder": '<path d="M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "file": '<path d="M6.5 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5z"/>'
            '<path d="M13.5 3.5V8h4M8.5 12.5h7M8.5 16h7"/>',
    "clipboard": '<rect x="5.5" y="4.5" width="13" height="16" rx="2"/><path d="M9 4.5V3.5h6v1M9 10h6M9 14h6"/>',
    "play": None,        # wypełnione – niżej
    "star-fill": None,
    "dot": None,
}
_FILLED = {
    "play": '<path d="M8.5 5.5v13l10-6.5z"/>',
    "star-fill": '<path d="M12 3.8l2.5 5.1 5.6.8-4.05 3.95.95 5.6L12 16.6l-5 2.65.95-5.6L3.9 9.7l5.6-.8z"/>',
    "dot": '<circle cx="12" cy="12" r="5"/>',
}
_cache: dict[tuple, QPixmap] = {}


def svg(name: str, color: str = TEXT_2, stroke: float = 1.8) -> str:
    if name in _FILLED:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}" stroke="none">'
                f'{_FILLED[name]}</svg>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_SVG[name]}</svg>')


def pixmap(name: str, color: str = TEXT_2, size: int = ICON) -> QPixmap:
    """Ikona jako obraz (rysowana w 2× – ostra także na ekranach o wysokiej gęstości)."""
    key = (name, color, size)
    pm = _cache.get(key)
    if pm is None:
        r = QSvgRenderer(QByteArray(svg(name, color).encode()))
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, size * 2, size * 2))
        p.end()
        pm.setDevicePixelRatio(2)
        _cache[key] = pm
    return pm


def icon(name: str, color: str = TEXT_2, size: int = ICON, active: str | None = None) -> QIcon:
    """QIcon; `active` – kolor dla stanu wybranego/najechanego (np. nawigacja)."""
    ic = QIcon(pixmap(name, color, size))
    if active:
        ic.addPixmap(pixmap(name, active, size), QIcon.Normal, QIcon.On)
        ic.addPixmap(pixmap(name, active, size), QIcon.Active, QIcon.Off)
    return ic


def dot(color: str, size: int = 8) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size * 2, size * 2)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


# pliki dla arkusza stylów (znaczek w polu wyboru, strzałki list)
_ASSET_DIR = Path(tempfile.gettempdir()) / "tac-ui-2"


def _asset(name: str, color: str, stroke: float = 2.0) -> str:
    _ASSET_DIR.mkdir(parents=True, exist_ok=True)
    f = _ASSET_DIR / f"{name}-{color.lstrip('#')}.svg"
    if not f.exists():
        f.write_text(svg(name, color, stroke), encoding="utf-8")
    return f.as_posix()


# ---------------------------------------------------------------- arkusz stylów
def _role_rules() -> str:
    """Kolor wg właściwości „role” – także dla etykiet z nazwą (hint, muted…), które mają własny kolor."""
    kinds = ["", "#hint", "#muted", "#strong", "#title", "#caption", "#display", "#pageTitle"]
    out = []
    for role, color in (("muted", TEXT_2), ("faint", TEXT_3), ("ok", OK), ("err", ERR), ("warn", WARN),
                        ("info", INFO), ("text", TEXT), ("gold", GOLD)):
        sel = ", ".join(f'QLabel{k}[role="{role}"]' for k in kinds)
        out.append(f"{sel} {{ color: {color}; }}")
    return "\n".join(out)


def stylesheet() -> str:
    check = _asset("check", BG, 3.0)
    down = _asset("chevron-down", TEXT_2)
    up = _asset("chevron-up", TEXT_2)
    down_off = _asset("chevron-down", TEXT_3)
    return f"""
* {{ font-family: {FAMILY}; font-size: {FS_BODY}px; color: {TEXT}; }}
QWidget {{ background: transparent; }}
QMainWindow, QDialog, QMessageBox, QProgressDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {LINE_2}; padding: {S1}px {S2}px; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {LINE_2}; padding: {S1}px; }}
QMenu::item {{ padding: {S1 + 2}px {S3}px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: {S1}px {S2}px; }}

/* tekst */
QLabel#pageTitle {{ font-size: {FS_PAGE}px; font-weight: {W_SEMI}; }}
QLabel#title {{ font-size: {FS_TITLE}px; font-weight: {W_SEMI}; }}
QLabel#display {{ font-size: {FS_DISPLAY}px; font-weight: {W_SEMI}; }}
QLabel#strong {{ font-weight: {W_SEMI}; }}
QLabel#caption {{ font-size: {FS_CAPTION}px; font-weight: {W_SEMI}; color: {TEXT_3}; }}
QLabel#muted {{ color: {TEXT_2}; }}
QLabel#hint {{ font-size: {FS_CAPTION}px; color: {TEXT_3}; }}
{_role_rules()}
QLabel#pill {{ font-size: {FS_CAPTION}px; font-weight: {W_SEMI}; border-radius: 9px; padding: 0 {S2}px;
              min-height: 18px; max-height: 18px; color: {TEXT_2}; background: {SURFACE_3}; }}
QLabel#pill[role="ok"] {{ color: {OK}; background: {rgba(OK, 0.12)}; }}
QLabel#pill[role="err"] {{ color: {ERR}; background: {rgba(ERR, 0.12)}; }}
QLabel#pill[role="warn"], QLabel#pill[role="gold"] {{ color: {WARN}; background: {rgba(WARN, 0.12)}; }}
QLabel#pill[role="info"] {{ color: {INFO}; background: {rgba(INFO, 0.12)}; }}

/* przyciski */
QPushButton, QToolButton {{ background: {SURFACE_2}; border: none; border-radius: {RADIUS}px;
    padding: 0 {S3}px; min-height: {CONTROL_H}px; max-height: {CONTROL_H}px; font-weight: {W_SEMI}; color: {TEXT}; }}
QPushButton:hover, QToolButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {LINE_2}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_3}; background: {SURFACE}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ON_ACCENT}; padding: 0 {S4}px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton#primary:disabled {{ background: {SURFACE_2}; color: {TEXT_3}; }}
QPushButton#ghost, QToolButton#ghost {{ background: transparent; color: {TEXT_2}; padding: 0 {S2}px; }}
QPushButton#ghost:hover, QToolButton#ghost:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#ghost:disabled {{ color: {TEXT_3}; background: transparent; }}
QPushButton#iconbtn, QToolButton#iconbtn {{ background: transparent; padding: 0; min-width: {CONTROL_H}px;
    max-width: {CONTROL_H}px; }}
QPushButton#iconbtn:hover, QToolButton#iconbtn:hover {{ background: {SURFACE_2}; }}
QPushButton#iconbtn:checked, QToolButton#iconbtn:checked {{ background: {SURFACE_3}; }}
QPushButton#iconbtn:disabled {{ background: transparent; }}
QPushButton#danger {{ background: transparent; padding: 0; min-width: {CONTROL_H}px; max-width: {CONTROL_H}px; }}
QPushButton#danger:hover {{ background: {rgba(ERR, 0.14)}; }}
QPushButton#seg {{ background: transparent; border: 1px solid {LINE_2}; color: {TEXT_2}; padding: 0 {S3}px;
    min-height: {CONTROL_H - 2}px; max-height: {CONTROL_H - 2}px; }}
QPushButton#seg:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#seg:checked {{ background: {SURFACE_3}; border-color: {TEXT_3}; color: {TEXT}; }}
QPushButton#seg:disabled {{ color: {TEXT_3}; border-color: {LINE}; background: transparent; }}
QPushButton#step {{ padding: 0; }}
QPushButton#link {{ background: transparent; color: {TEXT_2}; padding: 0 {S1}px; font-weight: {W_REGULAR}; }}
QPushButton#link:hover {{ color: {TEXT}; background: transparent; text-decoration: underline; }}
QPushButton#link:disabled {{ color: {TEXT_3}; }}
QPushButton#nav {{ background: transparent; color: {TEXT_2}; text-align: left; padding: 0 {S3}px;
    min-height: {ROW_H}px; max-height: {ROW_H}px; }}
QPushButton#nav:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#account {{ background: transparent; text-align: left; padding: 0;
    min-height: 44px; max-height: 44px; }}
QPushButton#account:hover {{ background: {SURFACE_2}; }}
QPushButton#update {{ background: {rgba(OK, 0.12)}; color: {OK}; text-align: left; padding: 0 {S3}px;
    min-height: {ROW_H}px; max-height: {ROW_H}px; }}
QPushButton#update:hover {{ background: {rgba(OK, 0.2)}; }}

/* pola */
QLineEdit, QSpinBox, QComboBox, QDateEdit, QTimeEdit, QDateTimeEdit {{ background: {SURFACE_2};
    border: 1px solid transparent; border-radius: {RADIUS}px; padding: 0 {S3 - 2}px;
    min-height: {CONTROL_H - 2}px; max-height: {CONTROL_H - 2}px; selection-background-color: {LINE_2}; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {LINE_2}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {TEXT_3}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_3}; background: {SURFACE}; }}
QLineEdit#locked {{ background: transparent; border-color: transparent; color: {TEXT_2}; }}
QLineEdit#time {{ font-size: {FS_TITLE}px; font-weight: {W_SEMI}; padding: 0; }}
QLineEdit#search {{ padding-left: {S3 + ICON + S2 - 2}px; }}
QAbstractSpinBox QLineEdit {{ padding: 0; border: none; background: transparent; min-height: 0; max-height: none; }}
QSpinBox {{ padding-right: {S6}px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: {S6 - 4}px; border: none;
    background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; margin-top: 3px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 3px; }}
QSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QComboBox {{ padding-right: {S6 + S1}px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: {S6}px;
    border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({down_off}); }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE_2}; padding: {S1}px;
    outline: 0; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 {S2}px; border-radius: 6px; }}
QPlainTextEdit, QTextEdit {{ background: {SURFACE}; border: none; border-radius: {RADIUS_L}px;
    padding: {S2}px; selection-background-color: {LINE_2}; }}
QPlainTextEdit#mono {{ font-family: {MONO}; font-size: {FS_CAPTION}px; }}

QCheckBox, QRadioButton {{ spacing: {S2}px; min-height: 24px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator {{ border: 1px solid {LINE_2}; border-radius: 4px; background: {SURFACE_2}; }}
QCheckBox::indicator:hover {{ border-color: {TEXT_3}; }}
QCheckBox::indicator:checked {{ background: {TEXT}; border-color: {TEXT}; image: url({check}); }}
QCheckBox::indicator:disabled {{ background: {SURFACE}; border-color: {LINE}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TEXT_3}; }}
QRadioButton::indicator {{ border: 1px solid {LINE_2}; border-radius: 8px; background: {SURFACE_2}; }}
QRadioButton::indicator:hover {{ border-color: {TEXT_3}; }}
QRadioButton::indicator:checked {{ width: 6px; height: 6px; border: 5px solid {TEXT}; background: {BG}; }}

QSlider {{ min-height: {CONTROL_H}px; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE_2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {TEXT_2}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {TEXT}; }}
QSlider::sub-page:horizontal:disabled {{ background: {LINE_2}; }}
QSlider::handle:horizontal:disabled {{ background: {TEXT_3}; }}

/* zakładki, listy */
QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {TEXT_2}; font-weight: {W_SEMI}; padding: 0 {S3}px;
    min-height: {CONTROL_H}px; border-bottom: 2px solid transparent; margin-right: {S1}px; }}
QTabBar::tab:first {{ padding-left: 0; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {TEXT}; }}
QListWidget, QTreeWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ border: none; padding: 0; }}
QListWidget::item:selected, QListWidget::item:hover {{ background: transparent; }}
QListWidget#nav::item {{ min-height: {ROW_H}px; padding: 0 {S3}px; border-radius: {RADIUS}px; color: {TEXT_2};
    font-weight: {W_SEMI}; }}
QListWidget#nav::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QTreeWidget::item {{ min-height: {CONTROL_H}px; border-radius: 6px; }}
QTreeWidget::item:hover {{ background: {SURFACE_2}; }}
QTreeWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QTreeWidget::branch {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {TEXT_3}; border: none; padding: 0 {S1}px;
    font-size: {FS_CAPTION}px; font-weight: {W_SEMI}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: {S2}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE_2}; border-radius: {S1}px; min-height: {S6 + S2}px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_3}; }}
QScrollBar:horizontal {{ background: transparent; height: {S2}px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {LINE_2}; border-radius: {S1}px; min-width: {S6 + S2}px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; width: 0; height: 0; }}
QSplitter::handle {{ background: transparent; }}

/* powierzchnie */
QFrame#sidebar {{ background: {SIDEBAR}; }}
QFrame#footer {{ background: {SIDEBAR}; }}
QFrame#panel, QFrame#card {{ background: {SURFACE}; border: none; border-radius: {RADIUS_L}px; }}
QFrame#row {{ background: {SURFACE}; border: none; border-radius: {RADIUS}px; }}
QFrame#row:hover {{ background: {SURFACE_2}; }}
QFrame#rowActive {{ background: {SURFACE_3}; border: none; border-radius: {RADIUS}px; }}
QLabel#todayTile {{ background: {SURFACE}; border-radius: {RADIUS_L}px; color: {TEXT_3}; }}
QLabel#todayTile:hover {{ background: {SURFACE_2}; }}
QFrame#hairline {{ background: {LINE}; border: none; min-height: 1px; max-height: 1px; }}
QStatusBar {{ background: {SIDEBAR}; color: {TEXT_2}; min-height: 28px; }}
QStatusBar::item {{ border: none; }}
QMessageBox QPushButton {{ min-width: 88px; }}
QProgressBar {{ background: {SURFACE_2}; border: none; border-radius: 3px; max-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {TEXT_2}; border-radius: 3px; }}
"""


def install(app: QApplication):
    """Czcionka, arkusz stylów i polskie napisy w standardowych oknach Qt („Tak/Nie”, menu kontekstowe)."""
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter"])
    f.setPixelSize(FS_BODY)
    app.setFont(f)
    app.setStyleSheet(stylesheet())
    tr = QTranslator(app)
    if tr.load("qtbase_pl", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
        app._tac_translator = tr   # noqa: SLF001 – trzymamy referencję


# ---------------------------------------------------------------- elementy
def set_role(label: QLabel, role: str | None):
    """Kolor tekstu wg roli (ok, err, warn, info, muted, faint, text) – bez wpisywania barw."""
    if label.property("role") != role:
        label.setProperty("role", role)
        label.style().unpolish(label)
        label.style().polish(label)


def label(text: str = "", kind: str | None = None, role: str | None = None, wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if kind:
        l.setObjectName(kind)
    if role:
        l.setProperty("role", role)
    l.setWordWrap(wrap)
    return l


def caption(text: str) -> QLabel:
    return label(text, "caption")


def hint(text: str) -> QLabel:
    return label(text, "hint", wrap=True)


def pill(text: str, role: str | None = None) -> QLabel:
    l = label(text, "pill", role)
    l.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    l.setAlignment(Qt.AlignCenter)
    return l


def hairline() -> QWidget:
    from PySide6.QtWidgets import QFrame
    f = QFrame()
    f.setObjectName("hairline")
    return f


def icon_label(name: str, color: str = TEXT_2, size: int = ICON) -> QLabel:
    l = QLabel()
    l.setPixmap(pixmap(name, color, size))
    l.setFixedSize(size, size)
    l.setAlignment(Qt.AlignCenter)
    return l


def button(text: str = "", icon_name: str | None = None, kind: str | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        color = ON_ACCENT if kind == "primary" else TEXT_2
        b.setIcon(icon(icon_name, color))
        b.setIconSize(QSize(ICON, ICON))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_button(icon_name: str, tip: str, kind: str = "iconbtn", color: str = TEXT_2) -> QPushButton:
    b = QPushButton()
    b.setObjectName(kind)
    b.setIcon(icon(icon_name, color))
    b.setIconSize(QSize(ICON, ICON))
    b.setFixedSize(CONTROL_H, CONTROL_H)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def vbox(parent=None, margins=(0, 0, 0, 0), spacing: int = 0) -> QVBoxLayout:
    v = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    return v


def hbox(parent=None, margins=(0, 0, 0, 0), spacing: int = 0) -> QHBoxLayout:
    h = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    h.setContentsMargins(*margins)
    h.setSpacing(spacing)
    return h


def page_header(title: str, subtitle: str, actions: list) -> tuple[QWidget, QLabel]:
    """Jednakowy nagłówek każdej strony: tytuł, jedna linijka opisu, akcje po prawej (wyrównane do tytułu)."""
    w = QWidget()
    h = hbox(w, (0, 0, 0, S6), S2)
    col = vbox(spacing=S1)
    col.addWidget(label(title, "pageTitle"))
    sub = label(subtitle, "muted", wrap=True)
    sub.setTextFormat(Qt.RichText)
    col.addWidget(sub)
    h.addLayout(col, 1)
    for b in actions:
        h.addWidget(b, 0, Qt.AlignTop)
    return w, sub


class EmptyState(QWidget):
    """Stan pusty: ikona, tytuł, jedno zdanie i opcjonalny przycisk – wyśrodkowane."""

    def __init__(self, icon_name: str, title: str, text: str = "", action: QPushButton | None = None):
        super().__init__()
        v = vbox(self, (S6, S6, S6, S6), S2)
        v.addStretch(1)
        self.icon = icon_label(icon_name, TEXT_3, 32)
        v.addWidget(self.icon, 0, Qt.AlignHCenter)
        v.addSpacing(S2)
        self.title = label(title, "title")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        v.addWidget(self.title)
        self.text = label(text, "muted", wrap=True)
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setVisible(bool(text))
        v.addWidget(self.text)
        self.action = action
        if action is not None:
            v.addSpacing(S2)
            v.addWidget(action, 0, Qt.AlignHCenter)
        v.addStretch(2)

    def set(self, icon_name: str, title: str, text: str = "", show_action: bool = True):
        self.icon.setPixmap(pixmap(icon_name, TEXT_3, 32))
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        if self.action is not None:
            self.action.setVisible(show_action)


# ---------------------------------------------------------------- okna komunikatów
_MSG_ICON = {"question": ("help", TEXT_2), "warning": ("alert", WARN), "error": ("alert-circle", ERR),
             "info": ("info", INFO), "ok": ("check-circle", OK)}


def message(parent, kind: str, title: str, text: str, buttons=QMessageBox.Ok,
            default=None) -> QMessageBox:
    """QMessageBox w stylu aplikacji: własna ikona, polskie przyciski, główny przycisk wyróżniony."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setTextFormat(Qt.AutoText)
    name, color = _MSG_ICON.get(kind, _MSG_ICON["info"])
    box.setIconPixmap(pixmap(name, color, 32))
    box.setStandardButtons(buttons)
    names = {QMessageBox.Yes: "Tak", QMessageBox.No: "Nie", QMessageBox.Ok: "OK", QMessageBox.Cancel: "Anuluj",
             QMessageBox.Close: "Zamknij"}
    for sb, txt in names.items():
        b = box.button(sb)
        if b is not None:
            b.setText(txt)
            b.setCursor(Qt.PointingHandCursor)
    main = box.button(default) if default is not None else None
    if main is not None:
        box.setDefaultButton(main)
    return box


def ask(parent, title: str, text: str, warn: bool = False) -> int:
    """Pytanie Tak/Nie – zwraca QMessageBox.Yes albo QMessageBox.No (jak QMessageBox.question)."""
    return message(parent, "warning" if warn else "question", title, text,
                   QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes).exec()


def warn(parent, title: str, text: str) -> int:
    return message(parent, "warning", title, text).exec()


def info(parent, title: str, text: str) -> int:
    return message(parent, "info", title, text).exec()
