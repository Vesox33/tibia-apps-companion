"""Snajper: obserwowanie zwolnień i automatyczna rezerwacja w chwili otwarcia terminu."""
from __future__ import annotations

import math
import struct
import sys
import uuid
import wave
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QTime, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

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
                if now >= due:
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
    def __init__(self, parent, kind: str, spot_name: str, res: list[Reservation], now: datetime,
                 time_field_cls, preset: tuple[datetime, datetime] | None = None):
        super().__init__(parent)
        self.kind, self.res, self.now = kind, res, now
        self.anchor = None
        self.bounds = (None, None)
        self._last_anchor_id = None
        self.setWindowTitle("Snajpuj termin" if kind == "snipe" else "Obserwuj zwolnienie")
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        title = QLabel(spot_name)
        title.setStyleSheet("font-size:16px; font-weight:800;")
        v.addWidget(title)
        expl = QLabel(
            "Aplikacja zarezerwuje ten termin w pierwszej chwili, gdy strona na to pozwoli "
            "(dokładnie dobę przed jego początkiem). Program musi być wtedy włączony."
            if kind == "snipe" else
            "Gdy w wybranym przedziale zwolni się miejsce, dostaniesz dźwięk i powiadomienie. "
            "Sprawdzam przy każdym odświeżeniu tablicy. Program musi być włączony.")
        expl.setWordWrap(True)
        expl.setStyleSheet("color:#8b8f98;")
        v.addWidget(expl)

        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        row = 0
        g.addWidget(QLabel("Dzień"), row, 0)
        dl = QHBoxLayout()
        self.day_group = QButtonGroup(self)
        for i in range(3):
            d = now.date() + timedelta(days=i)
            b = QPushButton(f"{['Dziś', 'Jutro', 'Pojutrze'][i]}  {d:%d.%m}")
            b.setCheckable(True)
            b.setObjectName("seg")
            self.day_group.addButton(b, i)
            dl.addWidget(b)
        dl.addStretch(1)
        g.addLayout(dl, row, 1)
        row += 1

        self.t1 = time_field_cls()
        self.t1.setFixedWidth(84)
        self.t1.setObjectName("bigtime")
        self.t2 = time_field_cls()
        self.t2.setFixedWidth(84)
        self.t2.setObjectName("bigtime")
        self.len_combo = QComboBox()
        lens = list(range(15, 181, 15))
        for m in lens:
            self.len_combo.addItem(fmt_len(m), m)

        if kind == "snipe":
            g.addWidget(QLabel("Początek"), row, 0)
            g.addWidget(self.t1, row, 1)
            row += 1
            g.addWidget(QLabel("Długość"), row, 0)
            g.addWidget(self.len_combo, row, 1)
            row += 1
            # domyślnie: minutę po końcu pierwszej rezerwacji, której koniec wypada za limitem doby
            lim = start_limit(now)
            start = ceil_minutes(lim + timedelta(minutes=1), 5)
            for r in sorted(res, key=lambda r: r.end):
                if r.end + timedelta(minutes=1) > lim:
                    start = r.end + timedelta(minutes=1)
                    break
            if preset and preset[0] and preset[0] > lim:
                start = preset[0]
            self.follow = QCheckBox("")
            self.follow.setChecked(True)
            self.follow.setStyleSheet("font-weight:600;")
            g.addWidget(self.follow, row, 1)
            row += 1
            # widełki – gdy tamta rezerwacja zniknie/skróci się
            self.range_box = QWidget()
            rb = QVBoxLayout(self.range_box)
            rb.setContentsMargins(22, 0, 0, 0)
            rb.setSpacing(6)
            rl = QLabel("Jeśli ją skróci, przesunie lub usunie, snajp się dostosuje. Interesuje mnie tylko:")
            rl.setWordWrap(True)
            rl.setStyleSheet("color:#8b8f98;")
            rb.addWidget(rl)
            r1 = QHBoxLayout()
            self.floor_on = QCheckBox("najwcześniej od")
            self.floor_t = time_field_cls()
            self.floor_t.setFixedWidth(70)
            self.floor_t.setObjectName("timefield")
            r1.addWidget(self.floor_on)
            r1.addWidget(self.floor_t)
            r1.addSpacing(16)
            self.cap_on = QCheckBox("najpóźniej do")
            self.cap_t = time_field_cls()
            self.cap_t.setFixedWidth(70)
            self.cap_t.setObjectName("timefield")
            r1.addWidget(self.cap_on)
            r1.addWidget(self.cap_t)
            r1.addStretch(1)
            rb.addLayout(r1)
            g.addWidget(self.range_box, row, 1)
            row += 1
            self.floor_t.set_time(QTime(start.hour, start.minute))
            cap0 = start + MAX_LEN
            self.cap_t.set_time(QTime(cap0.hour, cap0.minute))
            for w in (self.floor_t, self.cap_t):
                w.textChanged.connect(self._update)
                w.stepped.connect(lambda m, w=w: self._step(w, m))
            for c in (self.floor_on, self.cap_on, self.follow):
                c.toggled.connect(self._update)
            self._set_day(start)
            self.t1.set_time(QTime(start.hour, start.minute))
            self.len_combo.setCurrentIndex(len(lens) - 1)
        else:
            g.addWidget(QLabel("Od"), row, 0)
            g.addWidget(self.t1, row, 1)
            row += 1
            g.addWidget(QLabel("Do"), row, 0)
            g.addWidget(self.t2, row, 1)
            row += 1
            g.addWidget(QLabel("Min. wolne"), row, 0)
            g.addWidget(self.len_combo, row, 1)
            row += 1
            self.auto = QCheckBox("Od razu zarezerwuj automatycznie (zamiast tylko powiadomić)")
            g.addWidget(self.auto, row, 1)
            row += 1
            s, e = preset if preset else (ceil_minutes(now, 15), ceil_minutes(now, 15) + timedelta(hours=3))
            self._set_day(s)
            self.t1.set_time(QTime(s.hour, s.minute))
            self.t2.set_time(QTime(e.hour, e.minute))
            span = int((e - s).total_seconds() // 60)
            best = max([i for i, m in enumerate(lens) if m <= max(span, 15)] or [0])
            self.len_combo.setCurrentIndex(min(best, 3))   # domyślnie do 1 h
        v.addLayout(g)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-weight:600;")
        v.addWidget(self.info)
        self.bb = QDialogButtonBox()
        self.ok_btn = self.bb.addButton("Snajpuj" if kind == "snipe" else "Obserwuj", QDialogButtonBox.AcceptRole)
        self.ok_btn.setObjectName("primary")
        self.bb.addButton("Anuluj", QDialogButtonBox.RejectRole)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

        for w in (self.t1, self.t2):
            w.textChanged.connect(self._update)
            w.stepped.connect(lambda m, w=w: self._step(w, m))
        self.len_combo.currentIndexChanged.connect(self._update)
        self.day_group.idClicked.connect(lambda _: self._update())
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
        if a and self.kind == "snipe" and any(r.end == a for r in self.res):
            a += timedelta(minutes=1)   # wpisane „00:00” po rezerwacji do 00:00 → 00:01
        b = datetime(day.year, day.month, day.day, t2.hour(), t2.minute()) if t2.isValid() else None
        if a and b and b <= a:
            b += timedelta(days=1)
        return a, b

    def _update(self, *_):
        now = self.now
        a, b = self._times()
        mins = self.len_combo.currentData()
        problem, text = None, ""
        if a is None or (self.kind == "watch" and b is None):
            problem = "Wpisz poprawną godzinę."
        elif self.kind == "snipe":
            e = a + timedelta(minutes=mins)
            nxt = next((r for r in sorted(self.res, key=lambda r: r.start) if r.start > a), None)
            if nxt and nxt.start <= e:
                e = nxt.start - timedelta(minutes=1)
            clash = next((r for r in self.res if a <= r.end and e >= r.start), None)
            anchor = next((r for r in self.res if r.end + timedelta(minutes=1) == a), None)
            self.anchor = anchor
            self.follow.setVisible(anchor is not None)
            self.range_box.setVisible(anchor is not None and self.follow.isChecked())
            self.floor_t.setEnabled(self.floor_on.isChecked())
            self.cap_t.setEnabled(self.cap_on.isChecked())
            if anchor and anchor.id != self._last_anchor_id:
                self._last_anchor_id = anchor.id
                if not self.floor_on.isChecked():
                    self.floor_t.set_time(QTime(anchor.start.hour, anchor.start.minute))
            if anchor:
                self.follow.setText(f"Podążaj za rezerwacją {anchor.who} ({anchor.label})")
                self.follow.setToolTip("Jeśli ją skróci, przesunie lub usunie, snajp się dostosuje.")
            following = anchor is not None and self.follow.isChecked()
            floor, cap = self._bounds(a, anchor) if following else (None, None)
            if cap:
                e = min(e, cap)
            self.bounds = (floor, cap)
            if cap and floor and cap - floor < timedelta(minutes=15):
                problem = "Widełki są za wąskie."
            elif cap and cap <= a:
                problem = "„Najpóźniej do” musi być po początku snajpa."
            elif a <= start_limit(now):
                problem = "Ten termin można już zarezerwować normalnie – nie trzeba snajpić."
            elif clash or e <= a:
                problem = f"Koliduje z rezerwacją {clash.label} ({clash.who})." if clash else "Brak miejsca."
            else:
                ft = fire_time(a)
                text = (f"Zarezerwuję {daytxt(a, now)} {a:%H:%M}–{e:%H:%M} ({fmt_len(int((e - a).total_seconds() // 60))}).<br>"
                        f"Strzał: <b>{daytxt(ft, now)} o {ft:%H:%M}</b>"
                        + (f" — przycięte do {e:%H:%M}, bo potem jest rezerwacja {nxt.who}" if nxt and nxt.start - timedelta(minutes=1) == e else ""))
                if following:
                    lo = floor or anchor.start
                    text += (f"<br><span style='color:#8b8f98; font-weight:400'>Gdy {anchor.who} skróci lub usunie rezerwację: "
                             f"najwcześniej {daytxt(lo, now)} {lo:%H:%M}"
                             + (f", najpóźniej do {daytxt(cap, now)} {cap:%H:%M}" if cap else "") + "</span>")
                self.result = {"kind": "snipe", "start": iso(a), "end": iso(e), "want_len": mins}
        else:
            if b <= now:
                problem = "Ten przedział już minął."
            elif (b - a).total_seconds() / 60 < mins:
                problem = "Przedział jest krótszy niż minimalny wolny czas."
            else:
                text = (f"Szukam co najmniej {fmt_len(mins)} wolnego między {daytxt(a, now)} {a:%H:%M} "
                        f"a {daytxt(b, now)} {b:%H:%M}.")
                self.result = {"kind": "watch", "from": iso(a), "to": iso(b), "min_len": mins}
        if problem:
            self.info.setText(f"<span style='color:#ef4444'>✕ {problem}</span>")
            self.ok_btn.setEnabled(False)
        else:
            self.info.setText(f"<span style='color:#22c55e'>✓</span> {text}")
            self.ok_btn.setEnabled(True)

    def job(self) -> dict:
        j = dict(self.result)
        if self.kind == "watch":
            j["auto"] = self.auto.isChecked()
        elif self.anchor is not None and self.follow.isChecked():
            j.update(follow=True, anchor_id=self.anchor.id, anchor_start=iso(self.anchor.start))
            floor, cap = self.bounds
            if floor:
                j["floor"] = iso(floor)
            if cap:
                j["cap"] = iso(cap)
        return j
