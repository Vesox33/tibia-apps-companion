import json
from huntuploader import parser, spawns

HUNT = """Session data: From 2026-11-01, 18:00:00 to 2026-11-01, 19:00:00
Session: 01:00h
Raw XP Gain: 4,000,000
XP Gain: 6,000,000
Raw XP/h: 4,000,000
XP/h: 6,000,000
Loot: 1,000,000
Supplies: 300,000
Balance: 700,000
Damage: 10,000,000
Damage/h: 10,000,000
Healing: 2,000,000
Healing/h: 2,000,000
Killed Monsters:
  412x ripper spectres
  380x gazer spectres
  290x burster spectres
  120x arachnophobicas
Looted Items:
  900x a platinum coin
"""
PARTY = """Session data: From 2024-02-19, 16:35:59 to 2024-02-19, 17:39:48
Session: 01:03h
Loot Type: Leader
Loot: 3,019,278
Supplies: 911,660
Balance: 2,107,618
Elder Himanen (Leader)
\tLoot: 2,768,147
\tSupplies: 384,806
\tBalance: 2,383,341"""
INPUT = """Received Damage
Total: 2,203,082
Damage Types
  Physical 818,687 (37.2%)
Damage Sources
  dragolisk 840,378 (38.1%)"""


def db():
    return spawns.SpawnDB(json.load(open(spawns.FALLBACK, encoding="utf-8")))


def test_classify():
    assert parser.classify(HUNT) == parser.HUNT
    assert parser.classify(PARTY) == parser.PARTY
    assert parser.classify(PARTY.replace("\n", "\r\n")) == parser.PARTY
    assert parser.classify(INPUT) == parser.INPUT
    assert parser.classify("hello world") is None
    assert parser.classify("Session data: From a to b\nSession: 1h\nXP Gain: 5\nKilled Monsters:\n  1x rat") == parser.HUNT


def test_kills():
    k = parser.parse_killed_monsters(HUNT)
    assert k == {"ripper spectres": 412, "gazer spectres": 380, "burster spectres": 290, "arachnophobicas": 120}


def test_monster_names():
    idx = db().index
    for game, canon in [("dragon lords", "Dragon Lord"), ("dragon lord", "Dragon Lord"), ("Dragon Lords", "Dragon Lord"),
                        ("liches", "Lich"), ("mummies", "Mummy"), ("werewolves", "Werewolf"),
                        ("witches", "Witch"), ("sons of verminor", "Son of Verminor"),
                        ("acolytes of the cult", "Acolyte of the Cult"), ("behemoths", "Behemoth"),
                        ("cyclopes", "Cyclops"), ("dragon lord hatchlings", "Dragon Lord Hatchling")]:
        assert idx.match(game) == canon, game


def test_detect_buried_cathedral():
    r = db().detect(parser.parse_killed_monsters(HUNT))
    best = r.candidates[0]
    assert best.spot.display == "Buried Cathedral"
    assert best.spot.floors == ["-6", "-7", "-8"]
    assert best.spot.hunt_name("-7") == "Buried Cathedral (-7)"
    assert not r.unknown


def test_same_location_in_many_areas():
    d = db()
    nexus = [s.display for s in d.spots if s.location == "Haunted Nexus"]
    assert sorted(nexus) == ["Haunted Nexus (Northern Darama)", "Haunted Nexus (Tiquanda)", "Haunted Nexus (Venore)"]
    r = d.detect({"ripper spectres": 100, "arachnophobicas": 50})
    names = [c.spot.display for c in r.candidates]
    assert "Haunted Nexus (Venore)" in names and "Buried Cathedral" in names
    assert r.ambiguous


def test_dragon_lords_prefers_specific_lair():
    r = db().detect({"dragon lords": 200, "dragons": 150, "dragon hatchlings": 10})
    assert r.ambiguous
    assert "Dragon Lair" in r.candidates[0].spot.display


def test_unknown():
    r = db().detect({"blorgs": 3, "rats": 2})
    assert r.unknown == ["blorgs"]


def test_find_template():
    t = ["Buried Cathedral", "Edron Dragon Lair"]
    assert spawns.find_template("Buried Cathedral (-7)", "Buried Cathedral", t) == "Buried Cathedral"
    assert spawns.find_template("Edron Dragon Lair (-3)", "Edron Dragon Lair", ["edron dragon lair (-3)"]) == "edron dragon lair (-3)"
    assert spawns.find_template("X (-1)", "X", t) is None


def test_fingerprint_whitespace():
    assert parser.fingerprint(HUNT) == parser.fingerprint(HUNT.replace("\n", "\r\n") + "\n")
