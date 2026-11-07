"""Test BrowserWorker na lokalnej atrapie formularza (bez prawdziwej strony)."""
import os
import queue
import tempfile
import time
from pathlib import Path

import pytest

import mock_site
from huntuploader.browser import BrowserWorker, FillJob
from huntuploader.storage import Settings
from test_parser_spawns import HUNT, INPUT, PARTY

os.environ["THU_HEADLESS"] = "1"


@pytest.fixture
def env():
    srv, state, url = mock_site.start()
    s = Settings()
    s.base_url = url
    s.browser_channel = "auto"
    prof = tempfile.mkdtemp(prefix="thu-prof-")
    type(s).profile_dir = property(lambda self: Path(prof))
    ev = queue.Queue()
    w = BrowserWorker(s, lambda k, p: ev.put((k, p)))
    yield w, state, ev, url
    w.shutdown()
    time.sleep(0.5)
    srv.shutdown()


def wait(ev, kinds, timeout=60):
    end = time.time() + timeout
    logs = []
    while time.time() < end:
        try:
            k, p = ev.get(timeout=0.5)
        except queue.Empty:
            continue
        if k == "log":
            logs.append(p["msg"])
            print("LOG", p["msg"])
        if k == "error":
            raise AssertionError(f"error: {p} / {logs}")
        if k in kinds:
            return k, p, logs
    raise AssertionError(f"timeout waiting {kinds}; logs={logs}")


def job(**kw):
    d = dict(hunt_name="Buried Cathedral (-7)", party=PARTY, hunt=HUNT, input=INPUT,
             character="Paladin Vesox", make_public=True,
             match_names=("Buried Cathedral (-7)", "Buried Cathedral"),
             auto_save=True, fingerprints=["fp1"])
    d.update(kw)
    return FillJob(**d)


def test_template_then_fill_then_save(env):
    w, state, ev, _ = env
    w.fill(job())
    wait(ev, {"filled"})
    k, p, _ = wait(ev, {"saved"})
    assert p["job"].fingerprints == ["fp1"]
    s = state.saved[-1]
    assert s["hunt_session[hunt_name]"] == "Buried Cathedral (-7)"
    assert s["hunt_session[hunt_analyser_metadata]"].replace("\r\n", "\n").strip() == HUNT.strip()
    assert s["hunt_session[party_hunt_metadata]"].replace("\r\n", "\n").strip() == PARTY.strip()
    assert s["hunt_session[input_analyser_metadata]"].replace("\r\n", "\n").strip() == INPUT.strip()
    assert s["hunt_session[is_public]"] == "1"
    assert s["hunt_session[character_id]"] == "10152"
    # opis pochodzi z templatu => template został wybrany PRZED wpisaniem danych
    assert s["hunt_session[hunt_description]"] == "from template Buried Cathedral"


def test_late_template_overwrite_is_repaired(env):
    w, state, ev, _ = env
    state.late = True
    w.fill(job(make_public=False))
    wait(ev, {"filled"})
    wait(ev, {"saved"})
    s = state.saved[-1]
    assert s["hunt_session[hunt_analyser_metadata]"].strip()
    assert s["hunt_session[is_public]"] == "0"


def test_fill_only_does_not_save(env):
    w, state, ev, _ = env
    w.fill(job(auto_save=False, template=None, make_public=False))
    wait(ev, {"filled"})
    time.sleep(2)
    assert state.saved == []


def test_no_matching_template_fills_without(env):
    w, state, ev, _ = env
    w.fill(job(match_names=("Nie Ma (-1)", "Nie Ma")))
    _, p, _ = wait(ev, {"template_used"})
    assert p["name"] is None
    wait(ev, {"saved"})
    assert state.saved[-1]["hunt_session[hunt_description]"] == ""


def test_manual_no_template_wins(env):
    w, state, ev, _ = env
    w.fill(job(template_manual=True, template=None))
    _, p, _ = wait(ev, {"template_used"})
    assert p["name"] is None
    wait(ev, {"saved"})
    assert state.saved[-1]["hunt_session[hunt_description]"] == ""


def test_manual_other_template_wins(env):
    w, state, ev, _ = env
    w.fill(job(template_manual=True, template="edron dragon lair"))
    _, p, _ = wait(ev, {"template_used"})
    assert p["name"] == "Edron Dragon Lair"
    wait(ev, {"saved"})
    assert state.saved[-1]["hunt_session[hunt_description]"] == "from template Edron Dragon Lair"


def test_templates_refreshed_on_every_fill(env):
    w, state, ev, _ = env
    w.fill(job(auto_save=False))
    _, p, _ = wait(ev, {"templates"})
    assert p["names"] == list(mock_site.TEMPLATES.values())


def test_unknown_character_stops(env):
    w, state, ev, _ = env
    w.fill(job(character="Ktos Inny"))
    end = time.time() + 30
    got = None
    while time.time() < end:
        k, p = ev.get(timeout=30)
        if k in ("error", "fill_aborted", "filled", "saved"):
            got = (k, p)
            break
    assert got[0] == "fill_aborted" and "Paladin Vesox" in got[1]["msg"]
    time.sleep(1)
    assert state.saved == []


def test_no_character_keeps_page_default(env):
    w, state, ev, _ = env
    w.fill(job(character=""))
    wait(ev, {"saved"})
    assert state.saved[-1]["hunt_session[character_id]"] == "10152"


def test_list_templates(env):
    w, state, ev, _ = env
    w.list_templates()
    _, p, _ = wait(ev, {"templates"})
    assert p["names"] == list(mock_site.TEMPLATES.values())
    assert state.saved == []


def test_waits_for_login(env):
    w, state, ev, url = env
    state.logged_in = False
    w.open()
    wait(ev, {"need_login"})
    state.logged_in = True   # użytkownik się loguje
    wait(ev, {"logged_in"}, timeout=30)


def test_manual_template_missing_aborts(env):
    w, state, ev, _ = env
    w.fill(job(template_manual=True, template="Usunięty Template"))
    _, p, _ = wait(ev, {"fill_aborted", "filled", "saved"})
    assert "Usunięty Template" in p["msg"]
    time.sleep(1.5)
    assert state.saved == []


def test_auto_template_changes_after_refresh(env):
    w, state, ev, _ = env
    w.fill(job(template=None))   # w oknie: bez templatu, na stronie jest „Buried Cathedral”
    _, p, logs = wait(ev, {"template_used"})
    assert p["name"] == "Buried Cathedral"
    assert any("pasuje inny template" in l for l in logs)
    wait(ev, {"saved"})


def test_preys_replace_template_and_description(env):
    w, state, ev, _ = env
    w.fill(job(preys=[{"type": "Bonus Experience", "level": 7, "creature": "Ripper Spectre"},
                      {"type": "Improved Loot", "level": 10, "creature": "Nieznany Potwor"}],
               description_extra="XP Boost · Boosted: Ripper Spectre · Event: Double XP"))
    _, _, logs = wait(ev, {"filled"})
    assert any("zastępuję je" in l for l in logs)
    wait(ev, {"saved"})
    s = state.saved[-1]
    a = "hunt_session[hunt_session_preys_attributes]"
    assert s[f"{a}[0][prey_id]"] == "3" and s[f"{a}[0][prey_level]"] == "7" and s[f"{a}[0][creature_id]"] == "3"
    assert s[f"{a}[1][prey_id]"] == "4" and s[f"{a}[1][prey_level]"] == "10" and s[f"{a}[1][creature_id]"] == ""
    assert s[f"{a}[2][prey_id]"] == ""
    desc = s["hunt_session[hunt_description]"].replace("\r\n", "\n")
    assert desc == ("from template Buried Cathedral\n"
                    "XP Boost · Boosted: Ripper Spectre · Event: Double XP\n"
                    "Prey: Improved Loot 10★ – Nieznany Potwor")


def test_template_prey_kept_when_no_program_preys(env):
    w, state, ev, _ = env
    w.fill(job())
    wait(ev, {"saved"})
    s = state.saved[-1]
    assert s["hunt_session[hunt_session_preys_attributes][0][prey_id]"] == "1"
