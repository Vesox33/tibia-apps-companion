"""Klient tibialoot.com – tylko te same zapytania, które robi strona.

Zapytania mogą iść na dwa sposoby:
  * przez wbudowaną przeglądarkę (domyślnie) – ta sama sesja co przy logowaniu,
  * przez `requests` z ręcznie wklejonym ciasteczkiem (zapasowo).
Interpretacja odpowiedzi jest wspólna (funkcje poniżej).
"""
from __future__ import annotations

from datetime import datetime

import requests

from parser import Board, Reservation, parse_board, parse_form_errors, parse_spot_page

import os
BASE = os.environ.get("TL_BASE", "https://tibialoot.com")  # TL_BASE tylko do testów
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class NotLoggedIn(Exception):
    def __init__(self, html: str = "", url: str = ""):
        super().__init__("Niezalogowany")
        self.html, self.url = html, url


def board_url(guild_id: str) -> str:
    return f"{BASE}/servers/{guild_id}/reservations"


def spot_url(guild_id: str, spot_id: str) -> str:
    return f"{BASE}/servers/{guild_id}/spots/{spot_id}"


def reservation_form(spot_id: str, start: datetime, end: datetime) -> dict:
    return {
        "spot_id": spot_id,
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": end.strftime("%Y-%m-%dT%H:%M"),
    }


def htmx_headers(guild_id: str) -> dict:
    return {
        "HX-Request": "true",
        "HX-Target": "reservation-new-form",
        "HX-Current-URL": board_url(guild_id),
    }


def delete_headers(guild_id: str) -> dict:
    return {
        "HX-Request": "true",
        "HX-Target": "reservations-status",
        "HX-Current-URL": board_url(guild_id),
    }


def absolute(path: str) -> str:
    return path if path.startswith("http") else BASE + path


def interpret_delete(status: int, text: str) -> tuple[bool, str]:
    if status in (401, 403, 419):
        return False, f"Brak uprawnień (HTTP {status}). Zaloguj się ponownie."
    if 200 <= status < 400:
        errors = parse_form_errors(text)
        bad = [e for e in errors if not e.lower().startswith(("usunięto", "deleted"))]
        if bad and "text-ok" not in text:
            return False, "\n".join(bad)
        return True, "Rezerwacja usunięta."
    if status == 404:
        return False, "Rezerwacji już nie ma (HTTP 404)."
    return False, f"Błąd HTTP {status}"


def board_from_html(html: str, url: str = "") -> Board:
    board = parse_board(html)
    if not board.logged_in:
        raise NotLoggedIn(html, url)
    return board


def interpret_post(status: int, headers: dict, text: str, redirected: bool = False) -> tuple[bool, str]:
    h = {k.lower(): v for k, v in headers.items()}
    trig = " ".join(v for k, v in h.items() if k.startswith("hx-trigger"))
    if "reservation-saved" in trig or h.get("hx-redirect") or redirected or status in (0, 301, 302, 303):
        return True, "Rezerwacja zapisana."
    if status in (401, 403, 419):
        return False, f"Brak uprawnień (HTTP {status}). Zaloguj się ponownie."
    errors = parse_form_errors(text)
    if errors:
        return False, "\n".join(errors)
    if 200 <= status < 300:
        return True, "Wysłano. Sprawdzam, czy rezerwacja pojawiła się na tablicy…"
    return False, f"Błąd HTTP {status}"


class RequestsClient:
    """Zapasowy klient – używany tylko z ręcznie wklejonym ciasteczkiem."""

    def __init__(self, guild_id: str):
        self.guild_id = guild_id
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "pl,en;q=0.8"})

    def set_cookie_header(self, header: str):
        for part in header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                self.s.cookies.set(k.strip(), v.strip(), domain="tibialoot.com", path="/")

    def fetch_board(self) -> Board:
        r = self.s.get(board_url(self.guild_id), timeout=20)
        r.raise_for_status()
        return board_from_html(r.text, r.url)

    def fetch_spot_reservations(self, spot_id: str) -> list[Reservation]:
        r = self.s.get(spot_url(self.guild_id, spot_id), timeout=20)
        r.raise_for_status()
        return parse_spot_page(r.text, spot_id)

    def delete_reservation(self, path: str) -> tuple[bool, str]:
        headers = delete_headers(self.guild_id) | {"Origin": BASE, "Referer": board_url(self.guild_id)}
        r = self.s.post(absolute(path), headers=headers, timeout=20, allow_redirects=True)
        return interpret_delete(r.status_code, r.text)

    def create_reservation(self, spot_id: str, start: datetime, end: datetime) -> tuple[bool, str]:
        headers = htmx_headers(self.guild_id) | {"Origin": BASE, "Referer": board_url(self.guild_id)}
        r = self.s.post(board_url(self.guild_id), data=reservation_form(spot_id, start, end),
                        headers=headers, timeout=20, allow_redirects=False)
        return interpret_post(r.status_code, dict(r.headers), r.text)
