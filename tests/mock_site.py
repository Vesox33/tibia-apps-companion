"""Lokalna atrapa hunt-analyser.com do testów – nic nie trafia na prawdziwą stronę.

Strona /hunt_sessions/new to zapisana kopia formularza "New hunt session" (bez skryptów).
Doklejony skrypt naśladuje zachowanie prawdziwej strony (struktura z zapisanej strony po kliknięciu):
  - klik w "Choose a hunt template" PODMIENIA turbo-frame#hunt_template_selector na
    div[data-controller=template-selector] (pole "Search templates..." + przyciski-opcje),
  - wybór opcji wysyła GET /templates/hunt_sessions/selections/new?template_id=…,
    a odpowiedź PODMIENIA formularz (najgorszy przypadek: czyści wpisane pola),
  - tryb late: template dodatkowo nadpisuje pola z opóźnieniem 1,5 s.
Zapis (POST /hunt_sessions) jest tylko zapamiętywany w pamięci serwera.
"""
from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURE = Path(__file__).parent / "fixtures" / "new_hunt_session.html"
TEMPLATES = {"407": "Buried Cathedral", "2": "Edron Dragon Lair", "3": "Haunted Nexus (Venore)"}

MOCK_JS = r"""
<script>
(function(){
  const late = %LATE%;
  const formFrame = document.querySelector('turbo-frame#hunt_session_form');
  const linkSel = 'a[href*="/templates/hunt_sessions/selections"]';
  const pristine = formFrame.innerHTML;
  document.addEventListener('click', async (ev) => {
    const a = ev.target.closest(linkSel);
    if (a) {                                       // turbo-stream: replace hunt_template_selector
      ev.preventDefault();
      const r = await fetch('/templates/hunt_sessions/selections');
      const html = await r.text();
      await new Promise(r => setTimeout(r, 300));
      document.querySelector('turbo-frame#hunt_template_selector').outerHTML = html;
      document.querySelectorAll(linkSel).forEach(l => l.remove());
      return;
    }
    const box = ev.target.closest('[data-controller="template-selector"]');
    if (!box) return;
    const menu = box.querySelector('[data-template-selector-target="menu"]');
    if (ev.target.matches('[data-template-selector-target="input"]')) { menu.classList.remove('hidden'); return; }
    const opt = ev.target.closest('[data-template-selector-target="option"]');
    if (opt) {                                     // template-selector#choose
      box.querySelector('[data-template-selector-target="hiddenInput"]').value = opt.dataset.templateId;
      box.querySelector('[data-template-selector-target="input"]').value = opt.dataset.templateTitle;
      menu.classList.add('hidden');
      box.querySelector('form').requestSubmit();
    }
  });

  // --- preye (naśladuje kontroler hunt-session-preys)
  let preySlot = null;
  const card = i => document.querySelectorAll('[data-hunt-session-preys-target="card"]')[i];
  const setPrey = (c, id, name) => {
    c.querySelector('[data-role="prey-id"]').value = id;
    c.querySelector('[data-role="prey-name"]').textContent = name || 'Select prey';
    c.querySelectorAll('[data-role="prey-star"]').forEach(b => b.disabled = !id);
    c.querySelector('[data-role="prey-creature-button"]').disabled = !id;
    if (!id) { c.querySelector('[data-role="prey-level"]').value = ''; c.querySelector('[data-role="creature-id"]').value = ''; }
  };
  window.__thuSetPrey = (i, id, name, level, creature) => {
    const c = card(i); setPrey(c, id, name);
    c.querySelector('[data-role="prey-level"]').value = level; c.querySelector('[data-role="creature-id"]').value = creature;
  };
  document.addEventListener('click', async (ev) => {
    const c = ev.target.closest('[data-hunt-session-preys-target="card"]');
    if (ev.target.closest('[data-role="prey-icon-button"]')) {
      preySlot = c; document.querySelector('#prey_type_modal').classList.remove('hidden'); return;
    }
    const t = ev.target.closest('#prey_type_modal button[data-prey-id]');
    if (t) { setPrey(preySlot, t.dataset.preyId, t.dataset.preyName); document.querySelector('#prey_type_modal').classList.add('hidden'); return; }
    if (ev.target.closest('[data-role="prey-clear"]')) { setPrey(c, '', ''); return; }
    const star = ev.target.closest('[data-role="prey-star"]');
    if (star && !star.disabled) { c.querySelector('[data-role="prey-level"]').value = star.dataset.level; return; }
    const cb = ev.target.closest('[data-role="prey-creature-button"]');
    if (cb && !cb.disabled) {
      preySlot = c;
      const r = await fetch('/prey_creatures');
      await new Promise(r => setTimeout(r, 300));
      document.querySelector('#prey_creature_modal').innerHTML = await r.text();
      return;
    }
    const cr = ev.target.closest('#prey_creature_modal [data-creature-id]');
    if (cr) { preySlot.querySelector('[data-role="creature-id"]').value = cr.dataset.creatureId;
              document.querySelector('#prey_creature_modal').innerHTML = ''; }
  });
  document.addEventListener('input', ev => {
    if (!ev.target.matches('#prey_creature_modal input')) return;
    const q = ev.target.value.toLowerCase();
    document.querySelectorAll('#prey_creature_modal [data-creature-id]').forEach(b =>
      b.style.display = b.dataset.creatureName.toLowerCase().includes(q) ? '' : 'none');
  });
  document.addEventListener('submit', async (ev) => {
    const f = ev.target.closest('form[data-template-selector-target="form"]');
    if (!f) return;
    ev.preventDefault();
    const id = new FormData(f).get('template_id');
    const r = await fetch(f.action + '?template_id=' + encodeURIComponent(id));
    const tpl = await r.json();
    await new Promise(r => setTimeout(r, 400));
    formFrame.innerHTML = pristine;                // podmiana formularza => wpisane pola znikają
    document.querySelectorAll(linkSel).forEach(l => l.remove());
    if (!tpl.name) return;
    document.querySelector('#hunt_name').value = tpl.name;
    document.querySelector('#hunt_description').value = 'from template ' + tpl.name;
    window.__thuSetPrey(0, '1', 'Damage Boost', '5', '99');   // template ma prey w slocie 1
    if (late) setTimeout(() => {
      document.querySelector('#hunt_name').value = tpl.name;
      document.querySelector('#hunt_analyser_metadata').value = '';
      document.querySelector('#is_public').checked = !document.querySelector('#is_public').checked;
    }, 1500);
  });
})();
</script>
"""

# Fragment skopiowany ze strony po kliknięciu "Choose a hunt template" (lista zamieniona na atrapę)
TEMPLATE_SELECTOR = """
<div class="mb-4 relative" data-controller="template-selector">
  <label for="hunt_template_selector_input">Choose a hunt template -
    <a href="/templates/hunt_sessions">Edit templates</a></label>
  <form data-turbo-stream="true" data-template-selector-target="form" action="/templates/hunt_sessions/selections/new" accept-charset="UTF-8" method="get">
    <input autocomplete="off" data-template-selector-target="hiddenInput" type="hidden" name="template_id" id="template_id">
    <div class="relative">
      <input id="hunt_template_selector_input" type="text" placeholder="Search templates..." value="No Template" autocomplete="off" class="ha-input w-full pr-10" data-template-selector-target="input">
    </div>
    <div class="ha-dropdown hidden" data-template-selector-target="menu">
      <button type="button" data-template-id="" data-template-title="No Template" data-template-selector-target="option">No Template</button>
      {items}
    </div>
  </form>
</div>
"""


CREATURES = {"1": "Dragon Lord", "2": "Dragon", "3": "Ripper Spectre", "99": "Demon"}
CREATURE_MODAL = """<div class="modal"><h3>Select creature</h3>
<input type="text" placeholder="Search creatures...">
<div class="grid">{items}</div></div>"""


class State:
    def __init__(self):
        self.saved: list[dict] = []
        self.logged_in = True
        self.late = False


def make_handler(state: State):
    page = FIXTURE.read_text(encoding="utf-8")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/hunt_sessions/new":
                if not state.logged_in:
                    return self._send(302, headers={"Location": "/login"})
                body = page.replace("</body>", MOCK_JS.replace("%LATE%", "true" if state.late else "false") + "</body>")
                return self._send(200, body.encode())
            if u.path == "/login":
                if state.logged_in:  # symulacja: użytkownik zalogował się w oknie
                    return self._send(302, headers={"Location": "/"})
                return self._send(200, b'<meta http-equiv="refresh" content="1"><form method="post" action="/login"><input name="u">'
                                       b'<input type="password" name="p"><button>Log in</button></form>')
            if u.path == "/templates/hunt_sessions/selections":
                items = "".join(
                    f'<button type="button" data-template-id="{i}" data-template-title="{html.escape(n)}" '
                    f'data-template-selector-target="option">\n  {html.escape(n)}\n</button>' for i, n in TEMPLATES.items())
                return self._send(200, TEMPLATE_SELECTOR.replace("{items}", items).encode())
            if u.path == "/prey_creatures":
                items = "".join(f'<button type="button" data-creature-id="{i}" data-creature-name="{n}">'
                                f'<img alt="{n}"><span>{n}</span></button>' for i, n in CREATURES.items())
                return self._send(200, CREATURE_MODAL.replace("{items}", items).encode())
            if u.path == "/templates/hunt_sessions/selections/new":
                tid = parse_qs(u.query).get("template_id", [""])[0]
                return self._send(200, json.dumps({"name": TEMPLATES.get(tid, "")}).encode(), "application/json")
            if u.path.startswith("/hunt_sessions/"):
                return self._send(200, b"<h1>Hunt session saved</h1>")
            if u.path == "/":
                return self._send(200, b"<h1>Dashboard</h1>")
            return self._send(404, b"not found")

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            data = parse_qs(self.rfile.read(n).decode(), keep_blank_values=True)
            if u.path == "/login":
                state.logged_in = True
                return self._send(302, headers={"Location": "/"})
            if u.path == "/hunt_sessions":
                state.saved.append({k: v[-1] for k, v in data.items()})
                return self._send(302, headers={"Location": f"/hunt_sessions/{len(state.saved)}"})
            return self._send(404)

    return H


def start(port=0):
    state = State()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    srv, state, url = start(8765)
    print("Atrapa działa:", url + "/hunt_sessions/new")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
