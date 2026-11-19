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
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from version import ASSET_NAME, VERSION

API = os.environ.get("TAC_UPDATE_API", "https://api.github.com")   # zmienna tylko do testów
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))


FAIL_MARKER = Path(tempfile.gettempdir()) / "tac-update-failed.txt"

try:
    from paths import DATA as _DATA
except Exception:  # noqa: BLE001
    _DATA = Path(tempfile.gettempdir())
LOG = _DATA / "update.log"          # szczegółowy log aktualizacji (do diagnozy problemów)


def ulog(msg: str):
    """Dopisuje linijkę do update.log. Nigdy nie rzuca wyjątkiem."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 512 * 1024:      # nie rośnie w nieskończoność
            LOG.replace(LOG.with_suffix(".old.log"))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{VERSION}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


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
    ulog(f"check: {API}/repos/{repo}/releases/latest (frozen={FROZEN})")
    r = requests.get(f"{API}/repos/{repo}/releases/latest", timeout=timeout,
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "TibiaAppsCompanion"})
    ulog(f"check: HTTP {r.status_code}")
    if r.status_code == 404:
        raise UpdateError(f"Brak wydań w repozytorium {repo} (albo jest prywatne).")
    r.raise_for_status()
    data = r.json()
    tag = data.get("tag_name", "")
    if parse_version(tag) <= parse_version(VERSION):
        ulog(f"check: najnowsze wydanie {tag} – brak nowszej wersji")
        return None
    if FROZEN:
        asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
        if not asset:
            raise UpdateError(f"Wydanie {tag} nie ma paczki {ASSET_NAME}.")
        download, size = asset["browser_download_url"], int(asset.get("size") or 0)
    else:   # uruchamiane ze źródeł (python main.py) – bierzemy kod źródłowy wydania
        download, size = data.get("zipball_url"), 0
    ulog(f"check: dostępna {tag}, paczka {download} ({size} B)")
    return Release(version=tag.lstrip("vV"), notes=(data.get("body") or "").strip(),
                   url=data.get("html_url", ""), download=download, size=size)


def prepare(rel: Release, progress=lambda done, total: None) -> Path:
    """Pobiera i rozpakowuje. Zwraca folder z nowymi plikami aplikacji."""
    try:
        return _prepare(rel, progress)
    except BaseException as e:
        ulog(f"prepare: BŁĄD {e.__class__.__name__}: {e}")
        raise


def _prepare(rel: Release, progress) -> Path:
    work = Path(tempfile.mkdtemp(prefix="tac-update-"))
    zpath = work / "update.zip"
    ulog(f"prepare: pobieram {rel.download} → {zpath}")
    t0 = time.monotonic()
    with requests.get(rel.download, stream=True, timeout=30,
                      headers={"User-Agent": "TibiaAppsCompanion"}) as r:
        ulog(f"prepare: HTTP {r.status_code}, adres końcowy {r.url[:120]}, "
             f"Content-Length {r.headers.get('Content-Length')}, po {time.monotonic() - t0:.1f} s")
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        done = 0
        next_log = 0
        with open(zpath, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                progress(done, total)
                if done >= next_log:
                    dt = max(time.monotonic() - t0, 0.001)
                    ulog(f"prepare: {done / 1048576:.0f}/{total / 1048576:.0f} MB, "
                         f"{done / 1048576 / dt:.1f} MB/s, {dt:.0f} s")
                    next_log += 20 * 1048576
    dt = time.monotonic() - t0
    ulog(f"prepare: pobrano {done} B w {dt:.0f} s" + ("" if not total or done == total else f" (OCZEKIWANO {total} B)"))
    if total and done != total:
        raise UpdateError(f"Pobieranie przerwane: {done} z {total} bajtów.")
    out = work / "new"
    t1 = time.monotonic()
    with zipfile.ZipFile(zpath) as z:
        for m in z.namelist():   # nie pozwalamy wypakować poza folder
            if m.startswith("/") or ".." in Path(m).parts:
                raise UpdateError("Podejrzana ścieżka w paczce aktualizacji.")
        z.extractall(out)
    ulog(f"prepare: rozpakowano do {out} w {time.monotonic() - t1:.0f} s")
    root = _find_root(out)
    if root is None:
        raise UpdateError("Paczka nie wygląda na Tibia Apps Companion.")
    ulog(f"prepare: folder nowej wersji {root}")
    try:
        zpath.unlink()        # zip nie jest już potrzebny – nie kopiujemy go dwa razy na dysk
    except OSError:
        pass
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
    log = str(LOG)
    pid = os.getpid()
    L = lambda text: f'>> "{log}" echo %date% %time:~0,8% [bat] {text}\r\n'
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        + L(f"start, czekam na zamkniecie aplikacji (PID {pid})")
        + "set n=0\r\n"
        ":wait\r\n"
        # czekamy, aż stara wersja się zamknie; po 30 s zamykamy ją na siłę
        f'tasklist /NH /FI "PID eq {pid}" 2>nul | find "{pid}" >nul || goto copy\r\n'
        "set /a n+=1\r\n"
        f'if %n% GEQ 30 (taskkill /F /PID {pid} >nul 2>&1 & timeout /t 2 /nobreak >nul & goto killed)\r\n'
        "timeout /t 1 /nobreak >nul\r\n"
        "goto wait\r\n"
        ":killed\r\n"
        + L("aplikacja nie zamknela sie przez 30 s - zamknieta na sile")
        + ":copy\r\n"
        + L(f"kopiuje pliki do {APP_DIR}")
        # /E – kopiuj podfoldery; bez /MIR, żeby nie skasować np. .venv ani plików użytkownika
        + f'robocopy "{new_root}" "{APP_DIR}" /E /R:10 /W:1 /NFL /NDL /NJH /NP /UNILOG+:"{log}.robocopy.txt" >nul\r\n'
        "set rc=%errorlevel%\r\n"
        + L("robocopy zakonczone, kod %rc% (0-7 = OK, 8+ = blad)")
        + f'if %rc% GEQ 8 (> "{FAIL_MARKER}" echo blad)\r\n'
        + L("uruchamiam aplikacje ponownie")
        + f"{relaunch}\r\n"
        f'rd /s /q "{work}" >nul 2>&1\r\n'
        + L("koniec")
        + '(goto) 2>nul & del "%~f0"\r\n',
        encoding="utf-8")
    # tylko CREATE_NO_WINDOW: cmd dostaje niewidoczną konsolę, którą dziedziczą tasklist/find/robocopy.
    # (DETACHED_PROCESS powodował, że każde z tych poleceń otwierało własne okno.)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    ulog(f"launch: skrypt {bat}, folder aplikacji {APP_DIR}, nowe pliki {new_root}, exe {sys.executable}, PID {pid}")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True, cwd=tempfile.gettempdir())
    ulog("launch: skrypt uruchomiony")
    return bat


def cleanup(folder: Path):
    shutil.rmtree(folder, ignore_errors=True)
