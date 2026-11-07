"""Parsowanie HTML z tibialoot.com (tablica rezerwacji + strona respa)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

MONTHS = {
    # polskie skróty
    "sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
    "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "paz": 10, "lis": 11, "gru": 12,
    # angielskie (gdyby strona była po angielsku)
    "jan": 1, "feb": 2, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Strona potrafi odpowiedzieć po angielsku (zależnie od języka przeglądarki) – tłumaczymy to, co pokazujemy.
TRANSLATE = {
    "ongoing": "Trwa", "in progress": "Trwa", "now": "Trwa",
    "up next": "Następna", "next": "Następna",
    "scheduled": "Zaplanowana", "upcoming": "Zaplanowana",
    "free": "Wolny", "available": "Wolny",
    "taken": "Zajęty", "occupied": "Zajęty", "busy": "Zajęty",
    "none booked": "Brak", "none": "Brak", "nothing booked": "Brak",
    "ended": "Zakończona", "finished": "Zakończona", "past": "Zakończona",
}


def pl(text: str) -> str:
    return TRANSLATE.get(text.strip().lower(), text.strip())


TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})")
DAY_RE = re.compile(r"(\d{1,2})\s+([A-Za-zżźćńółęąśŻŹĆŃÓŁĘĄŚ]+)")
DUR_H_RE = re.compile(r"(\d+)\s*h")
DUR_M_RE = re.compile(r"(\d+)\s*min")
SPOT_ID_RE = re.compile(r"^spot-(\d+)$")
RES_ID_RE = re.compile(r"^reservation-(\d+)$")


@dataclass
class Reservation:
    id: str
    spot_id: str
    start: datetime
    end: datetime
    authors: list[str]
    status: str = ""          # Trwa / Następna / Zaplanowana
    actions: list[str] = field(default_factory=list)  # linki/akcje (np. edycja) jeśli są
    confirm: str = ""         # tekst potwierdzenia usunięcia ze strony

    @property
    def delete_path(self) -> str | None:
        """Ścieżka usuwania – strona pokazuje ją tylko przy rezerwacjach, które możesz usunąć (Twoich)."""
        for a in self.actions:
            kind, _, url = a.partition(":")
            if kind == "hx-post" and url.rstrip("/").endswith("/delete"):
                return url
        return None

    @property
    def can_delete(self) -> bool:
        return self.delete_path is not None

    @property
    def who(self) -> str:
        return " / ".join(self.authors) if self.authors else "?"

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


@dataclass
class Spot:
    id: str
    name: str
    region: str = ""
    region_slug: str = ""
    status: str = ""          # Wolny / Zajęty
    holder: str = ""
    holder_time: str = ""
    next_text: str = ""
    reservations: list[Reservation] = field(default_factory=list)

    @property
    def is_taken(self) -> bool:
        return self.status.lower().startswith(("zaj", "taken", "occup"))


@dataclass
class Board:
    spots: dict[str, Spot]
    options: list[dict]       # [{"value": id, "label": nazwa, "keywords": region}]
    author: str               # "Rezerwacja dla ..." – Twoja postać
    logged_in: bool


def _txt(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def parse_day_label(label: str, today: date) -> date | None:
    m = DAY_RE.search(label)
    if not m:
        return None
    day = int(m.group(1))
    mon = MONTHS.get(m.group(2).lower().rstrip(".")[:3])
    if not mon:
        return None
    # rok: wybieramy ten, przy którym data jest najbliżej dzisiaj
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(y, mon, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def parse_duration(text: str) -> timedelta | None:
    h = DUR_H_RE.search(text)
    m = DUR_M_RE.search(text)
    if not h and not m:
        return None
    return timedelta(hours=int(h.group(1)) if h else 0, minutes=int(m.group(1)) if m else 0)


def parse_schedules(root, spot_id: str, today: date) -> list[Reservation]:
    """Parsuje wszystkie bloki [data-schedule] w danym elemencie."""
    out: list[Reservation] = []
    for sched in root.select("[data-schedule]"):
        current_day: date | None = None
        for child in sched.find_all(recursive=False):
            if child.name == "ol":
                if current_day is None:
                    current_day = parse_day_label(child.get("aria-label", ""), today)
                for li in child.find_all("li", recursive=False):
                    r = _parse_res_li(li, spot_id, current_day)
                    if r:
                        out.append(r)
            else:
                d = parse_day_label(_txt(child), today)
                if d:
                    current_day = d
    return out


def _parse_res_li(li, spot_id: str, day: date | None) -> Reservation | None:
    m = RES_ID_RE.match(li.get("id", ""))
    if not m or day is None:
        return None
    ps = li.find_all("p")
    text_all = _txt(li)
    tm = TIME_RE.search(text_all)
    if not tm:
        return None
    sh, smin, eh, emin = map(int, tm.groups())
    start = datetime(day.year, day.month, day.day, sh, smin)
    dur = None
    for p in ps[:3]:
        dur = parse_duration(_txt(p)) if not TIME_RE.search(_txt(p)) else None
        if dur:
            break
    if dur:
        end = start + dur
    else:
        end = datetime(day.year, day.month, day.day, eh, emin)
        if end <= start:
            end += timedelta(days=1)
    authors = [_txt(a) for a in li.select("a[data-author-link]")]
    if not authors:
        # czasem autor może być bez linku
        sp = li.select_one("p span.truncate")
        if sp:
            authors = [s.strip() for s in _txt(sp).split("/") if s.strip()]
    pill = li.select_one("[data-status-pill]")
    actions = []
    for el in li.find_all(["a", "button", "form"]):
        for attr in ("hx-delete", "hx-post", "hx-get", "action"):
            if el.get(attr):
                actions.append(f"{attr}:{el.get(attr)}")
        if el.name == "a" and el.get("href") and "characters/" not in el["href"]:
            actions.append(f"href:{el['href']}")
    form = li.find("form", attrs={"data-confirm": True})
    return Reservation(
        id=m.group(1), spot_id=spot_id, start=start, end=end,
        authors=authors, status=pl(_txt(pill)), actions=actions,
        confirm=form.get("data-confirm", "") if form else "",
    )


def _add_current_holder(spot: Spot, now: datetime):
    """Trwająca rezerwacja bywa pokazana tylko w kolumnie „Teraz”, a nie w harmonogramie –
    dokładamy ją, żeby ten czas nie wyglądał na wolny."""
    if not spot.is_taken or not spot.holder_time:
        return
    m = TIME_RE.search(spot.holder_time)
    if not m:
        return
    sh, smin, eh, emin = map(int, m.groups())
    start = now.replace(hour=sh, minute=smin, second=0, microsecond=0)
    if start > now:                      # zaczęła się wczoraj (np. 23:00–01:00)
        start -= timedelta(days=1)
    end = start.replace(hour=eh, minute=emin)
    if end <= start:
        end += timedelta(days=1)
    if any(r.start <= now <= r.end for r in spot.reservations):
        return
    authors = [a.strip() for a in spot.holder.split("/") if a.strip()]
    spot.reservations.insert(0, Reservation(id=f"current-{spot.id}", spot_id=spot.id, start=start, end=end,
                                            authors=authors, status="Trwa"))


def parse_board(html: str, today: date | None = None, now: datetime | None = None) -> Board:
    today = today or (now.date() if now else date.today())
    soup = BeautifulSoup(html, "html.parser")

    logged_in = soup.find("form", action=re.compile(r"/logout$")) is not None
    author_el = soup.select_one("#reservation-new-form [data-author]")
    author = _txt(author_el)

    options: list[dict] = []
    opt_script = soup.find("script", id=re.compile(r"spot-options$"))
    if opt_script and opt_script.string:
        try:
            options = [o for o in json.loads(opt_script.string) if o.get("value")]
        except json.JSONDecodeError:
            options = []

    spots: dict[str, Spot] = {}
    region_el = soup.find(id="reservations-region") or soup
    for reg in region_el.select("details[data-board-region]"):
        reg_name = _txt(reg.find("h2"))
        reg_slug = reg.get("data-board-region", "")
        for li in reg.find_all("li", id=SPOT_ID_RE):
            sid = SPOT_ID_RE.match(li["id"]).group(1)
            link = li.select_one("a[data-board-spot-link]")
            name = _txt(link) or sid
            grid = li.find("div", recursive=False)
            cols = grid.find_all("div", recursive=False) if grid else []
            status = holder = holder_time = next_text = ""
            if len(cols) >= 3:
                pill = cols[2].select_one("[data-status-pill]")
                status = pl(_txt(pill))
                holder = " / ".join(_txt(a) for a in cols[2].select("a[data-author-link]"))
                tm = TIME_RE.search(_txt(cols[2]))
                holder_time = tm.group(0) if tm else ""
            if len(cols) >= 4:
                spans = cols[3].find_all("span", recursive=True)
                next_text = " ".join(_txt(s) for s in spans[1:2]) or _txt(cols[3])
                nt = _txt(cols[3])
                next_text = re.sub(r"^(Następna|Next|Up next)\s*", "", nt, flags=re.I)
                next_text = pl(next_text) if pl(next_text) == "Brak" else re.sub(r"\s+(Ty|You)\b", "", next_text)
            spot = Spot(
                id=sid, name=name, region=reg_name, region_slug=reg_slug,
                status=status, holder=holder, holder_time=holder_time,
                next_text=next_text,
            )
            spot.reservations = parse_schedules(li, sid, today)
            _add_current_holder(spot, now or datetime.now())
            spots[sid] = spot

    # respy z listy opcji, których nie ma na tablicy (np. przy filtrze)
    for o in options:
        if o["value"] not in spots:
            spots[o["value"]] = Spot(id=o["value"], name=o["label"], region=o.get("keywords", ""))
        elif not spots[o["value"]].region:
            spots[o["value"]].region = o.get("keywords", "")

    return Board(spots=spots, options=options, author=author, logged_in=logged_in)


def parse_spot_page(html: str, spot_id: str, today: date | None = None) -> list[Reservation]:
    soup = BeautifulSoup(html, "html.parser")
    return parse_schedules(soup, spot_id, today or date.today())


def parse_form_errors(html: str) -> list[str]:
    """Wyciąga komunikaty błędów z odpowiedzi na POST formularza."""
    soup = BeautifulSoup(html, "html.parser")
    msgs: list[str] = []
    for el in soup.select('[role="alert"], .text-danger, .text-red-400, .text-red-500, .text-err, .text-bad'):
        t = _txt(el)
        if t and t not in msgs:
            msgs.append(t)
    for el in soup.find_all(["p", "div", "span"]):
        cls = " ".join(el.get("class", []))
        if re.search(r"(danger|error|err\b|text-bad|text-red|text-crit)", cls):
            t = _txt(el)
            if t and t not in msgs and len(t) < 300:
                msgs.append(t)
    return msgs
