"""Pobiera bestiariusz tibiadraptor.com i buduje listę spawnów dla aplikacji.

Wynik: huntuploader/data/spawns.json (format 2: monsters + spawns z obszarem, lokacją, piętrami).
Lokacje o tej samej nazwie w kilku obszarach (np. Haunted Nexus) są rozdzielane osobnymi
zapytaniami z filtrem area + location.

Użycie (zwykle przez wydaj_spawny.bat → publish_spawns.py):
    python tools/scrape_spawns.py            # w tle, zapis do pliku tymczasowego i podsumowanie
    python tools/scrape_spawns.py --show     # z widocznym oknem przeglądarki
    python tools/scrape_spawns.py --csv      # dodatkowo spawny.csv i potwory.csv w dist/spawny/
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "huntuploader" / "data" / "spawns.json"

SKIP_HEADERS = {"content-length", "host", "cookie", "connection", "accept-encoding",
                "user-agent", "referer", "origin"}

FETCH_JS = """
async ({url, method, headers, body}) => {
  const opts = {method, headers, credentials: 'include'};
  if (body !== null && method !== 'GET') opts.body = body;
  const r = await fetch(url, opts);
  return {status: r.status, text: await r.text()};
}
"""


class ScrapeError(Exception):
    pass


def set_params_in_url(url, params):
    s = urlsplit(url)
    q = dict(parse_qsl(s.query, keep_blank_values=True))
    q.update({k: str(v) for k, v in params.items()})
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q), s.fragment))


def set_params_in_body(body, params):
    try:
        d = json.loads(body or "{}")
        if isinstance(d, dict):
            d.update(params)
            return json.dumps(d)
    except json.JSONDecodeError:
        pass
    q = dict(parse_qsl(body or "", keep_blank_values=True))
    q.update({k: str(v) for k, v in params.items()})
    return urlencode(q)


def fetch_bestiary(page, req, headers, filters=None, label=""):
    """Pobiera wszystkie strony bestiariusza z opcjonalnymi filtrami (np. area, location)."""
    filters = filters or {}
    monsters, n, last = [], 1, 1
    while n <= last:
        params = {**filters, "page": n}
        is_get = req["method"] == "GET"
        args = {
            "url": set_params_in_url(req["url"], params) if is_get else req["url"],
            "method": req["method"],
            "headers": headers,
            "body": None if is_get else set_params_in_body(req["body"], params),
        }
        res = page.evaluate(FETCH_JS, args)
        try:
            d = json.loads(res["text"])
        except json.JSONDecodeError:
            raise ScrapeError(f"{label}strona {n}: status {res['status']}, odpowiedź nie jest JSON-em:\n{res['text'][:500]}")
        batch = d.get("data") or []
        if not batch:
            break
        monsters += batch
        meta = d.get("meta", {})
        last = meta.get("last_page", n)
        print(f"\r{label}strona {n}/{last} ({len(monsters)}/{meta.get('total')})   ", end="", flush=True)
        n += 1
        page.wait_for_timeout(400)
    print()
    return monsters


def fmt_floor(z):
    """Tibia: z=7 to parter. Strona pokazuje 7 - z (np. z=13 -> -6)."""
    v = 7 - z
    return f"+{v}" if v > 0 else str(v)


def _launch(p, headless: bool):
    """Chromium z Playwrighta, a gdy go nie ma – Edge albo Chrome z systemu."""
    errors = []
    for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(headless=headless, **kw)
        except Exception as e:
            errors.append(str(e).splitlines()[0])
    raise ScrapeError("Nie udało się uruchomić przeglądarki: " + " | ".join(errors))


def scrape(show: bool = False) -> dict:
    from playwright.sync_api import sync_playwright
    captured = {}

    def on_request(r):
        if urlsplit(r.url).path.rstrip("/") == "/api/v1/bestiary" and not captured:
            captured.update(url=r.url, method=r.method, headers=r.headers, body=r.post_data)

    with sync_playwright() as p:
        browser = _launch(p, headless=not show)
        page = browser.new_page()
        page.on("request", on_request)
        page.goto("https://tibiadraptor.com/bestiary", wait_until="networkidle", timeout=60000)
        if not captured:
            raise ScrapeError("Nie przechwyciłem zapytania do /api/v1/bestiary – strona mogła się zmienić.")
        headers = {k: v for k, v in captured["headers"].items()
                   if not k.startswith(":") and k.lower() not in SKIP_HEADERS}

        # 1. Lista lokacji z obszarami
        res = page.evaluate(FETCH_JS, {"url": "/api/v1/locations/public", "method": "GET",
                                       "headers": {"Accept": "application/json"}, "body": None})
        locations = json.loads(res["text"])
        areas_of = defaultdict(list)
        for x in locations:
            areas_of[x["name"]].append(x.get("area") or "")
        dupes = {name: a for name, a in areas_of.items() if len(a) > 1}
        print(f"{len(locations)} lokacji, zdublowane nazwy: {', '.join(dupes) or 'brak'}")

        # 1b. Piętra: granice lokacji pobierane osobno dla każdego piętra (z = 0..15)
        floors_of = defaultdict(set)
        for z in range(16):
            res = page.evaluate(FETCH_JS, {
                "url": f"/api/v1/locations/public-boundaries?floor={z}", "method": "GET",
                "headers": {"Accept": "application/json"}, "body": None})
            try:
                data = json.loads(res["text"]).get("data") or []
            except (json.JSONDecodeError, AttributeError):
                print(f"Piętro {z}: brak danych (status {res['status']})")
                continue
            for x in data:
                floors_of[x["id"]].add(z)
            print(f"\rPiętra: {z + 1}/16", end="", flush=True)
            page.wait_for_timeout(300)
        print()
        floors_by_key = {}
        for x in locations:
            zs = sorted(floors_of.get(x["id"], ()))   # od najwyższego piętra
            floors_by_key[(x.get("area") or "", x["name"])] = [fmt_floor(z) for z in zs]

        # 2. Cały bestiariusz
        monsters = fetch_bestiary(page, captured, headers, label="Bestiariusz: ")
        monsters = list({m["name"]: m for m in monsters}.values())

        # 3. Zdublowane lokacje – osobno dla każdego obszaru
        dupe_mobs = {}
        for name, areas in dupes.items():
            for area in areas:
                found = fetch_bestiary(page, captured, headers, {"area": area, "location": name},
                                       label=f"{name} [{area}]: ")
                dupe_mobs[(area, name)] = {m["name"] for m in found}
        browser.close()

    spawns = defaultdict(set)
    for m in monsters:
        for loc in (m.get("locations") or "").split(","):
            loc = loc.strip()
            if not loc or loc in dupes:
                continue
            area = areas_of[loc][0] if loc in areas_of else ""
            spawns[(area, loc)].add(m["name"])
    for key, mobs in dupe_mobs.items():
        if mobs:
            spawns[key] = mobs
        else:
            print(f"Uwaga: filtr nic nie zwrócił dla {key[1]} [{key[0]}]")

    return {
        "format": 2,
        "version": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "tibiadraptor.com",
        "monsters": {m["name"]: [(m.get("class") or {}).get("name", ""), m.get("hitpoints"),
                                 m.get("experience"), m.get("difficulty")]
                     for m in sorted(monsters, key=lambda x: x["name"])},
        "spawns": [{"area": area, "location": loc, "floors": floors_by_key.get((area, loc), []),
                    "monsters": sorted(spawns[(area, loc)])}
                   for (area, loc) in sorted(spawns)],
    }


def write_csv(data: dict, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "spawny.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["obszar", "lokacja", "pietra", "liczba", "potwory"])
        for s in data["spawns"]:
            w.writerow([s["area"], s["location"], ", ".join(s["floors"]), len(s["monsters"]), ", ".join(s["monsters"])])
    with open(folder / "potwory.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["potwór", "klasa", "hp", "exp", "trudność"])
        for name, (cls, hp, exp, diff) in data["monsters"].items():
            w.writerow([name, cls, hp, exp, diff])


if __name__ == "__main__":
    out = scrape(show="--show" in sys.argv)
    tmp = ROOT / "dist" / "spawns.new.json"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if "--csv" in sys.argv:
        write_csv(out, ROOT / "dist" / "spawny")
    print(f"{len(out['monsters'])} potworów, {len(out['spawns'])} lokacji -> {tmp}")
