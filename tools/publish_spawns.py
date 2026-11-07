"""Aktualizacja listy spawnów: zbiera dane z tibiadraptor.com, pokazuje zmiany i publikuje je w repozytorium.

Zainstalowane aplikacje pobierają nową listę same (raz na dobę albo przyciskiem
Ustawienia → Dane i log → Aktualizuj teraz). Nowa wersja aplikacji nie jest potrzebna.

Publikować może tylko ktoś z prawem zapisu do repozytorium (domyślnie tylko właściciel).
Uruchamiaj przez wydaj_spawny.bat.

    --show    widoczne okno przeglądarki podczas zbierania
    --force   publikuj mimo dużego spadku liczby lokacji/potworów
    --from-file PLIK   nie zbieraj, użyj gotowego pliku (np. dist/spawns.new.json)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_spawns import ROOT, TARGET, ScrapeError, scrape  # noqa: E402

REL = TARGET.relative_to(ROOT).as_posix()
MIN_RATIO = 0.85     # tyle co najmniej lokacji i potworów musi zostać względem obecnej listy


def run(cmd, check=True, capture=False):
    print("  >", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=capture)


def validate(d) -> str | None:
    if not (isinstance(d, dict) and d.get("format") == 2 and isinstance(d.get("monsters"), dict)
            and isinstance(d.get("spawns"), list)):
        return "zły format pliku"
    for s in d["spawns"]:
        if not s.get("location") or not isinstance(s.get("monsters"), list) or not isinstance(s.get("floors"), list):
            return f"niepełny wpis: {s}"
    if not d["spawns"] or not d["monsters"]:
        return "pusta lista"
    return None


def key(s):
    return (s.get("area", ""), s["location"])


def diff(old: dict, new: dict) -> tuple[list[str], bool]:
    o = {key(s): s for s in old.get("spawns", [])}
    n = {key(s): s for s in new["spawns"]}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed = []
    for k in sorted(set(o) & set(n)):
        a, b = set(o[k]["monsters"]), set(n[k]["monsters"])
        if a != b or o[k].get("floors") != n[k].get("floors"):
            parts = []
            if b - a:
                parts.append("+ " + ", ".join(sorted(b - a)))
            if a - b:
                parts.append("− " + ", ".join(sorted(a - b)))
            if o[k].get("floors") != n[k].get("floors"):
                parts.append(f"piętra {o[k].get('floors')} → {n[k].get('floors')}")
            changed.append(f"  {name(k)}: " + "; ".join(parts))
    lines = [f"Lokacje: {len(o)} → {len(n)}   Potwory: {len(old.get('monsters', {}))} → {len(new['monsters'])}",
             f"Wersja: {old.get('version', '?')} → {new['version']}"]
    if added:
        lines += [f"\nNowe lokacje ({len(added)}):"] + [f"  + {name(k)}" for k in added]
    if removed:
        lines += [f"\nUsunięte lokacje ({len(removed)}):"] + [f"  − {name(k)}" for k in removed]
    if changed:
        lines += [f"\nZmienione lokacje ({len(changed)}):"] + changed
    nothing = not (added or removed or changed or set(old.get("monsters", {})) != set(new["monsters"]))
    return lines, nothing


def name(k):
    area, loc = k
    return f"{loc} [{area}]" if area and area != loc else loc


def main():
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        sys.exit("To nie jest repozytorium git. Najpierw wydaj aplikację (wydaj_wersje.bat) – to zakłada repozytorium.")

    if "--from-file" in sys.argv:
        new = json.loads(Path(sys.argv[sys.argv.index("--from-file") + 1]).read_text(encoding="utf-8"))
    else:
        try:
            new = scrape(show="--show" in sys.argv)
        except ScrapeError as e:
            sys.exit(f"\nZbieranie nie powiodło się: {e}")
    err = validate(new)
    if err:
        sys.exit(f"Nowa lista jest niepoprawna ({err}) – nic nie opublikowano.")

    old = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {}
    lines, nothing = diff(old, new)
    print("\n" + "\n".join(lines[:200]))
    if len(lines) > 200:
        print(f"  … i {len(lines) - 200} więcej")
    if nothing:
        print("\nBrak zmian w spawnach – nie ma czego publikować.")
        return

    o_sp, o_mo = len(old.get("spawns", [])), len(old.get("monsters", {}))
    if "--force" not in sys.argv and o_sp and (len(new["spawns"]) < o_sp * MIN_RATIO or len(new["monsters"]) < o_mo * MIN_RATIO):
        sys.exit("\nNowa lista jest wyraźnie mniejsza od obecnej – możliwe, że strona się zmieniła albo nie wczytała.\n"
                 "Nic nie opublikowano. Jeśli to zamierzone, uruchom z --force.")

    if input("\nOpublikować tę listę? [t/N]: ").strip().lower() not in ("t", "tak", "y"):
        print("Przerwano – nic nie opublikowano.")
        return

    run(["git", "pull", "--ff-only"], check=False)
    TARGET.write_text(json.dumps(new, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    run(["git", "add", REL])
    run(["git", "commit", "-m", f"Spawny {new['version']}", "--", REL])
    run(["git", "push", "origin", "HEAD"])
    print(f"\nGotowe – spawny {new['version']} opublikowane. Aplikacje pobiorą je w ciągu doby "
          "(albo od razu: Ustawienia → Dane i log → Aktualizuj teraz).")


if __name__ == "__main__":
    main()
