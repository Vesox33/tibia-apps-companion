"""Pobiera ikonki imbuementów i materiałów do assets/imbu/, żeby trafiły do wydania (działają offline).

Uruchom raz przed wydaniem wersji:  python tools/fetch_imbu_icons.py
Bez tego aplikacja i tak dociąga brakujące ikonki sama przy pierwszym otwarciu kalkulatora.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imbu_data import IMBUEMENTS  # noqa: E402
from imbu_icons import BUNDLED, download, file_name  # noqa: E402


def main():
    jobs = [("imb", n) for n in IMBUEMENTS] + sorted({("item", i) for _d, g in IMBUEMENTS.values() for i, _n in g})
    ok, bad = 0, []
    for kind, name in jobs:
        sub = "imbuements" if kind == "imb" else "items"
        if (BUNDLED / sub / file_name(name)).exists():
            ok += 1
            continue
        try:
            download(kind, name, dest_root=BUNDLED)
            ok += 1
            print("  ok   ", name)
        except Exception as e:
            bad.append(name)
            print("  BRAK ", name, "–", e)
    print(f"\nGotowe: {ok}/{len(jobs)} w {BUNDLED}")
    if bad:
        print("Nie udało się:", ", ".join(bad))


if __name__ == "__main__":
    main()
