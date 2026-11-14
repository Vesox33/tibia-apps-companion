"""Ikonki imbuementów i materiałów.

Kolejność szukania:
1. assets/imbu/   – dołączone do aplikacji (tools/fetch_imbu_icons.py),
2. %APPDATA%\\TibiaAppsCompanion\\cache\\imbu\\  – pobrane wcześniej,
3. internet (w tle): TibiaWiki (fandom), a gdy tam brak – tibiamonk.com.
Do czasu pobrania pokazywany jest zastępczy kwadrat z pierwszą literą.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from urllib.parse import quote

import requests
from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

import theme as T
from paths import DATA

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BUNDLED = APP_DIR / "assets" / "imbu"
CACHE = DATA / "cache" / "imbu"
SIZE = 32
UA = {"User-Agent": "TibiaAppsCompanion (+https://github.com/Vesox33/tibia-apps-companion)"}


def file_name(name: str) -> str:
    return name.replace(" ", "_") + ".gif"


def urls(kind: str, name: str) -> list[str]:
    f = quote(file_name(name), safe="_-'.")
    if kind == "imb":
        return [f"https://www.tibiamonk.com/imbuiments/{f}",
                f"https://tibia.fandom.com/wiki/Special:FilePath/Powerful_{f}"]
    return [f"https://tibia.fandom.com/wiki/Special:FilePath/{f}",
            f"https://www.tibiamonk.com/imbuiments/items/{f}"]


def local_path(kind: str, name: str) -> Path | None:
    sub = "imbuements" if kind == "imb" else "items"
    for base in (BUNDLED / sub, CACHE / sub):
        p = base / file_name(name)
        if p.exists() and p.stat().st_size:
            return p
    return None


def download(kind: str, name: str, dest_root: Path = CACHE, timeout=15) -> Path:
    sub = "imbuements" if kind == "imb" else "items"
    dest = dest_root / sub / file_name(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for u in urls(kind, name):
        try:
            r = requests.get(u, timeout=timeout, headers=UA)
            if r.ok and r.content[:3] in (b"GIF", b"\x89PN") and len(r.content) > 40:
                dest.write_bytes(r.content)
                return dest
            last = f"{u}: HTTP {r.status_code}"
        except Exception as e:
            last = f"{u}: {e}"
    raise IOError(last or "brak źródła")


def placeholder(name: str, size: int = SIZE) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T.SURFACE_3))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), 5, 5)
    p.setPen(QColor(T.TEXT_3))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(size // 2)
    p.setFont(f)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, name[:1].upper())
    p.end()
    return pm


def sprite(path: Path, size: int = SIZE) -> QPixmap:
    """Pixel art bez rozmycia: 1:1 (albo całkowita wielokrotność) w kwadracie `size`."""
    img = QImage(str(path))
    if img.isNull():
        return QPixmap()
    w, h = img.width(), img.height()
    if max(w, h) > size:
        img = img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    else:
        f = max(1, size // max(w, h))
        img = img.scaled(w * f, h * f, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawImage((size - img.width()) // 2, (size - img.height()) // 2, img)
    p.end()
    return out


class IconStore(QObject):
    """Daje ikonkę od razu (albo zastępczą) i woła `ready(kind, name)`, gdy dociągnie prawdziwą."""
    ready = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict[tuple[str, str], QPixmap] = {}
        self._pending: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()
        self.ready.connect(lambda k, n: self._cache.pop((k, n), None))

    def get(self, kind: str, name: str) -> QPixmap:
        key = (kind, name)
        if key in self._cache:
            return self._cache[key]
        p = local_path(kind, name)
        if p:
            pm = sprite(p)
            if not pm.isNull():
                self._cache[key] = pm
                return pm
        if key not in self._pending and key not in self._failed:
            self._pending.add(key)
            threading.Thread(target=self._fetch, args=key, daemon=True).start()
        return placeholder(name)

    def _fetch(self, kind, name):
        try:
            download(kind, name)
            self.ready.emit(kind, name)
        except Exception:
            self._failed.add((kind, name))
        finally:
            self._pending.discard((kind, name))
