"""Gdzie aplikacja trzyma dane: jeden folder %APPDATA%\\TibiaAppsCompanion.

    TibiaAppsCompanion\\
        config.json, web\\, sounds\\, error.log ...   – respy, snajper, logowanie, ustawienia
        hunty\\                                        – hunty: ustawienia, historia, spawny, profil przeglądarki

Przy starcie dane ze starych folderów (CelestaRezerwacje, TibiaHuntUploader) są tu przenoszone.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ID = "TibiaAppsCompanion"


def _roaming() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


DATA = _roaming() / APP_ID
HUNTS = DATA / "hunty"


def setup():
    """Wywołać przed importem modułów huntów – wskazuje im podfolder „hunty”."""
    DATA.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TIBIA_HUNT_UPLOADER_HOME", str(HUNTS))


def _old_locations() -> list[tuple[Path, Path]]:
    r = _roaming()
    if sys.platform == "win32":
        old_hunts = r / "TibiaHuntUploader"
    else:
        old_hunts = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "TibiaHuntUploader"
    return [
        (r / "CelestaRezerwacje" / "Celesta Rezerwacje", DATA),   # tak zapisywało Qt (firma\aplikacja)
        (r / "CelestaRezerwacje", DATA),
        (old_hunts, Path(os.environ.get("TIBIA_HUNT_UPLOADER_HOME") or HUNTS)),
    ]


def migrate() -> list[str]:
    """Przenosi dane ze starych folderów. Zwraca opis tego, co zrobiono (do logu)."""
    msgs: list[str] = []
    for old, new in _old_locations():
        if not old.is_dir() or old.resolve() == new.resolve():
            continue
        items = [p for p in old.iterdir() if not (old.name == "CelestaRezerwacje" and p.name == "Celesta Rezerwacje")]
        if not items:
            _try_rmdir(old)
            continue
        new.mkdir(parents=True, exist_ok=True)
        moved = kept = failed = 0
        for p in items:
            target = new / p.name
            if target.exists():
                kept += 1          # w nowym folderze jest już nowsza wersja – stara zostaje
                continue
            try:
                shutil.move(str(p), str(target))
                moved += 1
            except Exception:
                try:               # plik zablokowany (np. otwarty stary program) – kopiujemy
                    if p.is_dir():
                        shutil.copytree(p, target)
                    else:
                        shutil.copy2(p, target)
                    moved += 1
                except Exception:
                    failed += 1
        _try_rmdir(old)
        _try_rmdir(old.parent if old.parent.name == "CelestaRezerwacje" else None)
        msg = f"Przeniesiono dane z {old} do {new} ({moved} elem.)"
        if kept:
            msg += f", pominięto {kept} (już były w nowym folderze)"
        if failed:
            msg = "⚠ " + msg + f", nie udało się przenieść {failed} – zamknij stare programy i uruchom ponownie"
        msgs.append(msg)
    return msgs


def _try_rmdir(p: Path | None):
    if p is None or not p.is_dir():
        return
    try:
        leftovers = list(p.iterdir())
        if all(x.is_dir() and not any(x.iterdir()) for x in leftovers):
            for x in leftovers:
                x.rmdir()
            p.rmdir()
    except Exception:
        pass
