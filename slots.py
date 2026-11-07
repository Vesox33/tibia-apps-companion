"""Wyznaczanie wolnych okien na respie."""
from __future__ import annotations

from datetime import datetime, timedelta

from parser import Reservation

MAX_LEN = timedelta(hours=3)       # „Najwyżej 3 godziny.”
GAP = timedelta(minutes=1)         # na stronie kolejne rezerwacje zaczynają się minutę po końcu poprzedniej
MIN_WINDOW = timedelta(minutes=10)
HORIZON = timedelta(days=1)        # początek najwyżej dobę do przodu (np. o 17:14 -> jutro 17:13)


def start_limit(now: datetime) -> datetime:
    """Najpóźniejszy dozwolony początek rezerwacji."""
    return now.replace(second=0, microsecond=0) + HORIZON - timedelta(minutes=1)


def booking_range(now: datetime) -> tuple[datetime, datetime]:
    """Zakres, w którym może leżeć rezerwacja: od teraz do (ostatni początek + 3 h)."""
    return now, start_limit(now) + MAX_LEN


def busy_intervals(reservations: list[Reservation]) -> list[tuple[datetime, datetime]]:
    iv = sorted((r.start, r.end) for r in reservations)
    merged: list[list[datetime]] = []
    for s, e in iv:
        if merged and s <= merged[-1][1] + GAP:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def free_windows(reservations: list[Reservation], day_start: datetime, day_end: datetime,
                 now: datetime) -> list[tuple[datetime, datetime]]:
    """Wolne okna w [day_start, day_end). Okno = (pierwsza wolna minuta, ostatnia wolna minuta)."""
    lo = max(day_start, ceil_minutes(now, 1))
    out = []
    cursor = lo
    for s, e in busy_intervals(reservations):
        if e < cursor:
            continue
        free_end = s - GAP
        if free_end > cursor and s > lo:
            out.append((cursor, min(free_end, day_end)))
        cursor = max(cursor, e + GAP)
        if cursor >= day_end:
            break
    if cursor < day_end:
        out.append((cursor, day_end))
    lim = start_limit(now)
    return [(a, b) for a, b in out if b - a >= MIN_WINDOW and a < day_end and a <= lim]


def ceil_minutes(dt: datetime, step: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0) + (timedelta(minutes=1) if dt.second or dt.microsecond else timedelta())
    rem = dt.minute % step
    return dt if rem == 0 else dt + timedelta(minutes=step - rem)


def validate(reservations: list[Reservation], start: datetime, end: datetime, now: datetime) -> str | None:
    """Zwraca opis problemu albo None, jeśli termin wygląda na poprawny."""
    if end <= start:
        return "Koniec musi być po początku."
    if end - start > MAX_LEN:
        return "Najwyżej 3 godziny."
    if start < now.replace(second=0, microsecond=0):
        return "Początek jest w przeszłości."
    if start > start_limit(now):
        return f"Rezerwować można najwyżej dobę do przodu (początek do {start_limit(now):%d.%m %H:%M})."
    for r in reservations:
        if start <= r.end and end >= r.start:
            return f"Koliduje z rezerwacją {r.label} ({r.who})."
    return None
