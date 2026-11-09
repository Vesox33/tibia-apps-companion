"""Odtwarza wycinki mapy wokół miejsc Rashida (assets/maps/*.png) z aktualnej mapy tibiamaps.io.

Uruchamiaj tylko, gdy mapa w grze się zmieni:  python tools/make_rashid_maps.py
Dane: tibia-map-data (https://github.com/tibiamaps/tibia-map-data).
"""
import io
import sys
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from today import RASHID, map_file  # noqa: E402

URL = "https://raw.githubusercontent.com/tibiamaps/tibia-map-data/main/data/floor-07-map.png"
X0, Y0 = 31744, 30976      # lewy górny róg floor-07-map.png
TILES = 170                # wycinek 170×170 pól, w aplikacji pokazywany ×2

if __name__ == "__main__":
    print("Pobieram mapę…")
    world = Image.open(io.BytesIO(requests.get(URL, timeout=120).content)).convert("RGB")
    for city, _place, (x, y) in RASHID.values():
        left, top = x - X0 - TILES // 2, y - Y0 - TILES // 2
        out = map_file(city)
        out.parent.mkdir(parents=True, exist_ok=True)
        world.crop((left, top, left + TILES, top + TILES)).save(out, optimize=True)
        print(" ", out.relative_to(ROOT))
