"""Sterowanie przeglądarką (Edge, własny profil) przez Playwright – w osobnym wątku.

Kolejność przy wypełnianiu (ważne dla templatów):
  1. otwórz /hunt_sessions/new (i poczekaj na zalogowanie, jeśli trzeba),
  2. jeśli jest template – wybierz go i poczekaj, aż strona skończy go wczytywać,
  3. dopiero potem wpisz nazwę, analizatory, postać, "Make it public",
  4. po chwili sprawdź, czy pola nadal mają wpisane wartości (jeśli coś je wyczyściło – wpisz ponownie),
  5. opcjonalnie kliknij SAVE.

Struktura strony (z zapisanej kopii "New hunt session"):
  - formularz:  turbo-frame#hunt_session_form > form.ha-form[action$="/hunt_sessions"]
  - nazwa:      input#hunt_name
  - analizatory: textarea#party_hunt_metadata, #hunt_analyser_metadata, #input_analyser_metadata
  - postać:     select#character-select
  - publiczny:  input#is_public (checkbox)
  - templaty:   a[href*="/templates/hunt_sessions/selections"][data-turbo-stream]
                -> lista ładowana do turbo-frame#hunt_template_selector (poza formularzem)
  - SAVE:       ostatni button[type=submit] wewnątrz form.ha-form, bez atrybutu form=
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .spawns import find_template
from .storage import app_dir

SEL_FORM = 'form.ha-form[action$="/hunt_sessions"]'
SEL_NAME = "#hunt_name"
SEL_PARTY = "#party_hunt_metadata"
SEL_HUNT = "#hunt_analyser_metadata"
SEL_INPUT = "#input_analyser_metadata"
SEL_CHAR = "#character-select"
SEL_PUBLIC = "#is_public"
SEL_TEMPLATE_LINK = 'a[href*="/templates/hunt_sessions/selections"]'   # "Choose a hunt template"
SEL_TPL_BOX = '[data-controller="template-selector"]'                     # pojawia się po kliknięciu linku
SEL_TPL_INPUT = f'{SEL_TPL_BOX} [data-template-selector-target="input"]'
SEL_TPL_HIDDEN = f'{SEL_TPL_BOX} [data-template-selector-target="hiddenInput"]'
SEL_TPL_OPTION = f'{SEL_TPL_BOX} button[data-template-selector-target="option"]'
SEL_TPL_FORM = f'{SEL_TPL_BOX} form[data-template-selector-target="form"]'
TPL_RESPONSE = "/templates/hunt_sessions/selections/new"
SEL_SAVE = f"{SEL_FORM} button[type=submit]:not([form])"
SEL_DESC = "#hunt_description"
# Preye: 3 karty w sekcji [data-controller=hunt-session-preys]
SEL_PREY_CARD = '[data-hunt-session-preys-target="card"]'
SEL_PREY_TYPE_MODAL = "#prey_type_modal"
SEL_PREY_CREATURE_MODAL = "#prey_creature_modal"
PREY_TYPES = ("Bonus Experience", "Damage Boost", "Damage Reduction", "Improved Loot")

AUTO_ORDER = ("msedge", "chrome", "brave", "chromium")
BROWSER_NAMES = {"msedge": "Microsoft Edge", "chrome": "Google Chrome", "brave": "Brave",
                 "chromium": "Chromium (Playwright)"}


def find_brave() -> str | None:
    """Brave nie jest kanałem Playwright – szukamy pliku wykonywalnego w typowych miejscach."""
    import shutil
    rel = Path("BraveSoftware") / "Brave-Browser" / "Application" / "brave.exe"
    candidates = [Path(os.environ[v]) / rel for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
                  if os.environ.get(v)]
    candidates += [Path("/usr/bin/brave-browser"), Path("/opt/brave.com/brave/brave"),
                   Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("brave", "brave-browser", "brave.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


class TemplateMissing(Exception):
    """Ręcznie wybranego templatu nie ma już na stronie."""


@dataclass
class FillJob:
    hunt_name: str
    party: str = ""
    hunt: str = ""
    input: str = ""
    character: str = ""                 # "" = zostaw postać wybraną na stronie
    make_public: bool = False
    use_templates: bool = True
    template_manual: bool = False       # True = użyj dokładnie `template` (None = bez templatu)
    template: str | None = None
    match_names: tuple = ("", "")       # (nazwa huntu, lokacja) do dopasowania po odświeżeniu listy
    auto_save: bool = False
    fingerprints: list[str] = field(default_factory=list)
    preys: list[dict] = field(default_factory=list)   # [{"type": "Damage Boost", "level": 5, "creature": "Dragon Lord"}]
    description_extra: str = ""                       # np. "XP Boost · Boosted: Dragon Lord · Event: Double XP"


class BrowserWorker:
    """Wszystkie wywołania Playwright dzieją się w jednym wątku (wymóg sync API)."""

    def __init__(self, settings, on_event):
        self.settings = settings
        self.on_event = on_event          # on_event(kind:str, payload:dict) – wołane z wątku roboczego
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="browser", daemon=True)
        self._pw = None
        self._ctx = None
        self._pending: FillJob | None = None
        self._description: str | None = None
        self._stop = False
        self._cookies: list[dict] = []    # sesja przekazana z okna logowania aplikacji
        self._thread.start()

    # ------------------------------------------------------------------ API (z wątku GUI)
    def open(self):
        self._q.put(("open", None))

    def fill(self, job: FillJob):
        self._q.put(("fill", job))

    def list_templates(self):
        self._q.put(("templates", None))

    def import_cookies(self, cookies: list[dict]):
        """Sesja z okna logowania aplikacji → profil przeglądarki wypełniającej formularz."""
        self._q.put(("cookies", list(cookies)))

    def export_cookies(self):
        """Odeślij ciasteczka strony (zdarzenie "cookies") – np. po zalogowaniu w oknie przeglądarki."""
        self._q.put(("export_cookies", None))

    def shutdown(self):
        self._q.put(("quit", None))

    # ------------------------------------------------------------------ wątek
    def _emit(self, kind, **payload):
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def _log(self, msg):
        self._emit("log", msg=msg)

    def _run(self):
        while not self._stop:
            try:
                cmd, arg = self._q.get(timeout=0.25 if self._ctx is None else 0.0)
            except queue.Empty:
                self._pump()
                continue
            try:
                if cmd == "quit":
                    self._stop = True
                    break
                if cmd == "open":
                    page = self._page()
                    self._goto_new(page)
                    self._emit("opened")
                elif cmd == "fill":
                    self._do_fill(arg)
                elif cmd == "templates":
                    self._do_list_templates()
                elif cmd == "cookies":
                    self._cookies = arg
                    if self._ctx is not None and arg:
                        self._ctx.add_cookies(arg)
                elif cmd == "export_cookies":
                    if self._ctx is not None:
                        host = urlparse(self.settings.base_url).hostname or ""
                        self._emit("cookies", cookies=[c for c in self._ctx.cookies()
                                                       if host.endswith(c.get("domain", "").lstrip("."))])
            except Exception as e:
                self._log(f"Błąd przeglądarki: {e.__class__.__name__}: {e}")
                (app_dir() / "last_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                self._emit("error", msg=str(e))
                if self._is_closed_error(e):
                    self._reset()
        self._reset()

    def _pump(self):
        """Pozwala Playwright obsłużyć zdarzenia (np. odpowiedź po kliknięciu SAVE przez użytkownika)."""
        if self._ctx is None:
            return
        try:
            pages = self._ctx.pages
            if not pages:
                self._reset()
                self._emit("closed")
                return
            pages[0].wait_for_timeout(250)
        except Exception:
            self._reset()
            self._emit("closed")

    @staticmethod
    def _is_closed_error(e):
        return "closed" in str(e).lower() or "Target page" in str(e)

    def _reset(self):
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = None
        self._pw = None

    def _context(self):
        if self._ctx is not None:
            return self._ctx
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        headless = os.environ.get("THU_HEADLESS") == "1"  # tylko do testów
        wanted = (self.settings.browser_channel or "auto").strip().lower()
        order = [wanted] if wanted != "auto" else list(AUTO_ORDER)
        errors = []
        for ch in order:
            kwargs = dict(user_data_dir=str(self.settings.profile_dir / ch), headless=headless,
                          no_viewport=not headless, args=["--start-maximized"])
            if ch == "brave":
                exe = find_brave()
                if not exe:
                    errors.append("Brave: nie znaleziono brave.exe")
                    self._log("Nie znaleziono Brave.")
                    continue
                kwargs["executable_path"] = exe
            elif ch != "chromium":
                kwargs["channel"] = ch
            self._log(f"Uruchamiam przeglądarkę: {BROWSER_NAMES.get(ch, ch)}…")
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
                break
            except Exception as e:
                errors.append(f"{BROWSER_NAMES.get(ch, ch)}: {str(e).splitlines()[0]}")
                self._log(f"Nie udało się uruchomić {BROWSER_NAMES.get(ch, ch)}.")
        if self._ctx is None:
            self._pw.stop()
            self._pw = None
            raise RuntimeError(
                "Nie znalazłem żadnej obsługiwanej przeglądarki. Zainstaluj Edge, Chrome lub Brave albo wpisz "
                "'python -m playwright install chromium' i wybierz Chromium.\n" + "\n".join(errors))
        self._ctx.on("response", self._on_response)
        if self._cookies:
            try:
                self._ctx.add_cookies(self._cookies)
                self._log("Przekazano sesję z okna logowania aplikacji.")
            except Exception as e:
                self._log(f"Nie udało się przekazać sesji: {e}")
        return self._ctx

    def _page(self):
        ctx = self._context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(15000)
        return page

    # ------------------------------------------------------------------ wysłanie (SAVE)
    def _on_response(self, resp):
        try:
            req = resp.request
            if req.method != "POST":
                return
            path = urlparse(req.url).path.rstrip("/")
            if path != "/hunt_sessions":
                return
            status = resp.status
            if status < 400:
                job = self._pending
                self._pending = None
                self._emit("saved", job=job, status=status, location=resp.headers.get("location", ""))
            else:
                self._emit("save_failed", status=status)
        except Exception:
            pass

    # ------------------------------------------------------------------ logowanie / nawigacja
    def _new_url(self):
        return self.settings.base_url.rstrip("/") + "/hunt_sessions/new"

    def _goto_new(self, page, login_timeout=600):
        page.goto(self._new_url(), wait_until="domcontentloaded")
        if self._has_form(page):
            return
        self._log("Zaloguj się w otwartym oknie przeglądarki – czekam (do 10 min)…")
        self._emit("need_login")
        page.bring_to_front()
        deadline = time.time() + login_timeout
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            if self._has_form(page):
                break
            # po zalogowaniu strona zwykle przekierowuje gdzie indziej – wracamy do formularza
            if not page.locator("input[type=password]").count():
                try:
                    page.goto(self._new_url(), wait_until="domcontentloaded")
                except Exception:
                    pass
                if self._has_form(page):
                    break
        else:
            raise TimeoutError("Nie doczekałem się zalogowania.")
        self._log("Zalogowano – formularz gotowy.")
        self._emit("logged_in")
        try:
            host = urlparse(self.settings.base_url).hostname or ""
            self._emit("cookies", cookies=[c for c in self._ctx.cookies()
                                           if host.endswith(c.get("domain", "").lstrip("."))])
        except Exception:
            pass

    @staticmethod
    def _has_form(page):
        try:
            page.wait_for_selector(SEL_NAME, timeout=3000, state="attached")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ templaty
    def _open_template_selector(self, page):
        """Klik w 'Choose a hunt template' podmienia turbo-frame#hunt_template_selector
        na div[data-controller=template-selector] z listą przycisków-opcji."""
        box = page.locator(SEL_TPL_BOX)
        if not box.count():
            link = page.locator(SEL_TEMPLATE_LINK).first
            if not link.count():
                raise RuntimeError("Nie znalazłem linku 'Choose a hunt template'.")
            link.click()
            box.first.wait_for(state="attached", timeout=15000)
        page.wait_for_selector(SEL_TPL_INPUT, state="attached", timeout=15000)
        self._dump(page, "template_selector.html")

    def _dump(self, page, name):
        """Zapisuje HTML selektora templatów – przydatne, gdyby strona zmieniła strukturę."""
        try:
            d = app_dir() / "debug"
            d.mkdir(exist_ok=True)
            html = page.evaluate("s => { const f = document.querySelector(s); return f ? f.outerHTML : ''; }",
                                 SEL_TPL_BOX)
            (d / name).write_text(html or "", encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _template_options(page) -> list[dict]:
        return page.evaluate(
            """s => [...document.querySelectorAll(s)].map(b => ({
                   id: b.dataset.templateId || '',
                   title: (b.dataset.templateTitle || b.textContent || '').replace(/\\s+/g, ' ').trim()}))""",
            SEL_TPL_OPTION)

    def _read_templates(self, page) -> list[dict]:
        """Otwiera selektor i zwraca aktualne templaty [{id, title}] (bez "No Template")."""
        self._open_template_selector(page)
        out, seen = [], set()
        for o in self._template_options(page):
            k = o["title"].lower()
            if o["id"] and o["title"] and k not in seen:
                seen.add(k)
                out.append(o)
        self._log(f"Lista templatów odświeżona: {len(out)}"
                  + (" (szczegóły HTML w folderze debug)" if not out else ""))
        self._emit("templates", names=[o["title"] for o in out])
        return out

    def _do_list_templates(self):
        page = self._page()
        self._goto_new(page)
        self._read_templates(page)
        # zamknij selektor przeładowaniem strony – nic nie zostaje wysłane
        page.goto(self._new_url(), wait_until="domcontentloaded")

    def _choose_template(self, page, opt) -> bool:
        """Wybiera template (selektor jest już otwarty) i czeka, aż strona go wczyta."""
        self._log(f"Wybieram template: {opt['title']}")

        # znacznik na starym polu nazwy: pozwoli zauważyć, że formularz został podmieniony
        page.evaluate("s => document.querySelector(s)?.setAttribute('data-thu-old', '1')", SEL_NAME)
        button = page.locator(f'{SEL_TPL_OPTION}[data-template-id="{opt["id"]}"]').first
        with page.expect_response(lambda r: TPL_RESPONSE in r.url, timeout=20000) as resp_info:
            try:
                page.locator(SEL_TPL_INPUT).click()          # otwiera listę (jak użytkownik)
                button.wait_for(state="visible", timeout=3000)
                button.click()
            except Exception:
                # awaryjnie: to samo, co robi kontroler strony – ustaw template_id i wyślij formularz selektora
                self._log("Lista templatów się nie otworzyła – wybieram template bezpośrednio.")
                page.evaluate(
                    """([h, f, id]) => { document.querySelector(h).value = id;
                                         document.querySelector(f).requestSubmit(); }""",
                    [SEL_TPL_HIDDEN, SEL_TPL_FORM, opt["id"]])
        resp = resp_info.value
        if resp.status >= 400:
            raise RuntimeError(f"Strona nie wczytała templatu (HTTP {resp.status}).")

        # czekamy, aż Turbo podmieni formularz
        start = time.time()
        replaced = False
        while time.time() - start < 10:
            page.wait_for_timeout(250)
            replaced = page.evaluate(
                "s => { const e = document.querySelector(s); return !!e && !e.hasAttribute('data-thu-old'); }", SEL_NAME)
            if replaced:
                break
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_selector(SEL_NAME, timeout=15000)
        page.wait_for_timeout(500)
        self._log("Template wczytany" + (" (formularz został przeładowany)." if replaced else "."))
        return True


    # ------------------------------------------------------------------ preye
    @staticmethod
    def _click(loc, timeout=3000):
        """Zwykły klik; gdy element jest niewidoczny/zasłonięty – klik z poziomu strony (to samo zdarzenie)."""
        try:
            loc.click(timeout=timeout)
        except Exception:
            loc.evaluate("e => e.click()")

    def _prey_state(self, card) -> dict:
        return card.evaluate("""c => ({
            prey: (c.querySelector('[data-role="prey-id"]') || {}).value || '',
            level: (c.querySelector('[data-role="prey-level"]') || {}).value || '',
            creature: (c.querySelector('[data-role="creature-id"]') || {}).value || ''})""")

    def _clear_prey(self, page, card):
        if self._prey_state(card)["prey"]:
            self._click(card.locator('[data-role="prey-clear"]').first)
            page.wait_for_timeout(200)

    def _pick_creature(self, page, card, name) -> bool:
        """Okno wyboru potwora ładuje się dynamicznie – jego budowy nie znamy na pewno.
        Szukamy pola wyszukiwania i elementu o dokładnie takiej nazwie."""
        btn = card.locator('[data-role="prey-creature-button"]').first
        modal = page.locator(SEL_PREY_CREATURE_MODAL)
        self._click(btn)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if modal.inner_text(timeout=500).strip():
                    break
            except Exception:
                pass
            page.wait_for_timeout(250)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        try:
            (app_dir() / "debug").mkdir(exist_ok=True)
            (app_dir() / "debug" / "prey_creature_modal.html").write_text(
                page.evaluate("s => document.querySelector(s)?.outerHTML || ''", SEL_PREY_CREATURE_MODAL),
                encoding="utf-8")
        except Exception:
            pass
        search = modal.locator("input[type=search], input[type=text]")
        if search.count():
            search.first.fill(name)
            page.wait_for_timeout(700)
        marked = page.evaluate(r"""([s, name]) => {
            const root = document.querySelector(s); if (!root) return false;
            const norm = t => (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
            const want = norm(name);
            const els = [...root.querySelectorAll('*')].filter(e =>
                norm(e.dataset.creatureName) === want || norm(e.getAttribute('title')) === want ||
                norm(e.getAttribute('alt')) === want || norm(e.textContent) === want);
            if (!els.length) return false;
            const deepest = els.filter(e => !els.some(o => o !== e && e.contains(o)))[0] || els[0];
            const target = deepest.closest('button, a, [data-action], [role=option], li, label') || deepest;
            document.querySelectorAll('[data-thu-creature]').forEach(e => e.removeAttribute('data-thu-creature'));
            target.setAttribute('data-thu-creature', '1');
            return true;
        }""", [SEL_PREY_CREATURE_MODAL, name])
        if marked:
            self._click(page.locator("[data-thu-creature]").first)
            for _ in range(20):
                page.wait_for_timeout(250)
                if self._prey_state(card)["creature"]:
                    return True
        # zamknij okno, jeśli zostało otwarte
        page.keyboard.press("Escape")
        page.evaluate("s => { const m = document.querySelector(s); if (m) m.innerHTML = ''; }", SEL_PREY_CREATURE_MODAL)
        return False

    def _set_preys(self, page, job: FillJob) -> list[str]:
        """Ustawia preye. Zwraca linie do opisu dla preyów, których potwora nie udało się wybrać."""
        if not job.preys:
            return []
        cards = page.locator(SEL_PREY_CARD)
        n = cards.count()
        if n < len(job.preys):
            raise RuntimeError(f"Na stronie są tylko {n} sloty na preye.")
        had = [i for i in range(n) if self._prey_state(cards.nth(i))["prey"]]
        if had:
            self._log(f"Template miał preye (sloty: {', '.join(str(i + 1) for i in had)}) – zastępuję je preyami z programu.")
        for i in range(n):
            self._clear_prey(page, cards.nth(i))

        notes = []
        for i, p in enumerate(job.preys):
            card = cards.nth(i)
            ptype, level, creature = p["type"], int(p.get("level") or 1), (p.get("creature") or "").strip()
            self._click(card.locator('[data-role="prey-icon-button"]').first)
            option = page.locator(f'{SEL_PREY_TYPE_MODAL} button[data-prey-name="{ptype}"]').first
            try:
                option.wait_for(state="visible", timeout=3000)
            except Exception:
                pass
            self._click(option)
            page.wait_for_timeout(250)
            self._click(card.locator(f'[data-role="prey-star"][data-level="{level}"]').first)
            page.wait_for_timeout(200)
            st = self._prey_state(card)
            if not st["prey"] or st["level"] != str(level):
                raise RuntimeError(f"Nie udało się ustawić preya {i + 1} ({ptype} {level}★).")
            label = f"Prey: {ptype} {level}★" + (f" – {creature}" if creature else "")
            if creature and not self._pick_creature(page, card, creature):
                self._log(f"Nie udało się wybrać potwora „{creature}” dla preya {i + 1} – "
                          "dopisuję go do opisu (HTML okna zapisany w folderze debug).")
                notes.append(label)
            else:
                self._log(f"Ustawiono {label}.")
        return notes

    def _check_preys(self, page, job: FillJob) -> bool:
        cards = page.locator(SEL_PREY_CARD)
        for i, p in enumerate(job.preys):
            st = self._prey_state(cards.nth(i))
            if not st["prey"] or st["level"] != str(int(p.get("level") or 1)):
                return False
        return True

    # ------------------------------------------------------------------ opis
    @staticmethod
    def _compose_description(base: str, lines: list[str]) -> str:
        base = (base or "").rstrip()
        add = [l for l in lines if l and l not in base]
        return "\n".join(([base] if base else []) + add)

    # ------------------------------------------------------------------ wypełnianie
    def _apply_description(self, page):
        if self._description is not None:
            page.locator(SEL_DESC).fill(self._description)

    def _set_fields(self, page, job: FillJob):
        page.locator(SEL_NAME).fill(job.hunt_name)
        for sel, val in ((SEL_PARTY, job.party), (SEL_HUNT, job.hunt), (SEL_INPUT, job.input)):
            if val:
                page.locator(sel).fill(val)
        if job.character:
            sel = page.locator(SEL_CHAR)
            current = sel.evaluate("s => s.selectedOptions[0] ? s.selectedOptions[0].textContent.trim() : ''")
            # zmieniamy tylko, gdy trzeba: zmiana postaci przeładowuje WoD/sety/preye (np. z templatu)
            if current.strip().lower() != job.character.strip().lower():
                labels = sel.evaluate("s => [...s.options].map(o => o.textContent.trim())")
                match = next((l for l in labels if l.lower() == job.character.strip().lower()), None)
                if not match:
                    raise RuntimeError(f"Postaci „{job.character}” nie ma na liście.")
                sel.select_option(label=match)
                self._log(f"Wybrano postać: {match}")
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
        page.locator(SEL_PUBLIC).set_checked(bool(job.make_public))

    def _check_fields(self, page, job: FillJob) -> list[str]:
        bad = []
        norm = lambda s: re.sub(r"\s+", " ", (s or "")).strip()
        if norm(page.locator(SEL_NAME).input_value()) != norm(job.hunt_name):
            bad.append("nazwa")
        for label, sel, val in (("Party", SEL_PARTY, job.party), ("Hunt", SEL_HUNT, job.hunt), ("Input", SEL_INPUT, job.input)):
            if val and norm(page.locator(sel).input_value()) != norm(val):
                bad.append(label)
        if page.locator(SEL_PUBLIC).is_checked() != bool(job.make_public):
            bad.append("public")
        if self._description is not None and norm(page.locator(SEL_DESC).input_value()) != norm(self._description):
            bad.append("opis")
        return bad

    def _character_options(self, page) -> list[str]:
        return page.locator(SEL_CHAR).evaluate("s => [...s.options].map(o => o.textContent.trim()).filter(Boolean)")

    def _decide_template(self, job: FillJob, options: list[dict]):
        """Zwraca opcję templatu albo None. Rzuca TemplateMissing, gdy ręcznie wybranego już nie ma."""
        if not job.use_templates:
            return None
        titles = [o["title"] for o in options]
        if job.template_manual:
            name = job.template
            if name and name.lower() not in (t.lower() for t in titles):
                raise TemplateMissing(name)
        else:
            name = find_template(job.match_names[0], job.match_names[1], titles)
            if (name or "").lower() != (job.template or "").lower():
                self._log(f"Po odświeżeniu listy pasuje inny template: „{name or 'bez templatu'}” "
                          f"(w oknie było: „{job.template or 'bez templatu'}”) – używam nowego.")
        if not name:
            return None
        return next(o for o in options if o["title"].lower() == name.lower())

    def _do_fill(self, job: FillJob):
        page = self._page()
        page.bring_to_front()
        self._goto_new(page)

        # postać sprawdzamy na początku – jeśli jej nie ma, nic nie wypełniamy
        if job.character:
            chars = self._character_options(page)
            if job.character.strip().lower() not in (c.lower() for c in chars):
                msg = f"Postaci „{job.character}” nie ma na stronie. Dostępne: {', '.join(chars) or 'brak'}."
                self._log("✖ " + msg + " Formularz nie został wypełniony.")
                self._emit("fill_aborted", msg=msg)
                return

        # zawsze świeża lista templatów
        options = self._read_templates(page)
        try:
            opt = self._decide_template(job, options)
        except TemplateMissing as e:
            msg = f"Wybranego ręcznie templatu „{e}” nie ma już na stronie. Wybierz inny template w programie."
            self._log("✖ " + msg + " Formularz nie został wypełniony.")
            page.goto(self._new_url(), wait_until="domcontentloaded")   # zamyka otwarty selektor
            self._emit("fill_aborted", msg=msg)
            return
        self._emit("template_used", name=opt["title"] if opt else None)
        used_template = self._choose_template(page, opt) if opt else False

        self._set_fields(page, job)          # najpierw postać (jej zmiana przeładowuje preye)
        notes = self._set_preys(page, job)
        self._description = None
        extra = [l for l in (job.description_extra, *notes) if l]
        if extra:
            self._description = self._compose_description(page.locator(SEL_DESC).input_value(), extra)
        self._apply_description(page)
        for attempt in range(3):
            page.wait_for_timeout(800)
            bad = self._check_fields(page, job)
            if job.preys and not self._check_preys(page, job):
                bad.append("preye")
            if not bad:
                break
            self._log(f"Pola zostały zmienione przez stronę ({', '.join(bad)}) – wpisuję ponownie.")
            self._set_fields(page, job)
            if "preye" in bad:
                self._set_preys(page, job)
            self._apply_description(page)
        else:
            raise RuntimeError("Nie udało się utrwalić wartości w formularzu.")

        self._pending = job
        self._emit("filled", template_used=used_template, auto_save=job.auto_save)
        if job.auto_save:
            btn = page.locator(SEL_SAVE).last
            btn.scroll_into_view_if_needed()
            self._log("Klikam SAVE…")
            btn.click()
        else:
            page.locator(SEL_NAME).scroll_into_view_if_needed()
            self._log("Formularz wypełniony – sprawdź i kliknij SAVE w przeglądarce.")
