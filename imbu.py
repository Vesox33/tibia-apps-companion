"""Strona „Imbuementy”: kalkulator kosztu Vampirism / Void / Strike – market czy Gold Tokeny, grupa po grupie.

Wpisane ceny są zapamiętywane (config.json → "imbu"), więc przy następnym liczeniu wystarczy
poprawić te, które się zmieniły.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QBoxLayout, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from imbu_data import (DEFAULT_FEES, DURATION_H, IMBUEMENTS, RENAMED, TIERS, TOKENS_PER_GROUP, fmt_gold,
                       groups, parse_gold)
from imbu_icons import SIZE, IconStore

import theme as T

CSS = f"""
QPushButton#imbCard {{ background: {T.SURFACE}; border: none; border-radius: {T.RADIUS_L}px; text-align: left;
                       padding: 0; min-height: 64px; max-height: 64px; }}
QPushButton#imbCard:hover {{ background: {T.SURFACE_2}; }}
QPushButton#imbCard:checked {{ background: {T.SURFACE_3}; }}
QLabel#count {{ background: {T.SURFACE_2}; border-radius: {T.RADIUS}px; font-weight: {T.W_SEMI};
                min-height: {T.CONTROL_H}px; max-height: {T.CONTROL_H}px; }}
"""


# ---------------------------------------------------------------- pomocnicze widżety
class GoldEdit(QLineEdit):
    """Pole ceny: przyjmuje 1500, 1.5k, 45k, 1kk."""

    def __init__(self, placeholder="cena", width=110):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setToolTip("Np. 1500, 1.5k, 45k, 1kk")

    def value(self) -> int | None:
        return parse_gold(self.text())

    def set_value(self, v):
        self.blockSignals(True)
        self.setText("" if v is None else _short(v))
        self.blockSignals(False)


def _short(v: int) -> str:
    """Zapis do pola – krótki i łatwy do poprawienia (np. 1.5k)."""
    if v >= 1_000_000 and v % 10_000 == 0:
        return f"{v / 1_000_000:g}kk"
    if v >= 1_000 and v % 100 == 0:
        return f"{v / 1_000:g}k"
    return str(v)


def cap(text: str) -> QLabel:
    return T.caption(text)


def sep() -> QWidget:
    return T.hairline()


def card(layout_cls=QVBoxLayout, margins=(T.S4, T.S4, T.S4, T.S4), spacing=T.S3):
    f = QFrame()
    f.setObjectName("panel")
    lay = layout_cls(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f, lay


class ImbCard(QPushButton):
    """Kafelek wyboru imbuementu: ikonka, nazwa, opis."""

    def __init__(self, name: str, desc: str):
        super().__init__()
        self.setObjectName("imbCard")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h = T.hbox(self, (T.S3, T.S3, T.S4, T.S3), T.S3)
        self.icon = QLabel()
        self.icon.setFixedSize(SIZE + T.S2, SIZE + T.S2)
        self.icon.setAlignment(Qt.AlignCenter)
        h.addWidget(self.icon)
        v = T.vbox(spacing=0)
        self.name = T.label(name, "strong")
        self.desc = T.label(desc, "hint")
        v.addWidget(self.name)
        v.addWidget(self.desc)
        h.addLayout(v, 1)
        for w in (self.icon, self.name, self.desc):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.toggled.connect(self._style)
        self._style(False)

    def _style(self, on):
        T.set_role(self.name, "text" if on else "muted")


# ---------------------------------------------------------------- strona
class ImbuPage(QWidget):
    def __init__(self, cfg, log=lambda m: None):
        super().__init__()
        self.cfg = cfg
        self.log = log
        st = cfg.data.setdefault("imbu", {})
        st.setdefault("prices", {})
        st.setdefault("price_dates", {})
        st.setdefault("fees", list(DEFAULT_FEES))
        st.setdefault("token", None)
        st.setdefault("last", {"imb": "Vampirism", "tier": 2, "count": 1})
        for old, new in RENAMED.items():
            for k in ("prices", "price_dates"):
                if old in st[k]:
                    st[k].setdefault(new, st[k].pop(old))
        self.st = st
        self._imb = st["last"].get("imb") if st["last"].get("imb") in IMBUEMENTS else "Vampirism"
        self.icons = IconStore(self)
        self.icons.ready.connect(self._icon_ready)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.cfg.save)
        self.setStyleSheet(CSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)   # pasek nie pojawia się/znika przy zmianie poziomu
        body = QWidget()
        sc.setWidget(body)
        outer.addWidget(sc)
        v = T.vbox(body, (0, 0, T.S3, 0), T.S4)

        # --- 1. wybór imbuementu
        self.cards_box = QBoxLayout(QBoxLayout.LeftToRight)
        self.cards_box.setSpacing(T.S3)
        self.imb_group = QButtonGroup(self)
        self.cards: dict[str, ImbCard] = {}
        for name, (desc, _g) in IMBUEMENTS.items():
            c = ImbCard(name, desc)
            c.icon.setPixmap(self.icons.get("imb", name))
            c.clicked.connect(lambda _=False, n=name: self._pick(n))
            self.imb_group.addButton(c)
            self.cards_box.addWidget(c)
            self.cards[name] = c
        v.addLayout(self.cards_box)

        # --- 2. ustawienia: poziom, ilość, token, opłata
        f, opt = card(QHBoxLayout, spacing=0)

        def field(caption_lbl, *widgets):
            box = T.vbox(spacing=T.S2)
            box.addWidget(caption_lbl)
            row = T.hbox(spacing=T.S2)
            for w in widgets:
                row.addWidget(w)
            row.addStretch(1)
            box.addLayout(row)
            return box

        self.tier_group = QButtonGroup(self)
        tier_btns = []
        for i, t in enumerate(TIERS):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setCursor(Qt.PointingHandCursor)
            self.tier_group.addButton(b, i)
            tier_btns.append(b)
        self.sp_count = QSpinBox()
        self.sp_count.setRange(1, 20)
        self.sp_count.hide()                     # tylko przechowuje wartość; widać etykietę między − i +
        self.lb_count = QLabel()
        self.lb_count.setObjectName("count")
        self.lb_count.setFixedWidth(T.S6 * 2)
        self.lb_count.setAlignment(Qt.AlignCenter)
        self.lb_count.setToolTip("Ile przedmiotów imbuujesz tym samym (np. 3× Vampirism)")
        self.sp_count.valueChanged.connect(lambda n: self.lb_count.setText(str(n)))
        bt_minus, bt_plus = T.button("", "minus", "step", "Mniej"), T.button("", "plus", "step", "Więcej")
        for b, d in ((bt_minus, -1), (bt_plus, 1)):
            b.setFixedWidth(T.CONTROL_H)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, d=d: self.sp_count.setValue(self.sp_count.value() + d))
        self.ed_token = GoldEdit("np. 45k")
        self.ed_fee = GoldEdit("opłata", width=T.S6 * 4)
        self.bt_lock = T.icon_button("lock", "")
        self.bt_lock.setCheckable(True)
        self.bt_lock.toggled.connect(self._lock_toggled)
        self.bt_fee_reset = T.button("Przywróć", "undo", "ghost")
        self.bt_fee_reset.setToolTip("Przywróć opłaty z gry (7,5k / 60k / 250k)")
        self.bt_fee_reset.clicked.connect(self._fee_reset)
        self.fee_cap = cap("Opłata")

        self.f_tier = field(cap("Poziom"), *tier_btns)
        self.f_count = field(cap("Ilość sztuk"), bt_minus, self.lb_count, bt_plus)
        self.f_token = field(cap("Cena Gold Tokena"), self.ed_token)
        self.f_fee = field(self.fee_cap, self.ed_fee, self.bt_lock, self.bt_fee_reset)
        self.opt_grid = QGridLayout()
        self.opt_grid.setHorizontalSpacing(T.S6)
        self.opt_grid.setVerticalSpacing(T.S4)
        opt.addLayout(self.opt_grid)
        opt.addStretch(1)
        self._opt_cols = None
        v.addWidget(f)

        # --- 3. materiały + podsumowanie (obok siebie albo jedno pod drugim)
        self.main_box = QBoxLayout(QBoxLayout.LeftToRight)
        self.main_box.setSpacing(T.S4)
        mf, ml = card()
        mf.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # szerokość z układu, nie z treści
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(T.S4)
        self.grid.setVerticalSpacing(0)
        ml.addLayout(self.grid)
        self.note = T.hint("")
        ml.addSpacing(T.S1)
        ml.addWidget(self.note)
        ml.addStretch(1)
        self.main_box.addWidget(mf, 3)

        sf, sl = card(spacing=T.S2)
        self.sum_card = sf
        sl.addWidget(cap("Podsumowanie"))
        self.res_total = T.label("", "display")
        sl.addWidget(self.res_total)
        self.res_sub = T.label("", "muted")
        sl.addWidget(self.res_sub)
        sl.addSpacing(T.S1)
        sl.addWidget(sep())
        self.kv = QGridLayout()
        self.kv.setVerticalSpacing(T.S2)
        self.kv.setHorizontalSpacing(T.S3)
        self.kv_vals = {}
        for i, k in enumerate(("Materiały (1 szt.)", "Opłata (1 szt.)", "Jeden imbuement", f"Na godzinę ({DURATION_H} h)")):
            kl = T.label(k, "muted")
            vl = T.label("", "strong")
            vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.kv.addWidget(kl, i, 0)
            self.kv.addWidget(vl, i, 1)
            self.kv_vals[k] = vl
        sl.addLayout(self.kv)
        sl.addWidget(sep())
        self.res_cmp = T.label("", wrap=True)
        self.res_cmp.setTextFormat(Qt.RichText)
        sl.addWidget(self.res_cmp)
        sl.addStretch(1)
        self.main_box.addWidget(sf, 2)
        v.addLayout(self.main_box)
        v.addStretch(1)

        # --- stan początkowy
        last = st["last"]
        self.cards[self._imb].setChecked(True)
        self.tier_group.button(int(last.get("tier", 2))).setChecked(True)
        self.sp_count.setValue(int(last.get("count", 1)))
        self.lb_count.setText(str(self.sp_count.value()))
        self.ed_token.set_value(st.get("token"))

        self.tier_group.idClicked.connect(lambda _: self._rebuild())
        self.sp_count.valueChanged.connect(lambda _: self._changed())
        self.ed_token.textEdited.connect(lambda _: self._changed())
        self.ed_fee.textEdited.connect(lambda _: self._changed())
        self.price_edits: dict[str, GoldEdit] = {}
        self.item_icons: dict[str, QLabel] = {}
        self.row_widgets = []
        self._lock_toggled(False)
        self._layout_for(1200)
        self._rebuild()

    # --- układ zależny od szerokości
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_for(self.width())

    def _layout_for(self, w: int):
        wide = w >= 980
        self.main_box.setDirection(QBoxLayout.LeftToRight if wide else QBoxLayout.TopToBottom)
        # stała szerokość podsumowania – przełączanie imbuementu/poziomu nie przesuwa kolumn
        if wide:
            self.sum_card.setFixedWidth(320)
        else:
            self.sum_card.setMinimumWidth(0)
            self.sum_card.setMaximumWidth(16777215)
        self.cards_box.setDirection(QBoxLayout.LeftToRight if w >= 620 else QBoxLayout.TopToBottom)
        cols = 4 if w >= 900 else 2
        if cols != self._opt_cols:
            self._opt_cols = cols
            for i, box in enumerate((self.f_tier, self.f_count, self.f_token, self.f_fee)):
                self.opt_grid.removeItem(box)
                self.opt_grid.addLayout(box, i // cols, i % cols, Qt.AlignTop)

    # --- wybór
    @property
    def imb(self) -> str:
        return self._imb

    @property
    def tier(self) -> int:
        return max(self.tier_group.checkedId(), 0)

    def _pick(self, name):
        if name != self._imb:
            self._imb = name
            self._rebuild()

    def _icon_ready(self, kind, name):
        if kind == "imb" and name in self.cards:
            self.cards[name].icon.setPixmap(self.icons.get("imb", name))
        elif kind == "item" and name in self.item_icons:
            self.item_icons[name].setPixmap(self.icons.get("item", name))

    # --- opłata: zablokowana, dopóki ktoś świadomie jej nie odblokuje
    def _lock_toggled(self, unlocked: bool):
        self.ed_fee.setReadOnly(not unlocked)
        self.ed_fee.setObjectName("" if unlocked else "locked")
        self.ed_fee.style().unpolish(self.ed_fee)
        self.ed_fee.style().polish(self.ed_fee)
        self.ed_fee.setFocusPolicy(Qt.StrongFocus if unlocked else Qt.NoFocus)
        self.bt_lock.setIcon(T.icon("unlock" if unlocked else "lock"))
        self.bt_lock.setToolTip("Zablokuj opłatę" if unlocked else
                                "Opłata z gry. Kliknij, żeby odblokować i zmienić (np. gdy CipSoft zmieni cennik).")
        if unlocked:
            self.ed_fee.setFocus()
            self.ed_fee.selectAll()
        self._update_fee_reset()

    def _fee_reset(self):
        self.st["fees"] = list(DEFAULT_FEES)
        self.ed_fee.set_value(DEFAULT_FEES[self.tier])
        self.bt_lock.setChecked(False)
        self._changed()

    def _update_fee_reset(self):
        if hasattr(self, "bt_fee_reset"):
            self.bt_fee_reset.setVisible(self.st["fees"] != list(DEFAULT_FEES))

    # --- tabela materiałów
    def _rebuild(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.price_edits, self.item_icons, self.row_widgets = {}, {}, []
        heads = [("", 0), ("Materiał", 0), ("Cena za sztukę", 2), ("Market", 2), ("Tokeny", 2), ("Wybór", 0)]
        for c, (h, al) in enumerate(heads):
            if h:
                self.grid.addWidget(cap(h), 0, c, Qt.AlignRight if al == 2 else Qt.AlignLeft)
        self.grid.setRowMinimumHeight(0, 24)
        self.grid.addWidget(sep(), 1, 0, 1, len(heads))
        row = 2
        for item, n in groups(self.imb, self.tier):
            ic = QLabel()
            ic.setFixedSize(SIZE + 4, SIZE + 4)
            ic.setAlignment(Qt.AlignCenter)
            ic.setPixmap(self.icons.get("item", item))

            self.grid.addWidget(ic, row, 0)
            self.item_icons[item] = ic

            nm = QLabel(f"<span style='font-weight:{T.W_SEMI}'>{item}</span><br>"
                        f"<span style='color:{T.TEXT_2}'>{n} szt.</span>")
            date = self.st["price_dates"].get(item)
            if date:
                nm.setToolTip(f"Cena wpisana {date}")
            self.grid.addWidget(nm, row, 1)

            ed = GoldEdit(width=96)
            ed.set_value(self.st["prices"].get(item))
            ed.textEdited.connect(lambda _t, it=item: self._price_edited(it))
            self.grid.addWidget(ed, row, 2, Qt.AlignRight)
            self.price_edits[item] = ed
            mk, tk = QLabel(), QLabel()
            for w in (mk, tk):
                w.setMinimumWidth(70)
                w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pill = QLabel()
            self.grid.addWidget(mk, row, 3)
            self.grid.addWidget(tk, row, 4)
            pill.setFixedHeight(20)
            self.grid.addWidget(pill, row, 5, Qt.AlignLeft | Qt.AlignVCenter)
            self.grid.setRowMinimumHeight(row, 56)
            self.row_widgets.append((item, n, mk, tk, pill))
            row += 1
            if row < 2 + 2 * len(groups(self.imb, self.tier)) - 1:
                self.grid.addWidget(sep(), row, 0, 1, len(heads))
            row += 1
        self.grid.setColumnStretch(1, 1)
        for c, wmin in ((0, SIZE + 4), (2, 100), (3, 80), (4, 70), (5, 84)):
            self.grid.setColumnMinimumWidth(c, wmin)
        self.note.setText("Gold Tokeny: u Yany (Adventurer's Guild) kupujesz pakiet – grupę 1, grupy 1–2 albo 1–3 – "
                          f"po {TOKENS_PER_GROUP} tokeny za grupę. Kalkulator sprawdza każdy pakiet z resztą z marketu "
                          "i wybiera najtańszy. Ceny wpisuj jak w grze: 1.5k, 45k, 1kk.")
        self.fee_cap.setText(f"Opłata ({TIERS[self.tier]})")
        self.ed_fee.set_value(self.st["fees"][self.tier])
        self._changed()

    def _price_edited(self, item):
        v = self.price_edits[item].value()
        if v is None:
            self.st["prices"].pop(item, None)
        else:
            self.st["prices"][item] = v
            self.st["price_dates"][item] = datetime.now().strftime("%d.%m.%Y")
        self._changed()

    # --- liczenie
    @staticmethod
    def _pill(lbl: QLabel, kind: str | None, text: str):
        lbl.setObjectName("pill")
        lbl.setProperty("role", {"Market": "ok", "Tokeny": "gold"}.get(kind))
        lbl.setText(text)
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)

    def _changed(self):
        st = self.st
        st["token"] = self.ed_token.value()
        fee = self.ed_fee.value()
        if fee is not None and not self.ed_fee.isReadOnly():
            st["fees"][self.tier] = fee
        self._update_fee_reset()
        st["last"] = {"imb": self.imb, "tier": self.tier, "count": self.sp_count.value()}
        self._save_timer.start()

        token = st["token"]
        tok_group = token * TOKENS_PER_GROUP if token else None
        rows = self.row_widgets
        market = [(st["prices"].get(item) * n if st["prices"].get(item) is not None else None)
                  for item, n, *_ in rows]

        # U Yany kupuje się pakiet: grupa 1, grupy 1–2 albo grupy 1–3. Reszta z marketu.
        # Opcja k = pierwsze k grup za tokeny (k = 0 → wszystko z marketu).
        options = []   # (koszt materiałów, k)
        for k in range(len(rows) + 1):
            if k and tok_group is None:
                break
            rest = market[k:]
            if any(m is None for m in rest):
                continue
            options.append(((tok_group or 0) * k + sum(rest), k))
        best = min(options, key=lambda o: (o[0], -o[1])) if options else None

        for i, (item, n, mk, tk, pill) in enumerate(rows):
            mk.setText(fmt_gold(market[i]) if market[i] is not None else "—")
            tk.setText(fmt_gold(tok_group) if tok_group is not None else "—")
            if best is None:
                need = market[i] is None
                self._pill(pill, None, "wpisz cenę" if need else "—")
                for w in (mk, tk):
                    w.setStyleSheet(f"color:{T.TEXT_3};")
                continue
            kind = "Tokeny" if i < best[1] else "Market"
            self._pill(pill, kind, kind.upper())
            pill.setToolTip("")
            for lbl, chosen in ((mk, kind == "Market"), (tk, kind == "Tokeny")):
                lbl.setStyleSheet(f"color:{T.TEXT if chosen else T.TEXT_3};"
                                  f" font-weight:{T.W_SEMI if chosen else T.W_REGULAR};")

        cnt = self.sp_count.value()
        fee = st["fees"][self.tier]
        self.res_sub.setText(f"{cnt}× {TIERS[self.tier]} {self.imb}")
        self.kv_vals["Opłata (1 szt.)"].setText(fmt_gold(fee))
        if best is None:
            self.res_total.setText("—")
            T.set_role(self.res_total, "faint")
            for k, vl in self.kv_vals.items():
                if k != "Opłata (1 szt.)":
                    vl.setText("—")
            self.res_cmp.setText(f"<span style='color:{T.TEXT_2}'>Wpisz cenę Gold Tokena albo ceny materiałów "
                                 "(tych, których nie kupisz za tokeny).</span>")
            return
        best_sum, best_k = best
        one = best_sum + fee
        total = one * cnt
        self.res_total.setText(fmt_gold(total))
        T.set_role(self.res_total, None)
        self.kv_vals["Materiały (1 szt.)"].setText(fmt_gold(best_sum))
        self.kv_vals["Jeden imbuement"].setText(fmt_gold(one))
        self.kv_vals[f"Na godzinę ({DURATION_H} h)"].setText(fmt_gold(total / DURATION_H))

        def label(k):
            if k == 0:
                return "Wszystko z marketu"
            return f"{TIERS[k - 1]} za {k * TOKENS_PER_GROUP} GT" + (" + market" if k < len(rows) else "")

        html = []
        worst = max(c for c, _ in options)
        if worst > best_sum:
            html.append(f"<div style='color:{T.OK}; font-weight:{T.W_SEMI}; margin-bottom:{T.S2}px'>"
                        f"Oszczędzasz do {fmt_gold((worst - best_sum) * cnt)}</div>")
        html.append("<table width='100%' cellspacing='0' cellpadding='2'>")
        for c, k in options:
            on = k == best_k
            st_ = f"color:{T.TEXT if on else T.TEXT_2}; font-weight:{T.W_SEMI if on else T.W_REGULAR}"
            html.append(f"<tr><td style='{st_}'>{'✓ ' if on else ''}{label(k)}</td>"
                        f"<td align='right' style='{st_}'>{fmt_gold((c + fee) * cnt)}</td></tr>")
        html.append("</table>")
        self.res_cmp.setText("".join(html))
