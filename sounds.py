"""Wspólne dźwięki aplikacji – jedna głośność dla alarmu snajpera i dźwięków schowka."""
from __future__ import annotations

import array
import sys
import threading
import wave
from pathlib import Path

from PySide6.QtWidgets import QApplication

from sniper import make_alarm_wav

DATA = Path(__file__).with_name("huntuploader") / "data"


class Sounds:
    NAMES = ("alarm", "clip", "clip_dup")

    def __init__(self, folder: Path, volume: int):
        self.dir = folder / "sounds"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.volume = -1
        self.set_volume(volume)

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.wav"

    def set_volume(self, volume: int):
        volume = max(0, min(100, int(volume)))
        if volume == self.volume:
            return
        self.volume = volume
        make_alarm_wav(self.path("alarm"), volume)
        gain = (volume / 100) ** 2 * 1.6          # dźwięki schowka są nagrane ciszej niż alarm
        for name in ("clip", "clip_dup"):
            src = DATA / f"{name}.wav"
            if src.exists():
                _scaled_copy(src, self.path(name), gain)

    def play(self, name: str, times: int = 1):
        if self.volume <= 0:
            return
        p = self.path(name)
        if sys.platform == "win32" and p.exists():
            try:
                import winsound

                def run():
                    for _ in range(times):
                        winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                threading.Thread(target=run, daemon=True).start()
                return
            except Exception:
                pass
        QApplication.beep()


def _scaled_copy(src: Path, dst: Path, gain: float):
    with wave.open(str(src), "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    if params.sampwidth != 2:
        dst.write_bytes(src.read_bytes())
        return
    a = array.array("h", frames)
    if sys.byteorder == "big":
        a.byteswap()
    for i, v in enumerate(a):
        a[i] = max(-32767, min(32767, int(v * gain)))
    if sys.byteorder == "big":
        a.byteswap()
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(a.tobytes())
