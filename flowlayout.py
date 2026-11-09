"""FlowLayout – elementy w rzędzie, które zawijają się do następnej linii, gdy brakuje miejsca."""
from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class FlowLayout(QLayout):
    def __init__(self, parent=None, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._items = []
        self._h, self._v = hspacing, vspacing
        self._last_h = None
        self.setContentsMargins(QMargins(0, 0, 0, 0))

    def addItem(self, item):
        self._items.append(item)

    def addSpacing(self, w):        # zgodność z QHBoxLayout – w przepływie odstęp to po prostu pusty element
        from PySide6.QtWidgets import QSpacerItem
        self.addItem(QSpacerItem(w, 1, QSizePolicy.Fixed, QSizePolicy.Minimum))

    def addStretch(self, *_):
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        h = self._do(rect, False)
        if h != self._last_h:
            # po zawinięciu zmieniła się wysokość – rodzic musi przeliczyć układ z nową wysokością
            self._last_h = h
            QTimer.singleShot(0, self._relayout)

    def _relayout(self):
        try:
            self.invalidate()
            if self.parentWidget():
                self.parentWidget().updateGeometry()
        except RuntimeError:
            pass

    def _visible(self):
        return [it for it in self._items
                if not (isinstance(it, QWidgetItem) and it.widget() is not None and it.widget().isHidden())]

    def sizeHint(self):
        m = self.contentsMargins()
        w = sum(it.sizeHint().width() for it in self._visible()) + self._h * max(len(self._visible()) - 1, 0)
        return QSize(w + m.left() + m.right(), self.minimumSize().height())

    def minimumSize(self):
        s = QSize()
        for it in self._visible():
            s = s.expandedTo(it.minimumSize().expandedTo(it.sizeHint()))
        m = self.contentsMargins()
        h = max(s.height(), self._last_h or 0)
        return QSize(s.width() + m.left() + m.right(), h + m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            if isinstance(it, QWidgetItem) and it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x = r.x()
                y += line_h + self._v
                line_h = 0
            if not test:
                # elementy w jednej linii wyrównane do środka w pionie
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() - m.top()
