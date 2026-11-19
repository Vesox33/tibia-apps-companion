"""Wydanie nowej wersji: buduje .exe, pakuje i publikuje na GitHubie.
Użytkownicy dostaną ją automatycznie przy następnym uruchomieniu (albo w ciągu 6 h).

Uruchamiaj przez wydaj_wersje.bat. Wymaga: git, GitHub CLI (gh) i jednorazowego `gh auth login`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.py"
NAME = "TibiaAppsCompanion"
ASSET = f"{NAME}-win64.zip"


def run(cmd, check=True, capture=False):
    print("  >", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True,
                          capture_output=capture)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def read_version_file() -> tuple[str, str]:
    s = VERSION_FILE.read_text(encoding="utf-8")
    ver = re.search(r'^VERSION = "([^"]*)"', s, re.M).group(1)
    repo = re.search(r'^UPDATE_REPO = "([^"]*)"', s, re.M).group(1)
    return ver, repo


def write_version_file(ver: str | None = None, repo: str | None = None):
    s = VERSION_FILE.read_text(encoding="utf-8")
    if ver is not None:
        s = re.sub(r'^VERSION = "[^"]*"', f'VERSION = "{ver}"', s, flags=re.M)
    if repo is not None:
        s = re.sub(r'^UPDATE_REPO = "[^"]*"', f'UPDATE_REPO = "{repo}"', s, flags=re.M)
    VERSION_FILE.write_text(s, encoding="utf-8")


def changelog_notes(ver: str) -> str:
    """Opis wydania z CHANGELOG.md (sekcja „## [wersja]”), jeśli jest."""
    f = ROOT / "CHANGELOG.md"
    if not f.exists():
        return ""
    # spacje na początku linijek (np. po wklejeniu) nie przeszkadzają
    m = re.search(rf"^[ \t]*## \[?{re.escape(ver)}\]?.*?$(.*?)(?=^[ \t]*## |\Z)", f.read_text(encoding="utf-8"), re.M | re.S)
    return "\n".join(l.strip() for l in m.group(1).strip().splitlines()) if m else ""


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def bump(v: str) -> str:
    parts = list(vtuple(v)) + [0, 0, 0]
    return f"{parts[0]}.{parts[1]}.{parts[2] + 1}"


def remote_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def git_out(*args) -> str:
    r = run(["git", *args], check=False, capture=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def ensure_repo() -> str:
    """Repozytorium na GitHubie – przy pierwszym wydaniu zakłada je (publiczne).
    Działa też w świeżo rozpakowanym folderze bez .git: podpina go do istniejącego repozytorium."""
    _, repo = read_version_file()
    if not (ROOT / ".git").exists():
        run(["git", "init", "-b", "main"])
    if not repo:
        r = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], check=False, capture=True)
        repo = r.stdout.strip() if r.returncode == 0 else ""
    if not repo:
        name = input("\nNazwa nowego repozytorium na GitHubie [tibia-apps-companion]: ").strip() or "tibia-apps-companion"
        print("Repozytorium będzie PUBLICZNE (aplikacja pobiera z niego aktualizacje bez logowania).")
        if input("Założyć? [t/N]: ").strip().lower() not in ("t", "tak", "y"):
            sys.exit("Przerwano.")
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", "Pierwsza wersja"], check=False)
        run(["gh", "repo", "create", name, "--public", "--source", ".", "--remote", "origin", "--push"])
        r = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], capture=True)
        repo = r.stdout.strip()
    write_version_file(repo=repo)
    sync_with_remote(repo)
    return repo


def sync_with_remote(repo: str):
    """Ustawia lokalną historię na stan z GitHuba, nie ruszając plików w folderze.
    Dzięki temu wydanie zadziała po wgraniu nowych plików na stary folder albo do nowego,
    a commit wydania zawiera dokładnie różnicę względem GitHuba."""
    if not git_out("remote"):
        run(["git", "remote", "add", "origin", remote_url(repo)])
    run(["git", "fetch", "origin", "--tags", "--force"])
    if not git_out("rev-parse", "--verify", "--quiet", "origin/main"):
        return
    run(["git", "reset", "-q", "origin/main"])          # bez --hard: pliki w folderze zostają
    # lista spawnów jest publikowana osobno (wydaj_spawny) – zawsze bierzemy tę z GitHuba
    if git_out("ls-tree", "--name-only", "origin/main", "huntuploader/data/spawns.json"):
        run(["git", "checkout", "origin/main", "--", "huntuploader/data/spawns.json"])
    # grafiki, których brakuje w folderze (np. pobrane wcześniej ikonki), przywracamy z GitHuba zamiast je usuwać
    missing = [p for p in git_out("ls-files", "--deleted", "--", "assets").splitlines() if p]
    if missing:
        run(["git", "checkout", "origin/main", "--", *missing])


def released_version() -> str:
    """Najwyższa wydana wersja (tagi v* z GitHuba) albo pusty napis."""
    tags = [t.lstrip("v") for t in git_out("tag", "-l", "v*").split()]
    return max(tags, key=vtuple) if tags else ""


def changelog_top() -> str:
    """Najnowsza wersja opisana w CHANGELOG.md."""
    f = ROOT / "CHANGELOG.md"
    if not f.exists():
        return ""
    m = re.search(r"^[ \t]*## \[?(\d+(?:\.\d+)+)\]?", f.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def build():
    py = sys.executable
    sys.path.insert(0, str(ROOT / "tools"))
    from make_version_info import make as make_version_info
    vfile = make_version_info()
    # --noupx: pakowanie UPX to najczęstszy powód fałszywych alarmów antywirusów
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--noupx", "--name", NAME,
         "--version-file", str(vfile),
         "--icon", str(ROOT / "assets" / "icon.ico"),
         "--add-data", f"{ROOT / 'assets'};assets",
         "--add-data", f"{ROOT / 'huntuploader' / 'data'};huntuploader/data",
         str(ROOT / "main.py")])
    dist = ROOT / "dist" / NAME
    if not (dist / f"{NAME}.exe").exists():
        sys.exit("Budowanie nie dało pliku .exe – zobacz komunikaty powyżej.")
    zip_base = ROOT / "dist" / ASSET.removesuffix(".zip")
    archive = shutil.make_archive(str(zip_base), "zip", root_dir=ROOT / "dist", base_dir=NAME)
    return Path(archive)


def main():
    for tool in ("git", "gh"):
        if not have(tool):
            sys.exit(f"Brak programu „{tool}”. Zainstaluj: "
                     + ("https://git-scm.com/download/win" if tool == "git" else "https://cli.github.com/")
                     + " i uruchom ponownie.")
    if run(["gh", "auth", "status"], check=False, capture=True).returncode != 0:
        sys.exit("Najpierw zaloguj GitHub CLI: gh auth login")

    repo = ensure_repo()
    cur, _ = read_version_file()
    last = released_version()
    first = not last
    top = changelog_top()
    if first:
        sug = cur
    elif top and vtuple(top) > vtuple(last):
        sug = top
    else:
        sug = bump(last)
    print(f"\nOstatnie wydanie: {last or 'brak'}")
    ver = input(f"Wersja do wydania [{sug}] (Enter = {sug}): ").strip() or sug
    if not first and vtuple(ver) <= vtuple(last):
        sys.exit(f"Nowa wersja musi być wyższa od ostatnio wydanej ({last}).")
    if changelog_notes(ver):
        print(f"Opis wydania z CHANGELOG.md:\n{changelog_notes(ver)}\n")
    else:
        print(f"(W CHANGELOG.md nie ma sekcji „## [{ver}]” – możesz ją dopisać albo podać opis teraz.)")
    notes = changelog_notes(ver) or input("Co się zmieniło (Enter = opis automatyczny): ").strip()

    write_version_file(ver=ver)
    try:
        archive = build()
    except BaseException:
        write_version_file(ver=cur)
        raise
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", f"Wersja {ver}"], check=False)
    run(["git", "tag", f"v{ver}"])
    run(["git", "push", "origin", "HEAD", "--tags"])
    cmd = ["gh", "release", "create", f"v{ver}", str(archive), "--repo", repo,
           "--title", f"Tibia Apps Companion {ver}"]
    cmd += ["--notes", notes] if notes else ["--generate-notes"]
    run(cmd)
    print(f"\nGotowe! Wersja {ver} opublikowana: https://github.com/{repo}/releases/latest")
    print("Użytkownicy dostaną ją przy następnym uruchomieniu aplikacji.")


if __name__ == "__main__":
    main()
