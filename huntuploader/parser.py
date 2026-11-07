"""Rozpoznawanie tekstów skopiowanych z analizatorów Tibii."""
from __future__ import annotations

import hashlib
import re

PARTY = "party"
HUNT = "hunt"
INPUT = "input"

KIND_LABELS = {
    PARTY: "Party Hunt Analyzer",
    HUNT: "Hunt Analyzer",
    INPUT: "Input Analyzer",
}

_SESSION_RE = re.compile(r"^\s*Session data:\s*From\s+.+?\s+to\s+.+$", re.M)
_KILL_RE = re.compile(r"^\s*(\d[\d,.]*)x\s+(.+?)\s*$")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n﻿ ")
    return text


def classify(text: str) -> str | None:
    """Zwraca PARTY / HUNT / INPUT albo None, gdy to nie jest tekst z analizatora."""
    if not text:
        return None
    t = normalize_text(text)
    if len(t) > 200_000:
        return None
    first = t.lstrip().split("\n", 1)[0].strip()

    # Input Analyzer: "Received Damage" / "Damage Types" / "Damage Sources"
    if first.startswith("Received Damage") or first.startswith("Dealt Damage") or (
        "Damage Sources" in t and "Damage Types" in t and "Session data:" not in t
    ):
        return INPUT

    if not _SESSION_RE.search(t):
        return None

    # Party Hunt Analyzer ma "Loot Type:" i bloki graczy z wcięciem tabem
    if re.search(r"^\s*Loot Type:", t, re.M):
        return PARTY
    # Hunt Analyzer ma XP i listy potworów / itemów
    if re.search(r"^\s*(Raw )?XP Gain:", t, re.M) or "Killed Monsters:" in t:
        return HUNT
    if re.search(r"^\t\s*Loot:", t, re.M):
        return PARTY
    return None


def parse_killed_monsters(text: str) -> dict[str, int]:
    """Zwraca {nazwa z gry: liczba} z sekcji 'Killed Monsters:' Hunt Analyzera."""
    out: dict[str, int] = {}
    in_section = False
    for raw in normalize_text(text).split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("Killed Monsters"):
            in_section = True
            continue
        if not in_section:
            continue
        if not stripped or stripped.lower() == "none":
            continue
        m = _KILL_RE.match(line)
        if not m:
            # następna sekcja ("Looted Items:" itp.)
            if stripped.endswith(":"):
                break
            continue
        count = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        name = m.group(2).strip()
        out[name] = out.get(name, 0) + count
    return out


def session_line(text: str) -> str | None:
    m = _SESSION_RE.search(normalize_text(text))
    return m.group(0).strip() if m else None


def fingerprint(text: str) -> str:
    """Odcisk do wykrywania duplikatów (białe znaki są ignorowane)."""
    t = re.sub(r"\s+", " ", normalize_text(text)).strip()
    return hashlib.sha1(t.encode("utf-8")).hexdigest()
