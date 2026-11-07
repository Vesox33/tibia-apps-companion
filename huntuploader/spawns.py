"""Baza spawnów (spawns.json): pobieranie raz na dobę, kopia lokalna, wykrywanie spotu."""
from __future__ import annotations

import difflib
import json
import re
import shutil
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .storage import _atomic_write_json, _read_json, app_dir

# Lista spawnów dołączona do aplikacji. Ten sam plik leży w repozytorium i stamtąd aplikacja
# pobiera nowsze wersje (publikuje je tools/publish_spawns.py).
FALLBACK = Path(__file__).with_name("data") / "spawns.json"
REPO_PATH = "huntuploader/data/spawns.json"


def url_for_repo(repo: str, branch: str = "main") -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{REPO_PATH}"
ONE_DAY = 24 * 3600


# --------------------------------------------------------------------------- pobieranie

def cache_path() -> Path:
    return app_dir() / "spawns.json"


def _meta_path() -> Path:
    return app_dir() / "spawns_meta.json"


def _validate(data) -> bool:
    return (
        isinstance(data, dict)
        and data.get("format") == 2
        and isinstance(data.get("monsters"), dict)
        and isinstance(data.get("spawns"), list)
    )


def load_local() -> dict:
    """Lokalna kopia; jeśli jej nie ma albo dołączona do programu jest nowsza – ta dołączona."""
    data = _read_json(cache_path(), None)
    bundled = _read_json(FALLBACK, None) if FALLBACK.exists() else None
    if _validate(data) and not (_validate(bundled) and bundled.get("version", "") > data.get("version", "")):
        return data
    if FALLBACK.exists():
        shutil.copyfile(FALLBACK, cache_path())
        return _read_json(cache_path(), {})
    return {"format": 2, "version": "", "monsters": {}, "spawns": []}


def needs_update() -> bool:
    meta = _read_json(_meta_path(), {})
    return time.time() - float(meta.get("last_check", 0)) > ONE_DAY


def download(url: str, timeout: float = 15.0) -> tuple[dict | None, str]:
    """Pobiera plik. Zwraca (dane albo None, komunikat). Nie rusza kopii przy błędzie."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TibiaHuntUploader"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # brak internetu itp.
        return None, f"Nie udało się pobrać spawnów ({e.__class__.__name__}: {e}). Używam kopii lokalnej."
    if not _validate(data):
        return None, "Pobrany spawns.json ma nieoczekiwany format – zostawiam kopię lokalną."
    local = load_local()
    if data.get("version", "") <= local.get("version", ""):
        _atomic_write_json(_meta_path(), {"last_check": time.time(), "version": local.get("version", "")})
        return None, f"Spawny aktualne (wersja {local.get('version', '?')})."
    _atomic_write_json(cache_path(), data)
    _atomic_write_json(_meta_path(), {"last_check": time.time(), "version": data.get("version", "")})
    return data, f"Spawny zaktualizowane (wersja {data.get('version', '?')})."


# --------------------------------------------------------------------------- nazwy potworów

def _plural_word(w: str) -> set[str]:
    out = set()
    if re.search(r"[^aeiou]y$", w):
        out.add(w[:-1] + "ies")
    if re.search(r"(s|x|z|ch|sh)$", w):
        out.add(w + "es")
    if w.endswith("fe"):
        out.add(w[:-2] + "ves")
    if w.endswith("f"):
        out.add(w[:-1] + "ves")
    if w.endswith("man"):
        out.add(w[:-3] + "men")
    if w.endswith("o"):
        out.add(w + "es")
    if w.endswith("ouse"):
        out.add(w[:-4] + "ice")
    if w.endswith("ops"):
        out.add(w[:-1] + "es")  # cyclops -> cyclopes
    out.add(w + "s")
    return out


def plural_forms(name: str) -> set[str]:
    n = name.lower()
    forms = {n}
    words = n.split(" ")
    # ostatnie słowo w liczbie mnogiej
    for p in _plural_word(words[-1]):
        forms.add(" ".join(words[:-1] + [p]))
    # "son of verminor" -> "sons of verminor"
    if " of " in n:
        head, tail = n.split(" of ", 1)
        hw = head.split(" ")
        for p in _plural_word(hw[-1]):
            forms.add(" ".join(hw[:-1] + [p]) + " of " + tail)
    return forms


class MonsterIndex:
    def __init__(self, monster_names):
        self.names = list(monster_names)
        self.lookup: dict[str, str] = {}
        # najpierw formy pojedyncze (mają pierwszeństwo przed zbieżnymi formami mnogimi)
        for n in self.names:
            self.lookup.setdefault(n.lower(), n)
        for n in self.names:
            for f in plural_forms(n):
                self.lookup.setdefault(f, n)
        self._keys = list(self.lookup)

    def match(self, game_name: str) -> str | None:
        key = re.sub(r"\s+", " ", game_name.strip().lower())
        if key in self.lookup:
            return self.lookup[key]
        for art in ("a ", "an ", "the "):
            if key.startswith(art) and key[len(art):] in self.lookup:
                return self.lookup[key[len(art):]]
        close = difflib.get_close_matches(key, self._keys, n=1, cutoff=0.88)
        return self.lookup[close[0]] if close else None


# --------------------------------------------------------------------------- spoty

@dataclass
class Spot:
    area: str
    location: str
    floors: list[str]
    monsters: list[str]
    display: str = ""

    def hunt_name(self, floor: str | None) -> str:
        return f"{self.display} ({floor})" if floor else self.display


@dataclass
class SpotCandidate:
    spot: Spot
    coverage: float          # jaka część zabitych (ilościowo) pasuje do spotu
    specificity: float       # jaka część potworów spotu została zabita
    matched: list[str] = field(default_factory=list)


@dataclass
class DetectResult:
    candidates: list[SpotCandidate]
    unknown: list[str]               # nazwy z gry, których nie ma w bazie
    mapped: dict[str, str]           # nazwa z gry -> nazwa z bazy

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


class SpawnDB:
    def __init__(self, data: dict):
        self.version = data.get("version", "")
        self.index = MonsterIndex(data.get("monsters", {}).keys())
        raw = data.get("spawns", [])
        loc_areas: dict[str, set[str]] = {}
        for s in raw:
            loc_areas.setdefault(s.get("location", ""), set()).add(s.get("area", ""))
        self.spots: list[Spot] = []
        for s in raw:
            loc = s.get("location", "").strip()
            area = (s.get("area") or "").strip()
            if not loc:
                continue
            disp = f"{loc} ({area})" if len(loc_areas.get(loc, ())) > 1 and area else loc
            self.spots.append(Spot(area, loc, list(s.get("floors") or []), list(s.get("monsters") or []), disp))

    def detect(self, kills: dict[str, int], tolerance: float = 0.05, limit: int = 12) -> DetectResult:
        mapped: dict[str, str] = {}
        unknown: list[str] = []
        counts: dict[str, int] = {}
        for game_name, n in kills.items():
            canon = self.index.match(game_name)
            if canon:
                mapped[game_name] = canon
                counts[canon] = counts.get(canon, 0) + n
            else:
                unknown.append(game_name)
        total = sum(counts.values())
        if not total:
            return DetectResult([], unknown, mapped)

        cands = []
        for sp in self.spots:
            ms = set(sp.monsters)
            hit = [m for m in counts if m in ms]
            if not hit:
                continue
            cov = sum(counts[m] for m in hit) / total
            spec = len(hit) / max(len(ms), 1)
            cands.append(SpotCandidate(sp, cov, spec, sorted(hit)))
        if not cands:
            return DetectResult([], unknown, mapped)
        cands.sort(key=lambda c: (-c.coverage, -c.specificity, c.spot.display))
        best = cands[0].coverage
        top = [c for c in cands if c.coverage >= best - tolerance][:limit]
        return DetectResult(top, unknown, mapped)


def find_template(spot_name: str, location_name: str, templates: list[str]) -> str | None:
    """Template o nazwie spotu: najpierw dokładnie 'Lokacja (-7)', potem sama lokacja."""
    by_lower = {t.strip().lower(): t for t in templates}
    for key in (spot_name, location_name):
        if key and key.strip().lower() in by_lower:
            return by_lower[key.strip().lower()]
    return None
