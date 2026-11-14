"""Imbuementy: materiały dla każdego poziomu (Basic / Intricate / Powerful).

Każdy poziom dodaje jedną grupę materiałów do poprzednich. Źródło: TibiaWiki (Imbuing).
Gold Tokeny: u NPC Yany (Adventurer's Guild) każdą grupę materiałów można kupić za 2 Gold Tokeny.
"""
from __future__ import annotations

TIERS = ["Basic", "Intricate", "Powerful"]
TOKENS_PER_GROUP = 2
DEFAULT_FEES = [7_500, 60_000, 250_000]   # opłata przy shrine od aktualizacji z czerwca 2025 (100% szansy)
DURATION_H = 20

# Tylko te trzy imbuementy można zrobić z materiałów kupionych za Gold Tokeny (NPC Yana, Adventurer's Guild).
# nazwa: (opis, [(materiał, ilość) dla poziomu 1, 2, 3])
IMBUEMENTS: dict[str, tuple[str, list[tuple[str, int]]]] = {
    "Vampirism": ("Life leech", [("Vampire Teeth", 25), ("Bloody Pincers", 15), ("Piece of Dead Brain", 5)]),
    "Void": ("Mana leech", [("Rope Belt", 25), ("Silencer Claws", 25), ("Some Grimeleech Wings", 5)]),
    "Strike": ("Critical hit", [("Protective Charm", 20), ("Sabretooth", 25), ("Vexclaw Talon", 5)]),
}
RENAMED = {"Sabreteeth": "Sabretooth"}   # stare nazwy w zapisanych cenach


def groups(name: str, tier: int) -> list[tuple[str, int]]:
    """Grupy materiałów potrzebne do poziomu `tier` (0 = Basic, 1 = Intricate, 2 = Powerful)."""
    return IMBUEMENTS[name][1][: tier + 1]


def parse_gold(text: str) -> int | None:
    """„1500”, „1.5k”, „1,5k”, „45k”, „1kk”, „2.3kk” → liczba złota. None, gdy nie da się odczytać."""
    t = (text or "").strip().lower().replace(" ", "").replace(" ", "")
    if not t:
        return None
    mult = 1
    if t.endswith("kk"):
        mult, t = 1_000_000, t[:-2]
    elif t.endswith("k"):
        mult, t = 1_000, t[:-1]
    t = t.replace(",", ".")
    if t.count(".") > 1:            # 1.250.000 – kropki jako separatory tysięcy
        t = t.replace(".", "")
    try:
        v = float(t) * mult
    except ValueError:
        return None
    return int(round(v)) if v >= 0 else None


def fmt_gold(v: float | int | None) -> str:
    """Zapis jak w Tibii: 950 gp, 12,5k, 1,25kk."""
    if v is None:
        return "—"
    v = float(v)
    if v >= 1_000_000:
        s = f"{v / 1_000_000:.2f}".rstrip("0").rstrip(".") + "kk"
    elif v >= 10_000:
        s = f"{v / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    else:
        s = f"{int(round(v)):,}".replace(",", " ") + " gp"
    return s.replace(".", ",")
