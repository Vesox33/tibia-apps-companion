"""Aktualizacje z GitHub Releases.

1. check()   – pyta GitHuba o najnowsze wydanie i porównuje wersję.
2. prepare() – pobiera paczkę i rozpakowuje ją do folderu tymczasowego.
3. launch()  – uruchamia mały skrypt, który czeka na zamknięcie aplikacji, podmienia pliki
               i uruchamia ją ponownie. Dane w %APPDATA% nie są ruszane.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from version import ASSET_NAME, VERSION

API = os.environ.get("TAC_UPDATE_API", "https://api.github.com")   # zmienna tylko do testów
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))


FAIL_MARKER = Path(tempfile.gettempdir()) / "tac-update-failed.txt"


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


@dataclass
class Release:
    version: str
    notes: str
    url: str          # strona wydania
    download: str     # adres paczki
    size: int


class UpdateError(Exception):
    pass


def check(repo: str, timeout: int = 15) -> Release | None:
    """Zwraca nowsze wydanie albo None, gdy masz aktualną wersję."""
    if not repo or "/" not in repo:
        raise UpdateError("Nie ustawiono repozytorium aktualizacji.")
    r = requests.get(f"{API}/repos/{repo}/releases/latest", timeout=timeout,
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "TibiaAppsCompanion"})
    if r.status_code == 404:
        raise UpdateError(f"Brak wydań w repozytorium {repo} (albo jest prywatne).")
    r.raise_for_status()
    data = r.json()
    tag = data.get("tag_name", "")
    if parse_version(tag) <= parse_version(VERSION):
        return None
    if FROZEN:
        asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
        if not asset:
            raise UpdateError(f"Wydanie {tag} nie ma paczki {ASSET_NAME}.")
        download, size = asset["browser_download_url"], int(asset.get("size") or 0)
    else:   # uruchamiane ze źródeł (python main.py) – bierzemy kod źródłowy wydania
        download, size = data.get("zipball_url"), 0
    return Release(version=tag.lstrip("vV"), notes=(data.get("body") or "").strip(),
                   url=data.get("html_url", ""), download=download, size=size)


def prepare(rel: Release, progress=lambda done, total: None) -> Path:
    """Pobiera i rozpakowuje. Zwraca folder z nowymi plikami aplikacji."""
    work = Path(tempfile.mkdtemp(prefix="tac-update-"))
    zpath = work / "update.zip"
    with requests.get(rel.download, stream=True, timeout=30,
                      headers={"User-Agent": "TibiaAppsCompanion"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        done = 0
        with open(zpath, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                progress(done, total)
    out = work / "new"
    with zipfile.ZipFile(zpath) as z:
        for m in z.namelist():   # nie pozwalamy wypakować poza folder
            if m.startswith("/") or ".." in Path(m).parts:
                raise UpdateError("Podejrzana ścieżka w paczce aktualizacji.")
        z.extractall(out)
    root = _find_root(out)
    if root is None:
        raise UpdateError("Paczka nie wygląda na Tibia Apps Companion.")
    return root


def _find_root(folder: Path) -> Path | None:
    marker = "TibiaAppsCompanion.exe" if FROZEN else "main.py"
    for p in [folder, *sorted(folder.rglob(marker))]:
        cand = p if p.is_dir() else p.parent
        if (cand / marker).exists():
            return cand
    return None


def can_write_app_dir() -> bool:
    try:
        t = APP_DIR / ".write-test"
        t.write_text("x")
        t.unlink()
        return True
    except Exception:
        return False


def launch(new_root: Path) -> Path:
    """Uruchamia skrypt podmiany plików. Wywołujący musi zaraz potem zamknąć aplikację."""
    if sys.platform != "win32":
        raise UpdateError("Automatyczna podmiana plików działa tylko w Windows.")
    if FROZEN:
        relaunch = f'start "" "{sys.executable}"'
    elif (APP_DIR / "uruchom.bat").exists():
        relaunch = f'start "" /D "{APP_DIR}" "{APP_DIR / "uruchom.bat"}"'   # doinstaluje nowe biblioteki
    else:
        relaunch = f'start "" "{sys.executable}" "{APP_DIR / "main.py"}"'
    work = next((p for p in new_root.parents if p.name.startswith("tac-update-")), new_root.parent)
    bat = Path(tempfile.gettempdir()) / f"tac-apply-update-{os.getpid()}.bat"
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        ":wait\r\n"
        f'tasklist /NH /FI "PID eq {os.getpid()}" 2>nul | find " {os.getpid()} " >nul && '
        "(timeout /t 1 /nobreak >nul & goto wait)\r\n"
        # /E – kopiuj podfoldery; bez /MIR, żeby nie skasować np. .venv ani plików użytkownika
        f'robocopy "{new_root}" "{APP_DIR}" /E /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul\r\n'
        f'if errorlevel 8 (echo {APP_DIR}> "{FAIL_MARKER}")\r\n'
        f"{relaunch}\r\n"
        f'rd /s /q "{work}" >nul 2>&1\r\n'
        '(goto) 2>nul & del "%~f0"\r\n',
        encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True, cwd=tempfile.gettempdir())
    return bat


def cleanup(folder: Path):
    shutil.rmtree(folder, ignore_errors=True)
