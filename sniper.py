"""Snajper: obserwowanie zwolnień i automatyczna rezerwacja w chwili otwarcia terminu."""
from __future__ import annotations

import math
import struct
import sys
import uuid
import wave
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QLabel, QPushButton, QSizePolicy, QWidget)

import theme as T
from parser import Reservation
from slots import HORIZON, MAX_LEN, booking_range, ceil_minutes, free_windows, start_limit

PREFETCH_BEFORE = timedelta(seconds=25)  # świeże dane tuż przed strzałem
RETRY_EVERY = timedelta(seconds=5)   # gdy serwer jeszcze nie przyjmuje terminu
MAX_ATTEMPTS = 20                    # razem ~100 s prób (pokrywa też regułę „minuta później”), potem rezygnacja


def fire_time(start: datetime) -> datetime:
    """Strzał równo dobę przed początkiem (rezerwacja od 00:01 → strzał o 00:01 dzień wcześniej).
    Gdyby serwer przyjmował dopiero minutę później, ponawianie to pokryje."""
    return start - HORIZON


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M")


def fmt_len(mins: int) -> str:
    h, m = divmod(int(mins), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def daytxt(d: datetime, now: datetime) -> str:
    diff = (d.date() - now.date()).days
    return {0: "dziś", 1: "jutro", 2: "pojutrze"}.get(diff, f"{d:%d.%m}")


# ---------------------------------------------------------------- dźwięk
def make_alarm_wav(path: Path, volume: int = 80):
    """Generuje dźwięk alarmu z zadaną głośnością (0–100)."""
    amp = 0.9 * (max(0, min(100, volume)) / 100) ** 2   # kwadrat ≈ odczucie głośności
    rate = 22050
    frames = bytearray()
    for freq, dur in ((880, 0.14), (0, 0.06), (1175, 0.14), (0, 0.06), (1568, 0.26)):
        n = int(rate * dur)
        for i in range(n):
            env = min(1.0, i / 300, (n - i) / 600)
            v = amp * env * math.sin(2 * math.pi * freq * i / rate) if freq else 0.0
            frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def play_alarm(path: Path, times: int = 3):
    try:
        if sys.platform == "win32":
            import winsound
            import threading

            def run():
                for _ in range(times):
                    winsound.PlaySound(str(path), winsound.SND_FILENAME)
            threading.Thread(target=run, daemon=True).start()
            return
    except Exception:
        pass
    QApplication.beep()


# ---------------------------------------------------------------- logika
class Sniper(QObject):
    changed = Signal()

    def __init__(self, cfg, post, get_res, notify, me, clock, prefetch=lambda: None):
        """post(sid, start, end, cb(ok, msg)); get_res(sid) -> list[Reservation];
        notify(title, text, job, hit); me() -> nazwa postaci."""
        super().__init__()
        self.cfg = cfg
        self.post = post
        self.get_res = get_res
        self.notify = notify
        self.me = me
        self.clock = clock
        self.prefetch = prefetch
        self.jobs: list[dict] = cfg.data.setdefault("snipes", [])
        self._inflight: set[str] = set()

    def save(self):
        self.cfg.save()
        self.changed.emit()

    def add(self, job: dict):
        job.setdefault("id", uuid.uuid4().hex[:8])
        job.setdefault("status", "active")
        job.setdefault("msg", "")
        job.setdefault("attempts", 0)
        self.jobs.append(job)
        self.save()

    def remove(self, jid: str):
        self.jobs[:] = [j for j in self.jobs if j["id"] != jid]
        self.save()

    def active(self) -> list[dict]:
        return [j for j in self.jobs if j["status"] == "active"]

    # wywoływane co sekundę
    def tick(self, now: datetime):
        dirty = False
        for j in self.active():
            if j["kind"] == "snipe":
                if j["id"] in self._inflight:
                    continue
                if not j.get("next_try") and self.retarget(j, now):
                    dirty = True
                ft = fire_time(dt(j["start"]))
                if j.get("follow") and not j.get("prefetched") and timedelta(0) < ft - now <= PREFETCH_BEFORE:
                    j["prefetched"] = True
                    self.prefetch()
                due = datetime.fromisoformat(j["next_try"]) if j.get("next_try") else fire_time(dt(j["start"]))
                if now >= due and j.get("notify_only"):
                    self._opened(j)
                    dirty = True
                elif now >= due:
                    self._fire(j, now)
                elif dt(j["start"]) < now:
                    j["status"], j["msg"] = "expired", "Termin minął."
                    dirty = True
            elif j["kind"] == "watch" and dt(j["to"]) <= now:
                j["status"], j["msg"] = "expired", "Przedział minął."
                dirty = True
        if dirty:
            self.save()

    def retarget(self, j, now: datetime) -> bool:
        """Snajp „za rezerwacją”: początek = minuta po końcu tamtej rezerwacji.
        Skróci ją → strzelamy wcześniej; wydłuży/przesunie → później; usunie → od jej dawnego początku.
        Zwraca True, jeśli termin się zmienił."""
        if not j.get("follow") or not j.get("anchor_id"):
            return False
        res = self.get_res(j["spot_id"])
        if not res:            # brak danych (np. jeszcze nie odświeżono) – nic nie zmieniaj
            return False
        anchor = next((r for r in res if r.id == j["anchor_id"]), None)
        old, old_end = dt(j["start"]), dt(j["end"])
        if anchor:
            new = anchor.end + timedelta(minutes=1)
            why = f"{anchor.who} ma teraz {anchor.label}"
        else:
            new = max(dt(j["anchor_start"]), ceil_minutes(now, 1))
            why = "rezerwacja, za którą stałeś, została usunięta"
        floor = dt(j["floor"]) if j.get("floor") else None
        cap = dt(j["cap"]) if j.get("cap") else None
        if floor and new < floor:
            new = floor
            why += f"; czekam na Twoje „od {floor:%H:%M}”"
        # długość jak chciałeś, ale bez wchodzenia na kolejną rezerwację i nie dalej niż „do”
        e = new + timedelta(minutes=int(j.get("want_len", 180)))
        if cap:
            e = min(e, cap)
        me = self.me()
        for r in sorted(res, key=lambda r: r.start):
            if r.start > new and r.id != j["anchor_id"] and not (me and me in r.authors):
                e = min(e, r.start - timedelta(minutes=1))
                break
        if (new, e) == (old, old_end):
            return False
        if e - new < timedelta(minutes=15):
            msg = f"W Twoich widełkach nie ma teraz miejsca ({why})."
            if j.get("msg") != msg:
                j["msg"] = msg
                return True
            return False
        j["start"], j["end"] = iso(new), iso(e)
        j["prefetched"] = False
        j["msg"] = f"Przesunięto na {new:%H:%M}–{e:%H:%M} ({why})."
        if new < old:
            self.notify("Snajper: termin przesunięty wcześniej",
                        f"{j['spot_name']}: {why}. Nowy strzał {fire_time(new):%H:%M}.", j, None, sound=False)
        return True

    def on_board(self, now: datetime):
        """Po każdym odświeżeniu: przelicz snajpy „za rezerwacją” i sprawdź obserwacje."""
        changed = False
        for j in self.active():
            if j["kind"] == "snipe" and j["id"] not in self._inflight and not j.get("next_try"):
                changed |= self.retarget(j, now)
        if changed:
            self.save()
        self.check_watches(now)

    def _conflict(self, sid, s, e) -> Reservation | None:
        me = self.me()
        for r in self.get_res(sid):
            if s <= r.end and e >= r.start and not (me and me in r.authors):
                return r
        return None

    def _opened(self, j):
        """Snajp „tylko powiadom”: termin właśnie się otworzył – dźwięk i okno z „Pokaż i wybierz”."""
        s, e = dt(j["start"]), dt(j["end"])
        r = self._conflict(j["spot_id"], s, e)
        j["status"] = "done"
        if r:
            j["msg"] = f"Termin się otworzył, ale jest już zajęty: {r.label} {r.who}"
            self.notify("Termin się otworzył", f"{j['spot_name']}: {j['msg']}", j, None)
        else:
            j["msg"] = f"Termin się otworzył ({s:%d.%m %H:%M}–{e:%H:%M}) – powiadomiono."
            self.notify("Termin się otworzył!", f"{j['spot_name']}: można rezerwować {s:%H:%M}–{e:%H:%M}.", j, (s, e))

    def _fire(self, j, now):
        s, e = dt(j["start"]), dt(j["end"])
        r = self._conflict(j["spot_id"], s, e)
        if r and j["attempts"] == 0:
            j["status"] = "failed"
            j["msg"] = f"Ktoś był szybszy: {r.label} {r.who}"
            self.notify("Snajper: nie udało się", f"{j['spot_name']}: {j['msg']}", j, None)
            self.save()
            return
        self._inflight.add(j["id"])
        j["attempts"] += 1
        j["msg"] = f"Próba {j['attempts']}…"
        self.changed.emit()

        def done(ok, msg, j=j):
            self._inflight.discard(j["id"])
            if ok:
                j["status"], j["msg"] = "done", f"Zarezerwowano {s:%d.%m %H:%M}–{e:%H:%M}"
                self.notify("Snajper: zarezerwowano!", f"{j['spot_name']}  {s:%d.%m %H:%M}–{e:%H:%M}", j, None)
            elif j["attempts"] < MAX_ATTEMPTS:
                j["next_try"] = (self.clock() + RETRY_EVERY).isoformat(timespec="seconds")
                j["msg"] = f"Serwer odrzucił ({msg.splitlines()[0][:80]}), ponawiam…"
            else:
                j["status"], j["msg"] = "failed", msg.splitlines()[0][:120]
                self.notify("Snajper: nie udało się", f"{j['spot_name']}: {j['msg']}", j, None)
            self.save()

        self.post(j["spot_id"], s, e, done)

    # wywoływane po każdym odświeżeniu tablicy
    def check_watches(self, now: datetime):
        dirty = False
        lo, hi = booking_range(now)
        lim = start_limit(now)
        for j in self.active():
            if j["kind"] != "watch" or j["id"] in self._inflight:
                continue
            frm, to, need = dt(j["from"]), dt(j["to"]), timedelta(minutes=int(j["min_len"]))
            hit = None
            for a, b in free_windows(self.get_res(j["spot_id"]), lo, hi, now):
                s, e = max(a, frm), min(b, to)
                if s <= lim and e - s >= need:
                    hit = (s, e)
                    break
            key = iso(hit[0]) if hit else None
            if hit and key != j.get("last_hit"):
                j["last_hit"] = key
                dirty = True
                if j.get("auto"):
                    s = hit[0]
                    e = min(hit[1], s + MAX_LEN)
                    self._inflight.add(j["id"])

                    def done(ok, msg, j=j, s=s, e=e):
                        self._inflight.discard(j["id"])
                        if ok:
                            j["status"], j["msg"] = "done", f"Zarezerwowano {s:%d.%m %H:%M}–{e:%H:%M}"
                            self.notify("Zwolniło się – zarezerwowano!", f"{j['spot_name']}  {s:%H:%M}–{e:%H:%M}", j, None)
                        else:
                            j["msg"] = "Wolne, ale rezerwacja odrzucona: " + msg.splitlines()[0][:80]
                            self.notify("Zwolniło się, ale rezerwacja się nie udała", f"{j['spot_name']}: {msg}", j, (s, e))
                        self.save()
                    self.post(j["spot_id"], s, e, done)
                else:
                    j["msg"] = f"WOLNE: {hit[0]:%H:%M}–{hit[1]:%H:%M}"
                    self.notify("Zwolniło się!", f"{j['spot_name']}: wolne {hit[0]:%H:%M}–{hit[1]:%H:%M}", j, hit)
            elif not hit and j.get("last_hit"):
                j["last_hit"] = None
                j["msg"] = "Znów zajęte – czekam dalej."
                dirty = True
        if dirty:
            self.save()


# ---------------------------------------------------------------- okno tworzenia
class SnipeDialog(QDialog):
    """Jedno okno „Pilnuj terminu”. Tryb wybiera się sam z godziny:
    - termin jeszcze zamknięty (dalej niż doba) → snajp: rezerwacja (albo samo powiadomienie) w sekundzie otwarcia,
    - termin już otwarty, ale zajęty → obserwacja: powiadomienie (albo rezerwacja), gdy ktoś go zwolni."""

    def __init__(self, parent, kind: str, spot_name: str, res: list[Reservation], now: datetime,
                 time_field_cls, preset: tuple[datetime, datetime] | None = None):
        super().__init__(parent)
        self.res, self.now = res, now
        self.kind = "snipe"
        self.anchor = None
        self.bounds = (None, None)
        self._last_anchor_id = None
        self._auto_touched = False
        self.result = None
        self.setWindowTitle("Pilnuj terminu")
        self.setMinimumWidth(T.S6 * 24)
        v = T.vbox(self, (T.S6, T.S6, T.S6, T.S6), T.S4)
        head = T.vbox(spacing=T.S1)
        head.addWidget(T.label("Pilnuj terminu · " + spot_name, "title", wrap=True))
        head.addWidget(T.label("Termin, który jeszcze się nie otworzył, zarezerwuję w sekundzie otwarcia (dobę "
                               "wcześniej). Zajęty termin będę obserwować i dam znać, gdy ktoś go zwolni. "
                               "Program musi być wtedy włączony.", "muted", wrap=True))
        v.addLayout(head)

        g = QGridLayout()
        g.setHorizontalSpacing(T.S3)
        g.setVerticalSpacing(T.S3)
        g.setColumnMinimumWidth(0, T.S6 * 4)
        row = 0

        def lab(text):
            l = T.label(text, "muted")
            l.setFixedHeight(T.CONTROL_H)
            return l
        g.addWidget(lab("Dzień"), row, 0, Qt.AlignTop)
        dl = T.hbox(spacing=T.S2)
        self.day_group = QButtonGroup(self)
        for i in range(3):
            d = now.date() + timedelta(days=i)
            b = QPushButton(f"{['Dziś', 'Jutro', 'Pojutrze'][i]} {d:%d.%m}")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("seg")
            self.day_group.addButton(b, i)
            dl.addWidget(b)
        dl.addStretch(1)
        g.addLayout(dl, row, 1)
        row += 1

        self.t1 = time_field_cls()
        self.t2 = time_field_cls()
        g.addWidget(lab("Godziny"), row, 0, Qt.AlignTop)
        tl = T.hbox(spacing=T.S2)
        tl.addWidget(self.t1)
        tl.addWidget(T.label("–", "muted"))
        tl.addWidget(self.t2)
        tl.addStretch(1)
        g.addLayout(tl, row, 1)
        row += 1

        self.len_lab = lab("Min. wolne")
        self.len_combo = QComboBox()
        self.len_combo.setFixedWidth(T.S6 * 5)
        lens = list(range(15, 181, 15))
        for m in lens:
            self.len_combo.addItem(fmt_len(m), m)
        self.len_combo.setToolTip("Ile co najmniej musi się zwolnić, żeby dać znać")
        g.addWidget(self.len_lab, row, 0, Qt.AlignTop)
        g.addWidget(self.len_combo, row, 1, Qt.AlignLeft)
        row += 1

        self.auto = QCheckBox("Zarezerwuj automatycznie")
        self.auto.clicked.connect(self._auto_clicked)
        g.addWidget(self.auto, row, 1)
        row += 1

        self.follow = QCheckBox("")
        self.follow.setChecked(True)
        g.addWidget(self.follow, row, 1)
        row += 1
        # widełki – gdy rezerwacja przed nim zniknie/skróci się
        self.range_box = QWidget()
        rb = T.vbox(self.range_box, (T.S6, 0, 0, 0), T.S2)
        rb.addWidget(T.label("Jeśli ją skróci, przesunie lub usunie, dostosuję się. Interesuje mnie tylko:",
                             "hint", wrap=True))
        bg = QGridLayout()              # dwa wiersze: pola godzin w jednej kolumnie
        bg.setHorizontalSpacing(T.S2)
        bg.setVerticalSpacing(T.S2)
        self.floor_on = QCheckBox("najwcześniej od")
        self.floor_t = time_field_cls()
        self.cap_on = QCheckBox("najpóźniej do")
        self.cap_t = time_field_cls()
        bg.addWidget(self.floor_on, 0, 0)
        bg.addWidget(self.floor_t, 0, 1)
        bg.addWidget(self.cap_on, 1, 0)
        bg.addWidget(self.cap_t, 1, 1)
        bg.setColumnStretch(2, 1)
        rb.addLayout(bg)
        g.addWidget(self.range_box, row, 1)
        row += 1
        g.setColumnStretch(1, 1)
        v.addLayout(g)

        # domyślnie: termin, który otworzy się najwcześniej – minutę po końcu pierwszej rezerwacji za limitem doby
        lim = start_limit(now)
        start = ceil_minutes(lim + timedelta(minutes=1), 5)
        for r in sorted(res, key=lambda r: r.end):
            if r.end + timedelta(minutes=1) > lim:
                start = r.end + timedelta(minutes=1)
                break
        end = start + MAX_LEN
        if preset and preset[0] and preset[1]:
            start, end = preset
        self._set_day(start)
        self.t1.set_time(QTime(start.hour, start.minute))
        self.t2.set_time(QTime(end.hour, end.minute))
        self.len_combo.setCurrentIndex(3)            # 1 h
        self.floor_t.set_time(QTime(start.hour, start.minute))
        cap0 = start + MAX_LEN
        self.cap_t.set_time(QTime(cap0.hour, cap0.minute))

        panel = QFrame()
        panel.setObjectName("panel")
        pl = T.hbox(panel, (T.S4, T.S3, T.S4, T.S3), T.S3)
        self.info_icon = QLabel()
        self.info_icon.setFixedSize(T.ICON, T.ICON)
        pl.addWidget(self.info_icon, 0, Qt.AlignTop)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        pl.addWidget(self.info, 1)
        panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        v.addWidget(panel)
        v.addStretch(1)
        bb = T.hbox(spacing=T.S2)
        bb.addStretch(1)
        cancel = T.button("Anuluj")
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)
        self.ok_btn = T.button("Pilnuj", kind="primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        bb.addWidget(self.ok_btn)
        v.addLayout(bb)

        for w in (self.t1, self.t2, self.floor_t, self.cap_t):
            w.textChanged.connect(self._update)
            w.stepped.connect(lambda m, w=w: self._step(w, m))
        for c in (self.floor_on, self.cap_on, self.follow):
            c.toggled.connect(self._update)
        self.len_combo.currentIndexChanged.connect(self._update)
        self.day_group.idClicked.connect(lambda _: self._update())
        self._update()

    def _auto_clicked(self):
        self._auto_touched = True      # od teraz wybór użytkownika, a nie domyślny dla trybu
        self._update()

    def _set_day(self, d: datetime):
        i = max(0, min(2, (d.date() - self.now.date()).days))
        self.day_group.button(i).setChecked(True)

    def _step(self, w, mins):
        t = QTime.fromString(w.text(), "HH:mm")
        if t.isValid():
            w.set_time(t.addSecs(mins * 60))
            self._update()

    def _bounds(self, a: datetime, anchor):
        """Widełki: „od” = ostatnie wystąpienie tej godziny przed początkiem snajpa,
        „do” = pierwsze wystąpienie tej godziny po „od” (albo po początku tamtej rezerwacji)."""
        floor = cap = None
        if self.floor_on.isChecked():
            t = QTime.fromString(self.floor_t.text(), "HH:mm")
            if t.isValid():
                floor = datetime(a.year, a.month, a.day, t.hour(), t.minute())
                if floor > a:
                    floor -= timedelta(days=1)
        if self.cap_on.isChecked():
            t = QTime.fromString(self.cap_t.text(), "HH:mm")
            if t.isValid():
                base = floor or (anchor.start if anchor else a)
                cap = datetime(base.year, base.month, base.day, t.hour(), t.minute())
                while cap <= base:
                    cap += timedelta(days=1)
        return floor, cap

    def _times(self):
        day = self.now.date() + timedelta(days=max(self.day_group.checkedId(), 0))
        t1 = QTime.fromString(self.t1.text(), "HH:mm")
        t2 = QTime.fromString(self.t2.text(), "HH:mm")
        a = datetime(day.year, day.month, day.day, t1.hour(), t1.minute()) if t1.isValid() else None
        b = datetime(day.year, day.month, day.day, t2.hour(), t2.minute()) if t2.isValid() else None
        if a and b and b <= a:
            b += timedelta(days=1)
        if a and a > start_limit(self.now) and any(r.end == a for r in self.res):
            a += timedelta(minutes=1)   # wpisane „00:00” po rezerwacji do 00:00 → 00:01
        return a, b

    def _update(self, *_):
        now = self.now
        a, b = self._times()
        lim = start_limit(now)
        problem, text = None, ""
        self.result = None
        snipe = bool(a and a > lim)
        self.kind = "snipe" if snipe else "watch"
        if not self._auto_touched:        # domyślnie: zamknięty termin – rezerwuj, otwarty – tylko powiadom
            self.auto.blockSignals(True)
            self.auto.setChecked(snipe)
            self.auto.blockSignals(False)
        self.len_lab.setVisible(not snipe)
        self.len_combo.setVisible(not snipe)
        anchor = None
        if a is None or b is None:
            problem = "Wpisz poprawne godziny."
        elif snipe:
            e = min(b, a + MAX_LEN)
            nxt = next((r for r in sorted(self.res, key=lambda r: r.start) if r.start > a), None)
            if nxt and nxt.start <= e:
                e = nxt.start - timedelta(minutes=1)
            clash = next((r for r in self.res if a <= r.end and e >= r.start), None)
            anchor = next((r for r in self.res if r.end + timedelta(minutes=1) == a), None)
            if anchor and anchor.id != self._last_anchor_id:
                self._last_anchor_id = anchor.id
                if not self.floor_on.isChecked():
                    self.floor_t.set_time(QTime(anchor.start.hour, anchor.start.minute))
            if anchor:
                self.follow.setText(f"Podążaj za rezerwacją {anchor.who} ({anchor.label})")
                self.follow.setToolTip("Jeśli ją skróci, przesunie lub usunie, dostosuję termin.")
            following = anchor is not None and self.follow.isChecked()
            floor, cap = self._bounds(a, anchor) if following else (None, None)
            if cap:
                e = min(e, cap)
            self.bounds = (floor, cap)
            mins = int((e - a).total_seconds() // 60)
            if cap and floor and cap - floor < timedelta(minutes=15):
                problem = "Widełki są za wąskie."
            elif cap and cap <= a:
                problem = "„Najpóźniej do” musi być po początku."
            elif clash or e <= a:
                problem = f"Koliduje z rezerwacją {clash.label} ({clash.who})." if clash else "Brak miejsca."
            elif mins < 15:
                problem = "Za krótko – potrzeba co najmniej 15 minut."
            else:
                ft = fire_time(a)
                what = "Zarezerwuję" if self.auto.isChecked() else "Dam znać o terminie"
                text = (f"Termin otworzy się <span style='font-weight:{T.W_SEMI}'>{daytxt(ft, now)} o {ft:%H:%M}</span>. "
                        f"{what} {daytxt(a, now)} {a:%H:%M}–{e:%H:%M} ({fmt_len(mins)}) w tej sekundzie"
                        + (f" — przycięte do {e:%H:%M}, bo potem jest rezerwacja {nxt.who}"
                           if nxt and nxt.start - timedelta(minutes=1) == e else "") + ".")
                if following:
                    lo = floor or anchor.start
                    text += (f"<br><span style='color:{T.TEXT_2}'>Gdy {anchor.who} skróci lub usunie rezerwację: "
                             f"najwcześniej {daytxt(lo, now)} {lo:%H:%M}"
                             + (f", najpóźniej do {daytxt(cap, now)} {cap:%H:%M}" if cap else "") + "</span>")
                self.result = {"kind": "snipe", "start": iso(a), "end": iso(e), "want_len": mins}
        else:
            mins = self.len_combo.currentData()
            if b <= now:
                problem = "Ten przedział już minął."
            elif (b - a).total_seconds() / 60 < mins:
                problem = "Przedział jest krótszy niż minimalny wolny czas."
            else:
                what = "zarezerwuję go od razu" if self.auto.isChecked() else "dam znać"
                text = (f"Ten termin można już rezerwować. Gdy między {daytxt(a, now)} {a:%H:%M} a "
                        f"{daytxt(b, now)} {b:%H:%M} zwolni się co najmniej {fmt_len(mins)}, {what}. "
                        "Sprawdzam przy każdym odświeżeniu tablicy.")
                self.result = {"kind": "watch", "from": iso(a), "to": iso(b), "min_len": mins}
        self.anchor = anchor if snipe else None
        self.follow.setVisible(snipe and anchor is not None)
        self.range_box.setVisible(snipe and anchor is not None and self.follow.isChecked())
        self.floor_t.setEnabled(self.floor_on.isChecked())
        self.cap_t.setEnabled(self.cap_on.isChecked())
        self.ok_btn.setText(("Zarezerwuj w chwili otwarcia" if self.auto.isChecked() else "Powiadom o otwarciu")
                            if snipe else ("Obserwuj i rezerwuj" if self.auto.isChecked() else "Obserwuj"))
        if problem:
            self.info.setText(f"<span style='color:{T.ERR}'>{problem}</span>")
            self.info_icon.setPixmap(T.pixmap("alert-circle", T.ERR, T.ICON))
            self.ok_btn.setEnabled(False)
        else:
            self.info.setText(text)
            self.info_icon.setPixmap(T.pixmap("target" if snipe else "bell", T.TEXT_2, T.ICON))
            self.ok_btn.setEnabled(True)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """Okno rośnie, gdy pojawi się wiersz albo dłuższy opis – nic się nie ściska ani nie ucina."""
        lay = self.layout()
        need = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        if need > self.height():
            self.resize(self.width(), need)

    def job(self) -> dict:
        j = dict(self.result)
        if j["kind"] == "watch":
            j["auto"] = self.auto.isChecked()
        else:
            if not self.auto.isChecked():
                j["notify_only"] = True
            if self.anchor is not None and self.follow.isChecked():
                j.update(follow=True, anchor_id=self.anchor.id, anchor_start=iso(self.anchor.start))
                floor, cap = self.bounds
                if floor:
                    j["floor"] = iso(floor)
                if cap:
                    j["cap"] = iso(cap)
        return j
