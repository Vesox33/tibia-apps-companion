"""Ścieżki, ustawienia i historia wysłanych huntów (w %APPDATA%)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "TibiaHuntUploader"


def app_dir() -> Path:
    override = os.environ.get("TIBIA_HUNT_UPLOADER_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


@dataclass
class Settings:
    base_url: str = "https://hunt-analyser.com"
    setup_done: bool = False            # kreator pierwszego uruchomienia został ukończony
    multi_char: bool = False            # False = jedna postać – program nie zmienia wyboru na stronie
    characters: list[str] = field(default_factory=list)  # nazwy jak na liście "Your character"
    last_character: str = ""
    make_public: bool = False
    auto_save: bool = False             # False = tylko wypełnij, SAVE klika użytkownik
    use_templates: bool = True
    browser_channel: str = "auto"       # auto = Edge, potem Chrome, potem Chromium
    clipboard_listen: bool = True
    sound: bool = True                  # dźwięk po przechwyceniu tekstu ze schowka
    spawns_url: str = "https://raw.githubusercontent.com/Vesox33/Tibia-Hunt-Tracker/main/spawns.json"
    templates: list[str] = field(default_factory=list)   # ostatnio odczytane nazwy templatów
    templates_updated: float = 0.0
    window_geometry: str = ""
    spot_floors: dict = field(default_factory=dict)       # ostatnio wybrane piętro dla lokacji

    @classmethod
    def load(cls) -> "Settings":
        data = _read_json(app_dir() / "settings.json", {})
        s = cls()
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, v)
        # starsza wersja: jedna "inna postać"
        if data.get("character_enabled") and data.get("character") and not s.characters:
            s.characters, s.multi_char, s.last_character = [data["character"]], True, data["character"]
        return s

    def save(self) -> None:
        _atomic_write_json(app_dir() / "settings.json", asdict(self))

    @property
    def profile_dir(self) -> Path:
        return app_dir() / "browser-profile"  # osobny podfolder dla każdej przeglądarki


class History:
    """Odciski wysłanych huntów: {fingerprint: {...}}."""

    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "history.json"
        self._lock = threading.Lock()
        self.data: dict = _read_json(self.path, {})

    def find(self, fingerprints) -> dict | None:
        for fp in fingerprints:
            if fp and fp in self.data:
                return self.data[fp]
        return None

    def add(self, fingerprints, hunt_name: str, url: str = "") -> None:
        entry = {"hunt_name": hunt_name, "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"), "url": url}
        with self._lock:
            for fp in fingerprints:
                if fp:
                    self.data[fp] = entry
            _atomic_write_json(self.path, self.data)
